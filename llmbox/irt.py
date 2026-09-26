"""Item response theory for the llmbox suite: calibrate task families, score a model from few tasks, test adaptively.

A task family is kind x level (`longctx.count.L5`): tasks are generated from a seed, so parameters of the family carry
over to fresh tasks and every run can use new ones. The model is the two-parameter logistic

    E[score | theta] = sigmoid(a * (theta - b))

with the task's score in [0, 1] (partial credit: share of hidden tests or constraints) used as a quasi-Bernoulli
outcome - a 7/10 carries more information than a pass/fail. a is how sharply the family separates models, b how hard
it is. theta is one capability per model; the capability on our usual scale is the weighted expected score of the
reference task set at that theta, so adaptive and fixed runs report the same number.

Adaptive testing picks, after every task, the family with the most Fisher information per expected second on this
model (a^2 p (1-p) / seconds): our tasks differ 70x in cost, so information per task alone would waste time.
Everything is plain Python (the project has no dependencies); the data are small.
"""
from __future__ import annotations

import glob
import json
import math
import os
import statistics
from dataclasses import dataclass, field

from .hosts import HOME

GRID = [(-4.0 + 0.02 * i) for i in range(501)]   # theta from -4 to 6
PRIOR = (0.0, 1.5)


def _sig(z: float) -> float:
    return 1 / (1 + math.exp(-max(-35.0, min(35.0, z))))


def family_of(item_id: str) -> str:
    """'longctx.count.L5.1' -> 'longctx.count.L5' (the trailing number is the seed index)."""
    return item_id.rsplit(".", 1)[0]


@dataclass
class Resp:
    model: str
    family: str
    item: str
    score: float
    seconds: float
    tokens: int


def responses(content_hash: str | None = None, hosts_: tuple = ("box", "cloud")) -> list[Resp]:
    """Every graded task of every full suite run (optionally of one exact suite content)."""
    out = []
    for h in hosts_:
        for f in sorted(glob.glob(os.path.join(HOME, "results", h, "*.json"))):
            try:
                r = json.load(open(f))
            except ValueError:
                continue
            su = r.get("suite") or {}
            if r.get("kind") != "suite" or su.get("blocks") or (content_hash and su.get("content_hash") != content_hash):
                continue
            m = (r.get("recipe") or {}).get("id") or "?"
            for x in r.get("rows", []):
                out.append(Resp(m, family_of(x["id"]), x["id"], max(0.0, min(1.0, float(x["score"]))), float(x["seconds"]),
                                int(x.get("max_reply_tokens") or 0)))
    return out


@dataclass
class Bank:
    a: dict = field(default_factory=dict)          # family -> discrimination
    b: dict = field(default_factory=dict)          # family -> difficulty
    seconds: dict = field(default_factory=dict)    # family -> median seconds on the calibration models
    block: dict = field(default_factory=dict)      # family -> block
    theta: dict = field(default_factory=dict)      # calibration models -> theta
    weights: dict = field(default_factory=dict)    # block -> weight (for the capability scale)

    def p(self, fam: str, th: float) -> float:
        return _sig(self.a[fam] * (th - self.b[fam]))

    def info(self, fam: str, th: float) -> float:
        p = self.p(fam, th)
        return self.a[fam] ** 2 * p * (1 - p)

    def capability(self, th: float, ref: list[str] | None = None) -> float:
        """Expected weighted score (0-100) of the reference families at theta: the suite's usual number."""
        ref = ref or list(self.a)
        tot = 0.0
        wsum = 0.0
        for blk, w in self.weights.items():
            fams = [f for f in ref if self.block[f] == blk]
            if fams:
                tot += w * sum(self.p(f, th) for f in fams) / len(fams)
                wsum += w
        return 100 * tot / wsum if wsum else 0.0


def calibrate(resp: list[Resp], weights: dict, iters: int = 4000, lr: float = 0.05) -> Bank:
    """Joint MAP fit of theta per model and (a, b) per family; weak priors keep a small data set sane."""
    models = sorted({r.model for r in resp})
    fams = sorted({r.family for r in resp})
    th = {m: 0.0 for m in models}
    la = {f: 0.0 for f in fams}                     # log a
    b = {f: 0.0 for f in fams}
    for _ in range(iters):
        gth = {m: -th[m] / PRIOR[1] ** 2 for m in models}
        gla = {f: -la[f] / 0.5 ** 2 for f in fams}
        gb = {f: -b[f] / 2.0 ** 2 for f in fams}
        for r in resp:
            a = math.exp(la[r.family])
            p = _sig(a * (th[r.model] - b[r.family]))
            e = r.score - p
            gth[r.model] += a * e
            gb[r.family] -= a * e
            gla[r.family] += a * (th[r.model] - b[r.family]) * e
        for m in models:
            th[m] += lr * gth[m]
        for f in fams:
            la[f] = max(-3.0, min(1.5, la[f] + lr * gla[f]))
            b[f] += lr * gb[f]
    bank = Bank(a={f: math.exp(la[f]) for f in fams}, b=b, theta=th, weights=weights)
    for f in fams:
        rs = [r for r in resp if r.family == f]
        bank.seconds[f] = statistics.median(r.seconds for r in rs)
        bank.block[f] = f.split(".")[0]
    return bank


def posterior(bank: Bank, obs: list[tuple[str, float]], prior: tuple = PRIOR) -> tuple[float, float, list[float]]:
    """EAP theta and its sd from (family, score) observations, on a grid."""
    mu, sd = prior
    logw = []
    for t in GRID:
        lw = -0.5 * ((t - mu) / sd) ** 2
        for f, x in obs:
            p = bank.p(f, t)
            lw += x * math.log(max(p, 1e-12)) + (1 - x) * math.log(max(1 - p, 1e-12))
        logw.append(lw)
    mx = max(logw)
    w = [math.exp(v - mx) for v in logw]
    s = sum(w)
    w = [v / s for v in w]
    mean = sum(t * v for t, v in zip(GRID, w))
    sdv = math.sqrt(max(0.0, sum((t - mean) ** 2 * v for t, v in zip(GRID, w))))
    return mean, sdv, w


def capability_interval(bank: Bank, w: list[float], ref: list[str] | None = None) -> tuple[float, float, float]:
    """Posterior mean and 95% interval of the capability (0-100)."""
    caps = [bank.capability(t, ref) for t in GRID]
    mean = sum(c * v for c, v in zip(caps, w))
    order = sorted(zip(caps, w))
    acc, lo, hi = 0.0, order[0][0], order[-1][0]
    for c, v in order:
        acc += v
        if acc >= 0.025 and lo == order[0][0]:
            lo = c
        if acc >= 0.975:
            hi = c
            break
    return mean, lo, hi


def next_family(bank: Bank, th: float, done: set, slowness: float = 1.0, min_per_block: int = 0, counts: dict | None = None) -> str | None:
    """The family with the most information per expected second; blocks below their minimum go first."""
    cands = [f for f in bank.a if f not in done]
    if not cands:
        return None
    if min_per_block and counts is not None:
        short = [f for f in cands if counts.get(bank.block[f], 0) < min_per_block]
        if short:
            cands = short
    return max(cands, key=lambda f: bank.info(f, th) / (bank.seconds[f] * slowness))


def simulate(bank: Bank, truth: dict, true_seconds: dict, prior: tuple = PRIOR, ref: list[str] | None = None,
             budget_s: float = 45 * 60, target_halfwidth: float = 0.0, min_per_block: int = 1) -> list[dict]:
    """Replay an adaptive run on a model's recorded answers: truth = family -> score, true_seconds = family -> seconds.
    Returns the trajectory (elapsed minutes, estimate, interval) after every task."""
    done, obs, counts, spent, traj = set(), [], {}, 0.0, []
    th, _sd, w = posterior(bank, obs, prior)
    while True:
        f = next_family(bank, th, done | {x for x in bank.a if x not in truth}, 1.0, min_per_block, counts)
        if f is None:
            break
        done.add(f)
        obs.append((f, truth[f]))
        counts[bank.block[f]] = counts.get(bank.block[f], 0) + 1
        spent += true_seconds[f]
        th, _sd, w = posterior(bank, obs, prior)
        cap, lo, hi = capability_interval(bank, w, ref)
        traj.append({"n": len(obs), "family": f, "minutes": round(spent / 60, 1), "cap": round(cap, 1), "lo": round(lo, 1), "hi": round(hi, 1)})
        if spent >= budget_s or (target_halfwidth and (hi - lo) / 2 <= target_halfwidth):
            break
    return traj


# ---- persistence --------------------------------------------------------------------------------------------------

def bank_path(content_hash: str) -> str:
    return os.path.join(HOME, "irt", f"bank-{content_hash}.json")


def save(bank: Bank, content_hash: str, n_models: int) -> str:
    p = bank_path(content_hash)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump({"content_hash": content_hash, "n_models": n_models, "a": bank.a, "b": bank.b, "seconds": bank.seconds,
               "block": bank.block, "theta": bank.theta, "weights": bank.weights}, open(p, "w"), indent=1)
    return p


def load(content_hash: str) -> Bank | None:
    p = bank_path(content_hash)
    if not os.path.exists(p):
        return None
    d = json.load(open(p))
    return Bank(a=d["a"], b=d["b"], seconds=d["seconds"], block=d["block"], theta=d["theta"], weights=d["weights"])


# ---- block offsets: one capability plus a shrunk per-block deviation ---------------------------------------------------
#
# A model can be much better at one kind of work than its overall level (Nex: agentic 100, writing 59). With one theta,
# the few agentic tasks of a short run would be read through the other blocks. So each block b gets eta_b = theta + d_b,
# d_b ~ N(0, tau^2): with no tasks in a block its estimate is theta (wide); every task there pulls it toward the data.
# The next task is the one that shrinks the 95% interval of the weighted capability the most per expected second.

TAU = 1.0   # the real deviations are large (Nex: agentic 100, writing 59)


def _grid_post(bank: Bank, obs: list[tuple[str, float]], mu: float, sd: float) -> list[float]:
    logw = []
    for t in GRID:
        lw = -0.5 * ((t - mu) / sd) ** 2
        for f, x in obs:
            p = bank.p(f, t)
            lw += x * math.log(max(p, 1e-12)) + (1 - x) * math.log(max(1 - p, 1e-12))
        logw.append(lw)
    mx = max(logw)
    w = [math.exp(v - mx) for v in logw]
    s = sum(w)
    return [v / s for v in w]


def block_estimate(bank: Bank, obs: list[tuple[str, float]], prior: tuple = PRIOR, tau: float = TAU, draws: int = 60) -> dict:
    """Posterior of the weighted capability under theta + per-block offsets, by drawing theta from its posterior and,
    for each draw, eta_b = theta + d_b from its block posterior (prior N(theta, tau^2), the block's own answers).
    Blocks share theta, so their errors are correlated exactly as much as the data say. Returns per block the
    expected score (mean, var, eta, eta_var) and the capability with its 95% interval."""
    import random as _r
    rnd = _r.Random(len(obs) * 7919 + 17)
    th, sdth, wth = posterior(bank, obs, prior)
    cdf, acc = [], 0.0
    for v in wth:
        acc += v
        cdf.append(acc)
    thetas = [GRID[min(len(GRID) - 1, next(i for i, c in enumerate(cdf) if c >= (k + 0.5) / draws))] for k in range(draws)]
    coarse = [(-4.0 + 0.1 * i) for i in range(101)]
    fams = {blk: [f for f in bank.a if bank.block[f] == blk] for blk in bank.weights}
    mine = {blk: [(f, x) for f, x in obs if bank.block[f] == blk] for blk in bank.weights}
    wsum = sum(w for blk, w in bank.weights.items() if fams[blk])
    caps, per = [], {blk: [] for blk in bank.weights if fams[blk]}
    etas = {blk: [] for blk in per}
    for t in thetas:
        c = 0.0
        for blk in per:
            lw = []
            for e in coarse:
                v = -0.5 * ((e - t) / tau) ** 2
                for f, x in mine[blk]:
                    p = bank.p(f, e)
                    v += x * math.log(max(p, 1e-12)) + (1 - x) * math.log(max(1 - p, 1e-12))
                lw.append(v)
            mx = max(lw)
            ww = [math.exp(v - mx) for v in lw]
            u, acc = rnd.random() * sum(ww), 0.0
            e = coarse[-1]
            for ei, wi in zip(coarse, ww):
                acc += wi
                if acc >= u:
                    e = ei
                    break
            sc = sum(bank.p(f, e) for f in fams[blk]) / len(fams[blk])
            per[blk].append(sc)
            etas[blk].append(e)
            c += bank.weights[blk] * sc
        caps.append(100 * c / wsum)
    mean = lambda xs: sum(xs) / len(xs)
    var = lambda xs: sum((x - mean(xs)) ** 2 for x in xs) / max(1, len(xs) - 1)
    out = {blk: {"score": mean(v), "var": var(v), "eta": mean(etas[blk]), "eta_var": max(var(etas[blk]), 1e-4), "n": len(mine[blk])}
           for blk, v in per.items()}
    cs = sorted(caps)
    return {"theta": th, "theta_sd": sdth, "blocks": out, "capability": mean(caps),
            "lo": cs[max(0, int(0.025 * len(cs)))], "hi": cs[min(len(cs) - 1, int(0.975 * len(cs)))]}


def next_family_blocks(bank: Bank, est: dict, used: dict, slowness: float = 1.0, max_per_family: int = 3) -> str | None:
    """The family whose answer would shrink the capability's variance the most per expected second."""
    best, best_v = None, -1.0
    wsum = sum(bank.weights.values())
    for f in bank.a:
        if used.get(f, 0) >= max_per_family:
            continue
        blk = bank.block[f]
        b = est["blocks"].get(blk)
        if not b:
            continue
        info = bank.info(f, b["eta"])
        prec = 1 / max(b["eta_var"], 1e-6)
        d_eta_var = 1 / prec - 1 / (prec + info)
        fams = [g for g in bank.a if bank.block[g] == blk]
        # slope of the block score in eta, at the current estimate
        slope = sum(bank.a[g] * bank.p(g, b["eta"]) * (1 - bank.p(g, b["eta"])) for g in fams) / len(fams)
        gain = (bank.weights[blk] / wsum) ** 2 * slope ** 2 * d_eta_var
        v = gain / (bank.seconds[f] * slowness)
        if v > best_v:
            best, best_v = f, v
    return best
