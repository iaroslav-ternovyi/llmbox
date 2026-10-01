"""What a public site needs beyond its pages, added once every page is written: per page the canonical address, the
social preview (Open Graph / Twitter card), search hints and a Content-Security-Policy with the hashes of its inline
scripts; for the site sitemap.xml, robots.txt and _headers (Cloudflare Pages' header rules).

Addresses are extensionless (https://site/recipe-x): Cloudflare Pages redirects /recipe-x.html there, so that is the
address a search engine keeps. The CSP sits in each page as a meta tag because Pages allows only 100 header rules and
every page has its own inline DATA script."""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
import time

from ..public import server as _server, site as _site, stats as _stats

OG_IMAGE = "og.png"   # 1200x630, in assets/
# pages no search engine should list: a person's profile (people are found by choice, not by search), sign-in, errors
NOINDEX = re.compile(r"^(u-[0-9a-f]{8}|account|404)\.html$")


def _url(site: str, name: str) -> str:
    return site + "/" if name == "index.html" else f"{site}/{name[:-5]}"


def _ld(name: str, site: str, title: str, about: str) -> str:
    """schema.org data for the two pages a search engine should understand: the ranking (a dataset) and the tool."""
    if name == "index.html":
        d = [{"@context": "https://schema.org", "@type": "WebSite", "name": "llmbox", "url": site + "/", "description": about},
             {"@context": "https://schema.org", "@type": "Dataset", "name": "llmbox: local LLMs graded on real work and timed on real PCs",
              "description": about, "url": site + "/", "license": "https://www.apache.org/licenses/LICENSE-2.0",
              "creator": {"@type": "Organization", "name": "llmbox", "url": "https://github.com/iaroslav-ternovyi/llmbox"},
              "isAccessibleForFree": True, "dateModified": time.strftime("%Y-%m-%d")}]
    elif name == "install.html":
        d = [{"@context": "https://schema.org", "@type": "SoftwareApplication", "name": "llmbox", "applicationCategory": "DeveloperApplication",
              "operatingSystem": "Linux, macOS, Windows (WSL2)", "url": site + "/install", "description": about,
              "license": "https://www.apache.org/licenses/LICENSE-2.0", "offers": {"@type": "Offer", "price": "0", "priceCurrency": "EUR"},
              "codeRepository": "https://github.com/iaroslav-ternovyi/llmbox"}]
    else:
        return ""
    return "".join(f'<script type="application/ld+json">{json.dumps(x, ensure_ascii=False).replace("</", "<\\/")}</script>' for x in d)


def _csp(html: str, api: str) -> str:
    """Scripts: the site's own files and the page's inline ones by hash; the API for sign-in; fonts and styles as used."""
    hashes = " ".join(f"'sha256-{base64.b64encode(hashlib.sha256(s.encode()).digest()).decode()}'"
                      for s in re.findall(r"<script(?: type=\"application/ld\+json\")?>(.*?)</script>", html, re.S))
    fonts = "https://fonts.googleapis.com" if "fonts.googleapis.com" in html else ""
    return ("default-src 'self'; "
            f"script-src 'self' {hashes}; "
            f"style-src 'self' 'unsafe-inline' {fonts}; "
            f"font-src 'self'{' https://fonts.gstatic.com' if fonts else ''}; "
            f"img-src 'self' data: {_stats()}; "   # the visit counter is an image request to our GoatCounter
            f"connect-src 'self' {api}; "
            "base-uri 'self'; form-action 'self'; object-src 'none'").replace("  ", " ")


def head(html: str, name: str, site: str, api: str) -> str:
    """The page with its head completed (idempotent: a page that already has a canonical link is returned as it is)."""
    if 'rel="canonical"' in html:
        return html
    title = re.search(r"<title>(.*?)</title>", html, re.S)
    about = re.search(r'<meta name="description" content="([^"]*)"', html)
    title, about = (title.group(1) if title else "llmbox"), (about.group(1) if about else "")
    url = _url(site, name)
    tags = (f'<link rel="canonical" href="{url}">'
            f'<meta property="og:type" content="website"><meta property="og:site_name" content="llmbox">'
            f'<meta property="og:title" content="{title}"><meta property="og:description" content="{about}">'
            f'<meta property="og:url" content="{url}"><meta property="og:image" content="{site}/{OG_IMAGE}">'
            f'<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">'
            f'<meta name="twitter:card" content="summary_large_image"><meta name="theme-color" content="#0E0F0C">'
            + ('<meta name="robots" content="noindex">' if NOINDEX.match(name) else ""))
    html = html.replace("</head>", tags + _ld(name, site, title, about) + "</head>", 1)
    # the CSP last: it hashes the inline scripts, the structured data among them
    return html.replace("<head>", f'<head><meta http-equiv="Content-Security-Policy" content="{_csp(html, api)}">', 1)


DATA_LICENSE = """llmbox data: model scores, ranges and speeds - {site}/data/
License: Creative Commons Attribution 4.0 International (CC BY 4.0), https://creativecommons.org/licenses/by/4.0/
Credit: "llmbox ({site})". Built {built}; how the numbers are made: {site}/method

models.csv   a row per model and settings (recipe): its score as % of Claude Opus 5.5 with the 95% range, the score per
             use, the model file (Hugging Face repo, sha256), and its speed on the reference PC
speeds.csv   measurements per model and hardware class (the reference PC is one machine among them): median tokens/s in a
             short chat, 32k and 80k tokens into a session, and how many machines
models.json  the same as models.csv, as the site's model list carries it (recipes/index.json has the settings too)
"""


def data_export(out_dir: str, site: str) -> list[str]:
    """data/: the numbers behind the ranking for anyone to recompute or cite (CC BY 4.0), from the published model list."""
    p = os.path.join(out_dir, "recipes", "index.json")
    if not os.path.exists(p):
        return []
    idx = json.load(open(p))
    d = os.path.join(out_dir, "data")
    os.makedirs(d, exist_ok=True)
    uses = sorted({u for e in idx["recipes"] for u in (e.get("uses") or {})})
    m = io.StringIO()
    w = csv.writer(m)
    w.writerow(["id", "model", "score_pct_of_opus", "range_low", "range_high"] + [f"use_{u}" for u in uses]
               + ["hf_repo", "file", "sha256", "engine", "ref_gpu", "ref_ram_read_gbs", "ref_tokens_per_s", "ref_tokens_per_s_deep",
                  "ref_deep_k_tokens", "ref_measured"])
    rows = []
    for e in sorted(idx["recipes"], key=lambda e: -(e.get("score") or 0)):
        ref = e.get("reference") or {}
        rng = e.get("range") or [None, None]
        w.writerow([e["id"], e.get("name"), e.get("score"), rng[0], rng[1]] + [(e.get("uses") or {}).get(u) for u in uses]
                   + [e.get("hf_repo"), e.get("file"), e.get("sha256"), e.get("engine"), (ref.get("gpu") or "").replace("NVIDIA GeForce ", ""),
                      ref.get("ram_bw_gbs"), ref.get("decode_tps"), ref.get("deep_tps"), ref.get("deep_k"), ref.get("measured")])
        rows.append({k: e.get(k) for k in ("id", "name", "score", "range", "uses", "hf_repo", "file", "sha256", "engine", "reference", "measured")})
    s = io.StringIO()
    ws = csv.writer(s)
    ws.writerow(["id", "hardware_class", "median_tokens_per_s", "median_tokens_per_s_at_32k", "median_tokens_per_s_at_80k", "machines"])
    for e in idx["recipes"]:
        for cls, v in sorted((e.get("measured") or {}).items()):
            ws.writerow([e["id"], cls] + list(v[:4]))   # build.py: [t2, t32, t80, machines]
    files = {"models.csv": m.getvalue(), "speeds.csv": s.getvalue(),
             "models.json": json.dumps({"license": "CC BY 4.0", "credit": f"llmbox ({site})", "built": idx.get("built"), "models": rows}, indent=1),
             "LICENSE.txt": DATA_LICENSE.format(site=site, built=idx.get("built"))}
    out = []
    for n, text in files.items():
        open(os.path.join(d, n), "w", encoding="utf-8").write(text)
        out.append(os.path.join(d, n))
    return out


def finish(out_dir: str, written: list[str]) -> list[str]:
    """Complete every written page's head; write sitemap.xml, robots.txt and _headers. Returns the files it wrote."""
    site, api = _site(), _server()
    pages = sorted(os.path.basename(p) for p in written if p.endswith(".html"))
    for name in pages:
        p = os.path.join(out_dir, name)
        html = open(p, encoding="utf-8").read()
        new = head(html, name, site, api)
        if new != html:
            open(p, "w", encoding="utf-8").write(new)
    day = time.strftime("%Y-%m-%d")
    listed = [n for n in pages if not NOINDEX.match(n)]
    sitemap = ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
               + "".join(f"<url><loc>{_url(site, n)}</loc><lastmod>{day}</lastmod></url>\n" for n in listed) + "</urlset>\n")
    files = {"sitemap.xml": sitemap,
             "robots.txt": f"User-agent: *\nAllow: /\nDisallow: /account\n\nSitemap: {site}/sitemap.xml\n",
             # Cloudflare Pages header rules (the CSP is per page, in its head)
             "_headers": ("/*\n  X-Content-Type-Options: nosniff\n  X-Frame-Options: DENY\n  Referrer-Policy: strict-origin-when-cross-origin\n"
                          "  Permissions-Policy: camera=(), microphone=(), geolocation=(), interest-cohort=()\n"
                          "  Strict-Transport-Security: max-age=31536000; includeSubDomains\n"
                          "/*.css\n  Cache-Control: public, max-age=31536000, immutable\n"
                          "/*.js\n  Cache-Control: public, max-age=31536000, immutable\n"
                          "/install.sh\n  Content-Type: text/plain; charset=utf-8\n  Cache-Control: public, max-age=300\n")}
    if _stats():   # deploy/pages/functions counts these two (Cloudflare Pages); every other path stays static and free
        files["_routes.json"] = json.dumps({"version": 1, "include": ["/install.sh", "/recipes/index.json"], "exclude": []})
    # security.txt (RFC 9116): where to report a vulnerability - GitHub's private reporting
    files[".well-known/security.txt"] = ("Contact: https://github.com/iaroslav-ternovyi/llmbox/security/advisories/new\n"
                                         f"Expires: {time.strftime('%Y-%m-%dT00:00:00Z', time.gmtime(time.time() + 300 * 86400))}\n"
                                         f"Preferred-Languages: en, ru, uk, it\nCanonical: {site}/.well-known/security.txt\n"
                                         "Policy: https://github.com/iaroslav-ternovyi/llmbox/blob/main/SECURITY.md\n")
    out = []
    for n, text in files.items():
        p = os.path.join(out_dir, n)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w", encoding="utf-8").write(text)
        out.append(p)
    return out + data_export(out_dir, site)
