"""Dry-run a generated launcher and an existing one on the host and compare their llama-server arguments.
usage: python3 tests/dry_compare.py <recipe-id> <existing launcher path on host> [host]"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import recipe  # noqa: E402
from llmbox.host import Host  # noqa: E402


def dry(h: Host, text: str) -> list[str]:
    text = re.sub(r"(?ms)^for i in \$\(seq 1 90\).*?done[^\n]*\n", "", text)          # drop the VRAM wait loop
    text = re.sub(r"(?m)^exec (taskset -c \S+ )?", "printf '%s\\\\n' ", text)          # print argv instead of exec
    return h.run("bash -s 5999", input=text).stdout.splitlines()


def pairs(a: list[str]) -> dict:
    out, i = {}, 1
    while i < len(a):
        if a[i].startswith("-") and not re.fullmatch(r"-?\d+(\.\d+)?", a[i]):
            v = a[i + 1] if i + 1 < len(a) and (not a[i + 1].startswith("-") or re.fullmatch(r"-?\d+(\.\d+)?", a[i + 1])) else ""
            if re.fullmatch(r"-?\d+(\.\d+)?", v or "x"):
                v = f"{float(v):g}"
            out.setdefault(a[i], []).append(v)
            i += 2 if v != "" else 1
        else:
            i += 1
    return out


def main() -> int:
    rid, existing = sys.argv[1], sys.argv[2]
    h = Host(sys.argv[3] if len(sys.argv) > 3 else "box", ssh=__import__("llmbox.hosts", fromlist=["box_ssh"]).box_ssh())
    gen = dry(h, recipe.launcher(recipe.load("box", rid)))
    old = dry(h, h.run(f"cat {existing}").stdout)
    g, o = pairs(gen), pairs(old)
    diff = {k: (g.get(k), o.get(k)) for k in sorted(set(g) | set(o)) if g.get(k) != o.get(k)}
    print(f"{rid}: generated {len(gen)} args, existing {len(old)} args; binary same: {gen[:1] == old[:1]}")
    for k, (a, b) in diff.items():
        print(f"  {k:24s} generated={a}  existing={b}")
    print("  identical" if not diff else f"  {len(diff)} difference(s)")
    return 0 if not diff else 1


if __name__ == "__main__":
    sys.exit(main())
