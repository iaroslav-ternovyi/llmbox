"""Every test script in tests/, in parallel, with one line each and a total. A test passes when it exits 0.
Run: python3 tests/run_all.py            (all: ~1.5 min on 4 cores)
     python3 tests/run_all.py --quick    (without the two slow ones: the level fingerprints and techhelp's real-program checks)
Not here: tests/real_programs.py (needs Docker) and validate_estimate.py / dry_compare.py (tools, not tests)."""
import concurrent.futures as cf
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SLOW = {"test_levels_stable.py", "test_techhelp.py"}


def run(name: str) -> tuple[str, int, float, str]:
    t = time.time()
    p = subprocess.run([sys.executable, os.path.join(HERE, name)], capture_output=True, text=True, cwd=os.path.dirname(HERE))
    last = next((x for x in reversed((p.stdout + p.stderr).strip().splitlines()) if x.strip()), "")
    return name, p.returncode, time.time() - t, last


def main() -> int:
    names = sorted(f for f in os.listdir(HERE) if f.startswith("test_") and f.endswith(".py"))
    if "--quick" in sys.argv:
        names = [n for n in names if n not in SLOW]
    t0, failed = time.time(), []
    with cf.ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as ex:
        for name, rc, sec, last in sorted(ex.map(run, names)):
            print(f"{'ok  ' if rc == 0 else 'FAIL'} {name:28s} {sec:5.0f} s  {last[:80]}")
            if rc:
                failed.append(name)
    print(f"\n{len(names) - len(failed)} of {len(names)} passed in {time.time() - t0:.0f} s" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
