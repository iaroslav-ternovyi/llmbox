"""`llmbox` with no command (or `llmbox start`): from nothing to a model running on this computer, in one go.

The way `fly launch` and `ollama` start: look at the machine, show the plan with its reason, ask once, then do it.
  1. this computer   detected once (host "me"), shown in one line
  2. llama.cpp       a build found here (llmbox host add looks for it), else what to do
  3. the pick        every measured model fitted here; the best score that fits, and among the models not measurably
                     apart from it the one still fast 32k into a session - with the numbers and the why
  4. install         download (with its progress bar), sha256, the settings fitted to this computer
  5. measure         optional, 3 minutes: how fast it runs here against machines like it; sent only on a yes (not on
                     AMD yet: timing there comes after launch)
  6. start           it keeps running in the background; the address and how to connect an app
Every question has a default (Enter), --yes takes them all, and nothing leaves the computer without a yes.
"""
from __future__ import annotations

import os
import sys
import time

from . import hosts, hwclass, pick, recipe as rc, registry, results


def _ask(prompt: str, default: str, yes: bool, choices: str = "yn", off_tty: str | None = None) -> str:
    """off_tty: the answer when no person is at the terminal (a send: "n" - it needs a person's yes)."""
    if yes or not sys.stdin.isatty():
        return off_tty or default
    opts = "/".join(c.upper() if c == default else c for c in choices)
    while True:
        a = input(f"{prompt} [{opts}] ").strip().lower()[:1] or default
        if a in choices:
            return a


def _this_host(name: str | None, out) -> dict:
    """The profile of this computer: the one given, else the only local one, else detect it now as "me"."""
    if name:
        return hosts.load(name) if os.path.exists(hosts.path(name)) else hosts.detect(name, None)
    local = [n for n in hosts.names() if not hosts.load(n).get("ssh")]
    if local:
        return hosts.load(local[0])
    out("Looking at this computer (a few seconds: GPU, CPU, memory and its speed) ...")
    return hosts.detect("me", None)


def _fresh_registry(out) -> None:
    p = os.path.join(rc.recipes_dir(registry.REGISTRY), "index.json")
    if not os.path.exists(p) or time.time() - os.path.getmtime(p) > 86400:
        try:
            registry.pull(registry.DEFAULT_URL, out=lambda m: None)
        except (OSError, SystemExit) as e:
            if not os.path.exists(p):
                raise SystemExit(f"cannot reach {registry.DEFAULT_URL} for the list of models: {e}")
            out(f"(the model list could not be refreshed: {e}; using the one from {time.strftime('%Y-%m-%d', time.localtime(os.path.getmtime(p)))})")


def _machine_line(prof: dict) -> str:
    fp = results.host_fingerprint(prof)
    label = hwclass.label(hwclass.of_host(fp))
    return label if fp.get("apple_gpu_cores") is not None else f"{label} · {prof['hw']['ram_mib'] / 1024:.0f} GB RAM"   # a Mac's label has its memory


def _newer(out) -> None:
    """The model list names the llmbox version the site was built with: say so when this one is older."""
    from . import __version__, pick
    try:
        cur = pick.index().get("llmbox")
    except SystemExit:
        return
    v = lambda s: tuple(int(x) for x in str(s).split(".")[:3] if x.isdigit())
    if cur and v(cur) > v(__version__):
        out(f"(llmbox {cur} is out, this is {__version__}: `llmbox update` gets it and the newest model list)")


def best_for(prof: dict, model: str | None = None) -> tuple[dict | None, str, list[dict]]:
    """(the model llmbox picks for this computer, or the one asked for; why; every measured model that fits here), from
    the model list already pulled. The guided start installs the pick; `llmbox test` with no model tests it."""
    cls = hwclass.of_host(results.host_fingerprint(prof))
    cpu = prof["hw"].get("cpu") or {}
    rows = pick.rank(hosts.spec(prof), cpu.get("cores") or cpu.get("threads"), "all", cls, cls.startswith("apple-"), pick.engines_of(prof))
    ok = [x for x in rows if x["fits"] and x.get("use_score") is not None]
    if not ok:
        return None, "", ok
    if model:
        return next((x for x in ok if x["id"] == model), None), "the model you asked for", ok
    best, why = pick.choose(ok)
    return best, why, ok


def stops_timing(prof: dict) -> bool:
    """An AMD card or a Ryzen AI Max (and no NVIDIA card): llmbox installs and runs models there (the Vulkan build), but
    timing them - `llmbox test`, the guided start's measuring - comes after launch."""
    vendors = {g.get("vendor") for g in prof["hw"].get("gpus") or []}
    return "amd" in vendors and "nvidia" not in vendors


def main(yes: bool = False, model: str | None = None, host: str | None = None, plan_only: bool = False, out=print) -> int:
    prof = _this_host(host, out)
    host = prof["name"]
    out(f"This computer: {_machine_line(prof)}")
    cls = hwclass.of_host(results.host_fingerprint(prof))
    mac = cls.startswith("apple-")
    engine = prof["hw"].get("runtimes") or []
    if mac:   # llmbox runs and times models on Linux for now: a Mac gets the plan and the settings for the apps it has
        plan_only = True
    if not engine and not plan_only:
        from . import engine as eng
        pat, what = eng.asset_pattern(prof)
        out(f"llama.cpp (what runs the models) is not installed here: llmbox can get the official build for this computer - {what}.")
        if _ask("Install it into ~/.llmbox/engines?", "y", yes) == "y":
            eng.install(hosts.host_of(prof), prof, out=out, stream=sys.stdout.isatty())
            prof = hosts.detect(host, prof.get("ssh"), measure=False)   # it finds the new llama-server
            engine = prof["hw"].get("runtimes") or []
        if not engine:
            out("Without llama.cpp nothing can run; `llmbox` again when it is installed.")
    _fresh_registry(out)
    _newer(out)
    best, why, ok = best_for(prof, model)
    if not ok:
        out("None of the measured models fits this computer's memory yet.")
        return 1
    if model and not best:
        out(f"{model}: not among the models that fit here; `llmbox pick` lists them")
        return 1

    def plan(x: dict) -> None:
        how = "measured on machines like this one" if x.get("measured") else "predicted for this computer"
        out(f"\nBest for it: {x['name']}")
        out(f"  {x['score']:.0f}% of Claude Opus on real work" + (f" (95% range {x['range'][0]:.0f}-{x['range'][1]:.0f})" if x.get("range") else "")
            + f" · ~{x['t2']:.0f} tokens/s, ~{x['td'] or x['t2']:.0f} with 32k of context ({how}) · up to {x['ctx'] // 1024}k context")
        out(f"  why: {why if x is best else 'your choice'}")
        out(f"  download: {x.get('size_gb') or '?'} GB into {prof['hw'].get('models_dir_guess') or '~/models'}" + (" · Mac: speeds are rough" if mac else ""))
    # a second visit: what is here already, and whether something better came out for this computer
    from . import serving
    mine = [x for x in ok if x["id"] in set(rc.ids(host))]
    up = {s["rid"]: s for s in serving.running() if s.get("host") == host}
    if mine and not model:
        shown = sorted(mine, key=lambda x: (x["id"] not in up, -x["score"]))[:3]
        out("\nOn this computer: " + ", ".join(f"{x['name']} ({x['score']:.0f}%" + (f", running at {up[x['id']]['url']}/v1)" if x["id"] in up else ")")
                                                 for x in shown) + (f" and {len(mine) - len(shown)} more" if len(mine) > len(shown) else ""))
        if best["id"] in {x["id"] for x in mine}:
            out(f"{best['name']} is still the best for it.")
            if plan_only:
                return 0
            if best["id"] in up:
                return 0
            if _ask(f"Start {best['name']}?", "y", yes) == "y":
                return _start(host, best, out)
            return 0
        have = max(mine, key=lambda x: x["score"])
        out(f"Better for it now: {best['name']} - {best['score'] - have['score']:+.0f} points against {have['name']}"
            + (f", {min(best['t2'], best['td'] or best['t2']) / max(1, min(have['t2'], have['td'] or have['t2'])):.1f}x the speed" if best['t2'] else ""))
    plan(best)
    if mac:
        out(f"\nOn a Mac, llmbox picks but does not run models yet. The settings it was measured with, for LM Studio and Ollama:\n"
            f"  {registry.DEFAULT_URL.rstrip('/')}/recipe-{best['id']}.html#run")
        return 0
    if plan_only or not engine:
        return 0 if plan_only else 1
    a = _ask("\nInstall and start it?", "y", yes, "ynl")
    if a == "l":   # the others, numbered
        for i, x in enumerate(ok[:12], 1):
            out(f"  {i:2d}. {x['name'][:50]:50s} {x['score']:5.1f}%  ~{x['t2']:.0f} tok/s  {x.get('size_gb') or '?'} GB"
                + (f"  (needs {x['needs']}: build it yourself)" if x.get("needs") else ""))
        n = input("which one (number)? ").strip()
        if not n.isdigit() or not 1 <= int(n) <= min(12, len(ok)):
            out("nothing chosen")
            return 1
        best = ok[int(n) - 1]
        plan(best)
    elif a == "n":
        out(f"Nothing installed. Later: llmbox install {best['id']} --from registry --host {host} --apply")
        return 0
    from . import install, serving, speed, submit
    rid = best["id"]
    if install.run(rid, registry.REGISTRY, host, dry_run=False, out=out):
        return 1
    if stops_timing(prof):
        out("\nTiming on AMD comes after launch: the model is installed and starts now; `llmbox test` will time it then.")
    elif _ask("\nTime it on this computer and see how it compares with machines like it (about 3 minutes)?", "y", yes) == "y":
        m = speed.measure(host, rid, depths=[32000], unload=True)
        sp = m.get("speed") or {}
        if sp.get("decode_tps"):
            same = (next((e for e in pick.index()["recipes"] if e["id"] == rid), {}).get("measured") or {}).get(cls)
            out(f"  {sp['decode_tps']:.0f} tokens/s here" + (f"; machines like it: median {same[0]:.0f} ({same[3]} machine{'s' if same[3] != 1 else ''})"
                                                            if same else "; nobody has sent this hardware yet: yours would be the first"))
            slow = pick.slow_note(sp["decode_tps"], same[0] if same else best.get("t2"))
            if slow:
                out(slow)
            path = next((p for p, r in results.files(host) if r.get("id") == m.get("id")), None)
            a = "n" if yes else None   # --yes takes the defaults, never a send: that needs its own yes
            while a is None:
                a = _ask("  Send it so the site shows this hardware? (anonymous: no names, no paths; s = show exactly what is sent)", "y", False, "yns", off_tty="n")
                if a == "s":
                    submit.run([path], host, submit.DEFAULT_SERVER, dry_run=True)
                    a = None
            if a == "y" and path:
                try:
                    submit.run([path], host, submit.DEFAULT_SERVER, dry_run=False)
                except SystemExit as e:   # the server down: the model still starts; `llmbox submit` sends it later
                    out(f"  not sent ({e}); `llmbox submit` sends it later")
    return _start(host, best, out)


def _start(host: str, best: dict, out) -> int:
    """Serve it in the background and say how to use it."""
    from . import serving
    rid = best["id"]
    for s in serving.running():   # one model at a time on the card
        if s.get("host") == host and s["rid"] != rid:
            serving.stop_background(host, s["rid"])
            out(f"(stopped {s['rid']} to make room)")
    out(f"\nstarting {best['name']} ...")
    s = serving.start_background(host, rid)
    base = s["url"] + "/v1"
    out(f"{best['name']} is running: {base}  (model name: {rid})")
    out("  apps: any OpenAI-compatible client - base URL above, any API key (Open WebUI, Continue, Cline, LibreChat ...)")
    out(f"  try:  curl {base}/chat/completions -H 'Content-Type: application/json' -d '{{\"model\":\"{rid}\",\"messages\":[{{\"role\":\"user\",\"content\":\"Hello\"}}]}}'")
    out(f"  stop: llmbox stop {rid}  ·  measure it against machines like this one: llmbox test {rid}  ·  a profile: llmbox login")
    return 0
