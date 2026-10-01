"""A bug, said plainly: an unexpected error prints one line and keeps the traceback in ~/.llmbox/last-error.txt;
`llmbox bug` turns it into a GitHub issue the person reads and sends themselves (llmbox never reports anything on its
own: no telemetry, see the privacy page). Paths under the home folder are shortened to ~ so the username stays out."""
from __future__ import annotations

import os
import platform
import sys
import time
import traceback
import urllib.parse

from . import __version__

LAST = os.path.join(os.path.expanduser("~"), ".llmbox", "last-error.txt")
ISSUES = "https://github.com/iaroslav-ternovyi/llmbox/issues/new"


def _scrub(text: str) -> str:
    home = os.path.expanduser("~")
    return text.replace(home, "~") if home and home != "/" else text


def crashed(e: BaseException, argv: list[str]) -> int:
    """Called for an exception nothing caught: save the details, say what to do. The exit status."""
    tb = _scrub("".join(traceback.format_exception(type(e), e, e.__traceback__)))
    try:
        os.makedirs(os.path.dirname(LAST), exist_ok=True)
        with open(LAST, "w") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  llmbox {__version__}  command: llmbox {' '.join(argv)[:200]}\n{tb}")
    except OSError:
        pass
    print(f"\nllmbox hit a bug: {type(e).__name__}: {_scrub(str(e))[:300]}", file=sys.stderr)
    print(f"The details are in {_scrub(LAST)}. `llmbox bug` opens a GitHub issue with them filled in - you see it before sending.",
          file=sys.stderr)
    return 1


def report() -> None:
    """Open (or print) a new-issue link with the versions and the last error filled in."""
    last = open(LAST).read() if os.path.exists(LAST) else ""
    body = (f"**What happened**\n\n(what you ran and what you expected)\n\n**Versions**\n\n"
            f"- llmbox {__version__}, Python {platform.python_version()}, {platform.system()} {platform.release()} ({platform.machine()})\n\n"
            + (f"**Last error**\n\n```\n{last[-4000:]}\n```\n" if last else ""))
    title = ("Bug: " + last.splitlines()[-1][:80]) if last.strip() else "Bug: "
    url = f"{ISSUES}?{urllib.parse.urlencode({'title': title, 'body': body, 'labels': 'bug'})}"
    print("Opening a new GitHub issue with this filled in (nothing is sent until you press Submit there):\n")
    print(body)
    try:
        import webbrowser
        if webbrowser.open(url):
            return
    except Exception:
        pass
    print(f"Open this link to file it:\n{url}")
