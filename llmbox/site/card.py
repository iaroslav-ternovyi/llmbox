"""The result card: r/<id>.png, 1200x630, the og:image of a run's page and the picture people post.

  board + the run's records -> spec(): the values drawn, frozen in <HOME>/cards/<id>.json at the first draw
                               (inside the backup: a lost site folder redraws every card exactly as posted)
  spec -> svg(): the picture as SVG text; every string normalised and XML-escaped, no <image>, no link out
  svg -> render(): rsvg-convert reading the SVG on stdin (no base folder: nothing outside is loaded), in IBM Plex -
                   refused when fontconfig does not have Plex, rather than drawn in a fallback font
  draw(): a build draws only the cards whose PNG is missing; a failure is retried at the next build, and after the
          third it is written to <HOME>/cards/<id>.failed (the health check raises an alert) and left

What the card says (design review 2026-10-02): the hardware and model; the speed in a short chat, huge, and 32k
below; where it stands among machines like it (a strip of ticks from 5 others on, their speeds from 1 to 4, or that
it is the first with this model there); a "first" badge when the run holds the entry's credit; NOT CONFIRMED for a
speed over twice the prediction; the quality verdict when a signed-in quality run matches the model's published
score; the best model there; the address and `llmbox test`; the llama.cpp build, the backend and the date.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import unicodedata

from .. import hwclass
from ..hosts import HOME
from .words import esc

CARDS = os.path.join(HOME, "cards")
W, H, M = 1200, 630, 64
INK, MUTED, SOFT, FAINT, AMBER, BG, GRID = "#E8E4D8", "#9a9689", "#cfcabb", "#85817a", "#FFB000", "#0E0F0C", "#15160F"
COND, MONO = "IBM Plex Sans Condensed", "IBM Plex Mono"
TRIES = 3


class CardError(Exception):
    pass


def norm(s, n: int = 120) -> str:
    """Text for the card: NFC, no control characters (XML 1.0 refuses most of them), at most n characters."""
    s = unicodedata.normalize("NFC", str(s if s is not None else ""))
    s = "".join(c for c in s if c in "\t\n" or unicodedata.category(c) != "Cc").replace("\t", " ").replace("\n", " ")
    return s[:n]


def fit(s: str, px: float, width: float) -> str:
    """Cut a line to the width it has (about half the font size per character), with an ellipsis."""
    budget = max(4, int(width / (0.5 * px)))
    return s if len(s) <= budget else s[:budget - 1].rstrip() + "…"


def build_tag(b) -> str | None:
    """llama.cpp's build ("b11312") as the card prints it: only a well-formed tag, never free text from a record."""
    b = str(b or "").strip()
    return b if re.fullmatch(r"b\d{1,6}", b) else None


def spec(board: dict, sid: str, records: list[dict], models: dict, site: str, now: str | None = None, wait: bool = True) -> dict | None:
    """The values a run's card shows, at this moment. None while it is not ready: a run with a quality test waits for
    its explanations to be graded, up to a day after it was received (wait=False: the values now, for its page)."""
    run = board["runs"].get(sid)
    sp = next((r for r in records if r.get("kind") == "speed"), None)
    if not run or not sp:
        return None
    q = next((r for r in records if r.get("kind") == "suite"), None)
    sub = sp.get("submission") or {}
    received = sub.get("received") or ""
    if wait and q and any(x.get("pending") for x in q.get("rows") or []):
        try:
            waited = time.time() - time.mktime(time.strptime(received[:19], "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            waited = 0
        if waited < 86400:
            return None
    entry, cls, rid = run["entry"], run["cls"], run["rid"]
    meta = models.get(rid) or {}
    host = sp.get("host") or {}
    uni = hwclass.entry_kind(entry or "") in ("mac", "chip")
    ram = (f" · {round(host['ram_gib'])} GB" + ("" if uni else " RAM")) if host.get("ram_gib") else ""   # unified memory: the machine's whole memory
    hardware = (entry or hwclass.label(cls.split("|")[0])) + ram
    from . import board as B
    pl = B.place(board, rid, cls, run["t2"], run["machine"])
    ent = (board["entries"].get(entry) or {}) if entry else {}
    credit = ent.get("credit") or {}
    outlier = run["outlier"]
    pred = (sp.get("prediction") or {}).get("decode_tps_no_spec")
    badge = "NOT CONFIRMED" if outlier else credit.get("badge") if credit.get("sid") == sid else None
    if outlier:
        place = {"kind": "outlier", "text": f"over twice the prediction (~{pred:.0f} tok/s) · left out of comparisons until someone repeats it"
                 if pred else "over twice the prediction · left out of comparisons until someone repeats it"}
    elif pl["m"] >= 5:
        place = {"kind": "strip", "text": f"faster than {pl['faster_than']}% of {pl['m']} machines like it", "others": pl["others"]}
    elif pl["m"]:
        place = {"kind": "others", "text": f"{pl['m']} other{'s' if pl['m'] > 1 else ''} like it: " + " · ".join(f"{v:.0f}" for v in sorted(pl["others"])) + " tok/s"}
    elif badge:
        place = {"kind": "first", "text": f"the first {entry} on llmbox" if "USER" not in badge else "llmbox's own PC measured it before"}
    else:
        place = {"kind": "first", "text": f"the first with this model on {hwclass.label(cls)}"}
    quality = None
    if q and "anonymous" not in sub.get("flags", []) and meta.get("range") and meta.get("cap"):
        s = q.get("summary") or {}
        lo, hi = s.get("capability_ci95") or [None, None]
        k = meta["score"] / meta["cap"]
        if lo is not None and hi is not None and lo * k <= meta["range"][1] and hi * k >= meta["range"][0]:
            quality = f"quality: {meta['score']:.0f}% of Claude Opus 5.5 · this run agrees ✓"
    best = ent.get("best")
    if best and best["rid"] == rid:
        last = "llmbox's pick for this machine"
    elif best:
        tps = f"{best['measured']:.0f} tok/s" if best.get("measured") else f"~{best['predicted']:.0f} predicted"
        last = f"best here: {best['name']} · {best['score']:.0f}% of Opus · {tps}" if best.get("score") is not None else f"best here: {best['name']} · {tps}"
    else:
        last = None
    name = meta.get("name") or rid
    model, _, quant = name.rpartition(" ") if " " in name else (name, "", "")
    backend = cls.rsplit("|", 1)[-1].upper() if "|" in cls else ""
    tag = build_tag((sp.get("runtime") or {}).get("llama_cpp_build"))
    return {"v": 1, "id": sid, "hardware": hardware, "model": model, "quant": quant, "t2": round(run["t2"]),
            "deep": round(run["deep"]) if run.get("deep") else None, "badge": badge, "place": place, "quality": quality,
            "last": last, "outlier": outlier, "site": site.split("://", 1)[-1].rstrip("/"), "path": f"/r/{sid}",
            "build": " · ".join(x for x in (f"llama.cpp {tag}" if tag else "llama.cpp", backend, f"as of {received[:10] or (now or '')[:10]}") if x),
            "drawn": now or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def _t(x, y, s, px, fill, family=COND, weight=500, anchor="start", spacing=0.0) -> str:
    ls = f' letter-spacing="{spacing * px:.1f}"' if spacing else ""
    return (f'<text x="{x:.0f}" y="{y:.0f}" font-family="{family}" font-weight="{weight}" font-size="{px}" fill="{fill}"'
            f' text-anchor="{anchor}"{ls}>{esc(norm(s, 200))}</text>')


def _lines(text: str, px: float, width: float, most: int) -> list[str]:
    """Words onto at most `most` lines of the width (about half the font size per character); the last one cut short."""
    per, lines, cur = max(8, int(width / (0.5 * px))), [], ""
    # a number stays with its unit ("~28 tok/s", "32 GB", "45–65 GB/s"): a no-break space between them
    text = re.sub(r"(\d) (tok/s|GB/s|GB|k\b)", "\\1\u00a0\\2", norm(text, 200))
    for w in text.split(" "):
        if cur and len(cur) + 1 + len(w) > per:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    lines.append(cur)
    return lines[:most - 1] + [fit(" ".join(lines[most - 1:]), px, width)] if len(lines) > most else lines


def svg(c: dict) -> str:
    """The card as SVG text. Every string goes through norm() and esc(); nothing is loaded from anywhere.
      LLMBOX                                   [BADGE]
      hardware (46)
      model (34) quant
      63 (210, mono)  tok/s      place line (34, 1-3 lines)
                                 strip: ticks, the ring, end labels
      short chat · 41 at 32k     quality (30, 1-2 lines)
      best here / llmbox's pick (28)
      llmbox.pages.dev/r/<id>                  $ llmbox test
      llama.cpp b11312 · CUDA · as of <date> (24)"""
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
             f'<rect width="{W}" height="{H}" fill="{BG}"/>']
    parts += [f'<path d="M{x} 0V{H}" stroke="{GRID}" stroke-width="1"/>' for x in range(120, W, 120)]
    parts += [f'<path d="M0 {y}H{W}" stroke="{GRID}" stroke-width="1"/>' for y in range(120, H, 120)]
    parts.append(_t(M, 92, "LLMBOX", 30, AMBER, weight=700, spacing=0.18))
    if c.get("badge"):
        b = norm(c["badge"], 40)
        bw = len(b) * 26 * (0.6 + 0.16) + 28   # the letters, their spacing, and 14 px either side
        parts.append(f'<rect x="{W - M - bw:.0f}" y="58" width="{bw:.0f}" height="44" fill="none" stroke="{INK}" stroke-width="2"/>')
        parts.append(_t(W - M - bw / 2 + 2, 89, b, 26, INK, weight=600, anchor="middle", spacing=0.16))
    parts.append(_t(M, 156, fit(norm(c["hardware"]), 46, W - 2 * M), 46, INK, weight=600))
    quant = norm(c.get("quant") or "", 40)
    model = fit(norm(c["model"]), 34, W - 2 * M - (len(quant) + 1) * 17)
    parts.append(f'<text x="{M}" y="202" font-family="{COND}" font-weight="500" font-size="34" fill="{INK}">{esc(model)}'
                 + (f'<tspan dx="12" fill="{MUTED}">{esc(quant)}</tspan>' if quant else "") + "</text>")
    num = str(int(c["t2"]))
    parts.append(_t(M - 6, 400, num, 210, INK if c.get("outlier") else AMBER, family=MONO, weight=500))
    parts.append(_t(M + len(num) * 126 - 2, 400, "tok/s", 34, MUTED))   # the digits' side bearings make the gap
    parts.append(_t(M, 446, "short chat" + (f" · {c['deep']} at 32k context" if c.get("deep") else ""), 30, SOFT))
    x0, x1 = 600, W - M
    pl = c["place"]
    for i, ln in enumerate(_lines(pl["text"], 34, x1 - x0, 2 if pl["kind"] == "strip" else 3 if c.get("quality") else 4)):
        parts.append(_t(x0, 278 + i * 38, ln, 34, INK))
    if pl["kind"] == "strip":
        vals = list(pl["others"]) + [c["t2"]]
        lo, hi = min(vals), max(vals)
        span = (hi - lo) or 1
        X = lambda v: x0 + 20 + (x1 - x0 - 40) * (v - lo) / span
        y = 372
        parts.append(f'<path d="M{x0} {y}H{x1}" stroke="{MUTED}" stroke-width="2"/>')
        parts += [f'<rect x="{X(v) - 3:.1f}" y="{y - 40}" width="6" height="40" fill="{MUTED}"/>' for v in pl["others"]]
        parts.append(f'<circle cx="{X(c["t2"]):.1f}" cy="{y - 20}" r="17" fill="{BG}" stroke="{AMBER}" stroke-width="6"/>')
        parts.append(_t(x0, y + 34, f"{lo:.0f}", 28, MUTED))
        parts.append(_t(x1, y + 34, f"{hi:.0f} tok/s", 28, MUTED, anchor="end"))
    if c.get("quality"):
        for i, ln in enumerate(_lines(c["quality"], 30, x1 - x0, 2)):
            parts.append(_t(x0, 446 + i * 34, ln, 30, INK))
    if c.get("last"):
        parts.append(_t(M, 520, fit(norm(c["last"]), 28, x0 - M - 24 if c.get("quality") else W - 2 * M), 28, SOFT))
    parts.append(f'<text x="{M}" y="560" font-family="{COND}" font-weight="500" font-size="34" fill="{INK}">{esc(norm(c["site"], 60))}'
                 f'<tspan font-size="26" fill="{MUTED}">{esc(norm(c["path"], 30))}</tspan></text>')
    parts.append(_t(W - M, 560, "$ llmbox test", 32, INK, family=MONO, anchor="end"))
    parts.append(_t(M, 594, norm(c["build"], 80), 24, FAINT))
    return "".join(parts) + "</svg>"


def fonts_ok() -> bool:
    """fontconfig has IBM Plex Sans Condensed and IBM Plex Mono (rsvg-convert would otherwise use another font and
    say nothing)."""
    if not shutil.which("fc-match"):
        return False
    return all("Plex" in subprocess.run(["fc-match", f], capture_output=True, text=True).stdout for f in (COND, MONO))


def available() -> bool:
    return bool(shutil.which("rsvg-convert")) and fonts_ok()


def render(text: str) -> bytes:
    """The PNG of an SVG card: rsvg-convert reads it on stdin, so there is no folder to resolve anything against."""
    if not shutil.which("rsvg-convert"):
        raise CardError("rsvg-convert is not installed (apt install librsvg2-bin)")
    if not fonts_ok():
        raise CardError("IBM Plex is not installed (apt install fonts-ibm-plex): the card would come out in another font")
    r = subprocess.run(["rsvg-convert", "-w", str(W), "-h", str(H), "-f", "png"], input=text.encode(), capture_output=True, timeout=60)
    if r.returncode or not r.stdout.startswith(b"\x89PNG"):
        raise CardError(f"rsvg-convert: {(r.stderr or b'').decode(errors='replace').strip()[:200] or 'no PNG'}")
    return r.stdout


def removed() -> dict:
    """{id: reason} of the runs taken off the site (server.withdraw)."""
    p = os.path.join(CARDS, "removed.jsonl")
    if not os.path.exists(p):
        return {}
    return {e["id"]: e["reason"] for e in (json.loads(x) for x in open(p) if x.strip())}


def draw(board: dict, records: dict, models: dict, site: str, out_dir: str, now: str | None = None) -> dict:
    """Every run's card that is missing, drawn: {"drawn", "failed", "waiting", "skipped"}; the cards of removed runs deleted.
    records: {submission id: its records}; models: {recipe id: {name, score, cap, range}}."""
    os.makedirs(CARDS, exist_ok=True)
    d = os.path.join(out_dir, "r")
    os.makedirs(d, exist_ok=True)
    stats = {"drawn": 0, "failed": 0, "waiting": 0, "skipped": 0}
    if render is _render and not available():   # this machine cannot draw at all (a laptop build): no card is counted
        stats["unavailable"] = True              # as failed, so none is given up on; the server checks Plex at setup
        return stats
    gone = removed()
    for sid in gone:
        with __import__("contextlib").suppress(OSError):
            os.remove(os.path.join(d, f"{sid}.png"))
    for sid in board["runs"]:
        if sid in gone or not re.fullmatch(r"[0-9a-f]{12}", sid or ""):
            continue
        png = os.path.join(d, f"{sid}.png")
        if os.path.exists(png) or os.path.exists(os.path.join(CARDS, f"{sid}.failed")):
            stats["skipped"] += 1
            continue
        sp = os.path.join(CARDS, f"{sid}.json")
        c = json.load(open(sp)) if os.path.exists(sp) else spec(board, sid, records.get(sid) or [], models, site, now)
        if c is None:
            stats["waiting"] += 1
            continue
        if not os.path.exists(sp):   # frozen at the first draw: the numbers as they were when it was posted
            json.dump(c, open(sp, "w"), indent=1)
        try:
            data = render(svg(c))   # drawn first: a failure must not leave an empty file that looks drawn
            open(png + ".part", "wb").write(data)
            os.replace(png + ".part", png)
            stats["drawn"] += 1
            with __import__("contextlib").suppress(OSError):
                os.remove(os.path.join(CARDS, f"{sid}.tries"))
        except (CardError, OSError, subprocess.SubprocessError) as e:
            stats["failed"] += 1
            tp = os.path.join(CARDS, f"{sid}.tries")
            n = (int(open(tp).read() or 0) if os.path.exists(tp) else 0) + 1
            open(tp, "w").write(str(n))
            if n >= TRIES:   # given up: the health check reports it for a day
                open(os.path.join(CARDS, f"{sid}.failed"), "w").write(str(e))
    return stats


_render = render   # draw() tells the real renderer from one a test put in its place
