"""Where models run: the local machine or a remote one over ssh. Every host operation goes through here."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass

_HOSTSIDE = os.path.join(os.path.dirname(__file__), "hostside")
_GGUF = os.path.join(os.path.dirname(__file__), "gguf.py")
REMOTE_DIR = "~/.cache/llmbox"


@dataclass
class Host:
    name: str
    ssh: str | None = None      # "user@host" or an ~/.ssh/config alias; None = local

    @property
    def is_local(self) -> bool:
        return not self.ssh

    def run(self, cmd: str, timeout: int = 120, input: str | None = None) -> subprocess.CompletedProcess:
        """Run a shell command on the host."""
        argv = ["bash", "-lc", cmd] if self.is_local else \
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", self.ssh, cmd]
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, input=input)

    def _sync_agent(self) -> str:
        """Ship the stdlib agent + GGUF reader to the host (tiny; done every call so versions never drift)."""
        if self.is_local:
            d = os.path.expanduser(REMOTE_DIR)
            os.makedirs(d, exist_ok=True)
            for src in (os.path.join(_HOSTSIDE, "agent.py"), _GGUF):
                with open(src) as f, open(os.path.join(d, os.path.basename(src)), "w") as g:
                    g.write(f.read())
            return d
        payload = {os.path.basename(p): open(p).read() for p in (os.path.join(_HOSTSIDE, "agent.py"), _GGUF)}
        script = ("import json,os,sys;d=os.path.expanduser('%s');os.makedirs(d,exist_ok=True);"
                  "[open(os.path.join(d,k),'w').write(v) for k,v in json.load(sys.stdin).items()]") % REMOTE_DIR
        r = self.run(f"python3 -c {shlex.quote(script)}", input=json.dumps(payload))
        if r.returncode:
            raise RuntimeError(f"{self.name}: cannot install agent: {r.stderr.strip()[-300:]}")
        return REMOTE_DIR

    def agent(self, *args: str, timeout: int = 300) -> dict:
        d = self._sync_agent()
        r = self.run(f"python3 {d}/agent.py " + " ".join(shlex.quote(a) for a in args), timeout=timeout)
        if r.returncode:
            raise RuntimeError(f"{self.name}: agent {args[0]} failed: {r.stderr.strip()[-500:]}")
        return json.loads(r.stdout)
