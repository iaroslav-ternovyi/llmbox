"""`llmbox report`: leaderboard from saved suite results - terminal table and a self-contained static HTML page.

The HTML is the seed of the public site: one row per (model file + recipe + hardware), capability with its confidence
interval, per-block scores, measured speed and solved tasks per hour. No external assets (works offline, easy to host).
"""
from __future__ import annotations

import html
import json

from . import results

BLOCK_NAMES = {"agentic": "Agentic coding", "code": "Code", "tools": "Tools & automation", "techhelp": "Tech help",
               "knowledge": "Knowledge", "explain": "Explanations", "longctx": "Long documents", "writing": "Writing",
               "reasoning": "Reasoning"}


def latest_probe(recs: list[dict], rid: str, after: str = "") -> dict | None:
    """Newest 1-stream speed re-measurement of a recipe (`llmbox probe`, e.g. after `llmbox tune`) made after `after`."""
    ps = [x for x in recs if x.get("kind") == "probe" and (x.get("recipe") or {}).get("id") == rid and x.get("created", "") > after
          and ((x.get("summary") or {}).get("speed") or {}).get("decode_tps")]
    return max(ps, key=lambda x: x["created"]) if ps else None


def with_probe(rec: dict, recs: list[dict]) -> dict:
    """The suite record with its speed replaced by a newer re-measurement (quality is the suite's; speed is today's)."""
    p = latest_probe(recs, (rec.get("recipe") or {}).get("id"), rec.get("created", ""))
    if not p:
        return rec
    out = dict(rec, summary=dict(rec["summary"], speed=p["summary"]["speed"]))
    out["speed_note"] = f"re-measured {p['created'][:10]}" + (" after tune" if p.get("tuned") else "")
    return out


def _vkey(v) -> tuple:
    """'0.9.2' < '0.10-dev' < '0.10': numbers compare as numbers, a -dev version sorts before its release."""
    import re as _re
    nums = tuple(int(x) for x in _re.findall(r"\d+", str(v or "0").split("-")[0]))
    return nums + ((0,) if "-" in str(v) else (1,))


def rows(host: str | None = None, suite_version: str | None = None, tier: str | None = None) -> list[dict]:
    out = []
    recs = results.load_all(host) + (results.load_all("cloud") if host and host != "cloud" else [])
    from . import bench
    recs = [bench.rescore(with_probe(x, recs)) if x.get("kind") == "suite" else x for x in recs]   # current weights
    for rec in recs:
        if rec.get("kind") != "suite":
            continue
        su, s = rec.get("suite", {}), rec.get("summary", {})
        if suite_version and su.get("version") != suite_version:
            continue
        if tier and su.get("tier") != tier:
            continue
        r = rec.get("recipe") or {}
        m = rec.get("model") or {}
        part = su.get("blocks")   # a run of only some blocks: its capability is not comparable with full runs
        out.append({
            "id": (r.get("id") or "?") + (f" [{'+'.join(part)} only]" if part else ""), "partial": bool(part),
            "description": r.get("description", ""), "hf_repo": m.get("hf_repo"),
            "file": m.get("file") or (m.get("path") or "").split("/")[-1], "host": rec["host"], "suite": su,
            "capability": s.get("capability"), "ci": s.get("capability_ci95"), "blocks": s.get("blocks", {}),
            "speed": s.get("speed", {}), "solved_per_hour": s.get("solved_per_hour"), "items": s.get("items"),
            "wall_minutes": s.get("wall_minutes"), "created": rec.get("created", "")[:16],
            "recipe": {k: r.get(k) for k in ("placement", "speculative", "sampling", "chat", "antiloop") if k in r},
        })
    if suite_version is None and out:  # default: only the newest suite version, results of older versions are not comparable
        newest = max(out, key=lambda x: _vkey(x["suite"].get("version")))["suite"].get("version")
        out = [x for x in out if x["suite"].get("version") == newest]
    # newest result per (recipe id, host, suite version, tier)
    best: dict = {}
    for x in sorted(out, key=lambda x: x["created"]):
        best[(x["id"], x["host"].get("id"), x["suite"].get("version"), x["suite"].get("tier"))] = x
    rs = sorted(best.values(), key=lambda x: (x.get("partial", False), -(x["capability"] or 0)))   # partial runs listed last
    # 100% = the best frontier reference on the same suite version and tier (per block as well)
    for x in rs:
        refs = [r for r in rs if r["host"].get("id") == "cloud" and r["suite"].get("version") == x["suite"].get("version")
                and r["suite"].get("tier") == x["suite"].get("tier")]
        if refs:
            ref = max(refs, key=lambda r: r["capability"] or 0)
            x["ref"] = ref["id"]
            x["vs_ref"] = round(100 * x["capability"] / ref["capability"], 1) if ref["capability"] else None
            x["blocks_vs_ref"] = {b: round(100 * v / ref["blocks"][b], 1) for b, v in x["blocks"].items() if ref["blocks"].get(b)}
    return rs


def _depth_k(key: str) -> float:
    """'28k' / '88k' (speed probe) or '32-96k' / '96k+' (in-run buckets) -> lower bound in thousands of tokens"""
    import re
    m = re.match(r"(\d+)", key)
    return float(m.group(1)) if m else 0.0


def _deep(sp: dict) -> str:
    """slowest decode deep in the context (>= 24k tokens), where long agent sessions and documents spend their time"""
    bd = sp.get("by_depth") or {}
    v = [d["decode_tps"] for k, d in bd.items() if _depth_k(k) >= 24 and d.get("decode_tps")]
    return f"{min(v):.1f}" if v else "-"


def text_table(rs: list[dict]) -> str:
    if not rs:
        return "no suite results yet"
    blocks = [b for b in BLOCK_NAMES if any(b in r["blocks"] for r in rs)]
    head = (f"{'#':>2} {'model / recipe':24s} {'capability':>16s} {'vs ref':>6s} " + " ".join(f"{b[:7]:>7s}" for b in blocks)
            + f" {'tok/s':>6s} {'deep':>6s} {'step s':>6s} {'solved/h':>8s}  suite")
    lines = [head, "-" * len(head)]
    for i, r in enumerate(rs, 1):
        ci = f"({r['ci'][0]:.0f}-{r['ci'][1]:.0f})" if r.get("ci") else ""
        sp = r["speed"]
        vs = f"{r['vs_ref']:.0f}%" if r.get("vs_ref") is not None else "-"
        lines.append(f"{i:>2} {r['id'][:24]:24s} {r['capability']:>6.1f} {ci:>9s} {vs:>6s} "
                     + " ".join(f"{r['blocks'].get(b, float('nan')):>7.1f}" for b in blocks)
                     + f" {sp.get('decode_tps') or 0:>6.1f} {_deep(sp):>6s} {sp.get('typical_agent_step_s') or 0:>6.1f} {r['solved_per_hour'] or 0:>8.1f}"
                     + f"  v{r['suite'].get('version')}/{r['suite'].get('tier')}")
    return "\n".join(lines)


_CSS = """
:root{--bg:#f7f7f5;--fg:#1c1c1a;--muted:#6b6b66;--card:#fff;--line:#e3e2dc;--acc:#2f6f5e;--acc2:#b4d8cc;--warn:#b05a2a}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#141413;--fg:#ecebe6;--muted:#9c9b94;--card:#1d1d1b;--line:#2e2d2a;--acc:#6cc2a7;--acc2:#27463d;--warn:#e39a6b}}
:root[data-theme=dark]{--bg:#141413;--fg:#ecebe6;--muted:#9c9b94;--card:#1d1d1b;--line:#2e2d2a;--acc:#6cc2a7;--acc2:#27463d;--warn:#e39a6b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1180px;margin:0 auto;padding:28px 16px 60px}h1{font-size:26px;margin:0 0 4px}p.sub{color:var(--muted);margin:0 0 22px}
.hw{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 18px}.pill{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:4px 12px;font-size:13px;color:var(--muted)}
.wrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:14px}
table{border-collapse:collapse;width:100%;min-width:860px}th,td{padding:12px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle}
th{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);font-weight:600}tr:last-child td{border-bottom:0}
.rank{font-weight:700;color:var(--muted);width:32px}.name{font-weight:600}.file{font-size:12px;color:var(--muted)}
.cap{min-width:190px}.capnum{font-size:20px;font-weight:700}.ci{font-size:12px;color:var(--muted);margin-left:6px}
.bar{position:relative;height:8px;background:var(--line);border-radius:6px;margin-top:6px}.bar i{position:absolute;top:0;height:8px;border-radius:6px}
.bar .v{background:var(--acc)}.bar .r{background:var(--acc2);opacity:.9}
.blocks{display:grid;grid-template-columns:repeat(3,minmax(90px,1fr));gap:4px 10px;font-size:12px;color:var(--muted)}
.blocks b{color:var(--fg);font-weight:600}.speed b{font-size:16px}.lab{font-size:12px;padding:1px 8px;border-radius:999px;background:var(--acc2);color:var(--fg);margin-left:6px}
.lab.slow{background:none;border:1px solid var(--warn);color:var(--warn)}.sph{font-size:18px;font-weight:700}
details{margin-top:6px}summary{cursor:pointer;color:var(--muted);font-size:12px}pre{white-space:pre-wrap;font-size:11px;color:var(--muted);margin:6px 0 0}
footer{margin-top:22px;color:var(--muted);font-size:13px}a{color:var(--acc)}
"""


def html_page(rs: list[dict], title: str = "Local LLM leaderboard") -> str:
    hosts = {r["host"].get("id"): r["host"] for r in rs}
    pills = "".join(f'<span class="pill">{html.escape(h.get("gpu") or "")} · {h.get("vram_gib")} GB VRAM · '
                    f'{h.get("ram_gib")} GB RAM @ {h.get("ram_read_gbs") or "?"} GB/s · {html.escape(h.get("cpu") or "")}</span>'
                    for h in hosts.values())
    body = []
    for i, r in enumerate(rs, 1):
        lo, hi = r.get("ci") or [r["capability"], r["capability"]]
        sp = r["speed"]
        lab = sp.get("label") or ""
        model = html.escape(r["id"])
        link = f'<a href="https://huggingface.co/{html.escape(r["hf_repo"])}">{html.escape(r["hf_repo"])}</a>' if r.get("hf_repo") else ""
        blocks = "".join(f'<span>{BLOCK_NAMES.get(b, b)} <b>{v:.0f}</b></span>' for b, v in r["blocks"].items())
        if r["host"].get("id") == "cloud":
            rel = '<div class="file">frontier reference = 100%</div>'
        elif r.get("vs_ref") is not None:
            rel = f'<div class="file">{r["vs_ref"]:.0f}% of {html.escape(r["ref"])}</div>'
        else:
            rel = ""
        body.append(
            f'<tr><td class="rank">{i}</td>'
            f'<td><div class="name">{model}</div><div class="file">{html.escape(r["file"] or "")}</div><div class="file">{link}</div>'
            f'<details><summary>recipe</summary><pre>{html.escape(json.dumps(r["recipe"], indent=1, ensure_ascii=False))}</pre></details></td>'
            f'<td class="cap"><span class="capnum">{r["capability"]:.1f}</span><span class="ci">{lo:.0f}–{hi:.0f}</span>{rel}'
            f'<div class="bar"><i class="r" style="left:{lo}%;width:{max(hi - lo, 0.5)}%"></i><i class="v" style="left:0;width:{r["capability"]}%;opacity:.35"></i></div></td>'
            f'<td><div class="blocks">{blocks}</div></td>'
            f'<td class="speed"><b>{sp.get("decode_tps") or "?"}</b> tok/s<span class="lab {lab}">{lab}</span>'
            f'<div class="file">agent step ≈ {sp.get("typical_agent_step_s") or "?"} s · prefill {sp.get("prefill_tps") or "?"} tok/s</div>'
            + (('<div class="file">by context depth: ' + " · ".join(f'{html.escape(k)} {v["decode_tps"] or "?"} tok/s'
                                                                  for k, v in sp["by_depth"].items()) + "</div>") if sp.get("by_depth") else "")
            + '</td>'
            f'<td><span class="sph">{r["solved_per_hour"] or "?"}</span><div class="file">solved tasks / hour</div></td></tr>')
    ver = sorted({f'v{r["suite"].get("version")} {r["suite"].get("tier")}' for r in rs})
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(title)}</title><style>{_CSS}</style></head><body><main>'
            f'<h1>{html.escape(title)}</h1><p class="sub">Every model in its own best configuration, measured on the same hardware with the '
            f'llmbox standard suite: generated tasks weighted by how people use local LLMs (agentic coding 25%, code 10%, tools 20%, '
            f'long documents 15%, writing 15%, reasoning 15%). Capability is hardware-independent; speed and solved/hour are for this machine.</p>'
            f'<div class="hw">{pills}</div><div class="wrap"><table><thead><tr><th>#</th><th>Model / recipe</th><th>Capability (95% CI)</th>'
            f'<th>By block</th><th>Speed</th><th>Useful work</th></tr></thead><tbody>{"".join(body)}</tbody></table></div>'
            f'<footer>Suite: {", ".join(ver)}. Differences inside the confidence interval are noise.</footer></main></body></html>')
