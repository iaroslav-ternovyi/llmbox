"""How the numbers are made (method.html)."""
from __future__ import annotations

from .components import _fp_html, _marker
from .layout import _page
from .stats import _axis_of, _range_pct, surely_better
from .words import _GRADING, _name_of, esc, GROUPS, INSTALL, model_name, NOT_MEASURED, share, TIPS


def _optimize_table(opts: dict, ranked: set | None = None) -> str:
    rows = sorted(opts.items(), key=lambda kv: -(kv[1]["summary"]["llmbox"]["decode"] / kv[1]["summary"]["stock"]["decode"]))
    if not rows:
        return ""
    import statistics as _st
    gains = [o["summary"]["llmbox"]["decode"] / o["summary"]["stock"]["decode"] - 1 for _, o in rows]
    deep = [o["summary"]["llmbox"]["deep"] / o["summary"]["stock"]["deep"] - 1 for _, o in rows if o["summary"]["stock"].get("deep") and o["summary"]["llmbox"].get("deep")]
    link = lambda rid: f'<a href="recipe-{esc(rid)}.html">{esc(_name_of(opts[rid]))}</a>' if ranked is None or rid in ranked else esc(_name_of(opts[rid]))
    tr = "".join(f'<tr><td class="l">{link(rid)}</td><td>{o["summary"]["stock"]["decode"]:.0f}</td>'
                 f'<td>{o["summary"]["llmbox"]["decode"]:.0f}</td><td>{(o["summary"]["llmbox"]["decode"] / o["summary"]["stock"]["decode"] - 1) * 100:+.0f}%</td>'
                 f'<td>{o["summary"]["stock"].get("deep") or 0:.0f}</td><td>{o["summary"]["llmbox"].get("deep") or 0:.0f}</td>'
                 f'<td class="q">{esc(" ".join(o["summary"].get("tuned_flags") or []) or "defaults")}</td></tr>' for rid, o in rows)
    return (f"<h2 id='settings'>What the settings do</h2><p>Every model on the reference box, measured twice back to back: once the way "
            f"<code>llama-server -m model.gguf -c &lt;context&gt;</code> runs it with llama.cpp's own defaults, once with its llmbox recipe (placement of the "
            f"weights, KV cache type, speculative decoding with the model's MTP head where it has one, batch sizes, then <code>llmbox tune</code>). Same file, "
            f"same context. Median: <b>{_st.median(gains) * 100:+.0f}%</b> in a short chat"
            + (f", <b>{_st.median(deep) * 100:+.0f}%</b> at 32k" if deep else "") + ".</p>"
            f'<div class="tw"><table class="opt"><tr><th class="l">model</th><th>stock tok/s</th><th>llmbox tok/s</th><th>gain</th><th>stock at 32k</th>'
            f"<th>llmbox at 32k</th><th class='l'>tuned</th></tr>{tr}</table></div>")


def method_page(ref: dict | None, opts: dict | None = None, ranked: set | None = None, ref_row: dict | None = None,
                rs: list[dict] | None = None, look: dict | None = None) -> str:
    """How the numbers are made. The figures (weights, task counts, versions, depths) come from the code."""
    from .. import suite
    per = {}
    for b, _k, lvl in suite.QUICK_ITEMS:
        per.setdefault(b, []).append(lvl)
    from ..suite import sessions
    counts = {"sessions": sum(1 for b, k, _l in suite.QUICK_ITEMS if b == "agentic" and k in sessions.KINDS), "agentic": len(per.get("agentic", []))}
    rows = "".join(f'<tr class="grp"><td class="l" colspan="4">{esc(g)} · {sum(share(b) for b in bs) * 100:.0f}%</td></tr>'
                   + "".join(f'<tr><td class="l"><span class="m2">{esc(TIPS[b][0].split(" ·")[0])}</span></td><td>{share(b) * 100:.0f}%</td>'
                             f'<td>{len(per.get(b, []))}</td><td class="l q">{esc(_GRADING[b].format(**counts))}</td></tr>' for b in bs) for g, bs in GROUPS)
    n = len(suite.QUICK_ITEMS)
    n6 = sum(1 for *_x, lvl in suite.QUICK_ITEMS if lvl >= 6)
    ref_name = _name_of(ref) if ref else "the frontier model"
    ref_cap = (ref_row or {}).get("capability") or (ref or {}).get("summary", {}).get("capability")
    # the margin of error on real models: the top two (not apart) and the top one against a model it is measurably ahead of
    example = ""
    if rs and look and len(rs) > 2:
        order = sorted([r for r in rs if r.get("vs_ref") is not None], key=lambda r: -r["vs_ref"])
        a, b = order[0], order[1]
        c = next((r for r in order[2:] if surely_better(a, r)), None)
        ax = _axis_of([r["vs_ref"] for r in order])
        line = lambda r: (f"<div class='ex'><div class='mw'>{_marker(*look[r['id']])}<span class='m'>{esc(model_name(r))}</span></div>"
                          f"{_fp_html(r['vs_ref'], *_range_pct(r), look[r['id']][0], ax)}</div>")
        example = (f"<div class='exs'><div><div class='sc'>Not measurably apart</div>{line(a)}{line(b)}</div>"
                   + (f"<div><div class='sc'>Measurably apart</div>{line(a)}{line(c)}</div>" if c else "") + "</div>")
    toc = [("short", "In 30 seconds"), ("score", "The score"), ("tasks", "The tasks"), ("not", "What is not measured"), ("sure", "How sure the numbers are"),
           ("speed", "Speed"), ("settings", "What the settings do"), ("people", "Your computer"), ("trust", "How people's results are checked"),
           ("differ", "Why other rankings differ"), ("records", "What a run records"), ("versions", "Versions")]
    body = f'''
<section class="panel hd"><div><div class="crumb"><a href="index.html">Models</a> / how scores work</div><h1>How the numbers are made</h1>
 <p class="q" style="margin-top:6px">Suite v{esc(suite.VERSION)} · content hash {esc(suite.content_hash())}</p></div></section>
<div class="mdoc"><nav class="toc" aria-label="contents">{"".join(f'<a href="#{k}">{esc(t)}</a>' for k, t in toc)}</nav>
<article class="doc">
<section class="short" id="short"><h2>In 30 seconds</h2><ul>
<li><b>Real work, graded by programs.</b> {n} tasks from coding, tool use, questions about your own machine, documents and writing; hidden tests and checkers grade them, no model grades another.</li>
<li><b>Fresh tasks every run.</b> Tasks are generated from a seed, so a model cannot have seen the answers.</li>
<li><b>% of Claude Opus 5.5.</b> The score is the share of what a frontier model gets on the same tasks, with a 95% range; overlapping ranges mean "not measurably apart yet".</li>
<li><b>Speed on a real PC.</b> Timed on our test PC with the settings shown on each model page, and predicted for yours from the model file and your memory speeds.</li>
<li><b>Measured by people too.</b> Anyone can run the same test on their own computer; the server grades every answer again, and the site shows the speeds by kind of hardware.</li></ul></section>

<h2 id="score">The score</h2>
<p>Every model gets the same {n} tasks. A program grades each one from 0 to 100; no model grades another. The blocks are
weighted by how people use local models, and the weighted average is the <b>capability</b>.</p>
<p>The <b>score</b> is that capability as a share of what a frontier model gets on the same tasks: {esc(ref_name)}{f" scored {ref_cap:.1f}, which is 100%" if ref_cap else ""}.
It runs under the same conditions as a local model: the task's own system prompt, the same tools and the same graders.
Only the model differs.</p>

<h2 id="tasks">The tasks</h2>
<p>Nine blocks, shown on the site as four uses. The number after a use is its share of the score.</p>
<div class="tw"><table><tr><th class="l">BLOCK</th><th>WEIGHT</th><th>TASKS</th><th class="l">WHAT AND HOW IT IS GRADED</th></tr>{rows}</table></div>
<p>The weights are set for people who download and run local models, mostly developers.
Saved runs are re-weighted with them, and each task keeps the score it got.</p>
<p>Each kind of task has difficulty levels from 1 to 10. The quick suite uses hard ones, a few easier ones so that weak models
still register, and {n6} expert tasks that local models rarely solve, so the frontier has room above them.
Tasks are generated from a seed: a new seed gives fresh tasks that test the same rules with other names, numbers and files.
Most tasks ask several questions and give credit per question, test or constraint.</p>

<h2 id="not">What is not measured</h2>
<p>The test covers what developers and agent builders do with local models. It does not measure <b>{NOT_MEASURED}</b>.
Those are most of what people do with chatbots: in OpenAI's usage data for ChatGPT (June 2026) practical guidance is 32% of messages,
writing 22%, seeking information 19%, and technical help 4%. For open-weight models served on OpenRouter, roleplay was about half of all
tokens in 2025 (OpenRouter and a16z, "State of AI"). A score here says nothing about those uses: they need a human or model judge,
and this site grades only what a program can check.</p>

<h2 id="sure">How sure the numbers are</h2>
<p>A model's score is estimated from every answer it gave, in every run on these tasks, with item response theory. Each task family
(kind × level) has a measured difficulty and sharpness, calibrated on all measured models; a model has an overall level plus its own
strength or weakness per block. The score is the expected weighted result on the quick suite at that level, and the range next to it
is its 95% interval. Every further run adds answers and narrows the range.</p>
<p>A run is either <b>fixed</b> (every task family once: 40–110 minutes, depending on the model's speed) or <b>adaptive</b> (40 minutes:
after each task, the next one is the task that narrows the range most per second of this model's time, and tasks it always or never
solves are skipped). Measured on fresh tasks: six runs of one model, three of each kind, agreed within ±2.6 points.</p>
<p>Two models are <b>measurably apart</b> when the gap between their scores is larger than the 95% margin of that gap; overlapping lines
alone do not settle it. A dashed line in the ranking separates groups: each group starts with the first model that is measurably worse
than the top of the group above. Inside a group the order can still change with more runs.</p>
{example}

<h2 id="speed">Speed</h2>
<p>Speed is measured with one conversation at a time, the way one person uses the model: a fresh prompt of real code at about 2k, 30k
and 90k tokens, so nothing comes from the cache, with code as the answer, so speculative decoding sees realistic text.
<b>Tok/s</b> is how fast the answer is written; <b>first word</b> is how long the model reads the whole context before it starts.</p>
<p>Speed on other boxes is predicted: a token needs the active weights read once, from VRAM for what fits on the card and from system RAM for the rest,
plus a fixed cost for each layer that does not shrink on a faster card. So the time per token follows from the model file and the two memory speeds.
The card side and the fixed cost are fitted to public llama.cpp runs on eleven NVIDIA cards (half the predictions within 2% for dense models
and 4% for mixture-of-experts ones, the worst 15%), nineteen Apple chips and eight AMD ones. What the reference box read from its RAM is then scaled to what its run measured.
Macs and AMD cards are checked the same way on dense models (2% median error); a mixture-of-experts model on them has no public table yet,
and the page says <i>rough</i>.</p>

<p>The numbers behind every ranking, for anyone to recompute or cite: <a href="data/models.csv">models.csv</a> ·
<a href="data/speeds.csv">speeds.csv</a> · <a href="data/models.json">models.json</a> · the model settings in
<a href="recipes/index.json">recipes/</a>. Licence: <a href="data/LICENSE.txt">CC BY 4.0</a>, credit "llmbox".</p>

{_optimize_table(opts or {}, ranked)}

<h2 id="people">Your computer</h2>
<p>One line installs llmbox and asks the rest: it finds your card and memory, gets llama.cpp for it, picks the best model for
your computer, installs it with the settings measured here fitted to your hardware, and starts it (Linux with an NVIDIA card;
Macs get the pick, running on a Mac is coming). <code>llmbox test</code> then measures it and, on a yes, sends the result.</p>
<pre class="cmd">{esc(INSTALL)}
llmbox test &lt;model&gt;          # 3 min speed + 10 min quality, where you stand (--full: 40 min)</pre>
<p>Speeds are grouped by kind of hardware: the same graphics card (chip and memory) and system RAM of about the same speed, since a
mixture-of-experts model reads part of itself from RAM. A group shows the median machine, each machine counted once, and the spread once
five machines are in it. <code>llmbox run &lt;model&gt;</code> serves the model for everyday use without anything else installed.</p>

<h2 id="trust">How people's results are checked</h2>
<p>Nothing a computer sends is taken at its word.</p>
<ul>
<li><b>The answers are graded again by the server</b>, with the same graders, inside a sandbox (no network, nothing of the server
visible): the answers are code that runs. The server's grade replaces the one sent. A run with more than a fifth of its answers graded
differently is rejected.</li>
<li><b>The tasks come from a seed the server hands out</b> for each test, so they cannot be prepared in advance.</li>
<li><b>Quality runs count from people signed in</b> with GitHub (<code>llmbox login</code>); anonymous ones are kept but do not count.
Each person's results are on their <a href="people.html">page</a>, under a handle unless they choose to show their name, and
<code>llmbox forget</code> deletes the account and everything it sent.</li>
<li><b>The same model file with the same settings must score the same</b> on any machine. A run whose range does not meet the model's
range (answers from a stronger model, or a broken setup) is kept aside, out of the score. Verified answers that agree join the model's
score, and every run narrows its range for everyone.</li>
<li><b>Speed cannot be checked</b>, so it is shown as the median of a group, and a figure more than twice the prediction is left out until
a second machine confirms it.</li>
<li><b>Nothing names you or the machine</b>: paths lose the home folder, user and host names are removed, and a random id per install
counts machines. <code>llmbox submit --dry-run</code> prints exactly what would be sent.</li></ul>

<h2 id="differ">Why other rankings differ</h2>
<p>Public leaderboards mostly run full-precision models on public question sets. Here the same model is the 4-bit (or smaller) file people
actually download, run with the settings on its page, on tasks generated fresh for each run, so a model cannot have learned the answers.
The blocks are weighted for developer work, and the score is relative to Claude Opus 5.5 on the same tasks, not an absolute percentage.
A fine-tune can land above or below its base model: it is measured, not assumed.</p>

<h2 id="records">What a run records</h2>
<p>Every run keeps the server's exact command line and sampling defaults, the llama.cpp build, the model file's sha256, and the GPU and CPU
temperature, power and memory every five seconds. The settings are compared with the recipe: differences in speed settings keep the
recipe's score; differences in sampling, template, KV cache or model file make it a different recipe that needs its own score.
Replies are capped at 32k tokens with room kept for the answer after the thinking; a reply cut there, or thinking that repeats itself
(detected from the text, not from its length), is flagged on the run page and still counts as it was graded.</p>
<p>Every task has a time limit: 30 minutes, 15 for agentic coding. A model that has not answered by then scores 0 on that task, as it
would for a wrong answer: someone waiting half an hour for a reply has not been helped. The same goes for a document that does not fit the
model's context. Only failures that are not the model's (a crashed or restarting server) leave a task out.</p>

<h2 id="versions">Versions</h2>
<p>Scores compare only within one suite version. The content hash identifies the exact tasks and graders. Answers to tasks that did not
change carry over to the next version; a changed task needs new answers.</p>
</article></div>'''
    return _page(f"llmbox · how scores work (suite v{suite.VERSION})", "METHOD", body, ("pages.css", "method.css"),
                 about="How llmbox scores and times local AI models: tasks graded by programs, fresh every run, a score relative to Claude Opus 5.5 with its margin, speed predicted for your box.")


