"""install.html: how to get llmbox onto your computer, and account.html: signing in on the site."""
from __future__ import annotations

from .layout import _page
from .words import esc, SITE_URL

REPO = "https://github.com/iaroslav-ternovyi/llmbox"


def install_page() -> str:
    one = f"curl -fsSL {SITE_URL}/install.sh | sh"
    body = f'''
<section class="panel hd"><div><div class="crumb"><a href="index.html">Models</a> / get llmbox</div><h1>Get llmbox</h1>
 <p class="q" style="margin-top:6px">One program: it picks the model that fits your computer, installs it with the settings measured here,
 tunes it for your hardware, and measures it. Free and open source (Apache-2.0).</p></div></section>
<section class="panel pad"><div class="lbl">Install</div>
 <div class="copy"><pre class="cmd" id="one">{esc(one)}</pre><button class="btn cpy" type="button" data-copy="one">COPY</button></div>
 <p class="q">It makes a private Python environment in <code>~/.llmbox/venv</code> and puts the <code>llmbox</code> command in
 <code>~/.local/bin</code>: no sudo, nothing runs in the background. Or, with Python 3.12+: <code>pip install git+{esc(REPO)}</code>.</p></section>
<section class="panel pad"><div class="lbl">Then</div>
<pre class="cmd">llmbox host add me                                   # your card, CPU, RAM and its speed
llmbox pick                                          # what fits, how good, how fast: the pick for this computer
llmbox install &lt;model&gt; --from registry --host me --apply   # download and fit the settings to your hardware
llmbox run &lt;model&gt; --host me                         # use it: an OpenAI-compatible server until Ctrl-C
llmbox login                                         # optional: GitHub, for your profile page
llmbox test &lt;model&gt; --host me                        # measure it (speed + 40-minute quality test) and send it</pre></section>
<section class="panel pad"><div class="lbl">What it needs</div><ul class="plain">
 <li><b>Linux with an NVIDIA card</b> (or Windows with WSL2), and <a href="https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md">llama.cpp</a>
 built with CUDA (<code>llmbox host add</code> finds it in <code>~/*/build/bin</code> or on the PATH).</li>
 <li><b>Mac:</b> <code>llmbox pick</code> tells what fits and roughly how fast; running and measuring models on a Mac is coming.</li>
 <li>Python 3.12 or newer and git.</li></ul></section>
<section class="panel pad"><div class="lbl">Your data</div><ul class="plain">
 <li>Everything llmbox keeps is in <code>~/.llmbox</code> on your computer.</li>
 <li>Nothing is sent unless you run <code>llmbox test</code> or <code>llmbox submit</code>; <code>llmbox submit --dry-run</code> shows exactly what would go,
 and nothing in it names you or the machine. <a href="method.html#trust">How results are checked →</a></li>
 <li>Remove it: <code>rm -rf ~/.llmbox/venv ~/.local/bin/llmbox</code> (and <code>~/.llmbox</code> for its data).</li></ul>
 <p class="q" style="margin-top:10px">Source, issues and releases: <a href="{esc(REPO)}">{esc(REPO)}</a></p></section>'''
    return _page("llmbox · get llmbox", "", body, ("pages.css", "method.css"), ("install.js",),
                 about="Install llmbox: pick, install, tune and measure local AI models on your own computer. Free and open source.")


def account_page(api: str) -> str:
    body = '''
<section class="panel hd"><div><div class="crumb"><a href="index.html">Models</a> / account</div><h1 id="acct-h">Your account</h1>
 <p class="q" style="margin-top:6px" id="acct-sub"></p></div></section>
<div id="acct"><section class="panel pad"><p class="q">Loading…</p></section></div>
<section class="panel pad"><div class="lbl">On your computer</div>
 <p class="q">The llmbox program signs in on its own, in the terminal: <code>llmbox login</code> shows a code to enter at github.com.
 An account is optional: it gives your results a <a href="people.html">profile page</a> and lets your quality runs count toward the models' scores.
 The server keeps your GitHub id and name, never a GitHub token.</p></section>'''
    return _page("llmbox · account", "", body, ("pages.css", "method.css"), ("account.js",), data={"api": api},
                 about="Sign in to llmbox with GitHub: your profile page, its visibility, and deleting your data.")
