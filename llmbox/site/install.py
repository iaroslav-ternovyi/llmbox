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
<section class="panel pad"><div class="lbl">Then it asks</div>
 <p>The installer goes straight on to the guided start (or run <code>llmbox</code> any time): it looks at your computer,
 gets llama.cpp built for your graphics card if it is missing, shows the best model for it with the numbers and why,
 and after one yes downloads it, fits the settings, times it (3 minutes, optional) and leaves it running with the address
 to put in your app. Every question has a default; nothing is sent without its own yes.</p>
<pre class="cmd">$ llmbox
This computer: RTX 5070 12 GB · RAM 45–65 GB/s · 61 GB RAM

Best for it: Cyber-Tiel-Coder-35B-A3B UD-Q4_K_XL
  88% of Claude Opus on real work (95% range 83-91) · ~55 tokens/s, ~52 with 32k of context · up to 256k context
  why: the best score among the models that fit
  download: 22.7 GB into ~/models

Install and start it? [Y/n/l]</pre>
<p class="q">Afterwards:</p>
<pre class="cmd">llmbox                 # the guided start
llmbox doctor          # is this computer ready? each problem with its fix
llmbox test &lt;model&gt;    # 3 min speed + 10 min quality, where you stand against machines like yours (--full: 40 min)
llmbox stop            # stop what llmbox started
llmbox login           # optional: GitHub, for your profile page and for your quality runs to count</pre></section>
<section class="panel pad"><div class="lbl">What it needs</div><ul class="plain">
 <li><b>Linux with an NVIDIA card</b> (or Windows with WSL2) and its driver. llama.cpp comes with it: the official build for
 your card and driver, checked against its published sha256 (your own build is used if you have one).</li>
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


def privacy_page() -> str:
    body = '''
<section class="panel hd"><div><div class="crumb"><a href="index.html">Models</a> / privacy</div><h1>Privacy</h1>
 <p class="q" style="margin-top:6px">What llmbox and this site keep, why, and how to have it deleted.</p></div></section>
<section class="panel pad"><div class="lbl">This site</div><ul class="plain">
 <li>No cookies, no analytics, no trackers. The box you pick and your sign-in are kept in your own browser (localStorage) only.</li>
 <li>The graphics card preselected on the home page comes from what your browser reports; it is matched in the page and not sent anywhere.</li></ul></section>
<section class="panel pad"><div class="lbl">The llmbox program</div><ul class="plain">
 <li>It keeps everything in <code>~/.llmbox</code> on your computer and sends nothing unless you run <code>llmbox test</code> or
 <code>llmbox submit</code> and answer yes. <code>llmbox submit --dry-run</code> prints exactly what would be sent.</li>
 <li>What is sent: the hardware (graphics card, CPU, RAM and its speed, OS, driver), the model file and settings, the speed
 figures and, for a quality test, the model's answers. Paths lose your home folder; user and host names are removed. A random
 id per install counts machines.</li></ul></section>
<section class="panel pad"><div class="lbl">Accounts</div><ul class="plain">
 <li>Signing in with GitHub is optional. The server keeps your GitHub user id and name, a hash of each llmbox key it issued,
 and what you sent. It never keeps a GitHub token.</li>
 <li>Your profile page shows a handle, not your GitHub name, unless you choose to show it.</li>
 <li>The address a request comes from is kept only as a one-way hash, for rate limits.</li></ul></section>
<section class="panel pad"><div class="lbl">Deleting it</div><ul class="plain">
 <li><code>llmbox forget</code>, or DELETE on the <a href="account.html">account page</a>, deletes the account and every result it sent,
 at once. Anonymous results can be deleted on request with their submission id (<code>~/.llmbox/submitted.json</code>).</li>
 <li>Questions and requests: <a href="https://github.com/iaroslav-ternovyi/llmbox/issues">github.com/iaroslav-ternovyi/llmbox/issues</a>.</li></ul></section>'''
    return _page("llmbox · privacy", "", body, ("pages.css", "method.css"),
                 about="What llmbox and its site keep, why, and how to have it deleted: no cookies or trackers, uploads only on a yes, accounts deletable at once.")
