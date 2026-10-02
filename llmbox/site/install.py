"""install.html: how to get llmbox onto your computer, and account.html: signing in on the site."""
from __future__ import annotations

from .layout import _page
from .words import CURL, esc, SITE_URL

REPO = "https://github.com/iaroslav-ternovyi/llmbox"


def install_page() -> str:
    one = f"{CURL} {SITE_URL}/install.sh | sh"
    body = f'''
<section class="panel hd"><div><div class="crumb"><a href="index.html">Models</a> / get llmbox</div><h1>Get llmbox</h1>
 <p class="q" style="margin-top:6px">One program: it picks the model that fits your computer, installs it with the settings measured here,
 tunes it for your hardware, and measures it. Free and open source (Apache-2.0).</p></div></section>
<section class="panel pad"><div class="lbl">Your system</div><ul class="plain sys3">
 <li><b>Linux with an NVIDIA card</b>, or <b>a Mac with Apple silicon</b> (M1 or newer): the line below does everything, measuring included.
 AMD cards on Linux: models install and run; measuring them comes after launch.</li>
 <li><b>Windows:</b> the easiest way needs no llmbox at all: <a href="https://lmstudio.ai">LM Studio</a> or <a href="https://ollama.com">Ollama</a>
 with the file and settings shown on each model page (its <i>Run it yourself</i> panel has a tab for each). For all of llmbox, run the line below
 in WSL2 (details under <a href="#needs">What it needs</a>).</li></ul></section>
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
<section class="panel pad" id="needs"><div class="lbl">What it needs</div><ul class="plain">
 <li><b>Linux with an NVIDIA card</b> and its driver. llama.cpp comes with it: the official build for
 your card and driver, checked against its published sha256 (your own build is used if you have one).</li>
 <li><b>Windows:</b> in WSL2 with Ubuntu 24.04 (<code>wsl --install -d Ubuntu-24.04</code> in PowerShell), run the line above in its
 terminal. It uses your Windows NVIDIA driver; <code>llmbox doctor</code> says when WSL gives Linux too little of your memory
 (half of it unless told otherwise).</li>
 <li><b>Mac</b> with Apple silicon: llama.cpp's official Metal build, checked the same way. Speeds are measured at 32k of
 context (a Mac reads long prompts slowly, so the 80k figure is left to machines that read fast).</li>
 <li>Python 3.12 or newer and git.</li></ul></section>
<section class="panel pad"><div class="lbl">Your data</div><ul class="plain">
 <li>Everything llmbox keeps is in <code>~/.llmbox</code> on your computer.</li>
 <li>Nothing is sent unless you run <code>llmbox test</code> or <code>llmbox submit</code>; <code>llmbox submit --dry-run</code> shows exactly what would go,
 and nothing in it names you or the machine. <a href="method.html#trust">How results are checked →</a></li>
 <li>Remove it: <code>rm -rf ~/.llmbox/venv ~/.local/bin/llmbox</code> (and <code>~/.llmbox</code> for its data). When <code>~/.local/bin</code> was not on your PATH, the installer added one line marked <code># llmbox</code> to your shell's startup file (<code>~/.zshrc</code>, <code>~/.bashrc</code> or <code>~/.profile</code>); delete it too. <code>LLMBOX_NO_MODIFY_PATH=1</code> before <code>sh</code> stops it from adding the line.</li></ul>
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
    from ..public import operator, stats
    op = operator()
    who = (f"{esc(op.split('<')[0].strip())}, <a href=\"mailto:{esc(op.split('<')[1].rstrip('>'))}\">{esc(op.split('<')[1].rstrip('>'))}</a>"
           if "<" in op else '<b class="no">[the operator\'s name and e-mail: to be set before launch]</b>')
    counter = (f'<li><b>Visit counts</b> with <a href="https://www.goatcounter.com">GoatCounter</a>, run by llmbox itself on its server '
               f'(<a href="{esc(stats())}">the numbers are public</a>): the page, the page you came from, browser, system, screen size, '
               "country (worked out from the address, which is not kept), and a few clicks counted in aggregate - the hardware you pick, "
               "copying the install command, signing in. No cookies, nothing stored in your browser, no id that follows you; nothing at "
               "all when your browser sends Global Privacy Control or Do Not Track. Kept up to 25 months. Basis: measuring the "
               "audience of this site, exempt from consent in Spain (AEPD guide on analytics cookies, 2024; LSSI art. 22.2) and "
               "legitimate interest (GDPR art. 6.1.f).</li>"
               "<li>Downloads of the installer and model-list fetches by llmbox are counted per day and per llmbox version, "
               "without the address or any id.</li>") if stats() else "<li>No visit counting.</li>"
    body = f'''
<section class="panel hd"><div><div class="crumb"><a href="index.html">Models</a> / privacy</div><h1>Privacy</h1>
 <p class="q" style="margin-top:6px">What llmbox and this site keep, why, for how long, who else handles it, and how to have it deleted.</p></div></section>
<section class="panel pad"><div class="lbl">Who</div><ul class="plain">
 <li>llmbox is run by {who} (the controller under the GDPR). Write there for anything about your data.</li></ul></section>
<section class="panel pad"><div class="lbl">This site</div><ul class="plain">
 <li>No cookies and no third-party scripts or fonts: everything is served from this site. The box you pick and your
 sign-in are kept in your own browser (localStorage) only.</li>
 <li>The graphics card preselected on the home page comes from what your browser reports; it is matched in the page and not sent anywhere.</li>
 {counter}
 <li>Errors in this site's scripts may be reported to the server: the message, the page and the build, without the address; kept 30 days.</li></ul></section>
<section class="panel pad"><div class="lbl">The llmbox program</div><ul class="plain">
 <li>No telemetry. It keeps everything in <code>~/.llmbox</code> on your computer. What it fetches: the model list and settings
 from this site (saying which llmbox version asks), models from Hugging Face, llama.cpp builds from GitHub.</li>
 <li>It sends results only when you run <code>llmbox test</code> or <code>llmbox submit</code> and answer yes;
 <code>llmbox submit --dry-run</code> prints exactly what would go. What is sent: the hardware (graphics card, CPU, RAM and its
 speed, OS, driver), the model file and settings, the speed figures and, for a quality test, the model's answers. Paths lose
 your home folder; user and host names are removed. A random id per install counts machines.</li>
 <li>A crash is kept in <code>~/.llmbox/last-error.txt</code>; <code>llmbox bug</code> opens a GitHub issue you read and send yourself.</li></ul></section>
<section class="panel pad"><div class="lbl">Accounts and results</div><ul class="plain">
 <li>Signing in with GitHub is optional and asks GitHub for nothing beyond your public profile. The server keeps your GitHub
 user id and name, a hash of each llmbox key it issued, and what you sent; never a GitHub token. Basis: providing the
 account you asked for (GDPR art. 6.1.b).</li>
 <li>Your profile page shows a handle, not your GitHub name, unless you choose to show it. Results are published on this site
 and in its <a href="data/LICENSE.txt">data download</a> (CC BY 4.0) under that handle, or without any name when sent anonymously.</li>
 <li>Comments you post and your votes on settings are kept with your account; a comment is shown with your handle (or your
 GitHub name, if you made it public) and the machines you measured that model on. When you report a comment, the report
 (its reason and your account) is kept for the operator to decide on it. Basis: providing the comments you asked for
 (GDPR art. 6.1.b) and keeping them lawful (art. 6.1.c and f).</li>
 <li>For rate limits the server keeps a keyed hash of the address a request came from, changed daily and deleted after two days.</li>
 <li>Kept until you delete them.</li></ul></section>
<section class="panel pad"><div class="lbl">Who else handles it</div><ul class="plain">
 <li>Cloudflare serves this site and sees your address while doing so (Cloudflare, Inc., USA; under its data processing
 terms and the EU-US Data Privacy Framework).</li>
 <li>Hetzner (Germany) hosts the server with the accounts, results and visit counts.</li>
 <li>GitHub (USA) handles signing in, and anything you post in the repository's issues.</li>
 <li>Nothing is sold or used for advertising.</li></ul></section>
<section class="panel pad"><div class="lbl">Your rights</div><ul class="plain">
 <li><code>llmbox forget</code>, or DELETE on the <a href="account.html">account page</a>, deletes the account and every result,
 comment, vote and report it sent, at once. Anonymous results are deleted on request with their submission id (<code>~/.llmbox/submitted.json</code>).</li>
 <li>You can also ask for a copy of your data, for a correction, to limit or object to its use, and take your results elsewhere
 (they are in the CC BY 4.0 download). And you can complain to the Spanish data protection authority,
 <a href="https://www.aepd.es">AEPD</a>.</li></ul></section>'''
    return _page("llmbox · privacy", "", body, ("pages.css", "method.css"),
                 about="What llmbox and its site keep, why, for how long and who else handles it: no cookies, counting without ids, uploads only on a yes, accounts deletable at once.")


def terms_page() -> str:
    body = '''
<section class="panel hd"><div><div class="crumb"><a href="index.html">Models</a> / terms</div><h1>Terms</h1>
 <p class="q" style="margin-top:6px">Short, because llmbox is free and open source.</p></div></section>
<section class="panel pad"><div class="lbl">The program and the site</div><ul class="plain">
 <li>llmbox is open source under the <a href="https://github.com/iaroslav-ternovyi/llmbox/blob/main/LICENSE">Apache License 2.0</a>.
 It comes without any warranty: scores and speeds are measurements and predictions, not promises, and running large models
 works your hardware hard. You use it at your own risk.</li>
 <li>The models llmbox downloads are not ours: each comes from Hugging Face under its own licence, which applies to your use of it.</li>
 <li>NVIDIA, GeForce, AMD, Radeon, Ryzen, Apple, Mac and the names of models are their owners' trademarks, used here only to say
 which hardware or model a number belongs to. llmbox is not affiliated with any of them.</li></ul></section>
<section class="panel pad"><div class="lbl">What you send</div><ul class="plain">
 <li>By sending results you allow llmbox to publish them on this site and in its <a href="data/LICENSE.txt">data download</a>
 under Creative Commons Attribution 4.0, under your handle or without a name. You can delete them at any time
 (<a href="privacy.html">how</a>); copies others already downloaded stay theirs under that licence.</li>
 <li>Send only results of runs you made. Results that look wrong are held back or marked, not deleted silently, and a
 machine or account that sends made-up results can be blocked.</li>
 <li>Settings you share (<code>llmbox test --set</code>) are published with the run that measured them, the same way.</li></ul></section>
<section class="panel pad" id="comments"><div class="lbl">Comments and votes</div><ul class="plain">
 <li>Anyone signed in with GitHub can comment on a model and vote on settings. A comment shows your handle (or your GitHub
 name, if you made it public) and the machines you measured that model on.</li>
 <li>Keep to the model: what you ran, on what, what happened. No advertising, no abuse, nothing illegal, nobody's personal data.
 You stay responsible for what you write and allow llmbox to show it on this site; it is not part of the data download.</li>
 <li>Anyone signed in can report a comment, with the reason. Three reports hide it until the operator looks; the operator may
 then remove it, and tells its author why on the comment itself. An account that keeps breaking these rules loses the right
 to comment and vote. If you think a decision was wrong, write to the operator (<a href="privacy.html">contact</a>).</li>
 <li>You can delete your own comments at any time; deleting your account deletes all of them, and your votes.</li></ul></section>'''
    return _page("llmbox · terms", "", body, ("pages.css", "method.css"),
                 about="llmbox's terms: open source without warranty, models under their own licences, results published under CC BY 4.0 and deletable, rules for comments.")

