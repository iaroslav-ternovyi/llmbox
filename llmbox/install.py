"""`llmbox install <recipe> --host <name>`: put a recipe on a machine - fit, download, verify, launcher, llama-swap entry.

Steps (each one is skipped when already done, so install can be re-run after an interruption):
  1. fit        the hardware layer for this host (`llmbox fit`); the portable layer stays the recipe's
  2. download   the GGUF parts from Hugging Face into the host's models folder, resumable; a gated repo needs HF_TOKEN
                in the host's own environment (it is never sent from here)
  3. verify     sha256 of the file against Hugging Face's LFS id
  4. launcher   <llama-swap dir>/start-<id>.sh generated from the recipe; an existing, different launcher is left alone
                (hand-written launchers are common) unless --force, and then it is kept as .bak
  5. entry      the llama-swap config entry, appended with a dated backup of the config; llama-swap picks it up on its
                next config reload
  6. check      `llmbox recipe check`: the entry really starts what the recipe says
Nothing is written while the host is serving or benchmarking (the box rule: config edits only when idle), and
--dry-run only looks: it prints every command and file it would write.
"""
from __future__ import annotations

import os
import shlex
import time
from dataclasses import dataclass

from . import fit as F
from . import hosts, recipe as rc


@dataclass
class Step:
    name: str
    state: str          # done / todo / blocked / skipped
    detail: str
    cmd: str = ""       # what runs on the host (shown in --dry-run)


def _q(p: str) -> str:
    return shlex.quote(p)


def _remote_size(h, path: str) -> int:
    r = h.run(f"stat -c %s {_q(path)} 2>/dev/null || echo -1", timeout=30)
    try:
        return int(r.stdout.strip() or -1)
    except ValueError:
        return -1


def plan(rid: str, src: str, target: str, force: bool = False, unload: bool = False) -> tuple[dict, list[Step], object]:
    """The fitted recipe and the steps with their current state on the host (read-only probes)."""
    from . import hf
    prof = hosts.load(target)
    h = hosts.host_of(prof)
    r = rc.load(src, rid)
    same = src == target
    shape = F.shape_for(r, host=h if same else None)
    f = F.fit(r, shape, hosts.spec(prof), cores=(prof["hw"].get("cpu") or {}).get("cores") or (prof["hw"].get("cpu") or {}).get("threads"),
              cal=F.calibration(r, shape, src), same_host=same)
    steps = []
    if not f.fits:
        steps.append(Step("fit", "blocked", f"does not fit: {f.reason}; " + "; ".join(f.alternatives)))
        return r, steps, h
    fitted = r if same else F.apply(r, f, models_dir=prof["hw"].get("models_dir_guess"))
    steps.append(Step("fit", "done", f"context {f.ctx // 1024}k, ~{f.tps:.0f} tok/s predicted ({f.calibration.source})"))

    busy = hosts.free_up(h) if unload else h.agent("busy", timeout=60)   # --unload: an idle loaded model may go
    idle = not busy.get("busy")

    # 2-3: model parts
    m = fitted["model"]
    gf = None
    if m.get("hf_repo") and m.get("file"):
        gf = next((x for x in hf.list_gguf(m["hf_repo"]) if x.name == m["file"]), None)
    dst_dir = os.path.dirname(m["path"])
    parts = gf.parts if gf else [m["file"]]
    missing = []
    for i, p in enumerate(parts):
        dst = os.path.join(dst_dir, os.path.basename(p))
        have, want = _remote_size(h, dst), (gf.sizes[i] if gf else None)
        if want is None or have != want:
            missing.append((p, dst, want, have))
    if not missing:
        steps.append(Step("download", "done", f"{len(parts)} file(s) in {dst_dir}"))
    elif not gf:
        steps.append(Step("download", "blocked", f"{m['path']} missing and the recipe names no Hugging Face file"))
    else:
        cmds = [f"mkdir -p {_q(dst_dir)}"] + [
            f'curl -L --fail -C - ${{HF_TOKEN:+-H "Authorization: Bearer $HF_TOKEN"}} -o {_q(dst + ".part")} {_q(gf.url(p))} && mv {_q(dst + ".part")} {_q(dst)}'
            for p, dst, _w, _h in missing]
        got = sum(max(0, hv) for *_x, hv in missing)
        steps.append(Step("download", "todo" if idle or force else "blocked",
                          f"{sum(w for _p, _d, w, _h in missing) / 2**30:.1f} GB from {m['hf_repo']}" + (f" ({got / 2**30:.1f} GB already there)" if got > 0 else "")
                          + ("" if idle or force else " - host busy"), " && ".join(cmds)))
    want_sha = (gf.sha256[0] if gf and len(gf.parts) == 1 else None) or m.get("sha256")
    steps.append(Step("verify", "todo" if want_sha else "skipped", f"sha256 against {want_sha[:16]}…" if want_sha else "no sha256 known for this file"))

    # 4-5: launcher and llama-swap entry
    cfg = (prof["hw"].get("llama_swap") or {}).get("config") or os.path.expanduser(rc.LLAMA_SWAP_CONFIG)
    lpath = os.path.join(os.path.dirname(cfg), f"start-{rid}.sh")
    text = rc.launcher(fitted)
    cur = h.run(f"cat {_q(lpath)} 2>/dev/null", timeout=30).stdout
    if cur == text:
        steps.append(Step("launcher", "done", lpath))
    elif cur and not force:
        steps.append(Step("launcher", "skipped", f"{lpath} exists and differs (hand-written?); kept. --force replaces it, keeping a .bak"))
    else:
        steps.append(Step("launcher", "todo" if idle else "blocked", lpath + ("" if idle else " - host busy"),
                          f"cat > {_q(lpath)} <<'LLMBOX'\n{text}LLMBOX\nchmod +x {_q(lpath)}"))
    has = h.run(f"grep -qE '^  {rid}:' {_q(cfg)} && echo yes || echo no", timeout=30).stdout.strip() == "yes"
    entry = rc.llama_swap_entry(fitted, lpath)
    last_top = h.run(f"grep -E '^[A-Za-z]' {_q(cfg)} | tail -1", timeout=30).stdout.strip()
    if has:
        steps.append(Step("entry", "done", f"{rid} is in {cfg}"))
    elif not last_top.startswith("models:"):   # appending would land in another section
        steps.append(Step("entry", "blocked", f"{cfg} has a section after models: ({last_top!r}); add the entry by hand:\n{entry}"))
    else:
        steps.append(Step("entry", "todo" if idle else "blocked", f"append to {cfg}" + ("" if idle else " - host busy"),
                          f"cp {_q(cfg)} {_q(cfg)}.bak-{time.strftime('%Y%m%d-%H%M%S')} && {{ [ -z \"$(tail -c1 {_q(cfg)})\" ] || echo >> {_q(cfg)}; }} "
                          f"&& cat >> {_q(cfg)} <<'LLMBOX'\n{entry}LLMBOX"))
    steps.append(Step("check", "todo", "llmbox recipe check: the entry starts what the recipe says"))
    return fitted, steps, h


def run(rid: str, src: str, target: str, dry_run: bool = True, force: bool = False, out=print, unload: bool = False) -> int:
    fitted, steps, h = plan(rid, src, target, force, unload=unload and not dry_run)
    for s in steps:
        out(f"{s.state.upper():8s} {s.name:9s} {s.detail}")
        if dry_run and s.cmd and s.state == "todo":
            out("         $ " + s.cmd.replace("\n", "\n           "))
    if dry_run or any(s.state == "blocked" for s in steps):
        if any(s.state == "blocked" for s in steps):
            out("\nnothing written: a step is blocked (see above)")
        return 1 if any(s.state == "blocked" for s in steps) else 0
    if src != target:   # the fitted copy lives with the host's recipes
        path = os.path.join(rc.recipes_dir(target), f"{rid}.toml")
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "w").write(F.to_toml(fitted, header=[f"Installed by `llmbox install` on {time.strftime('%Y-%m-%d')}."]))
    for s in steps:
        if s.state != "todo":
            continue
        if s.name == "download":
            out("downloading (resumable; re-run install after an interruption) ...")
            r = h.run(s.cmd, timeout=6 * 3600)
            if r.returncode:
                out(f"download failed: {r.stderr.strip()[-400:]}")
                return 1
        elif s.name == "verify":
            got = h.agent("sha256", fitted["model"]["path"], timeout=3600).get("sha256")
            want = s.detail.split("against ")[1].rstrip("…")
            if not str(got).startswith(want):
                out(f"sha256 MISMATCH: {got} (want {want}…): the file is not the recipe's; nothing else written")
                return 1
            out(f"sha256 ok {str(got)[:16]}")
        elif s.name in ("launcher", "entry"):
            if s.name == "launcher" and force:
                h.run(f"f={_q(s.detail.split(' ')[0])}; [ -f \"$f\" ] && cp \"$f\" \"$f.bak-{time.strftime('%Y%m%d-%H%M%S')}\"", timeout=30)
            r = h.run(s.cmd, timeout=60)
            if r.returncode:
                out(f"{s.name} failed: {r.stderr.strip()[-300:]}")
                return 1
            out(f"{s.name} written")
        elif s.name == "check":
            d = rc.diff(rc.load(target, rid), rc.live_argv(h, rid))
            out("check OK" if not d else "check DIFF:\n  " + "\n  ".join(d))
    return 0
