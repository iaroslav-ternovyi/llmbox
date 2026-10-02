"""`llmbox test` (llmbox/cli.py cmd_test) and what it may change, with everything heavy stubbed (no model runs):
signed in, the quality test runs and nothing is offered; anonymous on a terminal, the test offers to sign in (with the
badge as the reason on a machine nobody measured) - no means speed only, yes means sign in, then the quality test;
--yes or no terminal means speed only with a note; --no-submit keeps the quality test for this person; with no model
it tests llmbox's pick, installed first; on AMD it stops before any download, and the guided start installs and runs
there but skips its measuring; the credit-name prompt comes only on a first and defaults to no; an outlier is said
before the send; after a send the CLI prints the run's page and its clock time. (AMD still gets the Vulkan build to
install and run: tests/test_engine.py.)
Run: python3 tests/test_cli_test.py"""
import contextlib
import io
import json
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
home = tempfile.mkdtemp()
os.environ["LLMBOX_HOME"] = home
os.environ["LLMBOX_NO_DB"] = "1"
from llmbox import account, cli, hosts, install, irt, pick, recipe as rc, results, serving, submit, suite, wizard  # noqa: E402

NV = {"vendor": "nvidia", "name": "NVIDIA GeForce RTX 3080", "vram_mib": 10240}
AMD = {"vendor": "amd", "name": "AMD Radeon RX 7900 XTX", "vram_mib": 24560}


def profile(gpu):
    return {"name": "me", "hw": {"gpus": [gpu], "cpu": {"model": "x", "threads": 16, "cores": 8}, "ram_mib": 65536, "os": "Linux-6.8-x86_64"},
            "ram_bw": {"ram_read_gbs": 60}}


calls: list = []
state = {"key": None, "answers": [], "tty": True, "prof": profile(NV), "installed": ["qwen-x"], "public": False, "recipes": []}
hosts.load = lambda h: state["prof"]
rc.load = lambda h, r: {}
rc.ids = lambda h: state["installed"]
suite.content_hash = lambda: "h"
irt.canonical = lambda h: "c"
irt.RELEASES = {"c": "v-test"}
irt.load = lambda c: True
account.key_for = lambda s: state["key"]
account.login = lambda s, **k: (calls.append("login"), state.update(key="k"))
account.whoami = lambda s: {"public": state["public"]}
account.set_public = lambda s, p: calls.append(("set_public", p))
submit.fresh_seed = lambda s: 1234
pick.index = lambda: {"recipes": state["recipes"]}
wizard._fresh_registry = lambda out: None
ROW = {"id": "qwen-x", "name": "Qwen X", "t2": 44.0, "td": 30.0, "measured": 0, "size_gb": 21.4, "score": 80.0, "ctx": 32768}
wizard.best_for = lambda prof, model=None: (dict(ROW, id=model or "qwen-x"), "the best score that fits", [ROW])
install.run = lambda rid, src, host, dry_run=True, **k: (calls.append(("install", rid)), 0)[1]
cli._standing = lambda host, rid, recs, entry=None, predicted=None: calls.append(("standing", rid, predicted))


@contextlib.contextmanager
def served(host, rid):
    yield "http://127.0.0.1:9"


serving.served = served


def fake_main(argv):   # the speed and bench steps write their records, as the real ones do
    calls.append(argv[0])
    if argv[0] == "speed":
        state["depths"] = [argv[i + 1] for i, x in enumerate(argv) if x == "--depth"]
    assert "--speed-probe" not in argv, argv   # step 1 timed every depth; a second probe cost a Mac ~15 minutes
    kind = "speed" if argv[0] == "speed" else "suite"
    rec = {"schema": results.SCHEMA, "kind": kind, "id": f"{kind}-1", "recipe": {"id": argv[1]}, "speed": {"decode_tps": state.get("dec", 40.0)},
           "prediction": {"decode_tps_no_spec": 30.0}}
    d = os.path.join(home, "results", "me")
    os.makedirs(d, exist_ok=True)
    json.dump(rec, open(os.path.join(d, f"{kind}-{len(calls)}.json"), "w"))


cli.main = fake_main
submit.ask_and_send = lambda paths, host, server, yes=False: (calls.append(("send", len(paths))), True)[1]
sys.stdin = types.SimpleNamespace(isatty=lambda: state["tty"])
import builtins  # noqa: E402
builtins.input = lambda prompt="": (calls.append(("asked", prompt)), state["answers"].pop(0) if state["answers"] else "")[1]


def test(recipe="qwen-x", **kw):
    calls.clear()
    a = types.SimpleNamespace(recipe=recipe, host="me", full=False, budget=None, yes=False, server="http://127.0.0.1:9", no_submit=False)
    for k, v in kw.items():
        setattr(a, k, v)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            cli.cmd_test(a)
            code = 0
        except SystemExit as e:
            code = e.code
    return out.getvalue(), code


def asked(fragment):
    return [c for c in calls if isinstance(c, tuple) and c[0] == "asked" and fragment in c[1]]


# 1 signed in: speed, quality, standing, the send; nothing offered
state.update(key="k")
out, code = test()
assert code == 0 and calls.count("speed") == 1 and calls.count("bench") == 1 and not asked("Sign in"), (out, calls)
assert ("send", 2) in calls and "1/3 speed" in out and "3/3 where this stands" in out, (out, calls)
assert state["depths"] == ["32000", "80000"], state["depths"]
# a Mac is timed at 32k only: its prompt reading makes 80k half the test (M2 Max: 8 of 16 minutes)
MAC = {"vendor": "apple", "name": "Apple M2 Max", "vram_mib": 32768, "unified": True, "apple_gpu_cores": 38}
state.update(prof=profile(MAC))
out, code = test()
assert code == 0 and state["depths"] == ["32000"], (code, state["depths"], out[-500:])
state.update(prof=profile(NV))

# 2 anonymous on a terminal, no: speed only, then the send; the offer names the badge (nobody measured an RTX 3080 10 GB)
state.update(key=None, answers=["n", "y"])
out, code = test()
assert asked("Nobody has measured an RTX 3080 10 GB yet. Sign in with GitHub and your card says FIRST ON THIS CARD"), calls
assert "bench" not in calls and calls.count("speed") == 1 and ("send", 1) in calls, calls
assert "the 10-minute quality test is skipped" in out and "1/2 speed" in out and "2/2 where this stands" in out, out

# 3 anonymous on a terminal, yes: sign in, then the quality test
state.update(key=None, answers=["y"])
out, code = test()
assert "login" in calls and calls.index("login") < calls.index("bench"), calls

# 4 anonymous with --yes (and with no terminal): speed only with a note, no question
for tty, yes in ((True, True), (False, False)):
    state.update(key=None, answers=[], tty=tty)
    out, code = test(yes=yes)
    assert "bench" not in calls and not asked("Sign in") and "quality test is skipped" in out, (tty, yes, calls)
state["tty"] = True

# 5 --no-submit, anonymous: the quality test runs for this person, no offer, nothing sent
state.update(key=None, answers=[])
out, code = test(no_submit=True)
assert "bench" in calls and not asked("Sign in") and not any(c[0] == "send" for c in calls if isinstance(c, tuple)) and "not sent (--no-submit)" in out, calls

# someone already measured the entry: a plain offer, no badge
state.update(key=None, answers=["n"], recipes=[{"id": "other", "measured": {"rtx-3080-10g|ram-45-65|cuda": [50, 40, None, 3]}}])
out, code = test()
assert asked("Sign in with GitHub so your quality result counts?") and not asked("FIRST ON"), calls
assert "RTX 3080 10 GB: measured with other models; this one is predicted ~44 tok/s" in out, out
state["recipes"] = []

# 7 no model: llmbox's pick, installed first (it is not installed yet)
state.update(key="k", answers=["y"], installed=[])
out, code = test(recipe=None)
assert ("install", "qwen-x") in calls and calls.index(("install", "qwen-x")) < calls.index("speed"), calls
assert "llmbox's pick for this computer: Qwen X" in out and "21.4 GB to download" in out, out
state["installed"] = ["qwen-x"]

# the start line on a machine nobody measured; the credit-name prompt on a first: default no, yes makes the profile public
state.update(key="k", answers=[""])
out, code = test()
assert out.startswith("RTX 3080 10 GB: nobody has measured one yet · predicted ~44 tok/s"), out
assert asked("You may be the first on RTX 3080 10 GB. Show your GitHub name on your llmbox profile and runs, including as the first there? [y/N]"), calls
assert not any(isinstance(c, tuple) and c[0] == "set_public" for c in calls), calls
state.update(answers=["y"])
out, code = test()
assert ("set_public", True) in calls
state.update(public=True, answers=[])
out, code = test()
assert not asked("You may be the first"), "already public: no question"
state["public"] = False

# an outlier is said before the send
state.update(key="k", answers=["n"], dec=90.0)
out, code = test()
assert "over twice the prediction (~30 tok/s): it will be published as not confirmed" in out, out
state.pop("dec")

# a send that fails: said, and kept for `llmbox submit`
def refuse(paths, host, server, yes=False):
    raise SystemExit("intake at http://127.0.0.1:9 refused: 429 more than 30 submissions in an hour")
submit.ask_and_send, ok_send = refuse, submit.ask_and_send
out, code = test()
assert code == 0 and "not sent: intake at http://127.0.0.1:9 refused: 429" in out and "Saved; `llmbox submit` sends it later." in out, out
submit.ask_and_send = ok_send

# AMD: stops before any download, with the prediction and the card's feed
state.update(prof=profile(AMD), key="k", answers=[])
out, code = test(recipe=None)
assert "AMD timing isn't supported yet (planned after launch). Nothing was downloaded." in str(code) and "~44 tok/s, rough" in str(code), code
assert "feeds/rx-7900-xtx-24gb.xml" in str(code) and calls == [], calls
assert wizard.stops_timing(profile(AMD)) and not wizard.stops_timing(profile(NV)) and not wizard.stops_timing({"hw": {"gpus": [AMD, NV]}})

# the guided start on AMD installs and starts the pick, without measuring
measured = []
from llmbox import speed  # noqa: E402
speed.measure = lambda *a, **k: measured.append(a) or {}
wizard._this_host = lambda name, out: dict(state["prof"], hw=dict(state["prof"]["hw"], runtimes=["llama.cpp"]))
wizard._newer = lambda out: None
wizard._start = lambda host, best, out: 0
serving.running = lambda: []
lines: list = []
state["installed"] = []   # a first run: install, (no) measuring, start
assert wizard.main(yes=True, out=lines.append) == 0
assert not measured and any("Timing on AMD comes after launch" in x for x in lines), lines

# 6 after a send: the run's page and its clock time
os.environ["TZ"] = "Europe/Madrid"
import time  # noqa: E402
time.tzset()
submit.send = lambda b, server: {"id": "3f9a2c1e4b7d", "page": "r/3f9a2c1e4b7d", "ahead": 3, "live_at": "2026-10-02T12:20:00Z"}
submit.ledger = lambda: {}
submit.LEDGER = os.path.join(home, "ledger.json")
p = os.path.join(home, "results", "me", "speed-x.json")
json.dump({"schema": results.SCHEMA, "kind": "speed", "id": "r1", "recipe": {"id": "qwen-x"}, "speed": {"decode_tps": 40}}, open(p, "w"))
submit.bundle = lambda recs: {}
said: list = []
submit.run([p], "me", "http://127.0.0.1:9", dry_run=False, out=said.append)
from llmbox import registry  # noqa: E402
assert said == [f"sent · your page and card: {registry.DEFAULT_URL.rstrip('/')}/r/3f9a2c1e4b7d",
                "  live at about 14:20 (3 ahead); until then it shows your place in the queue"], said
print("all passed")
