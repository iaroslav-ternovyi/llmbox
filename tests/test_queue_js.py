"""The site's queue page (llmbox/site/assets/queue.js): what /r/<id> says before the run's page is published, for each
status reply of the intake: in the queue with its place and clock time, checked and waiting for the next publish,
publishing delayed, published (load the page), rejected (the reason, as text) and an address nobody sent (stays "not
found"). Needs Node (CI has it); skipped without.
Run: python3 tests/test_queue_js.py"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
JS = os.path.join(HERE, "..", "llmbox", "site", "assets", "queue.js")

if not shutil.which("node"):
    print("skipped: no node")
    sys.exit(0)

cases = {
    "received": {"status": "received", "ahead": 12, "live_at": "2026-10-02T12:20:00Z", "delayed": False, "published": None},
    "accepted": {"status": "accepted", "live_at": "2026-10-02T12:35:00Z", "delayed": False, "published": None},
    "delayed": {"status": "accepted", "live_at": "2026-10-02T12:35:00Z", "delayed": True, "published": None},
    "published": {"status": "accepted", "published": "2026-10-02T12:31:07Z"},
    "rejected": {"status": "rejected", "reason": "<img src=x onerror=alert(1)> 0 of 1 records accepted"},
    "unknown": None,
}
script = f"""
process.env.TZ = "Europe/Madrid";
const {{ queueView }} = require({json.dumps(os.path.abspath(JS))});
const cases = {json.dumps(cases)};
const out = {{}};
for (const k in cases) out[k] = queueView(cases[k]);
console.log(JSON.stringify(out));
"""
r = subprocess.run(["node", "-e", script], capture_output=True, text=True, env=dict(os.environ, TZ="Europe/Madrid"))
assert r.returncode == 0, r.stderr
v = json.loads(r.stdout)

assert v["received"]["line"] == "Your result is in the queue · 12 ahead · live at about 14:20. This page fills in by itself.", v["received"]
assert v["accepted"]["line"] == "Your result is checked · live at about 14:35. This page fills in by itself.", v["accepted"]
assert "Publishing is delayed" in v["delayed"]["line"] and "live at" not in v["delayed"]["line"], v["delayed"]
assert v["published"] == {"page": True}, v["published"]
assert v["rejected"]["done"] is True and v["rejected"]["line"].startswith("This result was not accepted: <img"), v["rejected"]   # shown as text
assert v["unknown"] is None
# the page sets text only: no innerHTML anywhere in the script
assert "innerHTML" not in open(JS).read()
print("all passed")
