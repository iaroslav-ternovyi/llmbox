"""A model's page (recipe-<id>.html): the verdict, where it ranks, what it is good at, speed, how to run it, settings, runs."""
from __future__ import annotations

import json
import os

from .. import report
from .components import _cmp_href, _depth_bars, _fp_html, _groups_html, _groups_legend, _marker, _pct, _stands_sentence
from .data import GPUS
from .layout import _page
from .stats import _axis_of, _range_pct
from .words import _ago, _human, _lineage, _name_of, _quant, _size, CURL, esc, model_name, task_name, variant, SITE_URL


# flags only the reference box needs (its port manager, RAM budget, core count, load mode)
_BOX_ONLY = {"--port": 1, "--cache-ram": 1, "--threads": 1, "--load-mode": 1}


# DRY flags that come with llmbox's --dry-think-only patch: without the patch they would penalize the answer too
_DRY = {"--dry-multiplier", "--dry-base", "--dry-allowed-length", "--dry-penalty-last-n"}


def portable_args(r: dict) -> tuple[list[str], list[str]]:
    """The recipe's llama-server flags as anyone can run them: the model by file name, without what only the reference
    box or llmbox's patched build needs. Returns (args, what was left out)."""
    from .. import recipe as rc
    a = rc.server_args(r)
    out, left = [], []
    i = 0
    while i < len(a):
        x = a[i]
        if x in _BOX_ONLY:
            i += 1 + _BOX_ONLY[x]
        elif x == "--dry-think-only":
            left.append("the repetition penalty inside the thinking only (a llmbox patch)")
            i += 1
        elif x in _DRY and "--dry-think-only" in a:
            i += 2
        elif x == "--reasoning-loop":
            left.append("the reasoning-loop detector (a llmbox patch)")
            i += 2
        elif x == "-m":
            out += ["-m", "@MODEL@/" + os.path.basename(a[i + 1])]   # the caller puts its models folder in
            i += 2
        else:
            out.append(x)
            i += 1
    return out, left


def _FLAG_ORDER(flag: str) -> int:
    """Where a flag goes in a shown command line (llama-server does not care about the order; a reader does)."""
    if flag in ("-m", "--model"):
        return 0
    if flag in ("-c", "--ctx-size"):
        return 1
    if flag in ("--temp", "--top-p", "--top-k", "--min-p", "--presence-penalty", "--repeat-penalty", "-n", "--n-predict"):
        return 2
    if flag in ("--jinja", "--chat-template-kwargs", "--reasoning-budget", "--reasoning-budget-message", "--reasoning-format"):
        return 3
    if flag == "--logit-bias":
        return 9
    return 5


def _cmd_lines(args: list[str], win: bool = False) -> str:
    """One flag with its value per line, quoted for the shell (Windows: cmd.exe quoting and ^ continuations)."""
    import shlex

    def q(v: str) -> str:
        if v == "${PORT}":   # llama-swap's macro, substituted before the command runs
            return v
        if v.startswith("@MODEL@/"):
            return ("C:\\models\\" if win else "~/models/") + v[len("@MODEL@/"):]
        if not win:
            return shlex.quote(v)
        return '"' + v.replace('"', '\\"') + '"' if any(c in v for c in ' "{}') else v
    parts, cur = [], []
    for x in args:
        if x.startswith("-") and not x.lstrip("-").replace(".", "").isdigit() and cur:
            parts.append(" ".join(cur))
            cur = []
        cur.append(q(x))
    if cur:
        parts.append(" ".join(cur))
    parts.sort(key=lambda x: _FLAG_ORDER(x.split(" ")[0]))   # the model, context, sampling and thinking first; tuning after; the loop biases last
    exe = "llama-server.exe" if win else "llama-server"
    return (" ^\n  " if win else " \\\n  ").join([exe] + parts)


def _run_panel(rid: str, rec: dict, model_now: dict | None = None, on_hf: bool | None = None) -> str:
    """Run it yourself: the measured settings for llama-server (Linux, macOS, Windows), llama-swap, LM Studio and Ollama,
    each with a copy button. Only settings with a real equivalent in an app are listed there; what it lacks is said."""
    r = rec.get("recipe") or {}
    m = rec.get("model") or r.get("model") or {}
    if not r.get("placement") or not m.get("file"):
        return ""
    args, left = portable_args(r)
    p, sp, smp = r["placement"], r.get("speculative") or {}, r.get("sampling") or {}
    kw = (r.get("chat") or {}).get("template_kwargs") or {}
    m = dict(m, **{k: v for k, v in (model_now or {}).items() if k in ("hf_repo",) and v})   # a repo fixed in the recipe since the run
    fname, repo = os.path.basename(m["file"]), m.get("hf_repo") or ""
    ctx = p.get("ctx") or 0
    kv = p.get("kv_type") or "f16"
    dl = f"https://huggingface.co/{repo}/resolve/main/{m['file']}" if repo and on_hf is not False else ""
    swap = ("models:\n  " + rid + ":\n    cmd: |\n      "
            + _cmd_lines(["--port", "${PORT}"] + args).replace("~/models/", "/path/to/models/").replace("\n", "\n      "))
    names = [("temp", "Temperature", "temperature"), ("top_p", "Top P", "top_p"), ("top_k", "Top K", "top_k"),
             ("min_p", "Min P", "min_p"), ("presence_penalty", "Presence penalty", "presence_penalty"),
             ("repeat_penalty", "Repeat penalty", "repeat_penalty")]
    think = ", ".join(f"{k} = {json.dumps(v)}" for k, v in kw.items())
    max_tok = smp.get("max_tokens", 32768)
    try:
        from .. import fit as _F
        moe = _F.shape_for(r).is_moe
    except Exception:
        moe = False
    lms = ([("Context Length", f"{ctx:,}" if ctx else "the model's maximum"), ("GPU Offload", "all layers")]
           + ([("MoE expert weights", "on the CPU when the model is bigger than your VRAM (LM Studio's option to keep MoE expert weights on the CPU)")] if moe else [])
           + [
            ("Flash Attention", "on"), ("K Cache / V Cache quantization", kv if kv != "f16" else "off (f16)")]
           + [(label, smp[k]) for k, label, _ in names if k in smp]
           + [("Max response length", f"{max_tok:,} tokens")] + ([("Thinking (chat template)", think)] if think else []))
    lms_txt = "\n".join(f"{k}: {v}" for k, v in lms)
    quant = _quant(fname).split(" ")[0]
    mf = ([f"FROM hf.co/{repo}:{quant}" if repo else f"FROM ./{fname}", f"PARAMETER num_ctx {ctx or 32768}"]
          + [f"PARAMETER {o} {smp[k]}" for k, _, o in names if k in smp] + [f"PARAMETER num_predict {max_tok}"])
    oll_kv = kv if kv in ("f16", "q8_0", "q4_0") else "q8_0"
    oll = "\n".join(mf) + f"\n\n# the server:\nOLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE={oll_kv} ollama serve\n# then:\nollama create {rid} -f Modelfile"
    lacks = ", ".join(x for x in ("MTP speculative decoding" if sp.get("type") == "draft-mtp" else "",
                                   "the anti-loop logit bias" if "--logit-bias" in args else "",
                                   "the thinking budget" if "--reasoning-budget" in args else "") if x)
    left_note = (" Left out: " + "; ".join(left) + " - the model runs without it and may loop a little more often.") if left else ""

    def tab(key: str, body: str, note: str, on: bool = False) -> str:
        n = body.count("\n") + 1
        more = f'<button class="more" type="button">show all {n} lines ▾</button>' if n > 14 else ""
        return (f'<div class="rp{" on" if on else ""}" data-t="{key}"><p class="q rpn">{esc(note)}</p>'
                f'<pre class="cp{" clip" if more else ""}">{esc(body)}</pre>{more}<button class="btn cpy" type="button">COPY</button></div>')
    made = ("" if on_hf is not False else
            f" This file was made on the reference box - the <a href=\"https://huggingface.co/{esc(repo)}\" rel=\"noopener\">{esc(repo)}</a> "
            "GGUF with Qwen3.6's MTP layer grafted in for speculative decoding. With the plain file from that repo, leave out the "
            "two --spec lines: same answers, a little slower" if "graft" in fname else
            " This exact file is not on Hugging Face under this name")
    head = (f'<p class="q" style="margin:0 0 12px">File <b>{esc(fname)}</b>'
            + (f' · <a href="{esc(dl)}" rel="noopener">download it from Hugging Face</a>' if dl else "") + made
            + ". The settings this model was measured with. On another box <code>--fit</code> places the weights for it by itself.</p>")
    return ('<section class="panel pad run"><div class="lbl">Run it yourself</div>' + head
            + '<div class="rtabs" role="group" aria-label="how to run it">'
            + "".join(f'<button type="button" class="{"on" if i == 0 else ""}" data-t="{k}">{t}</button>' for i, (k, t) in
                      enumerate([("one", "One line"), ("srv", "llama-server"), ("win", "Windows"), ("swap", "llama-swap"), ("lms", "LM Studio"), ("oll", "Ollama")]))
            + "</div>"
            + tab("one", f"{CURL} {SITE_URL}/install.sh | sh -s -- {rid}",
                  ("The shortest way (Linux or a Mac): installs llmbox and llama.cpp, fits these settings to your computer, downloads the model, "
                   "and starts it - it asks before each step and before sending anything." if (r.get("runtime") or {}).get("engine", "llama.cpp") == "llama.cpp" else
                   f"This model runs on {(r.get('runtime') or {}).get('engine')}, which has no release builds for llmbox to install: build it "
                   "(github.com/ikawrakow/ik_llama.cpp) into ~/ik_llama.cpp first, then this line does the rest."), on=True)
            + tab("srv", _cmd_lines(args), "Linux and macOS (Metal), with a current llama.cpp." + left_note)
            + tab("win", _cmd_lines(args, win=True), "cmd.exe, with llama-server.exe from a llama.cpp release (the CUDA build for NVIDIA cards)." + left_note)
            + tab("swap", swap, "An entry for llama-swap's config.yaml: one server per model, started when a request asks for it.")
            + tab("lms", lms_txt, "LM Studio on macOS or Windows: the model's load and inference settings."
                  + (f" LM Studio has no {lacks}: expect less speed or more looping than measured here." if lacks else ""))
            + tab("oll", oll, "Ollama: a Modelfile and the server's environment." + (f" Ollama has no {lacks}." if lacks else ""))
            + "</section>")


def _near_html(rid: str, rs: list[dict], clouds: list[dict], ranks: dict, look: dict) -> str:
    """Where the model sits: the two models above and below it, and the Claude models in that span, on the ranking's scale."""
    order = sorted([r for r in rs if r.get("vs_ref") is not None], key=lambda r: (-r["vs_ref"], r["id"]))
    i = next((n for n, r in enumerate(order) if r["id"] == rid), None)
    if i is None:
        return ""
    pick = order[max(0, i - 2): i + 3]
    top, bot = max(r["vs_ref"] for r in pick), min(r["vs_ref"] for r in pick)
    top = 100.5 if i < 2 else top
    cl = [c for c in clouds if c.get("vs_ref") is not None and bot - 0.5 <= c["vs_ref"] <= top + 0.5]
    ax = _axis_of([r.get("vs_ref") for r in rs])
    rows = []
    for r in sorted(pick + cl, key=lambda r: -(r["vs_ref"])):
        cloud = r in cl
        nm = model_name(r)
        if cloud:
            rows.append(f"<div class='nr cl'><span class='rk'>☁</span><div class='mw'><span></span><span class='m'>{esc(nm)}</span></div>"
                        f"{_fp_html(r['vs_ref'], 0, 0, '', ax, cloud=True)}<span></span></div>")
            continue
        col, kind = look[r["id"]]
        lo, hi = _range_pct(r)
        me = r["id"] == rid
        link = f"<span class='m'>{esc(nm)}</span>" if me else f"<a class='m' href='recipe-{esc(r['id'])}.html'>{esc(nm)}</a>"
        rows.append(f"<div class='nr{' me' if me else ''}'><span class='rk'>{ranks[r['id']][0]}</span><div class='mw'>{_marker(col, kind)}{link}<span class='qt'>{esc(variant(r['id'], r['file']))}</span></div>"
                    f"{_fp_html(r['vs_ref'], lo, hi, col, ax)}"
                    + ("<span class='q'>this model</span>" if me else f"<a class='cmpl' href='{esc(_cmp_href(rid, r['id']))}'>compare →</a>") + "</div>")
    labels = "".join(f"<span style='left:{max(0, min(100, (v - ax[0]) / (100 - ax[0]) * 100)):.2f}%'>{v}</span>" for v in range(ax[0], 101, ax[1]))
    return (f"<div class='near'><div class='nr hd'><span></span><span></span><div class='fp'><div class='trk axis'>{labels}</div><span class='num'></span></div><span></span></div>"
            + "".join(rows) + "</div>")


def _settings_panel(rcp: dict, opt: dict | None) -> str:
    """What the model was measured with, split by what sets the score and what only sets the speed, why, and what the
    settings are worth against llama.cpp's defaults on the same box."""
    notes = (rcp.get("notes") or {}).get("lines", [])
    sam, al, pl = rcp.get("sampling") or {}, rcp.get("antiloop") or {}, rcp.get("placement") or {}
    spc, rt = rcp.get("speculative") or {}, rcp.get("runtime") or {}
    chat = (rcp.get("chat") or {}).get("template_kwargs") or {}
    portable = [("Sampling", " · ".join(f"{k.replace('_', '-')} {v}" for k, v in sam.items() if k != "max_tokens") or "server defaults"),
                ("Template", " · ".join(f"{k.replace('_', ' ')} {_human(v)}" for k, v in chat.items()) or "model default"),
                ("Thinking", (f'stops {al.get("reasoning_budget"):,} tokens before the limit' if (al.get("reasoning_budget") or -1) >= 0 else "no limit")
                 + (f' · loop detector {al["reasoning_loop"]}' if al.get("reasoning_loop") else "")),
                ("Anti-loop", (f'bias −{al.get("marker_bias")} on {len(al.get("marker_ids") or [])} tokens that start loops' if al.get("marker_ids") else "none")
                 + (f' · DRY in thinking, allowed {al.get("dry_allowed_length")}' if al.get("dry_think_only") else "")),
                ("KV cache", pl.get("kv_type", "?")),
                ("Speculative", f'{spc.get("type")} · draft {spc.get("draft_max")}' if spc.get("type") else "off")]
    hw = [("Context", f'{round((pl.get("ctx") or 0) / 1024)}k'), ("Batch", f'{pl.get("batch")} / ubatch {pl.get("ubatch")}'),
          ("Threads", f'{rt.get("threads")} on cores {rt.get("cpu_affinity") or "any"}')]
    worth = ""
    if opt:
        s = opt["summary"]
        st, lb = s["stock"], s["llmbox"]
        pct = lambda a, b: f" <em>{(b / a - 1) * 100:+.0f}%</em>" if a and b else ""
        worth = (f"<div class='worth'><div class='sc'>What they are worth on the reference PC</div><dl>"
                 f"<dt>Short chat</dt><dd>{st['decode']:.0f} → <b>{lb['decode']:.0f}</b> tok/s{pct(st['decode'], lb['decode'])}</dd>"
                 + (f"<dt>At 32k</dt><dd>{st['deep']:.0f} → <b>{lb['deep']:.0f}</b> tok/s{pct(st['deep'], lb['deep'])}</dd>" if st.get("deep") and lb.get("deep") else "")
                 + (f"<dt>First word, 12k prompt</dt><dd>{12000 / st['prefill']:.1f} → <b>{12000 / lb['prefill']:.1f}</b> s</dd>" if st.get("prefill") and lb.get("prefill") else "")
                 + f"</dl><p class='q'>Stock = <code>llama-server -m model.gguf -c {pl.get('ctx') or 0}</code> with llama.cpp's own defaults, same file, same box, measured back to back.</p></div>")
    return ('<section class="panel recipe"><div class="lbl">Settings and why</div><div class="rgrid">'
            '<div><div class="sc">Same on every box · these set the score</div><dl>' + "".join(f"<dt>{k}</dt><dd class='val'>{esc(v)}</dd>" for k, v in portable) + "</dl></div>"
            '<div><div class="sc">Fitted to each box · speed only</div><dl>' + "".join(f"<dt>{k}</dt><dd>{esc(v)}</dd>" for k, v in hw) + "</dl>"
            '<p class="q" style="margin-top:10px">values of the reference PC; <code>--fit</code> finds them on yours</p></div></div>'
            + (f'<div class="notes"><div class="sc">Why these settings</div><ul>{"".join(f"<li>{esc(n)}</li>" for n in notes)}</ul></div>' if notes else "")
            + worth + "</section>")


def _shared_panel(rid: str, vs: list[dict], community: list | None, users: dict, ours: dict, k: float | None) -> str:
    """llmbox's settings and the settings people measured instead (llmbox test --set, llmbox/social.py), each with its
    numbers per machine and the votes (filled in by social.js from the API). ours: {t2, t32, score, machines} of the
    recipe; k turns a raw capability into % of Opus."""
    from .. import social
    by_cls = {c["class"]: c for c in community or []}
    pct = lambda cap: f"{cap * k:.0f}%" if cap is not None and k else "—"

    def who(h: str) -> str:
        u = users.get(h) or {}
        return f"<a href='{esc(h)}.html'>{esc(u.get('login') if u.get('public') and u.get('login') else h)}</a>"

    def run_row(r: dict, answers: bool) -> str:
        base, t2, t32 = (by_cls.get(r["cls"]) or {}).get("t2"), r.get("t2"), r.get("t32")
        d = f" <span class='{'up' if t2 >= base else 'dn'}'>{(t2 / base - 1) * 100:+.0f}%</span>" if t2 and base else ""
        score = (f"{pct(r['score'])}<span class='q'> · 10-min test</span>" if r.get("score") is not None
                 else "<span class='q'>not tested</span>" if answers and r["kind"] == "speed" else "")
        flag = " <span class='q'>unconfirmed</span>" if r.get("outlier") else ""
        return (f"<tr><td class='l'>{who(r['by'])}</td><td class='l'>{esc(r['machine'])}</td>"
                f"<td>{f'{t2:.0f}' if t2 else ''}{d}{flag}</td><td>{f'{t32:.0f}' if t32 else ''}</td><td>{score}</td>"
                f"<td class='q'>{esc(r['when'][:10])}</td></tr>")

    t2, t32, n = ours.get("t2"), ours.get("t32"), ours.get("machines")
    summary = " · ".join(x for x in (f"{t2:.0f} tok/s" if t2 else "", f"{t32:.0f} at 32k" if t32 else "") if x)
    blocks = [f"<div class='var ours' data-key='rid:{esc(rid)}'><div class='vhead'><div><b>llmbox's settings</b>"
              f"<span class='q'> · optimized on the reference PC, fitted to each box by <code>--fit</code> (<a href='#run'>run it</a>)</span></div>"
              f"<span class='vote' data-target='rid:{esc(rid)}'></span></div>"
              f"<p class='vsum'>{summary + ' on the reference PC · ' if summary else ''}score {pct(ours.get('score'))}"
              f"{f' · measured on {n} more machine' + ('s' if n != 1 else '') if n else ''}</p></div>"]
    for v in vs:
        cmd = f"llmbox test {rid} " + " ".join(f"--set {f}" for f in social.flags(v["settings"]))
        blocks.append(f"<div class='var' data-key='{esc(v['key'])}'><div class='vhead'><div><span class='set'>{esc(social.describe(v['settings']))}</span>"
                      + ("<span class='q'> · changes the answers</span>" if v["answers"] else "<span class='q'> · speed only</span>")
                      + f"</div><span class='vote' data-target='{esc(v['key'])}'></span>"
                      f"<button class='btn vcopy' type='button' data-copy='{esc(cmd)}' title='{esc(cmd)}'>COPY</button></div>"
                      "<div class='tw'><table class='vruns'><tr><th class='l'>BY</th><th class='l'>MEASURED ON</th><th>TOK/S<br><span class='faint'>vs llmbox's</span></th>"
                      "<th>AT 32K</th><th>SCORE</th><th></th></tr>" + "".join(run_row(r, v["answers"]) for r in v["runs"]) + "</table></div></div>")
    keys = ", ".join(f"<code>{esc(x)}</code>" for x in social.SHAREABLE)
    empty = ("<p class='vnone'>Nobody has shared other settings for this model yet. If you think llmbox's can be beaten on your machine, "
             "measure yours and they appear here, with your numbers, for others to try and vote on.</p>") if not vs else ""
    return (f'<section class="panel pad shared" id="shared"><div class="lbl">Settings people measured</div>'
            f'<p class="q vtop">Other settings people ran this model with, each with the run behind it: a setting is only offered with its measurement. '
            f'The % is against llmbox\'s settings on the same card or Mac. Vote for the ones that work for you.</p>'
            + "".join(blocks) + empty +
            f"<details class='vhow'><summary>Share your settings</summary><p>Signed in (<code>llmbox login</code>): "
            f"<code>llmbox test {esc(rid)} --set placement.ubatch=1024</code>, one <code>--set</code> per setting. "
            f"The run is checked like any other; settings that change the answers get a score from the 10-minute quality test that runs with them. "
            f"Settings that can be shared: {keys}. Paths, the engine and free server flags stay on your machine.</p></details></section>")


def _talk_panel(rid: str) -> str:
    """Comments: rendered by social.js from the API (people's text never goes into the page's HTML)."""
    return (f'<section class="panel pad talk" id="talk"><div class="lbl">Comments</div>'
            f'<div id="comments" data-rid="{esc(rid)}" aria-live="polite"><p class="q">Loading comments…</p></div>'
            '<noscript><p class="q">Comments need JavaScript.</p></noscript>'
            '<p class="q crules">Plain text, signed in with GitHub. Someone who measured this model is marked with the machines they ran it on. '
            'Readers can report a comment: three reports hide it until the operator looks (<a href="terms.html#comments">rules</a>).</p></section>')


def _runs_panel(runs: list[dict], ref: dict | None, counted: dict) -> str:
    rows = []
    for x in sorted(runs, key=lambda r: r.get("created", ""), reverse=True):
        s, h, rt = x["summary"], x.get("host") or {}, x.get("runtime") or {}
        cap = s.get("capability_this_run", s.get("capability"))
        vs = 100 * cap / ref["summary"]["capability"] if ref and cap is not None else None
        su = x.get("suite") or {}
        rows.append(f"<tr><td class='l'><a href='run-{x['id'][:8]}.html'>{x['id'][:8]}</a><br><span class='q'>{_ago(x.get('created', ''))}</span></td>"
                    f"<td class='l'>v{esc(report.version_of(su))}<br><span class='q'>{'adaptive, ' + str(su.get('budget') or 40) + ' min' if su.get('adaptive') else 'every task once' if not su.get('blocks') else 'blocks: ' + ', '.join(su['blocks'])}</span></td>"
                    f"<td class='l cfg'>{esc((h.get('gpu') or '').replace('NVIDIA GeForce ', ''))} · {h.get('ram_gib')} GB<br><span class='q'>llama.cpp {esc(rt.get('llama_cpp_build') or '?')}</span></td>"
                    f"<td>{counted.get(x.get('created'), 0)}</td><td>{_pct(vs)}</td><td>{(s.get('speed') or {}).get('decode_tps') or 0:.0f}</td></tr>")
    return ('<section class="panel runs"><div class="lbl">Runs</div><div class="tw"><table><tr><th class="l">RUN</th><th class="l">SUITE</th><th class="l">BOX</th>'
            '<th>ANSWERS<br><span class="faint">counted</span></th><th>THIS RUN<br><span class="faint">% of Opus</span></th><th>TOK/S</th></tr>' + "".join(rows)
            + '</table></div><p class="q rn">The score at the top pools every answer from these runs that still counts in this version of the test. '
            'Answers to tasks that changed since a run are left out.</p></section>')


def _range_note(pool: list, runs: list) -> str:
    """Why the range is as wide as it is: how many answers make it, and what narrows it."""
    n = len(pool)
    if not n:
        return ""
    return (f"This one is from {n} answer{'s' if n != 1 else ''} in {len(runs)} run{'s' if len(runs) != 1 else ''}; it narrows with every run "
            f"(about half as wide at four times the answers), and <a href=\"method.html#people\">runs from people's computers</a> count too.")


def _people_line(cls: list | None) -> str:
    """How many people's machines measured the model (llmbox submit), besides the reference PC."""
    others = [c for c in cls or [] if not c.get("ref")] + [dict(c, machines=c["machines"] - 1) for c in cls or [] if c.get("ref") and c["machines"] > 1]
    n = sum(c["machines"] for c in others)
    if not n:
        return ""
    kinds = len({c["class"] for c in others})
    return f" People measured it on {n} more machine{'s' if n > 1 else ''} ({kinds} kind{'s' if kinds > 1 else ''} of hardware)."


def recipe_page(rid: str, rec: dict, ref: dict | None, ctx: dict) -> str:
    """A model: the verdict (score, speed and fit on your box, reliability), where it ranks, what it is good at, speed as
    the context grows, how to run it, the settings and why, the runs behind the numbers."""
    s, sp = rec["summary"], rec["summary"]["speed"]
    m, rcp = rec.get("model") or {}, rec.get("recipe") or {}
    rs, ranks, med, look = ctx["rs"], ctx["ranks"], ctx["med"], ctx["look"]
    row = next(r for r in rs if r["id"] == rid)
    col, kind = look[rid]
    nm = _name_of(rec)
    pool, fl = ctx["pool_rows"], ctx["flags"]
    # reliability and lost tasks over the pooled answers, each with its own run's thinking flags
    fx = lambda x: (fl.get(x.get("_run")) or {}).get(x["id"], {})
    n_cut = sum(1 for x in pool if fx(x).get("cut"))
    n_loop = sum(1 for x in pool if fx(x).get("loop"))
    lost = {}
    for x in sorted(pool, key=lambda x: x["score"]):
        if x["score"] < 0.99:
            f = fx(x)
            why = ("no answer within the time limit" if x.get("zero") == "time" else "does not fit its context" if x.get("zero") == "context"
                   else "thinking looped" if f.get("loop") else "ran out of thinking room" if f.get("cut") else "wrong answer" if x["score"] < 0.01 else "partly right")
            lost.setdefault(x["block"], []).append(f"{esc(task_name(x['id']))} <span class='faint'>· {x['score'] * 100:.0f} · {why}</span>")
    pl, lo, hi, _ = ranks[rid]
    vs = row.get("vs_ref")
    rlo, rhi = _range_pct(row)
    tps, deep = sp.get("decode_tps"), report._deep(sp)
    shp = ctx["shape"] or {}
    ref_box = ctx["ref_box"]
    lin = _lineage(m.get("hf_repo"))
    org = (lin.get("model") or m.get("hf_repo") or "").split("/")[0]
    origin = (f"fine-tune of {lin['of'].split('/')[-1]}" if kind == "fine-tune" and lin.get("of") else
              f"uncensored remix of {lin['of'].split('/')[-1]}" if kind == "uncensored" and lin.get("of") else f"release by {org}" if org else "")
    size = _size(shp and {"params": shp.get("params"), "moe": shp.get("moe"), "active": shp.get("active")}, nm)
    hf = f"https://huggingface.co/{m['hf_repo']}" if m.get("hf_repo") else ""
    meta = " · ".join(x for x in (origin, size, f"{m['bytes'] / 1e9:.1f} GB file" if m.get("bytes") else "", f"<a href='{esc(hf)}' rel='noopener'>Hugging Face</a>" if hf else "") if x)
    runs_n = len(ctx["runs"])
    k = vs / row["capability"] if vs is not None and row.get("capability") else None   # raw capability -> % of Opus
    cl = ctx.get("community") or []
    ours = {"t2": tps, "t32": float(deep) if deep != "-" else None, "score": row.get("capability"),
            "machines": sum(c["machines"] for c in cl if not c.get("ref")) + sum(c["machines"] - 1 for c in cl if c.get("ref"))}
    tiles = (f"<div><span class='sc'>Score</span><b>{_pct(vs)}</b><span>of Claude Opus 5.5 · range {rlo:.0f}–{rhi:.0f}</span>"
             f"<span>place {pl} of {len(rs)}{f' · tied with {lo}–{hi}' if lo != hi else ''}</span></div>"
             f"<div id='vspd'><span class='sc'>Speed</span><b>{f'{tps:.0f}' if tps else '—'}<small> tok/s</small></b>"
             f"<span class='sub'>short chat{f' · {float(deep):.0f} with a long document' if deep != '-' else ''}</span><span class='src'>measured on the reference PC</span></div>"
             f"<div id='vfit'><span class='sc'>Fits</span><b>{'✓ ' + str(round(shp['ctx'] / 1024)) + 'k' if shp.get('ctx') else '—'}</b>"
             f"<span class='sub'>context on the reference PC</span><span class='src'>{esc(ref_box)}</span></div>"
             f"<div><span class='sc'>Reliability</span><b>{n_cut + n_loop}<small> of {len(pool)}</small></b>"
             f"<span>answers {'where the thinking ran out of room (' + str(n_cut) + ') or looped (' + str(n_loop) + ')' if n_cut + n_loop else 'with a thinking problem: none'}</span>"
             f"<span class='src'>{runs_n} run{'s' if runs_n != 1 else ''} · {ctx['solved_h']:.0f} tasks solved per hour</span></div>")
    body = f'''
<section class="panel title"><div><div class="crumb"><a href="index.html">Models</a> / {esc(nm)}</div>
 <h1>{_marker(col, kind, 18)} {esc(nm)} <span class="muted" style="font-weight:500">· {esc(variant(rid, m.get("file"), full=True))}</span></h1>
 <div class="meta">{meta}</div></div>
 <div class="acts"><a class="btn solid" href="#run">RUN IT</a><a class="btn" href="{esc(ctx['cmp'])}">COMPARE</a></div></section>
{f'<section class="panel cavp"><b>⚠ This test had a flaw.</b> {esc(ctx["caveat"])}</section>' if ctx.get("caveat") else ""}
<section class="panel verdict"><div class="tiles">{tiles}</div><p class="say">{_stands_sentence(s["blocks"], med)}</p></section>
<section class="panel pad"><div class="lbl">Where it ranks</div>{_near_html(rid, rs, ctx["clouds"], ranks, look)}
 <p class="q" style="margin-top:12px">% of Claude Opus 5.5's score on the same tasks. The line is the 95% range: models whose lines overlap are not measurably apart yet.
 {_range_note(pool, ctx.get("runs") or [])} <a href="index.html">Full ranking →</a></p></section>
<section class="panel pad"><div class="lbl">What it is good at</div>{_groups_html(s["blocks"], med, col, (ref or {}).get("summary", {}).get("blocks"), lost)}
 {_groups_legend(ref=bool(ref))}<p class="q">Click a line to see the tasks it lost and why.</p></section>
<section class="panel pad"><div class="lbl">Speed as the context grows</div>
 <p class="yb" id="yourbox" hidden></p>{_depth_bars([(rid, sp.get("by_depth") or {})])}
 <p class="q" style="margin-top:16px">Measured on the reference PC ({esc(ref_box)}).{_people_line(ctx.get("community"))} <a href="hardware-{esc(rid)}.html">Speed on {len(GPUS)} other graphics cards and Macs →</a></p></section>
<div id="run"></div>{_run_panel(rid, rec, ctx["model_now"], ctx["on_hf"])}
{_settings_panel(rcp, ctx["opt"])}
{_shared_panel(rid, ctx.get("variants") or [], ctx.get("community"), ctx.get("users") or {}, ours, k)}
{_runs_panel(ctx["runs"], ref, ctx["counted"])}
{_talk_panel(rid)}'''
    about = (f"{nm} {variant(rid, m.get('file'), full=True)}: {_pct(vs)} of Claude Opus 5.5 on real work (coding, tools, documents, writing)"
             + (f", {tps:.0f} tok/s on {ref_box}" if tps else "") + ". The file, and the settings to run it in llama.cpp, LM Studio or Ollama.")
    return _page(f"llmbox · {nm} · {variant(rid, m.get('file'), full=True)}", "MODELS", body, ("pages.css", "model.css"),
                 ("plan.js", "model.js", "runcmd.js", "social.js"), {"sh": shp, "gpus": GPUS, "ref": ctx["ref_hw"], "api": ctx.get("api") or "", "rid": rid},
                 about=about)


