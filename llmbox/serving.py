"""A model served by llmbox itself, without llama-swap: `llmbox run <id>` for everyday use, and `llmbox test` for the
length of its run. The server is the recipe's command line (the same flags a llama-swap launcher would run), started
detached on the host by the agent (serve-start), on a free port; the host's llama-swap model is unloaded first so the
card is free. On a machine reached over ssh the server listens on every address (the controller has to reach it);
on this machine only on localhost.
"""
from __future__ import annotations

import contextlib
import json
import os
import time

from . import hosts, recipe as rc
from .speed import model_files


def address(name: str) -> str:
    """The host part of the machine's model address (its profile endpoint, else its ssh address, else localhost)."""
    ep = hosts.endpoint(name)
    return ep.split("://", 1)[-1].rsplit(":", 1)[0]


def start(host_name: str, rid: str, port: int | None = None, overrides: list[str] | None = None) -> dict:
    """{url, port, pid, log, cmd} of a llama-server running the recipe (with `--set` overrides: a variant); raises
    RuntimeError when it does not come up."""
    prof = hosts.load(host_name)
    h = hosts.host_of(prof)
    r = rc.load_set(host_name, rid, overrides)
    hosts.free_up(h)
    args = rc.server_args(r)
    i = args.index("--port")
    args = args[:i] + args[i + 2:]
    local = not prof.get("ssh")
    from . import engine
    spec = {"server": engine.ensure_server(host_name, dict(r, id=rid)), "args": args, "affinity": r["runtime"]["cpu_affinity"],
            "model_files": model_files(r["model"]["path"]), "headroom_mib": r["placement"]["cache_ram_headroom_mib"],
            "bind": "127.0.0.1" if local else "0.0.0.0", "port": port}
    m = h.agent("serve-start", json.dumps(spec), timeout=1200)
    if m.get("error"):
        tail = h.run(f"tail -5 {m.get('log')}", timeout=30).stdout if m.get("log") else ""
        raise RuntimeError(f"{rid} on {host_name}: {m['error']}\n{tail}")
    return dict(m, url=f"http://{'127.0.0.1' if local else address(host_name)}:{m['port']}")


def stop(host_name: str, pid: int) -> dict:
    return hosts.host_of(hosts.load(host_name)).agent("serve-stop", str(pid), timeout=120)


def _state(host_name: str, rid: str) -> str:
    return os.path.join(hosts.HOME, "serving", f"{host_name}-{rid}.json")


def start_background(host_name: str, rid: str, port: int | None = None) -> dict:
    """Serve the recipe and leave it running (llmbox stop ends it); remembered in ~/.llmbox/serving."""
    s = start(host_name, rid, port)
    os.makedirs(os.path.dirname(_state(host_name, rid)), exist_ok=True)
    json.dump(dict(s, host=host_name, rid=rid, started=time.strftime("%Y-%m-%dT%H:%M:%S")), open(_state(host_name, rid), "w"))
    return s


def running() -> list[dict]:
    """The servers llmbox left running that still are (a reboot or a crash ends them: their notes are removed)."""
    d = os.path.join(hosts.HOME, "serving")
    out = []
    for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        try:
            s = json.load(open(os.path.join(d, f)))
            alive = hosts.host_of(hosts.load(s["host"])).agent("serve-alive", str(s["pid"]), timeout=60).get("alive")
        except (OSError, ValueError, KeyError, RuntimeError):
            alive = False
        if alive:
            out.append(s)
        else:
            os.remove(os.path.join(d, f))
    return out


def stop_background(host_name: str, rid: str) -> bool:
    p = _state(host_name, rid)
    if not os.path.exists(p):
        return False
    s = json.load(open(p))
    stop(host_name, s["pid"])
    os.remove(p)
    return True


@contextlib.contextmanager
def served(host_name: str, rid: str, overrides: list[str] | None = None):
    """The recipe served for the length of the block: yields its OpenAI-compatible base URL."""
    s = start(host_name, rid, overrides=overrides)
    try:
        yield s["url"]
    finally:
        stop(host_name, s["pid"])
