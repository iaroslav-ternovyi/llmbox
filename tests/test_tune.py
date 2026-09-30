"""The knobs tune tries (llmbox/tune.py). The thread count is tried only where the CPU computes experts (MoE with
experts in RAM): K2-Horizon on 8 cores, 2026-09-30, ran 28.9 tok/s at 4-7 threads and 26.1 at 8.
Run: python3 tests/test_tune.py"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import recipe as rc, tune  # noqa: E402


def rec(**placement):
    return rc._merge(rc.DEFAULTS, {"runtime": {"threads": placement.pop("threads", 0)}, "placement": placement})


moe = SimpleNamespace(is_moe=True, n_mtp_layers=0, n_layers=48, context_length=131072)
dense = SimpleNamespace(is_moe=False, n_mtp_layers=0, n_layers=64, context_length=131072)

names = lambda vs: {ov[0] for _n, ov, _ok in vs if ov}
v = tune.variants(rec(fit=False, n_cpu_moe=46, threads=4), moe, cores=8)
assert {"runtime.threads=7", "runtime.threads=8"} <= names(v) and "runtime.threads=4" not in names(v), names(v)
assert all(ok for n, ov, ok in v if ov and ov[0].startswith("runtime.threads")), "thread variants are speed-only"
v = tune.variants(rec(fit=True), moe, cores=8)
assert {"runtime.threads=4", "runtime.threads=7"} <= names(v) and "runtime.threads=8" not in names(v), names(v)   # 0 = all cores
assert not any(x.startswith("runtime.threads") for x in names(tune.variants(rec(fit=True), dense, cores=8))), "dense on the card: no thread knob"
assert not any(x.startswith("runtime.threads") for x in names(tune.variants(rec(fit=True), moe))), "cores unknown: no thread knob"

assert tune.flags_of(["runtime.threads=6"]) == ["--threads", "6"]
line = "    cmd: /home/u/run.sh ${PORT} --threads 4"
assert tune.new_cmd_line(line, ["--threads", "6"], ["--threads", "4"]).endswith("${PORT} --threads 6")
print("all passed")
