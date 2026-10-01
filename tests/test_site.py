"""The whole site builds from the local records without an exception, and every page links only to pages that exist.
Run: python3 tests/test_site.py"""
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import site  # noqa: E402

out = tempfile.mkdtemp(prefix="llmbox-site-")
pages = site.build(out)
names = {os.path.basename(p) for p in pages}
bad = []
for p in (x for x in pages if x.endswith((".html", ".js"))):
    html = open(p).read()
    for href in re.findall(r'href="([^"#?]+\.html)(?:[#?][^"]*)?"', html):
        if href not in names and "${" not in href:   # a link a page script fills in (recipe-${id}.html)
            bad.append(f"{os.path.basename(p)} -> {href}")
# every page: canonical, social preview, a CSP whose hashes cover its inline scripts; search files beside them
import base64, hashlib  # noqa: E402
for p in (x for x in pages if x.endswith(".html")):
    html, n = open(p).read(), os.path.basename(p)
    for tag in ('rel="canonical"', 'property="og:image"', 'name="twitter:card"', 'http-equiv="Content-Security-Policy"'):
        if tag not in html:
            bad.append(f"{n}: no {tag}")
    csp = re.search(r'Content-Security-Policy" content="([^"]*)"', html)
    for s in re.findall(r"<script>(.*?)</script>", html, re.S):
        h = "'sha256-" + base64.b64encode(hashlib.sha256(s.encode()).digest()).decode() + "'"
        if csp and h not in csp.group(1):
            bad.append(f"{n}: an inline script outside its CSP")
    if (n.startswith("u-") or n in ("account.html", "404.html")) != ('name="robots" content="noindex"' in html):
        bad.append(f"{n}: noindex where it should not be, or missing")
sm = open(os.path.join(out, "sitemap.xml")).read()
if "account" in sm or "/404" in sm or "/u-" in sm or sm.count("<url>") < 50:
    bad.append("sitemap.xml lists a private page or too few")
if "Sitemap:" not in open(os.path.join(out, "robots.txt")).read() or "X-Frame-Options" not in open(os.path.join(out, "_headers")).read():
    bad.append("robots.txt / _headers incomplete")
print(f"{len(pages)} pages built in {out}")
print("broken links and head problems:", bad or "none")
sys.exit(1 if bad or "index.html" not in names or "method.html" not in names else 0)
