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
import hashlib
import json
import math
import os
import statistics
from dataclasses import dataclass, field

from . import results
from .hosts import HOME

GRID = [(-4.0 + 0.02 * i) for i in range(501)]   # theta from -4 to 6
# discrimination prior: log a ~ N(0, 1), a <= 8. With sd 0.5 and a <= 4.5 a step item (tools.bulk_discount L6: every local
# model 0, Opus 1) was fitted as a gentle slope that promised Tiel 0.21; three adaptive draws of it at 0 pulled Tiel's
# estimate 6 points below its fixed runs.
LOG_A_SD, LOG_A_MAX = 1.0, 2.08
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


def responses(content_hash: str | list | None = None, hosts_: tuple = ("box", "cloud")) -> list[Resp]:
    """Every graded task of every full suite run (optionally of some exact suite contents: a hash or a list of them,
    e.g. two versions whose tasks are the same and whose grader fix was applied to the older runs by regrade)."""
    hashes = {content_hash} if isinstance(content_hash, str) else set(content_hash or [])
    hashes = set().union(*(equivalent(h) for h in hashes)) if hashes else hashes
    out = []
    for h in hosts_:
        for f, r in results.files(h):
            su = r.get("suite") or {}
            if r.get("kind") != "suite" or su.get("blocks") or su.get("tier") not in ("quick", "adaptive", "medium", "deep") \
                    or (hashes and su.get("content_hash") not in hashes):
                continue
            m = (r.get("recipe") or {}).get("id") or "?"
            for x in r.get("rows", []):
                if x.get("pending") or x.get("error"):
                    continue
                out.append(Resp(m, family_of(x["id"]), x["id"], max(0.0, min(1.0, float(x["score"]))), float(x["seconds"]),
                                int(x.get("max_reply_tokens") or 0)))
    return out


def _regrade(fam: str, row: dict, cache: dict) -> float | None:
    """The current grader's score for a saved answer to a text-graded task (the task regenerated from its id)."""
    import hashlib
    from . import suite
    key = f"{row['id']}:{hashlib.sha256((row.get('final') or '').encode()).hexdigest()[:10]}"
    if key in cache:
        return cache[key]
    block, kind, lv = fam.rsplit(".", 2)
    try:
        it = suite.BLOCKS[block][kind](int(row["id"].rsplit(".", 1)[1]), int(lv[1:]))
        sc = round(max(0.0, min(1.0, float(it.check(row["final"], row)))), 4)
    except Exception:
        sc = None
    cache[key] = sc
    return sc


def too_long(row: dict) -> bool:
    e = str(row.get("error") or "")
    return "exceeds the available context" in e or "context size" in e.lower() and "exceed" in e.lower()


def pool(hosts_: tuple = ("box", "cloud")) -> dict:
    """{(recipe id, host): [rows]}: every answer, from any suite version and any run (fixed, adaptive, one block only),
    to a task family that is unchanged in the current suite (llmbox/famfp.py). Text-graded answers from another version
    are graded again by the current grader; answers graded on a tool world, a workspace or by the reader count as saved.
    An answer saved twice (a resumed run) counts once."""
    from . import famfp, suite
    from .bench import TEXT_GRADED
    cur = suite.content_hash()
    recs = []
    for h in hosts_:
        for f, r in results.files(h):
            su = r.get("suite") or {}
            if r.get("kind") != "suite" or su.get("tier") not in ("quick", "adaptive", "medium", "deep") or not su.get("content_hash"):
                continue
            recs.append((h, r))
    fams_by_hash: dict = {}
    for h, r in recs:
        fams_by_hash.setdefault(r["suite"]["content_hash"], set()).update(family_of(x["id"]) for x in r.get("rows", []))
    allf = set().union(*fams_by_hash.values()) if fams_by_hash else set()
    now = famfp.fingerprints(cur, allf)
    old = {ch: famfp.fingerprints(ch, fs) for ch, fs in fams_by_hash.items()}
    cp = os.path.join(HOME, "irt", f"regrade-{cur}.json")
    cache = json.load(open(cp)) if os.path.exists(cp) else {}
    n0 = len(cache)
    out: dict = {}
    for h, r in sorted(recs, key=lambda hr: hr[1].get("created", "")):
        ch = r["suite"]["content_hash"]
        rid = (r.get("recipe") or {}).get("id") or "?"
        rows = out.setdefault((rid, h), {})
        for x in r.get("rows", []):
            fam = family_of(x["id"])
            if too_long(x):   # the task does not fit the model's context: a real 0, not a failed measurement
                x = dict(x, score=0.0, error=None)
            if x.get("pending") or x.get("error") or not now.get(fam) or old[ch].get(fam) != now[fam]:
                continue
            if ch != cur and fam.split(".")[0] in TEXT_GRADED and x.get("final") is not None:
                sc = _regrade(fam, x, cache)
                if sc is None:
                    continue
                x = dict(x, score=sc)
            # a resumed run copies finished answers from the attempt before it: the same answer counts once; two
            # different answers to one task (two runs with the same seed) are two samples and both count
            rows[f"{x['id']}:{hashlib.sha256(str(x.get('final')).encode()).hexdigest()[:10]}"] = dict(x, _run=r.get("created"))
    if len(cache) != n0:
        json.dump(cache, open(cp, "w"))
    return {k: list(v.values()) for k, v in out.items() if v}


def pooled_responses(hosts_: tuple = ("box", "cloud")) -> list[Resp]:
    """responses() over pool(): the calibration data of the current suite, old versions included where unchanged."""
    return [Resp(m, family_of(x["id"]), x["id"], max(0.0, min(1.0, float(x["score"]))), float(x["seconds"]),
                 int(x.get("max_reply_tokens") or 0)) for (m, _h), rows in pool(hosts_).items() for x in rows]


@dataclass
class Bank:
    a: dict = field(default_factory=dict)          # family -> discrimination
    b: dict = field(default_factory=dict)          # family -> difficulty
    seconds: dict = field(default_factory=dict)    # family -> median seconds on the calibration models
    block: dict = field(default_factory=dict)      # family -> block
    theta: dict = field(default_factory=dict)      # calibration models -> theta
    weights: dict = field(default_factory=dict)    # block -> weight (for the capability scale)
    tau: float = 1.0                                # sd of a model's per-block deviation from its theta
    dev: dict = field(default_factory=dict)        # calibration models -> {block: deviation}
    neff: dict = field(default_factory=dict)       # family -> effective number of independent answers in one task
    prior: tuple = PRIOR                            # theta prior: the calibration models' mean and sd
    scale: list = field(default_factory=list)       # families the capability averages over (answered by MIN_SCALE_MODELS+)
    provisional: list = field(default_factory=list)  # families with guessed parameters: adaptive runs may pick them, no score

    def n(self, fam: str) -> float:
        return self.neff.get(fam, 1.0)

    def p(self, fam: str, th: float) -> float:
        return _sig(self.a[fam] * (th - self.b[fam]))

    def info(self, fam: str, th: float) -> float:
        p = self.p(fam, th)
        return self.n(fam) * self.a[fam] ** 2 * p * (1 - p)

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


def calibrate_blocks(resp: list[Resp], weights: dict, iters: int = 6000, lr: float = 0.03, tau0: float = 1.0,
                     fixed_tau: float | None = None) -> Bank:
    """Joint MAP fit with block offsets, the model the scoring uses: eta(model, block) = theta(model) + d(model, block),
    d ~ N(0, tau^2), family 2PL on eta. tau is estimated from the data (empirical Bayes, re-set every 500 steps).
    A unidimensional fit reads a model that is strong in one block and weak in another (Nex: agentic 100, writing 59)
    as family difficulty; the offsets absorb that."""
    models = sorted({r.model for r in resp})
    fams = sorted({r.family for r in resp})
    blocks = sorted({f.split(".")[0] for f in fams})
    th = {m: 0.0 for m in models}
    d = {(m, b): 0.0 for m in models for b in blocks}
    la = {f: 0.0 for f in fams}
    b_ = {f: 0.0 for f in fams}
    tau = fixed_tau or tau0
    for it in range(iters):
        gth = {m: -th[m] / PRIOR[1] ** 2 for m in models}
        gd = {k: -v / tau ** 2 for k, v in d.items()}
        gla = {f: -la[f] / LOG_A_SD ** 2 for f in fams}
        gb = {f: -b_[f] / 2.0 ** 2 for f in fams}
        for r in resp:
            blk = r.family.split(".")[0]
            a = math.exp(la[r.family])
            eta = th[r.model] + d[(r.model, blk)]
            p = _sig(a * (eta - b_[r.family]))
            e = r.score - p
            gth[r.model] += a * e
            gd[(r.model, blk)] += a * e
            gb[r.family] -= a * e
            gla[r.family] += a * (eta - b_[r.family]) * e
        for m in models:
            th[m] += lr * gth[m]
        for k in d:
            d[k] += lr * gd[k]
        for f in fams:
            la[f] = max(-3.0, min(LOG_A_MAX, la[f] + lr * gla[f]))
            b_[f] += lr * gb[f]
        if fixed_tau is None and it % 500 == 499:   # empirical Bayes for tau (biased low on small data: prefer choose_tau)
            tau = max(0.3, min(2.0, math.sqrt(sum(v * v for v in d.values()) / len(d))))
    bank = Bank(a={f: math.exp(la[f]) for f in fams}, b=b_, theta=th, weights=weights, tau=tau,
                dev={m: {b: round(d[(m, b)], 3) for b in blocks} for m in models})
    for f in fams:
        rs = [r for r in resp if r.family == f]
        bank.seconds[f] = statistics.median(r.seconds for r in rs)
        bank.block[f] = f.split(".")[0]
    bank.neff = effective_n(bank, resp)
    by = {}
    for r in resp:
        by.setdefault(r.family, set()).add(r.model)
    bank.scale = sorted(f for f in fams if len(by[f]) >= MIN_SCALE_MODELS)
    ths = list(th.values())
    if len(ths) >= 3:
        bank.prior = (statistics.mean(ths), max(1.5, statistics.stdev(ths) * 2))   # wide: new models may be outside
    return bank


def effective_n(bank: Bank, resp: list[Resp], cap: float = 6.0) -> dict:
    """How many independent answers one task of a family is worth: the Bernoulli variance p(1-p) over the observed
    squared residual (a 5-question task whose questions moved independently would be worth 5; ours move together more
    often than not). Shrunk toward 1 with a pseudo-count, per block first, capped: residuals include misfit, so this
    errs low."""
    def resid(rs):
        num = den = 0.0
        for r in rs:
            eta = bank.theta.get(r.model, 0.0) + bank.dev.get(r.model, {}).get(r.family.split(".")[0], 0.0)
            p = bank.p(r.family, eta)
            num += p * (1 - p)
            den += (r.score - p) ** 2
        return num, den
    out, k = {}, 4.0   # pseudo-observations of a Bernoulli task (n = 1)
    blocks = {f.split(".")[0] for f in bank.a}
    for blk in blocks:
        num, den = resid([r for r in resp if r.family.split(".")[0] == blk])
        nb = max(1.0, min(cap, (num + k * 0.25) / (den + k * 0.25)))
        for f in [f for f in bank.a if bank.block.get(f) == blk]:
            n2, d2 = resid([r for r in resp if r.family == f])
            out[f] = round(max(1.0, min(cap, (n2 + k * 0.25 * nb) / (d2 + k * 0.25))), 2)
    return out


def _loglik(bank: Bank, rs: list[Resp]) -> float:
    ll = 0.0
    for r in rs:
        eta = bank.theta.get(r.model, 0.0) + bank.dev.get(r.model, {}).get(r.family.split(".")[0], 0.0)
        p = min(max(bank.p(r.family, eta), 1e-6), 1 - 1e-6) if r.family in bank.a else 0.5
        ll += r.score * math.log(p) + (1 - r.score) * math.log(1 - p)
    return ll


def choose_tau(resp: list[Resp], weights: dict, taus: tuple = (0.3, 0.5, 0.7, 1.0, 1.4), folds: int = 3, seed: int = 1,
               iters: int = 3000) -> tuple[float, dict]:
    """tau by cross-validation: hold out one answer per (model, block) in each fold, fit on the rest, score the held-out
    answers. The empirical-Bayes estimate from MAP offsets is biased low (the offsets it averages are already shrunk)."""
    import random as _r
    rnd = _r.Random(seed)
    groups: dict = {}
    for i, r in enumerate(resp):
        groups.setdefault((r.model, r.family.split(".")[0]), []).append(i)
    held = [set() for _ in range(folds)]
    for idx in groups.values():
        if len(idx) < 2:
            continue
        pick = rnd.sample(idx, min(folds, len(idx)))
        for k, i in enumerate(pick):
            held[k].add(i)
    score = {}
    for t in taus:
        ll = 0.0
        for k in range(folds):
            train = [r for i, r in enumerate(resp) if i not in held[k]]
            bank = calibrate_blocks(train, weights, iters=iters, fixed_tau=t)
            ll += _loglik(bank, [resp[i] for i in held[k]])
        score[t] = ll
    return max(score, key=score.get), score


def posterior(bank: Bank, obs: list[tuple[str, float]], prior: tuple | None = None) -> tuple[float, float, list[float]]:
    """EAP theta and its sd from (family, score) observations, on a grid."""
    mu, sd = prior or bank.prior
    logw = []
    for t in GRID:
        lw = -0.5 * ((t - mu) / sd) ** 2
        for f, x in obs:
            p = bank.p(f, t)
            lw += bank.n(f) * (x * math.log(max(p, 1e-12)) + (1 - x) * math.log(max(1 - p, 1e-12)))
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


def cost_model(bank: Bank, seen: list[tuple[str, float]]):
    """Expected seconds of a family for the model under test, from the tasks it already did: the family's own time
    if it ran, else the bank median times this model's slowness in that block (else overall). Models differ in what
    is slow for them: Occamy spent 32 of 106 min on techhelp, Tiel 5 of 55."""
    own: dict = {}
    for f, s in seen:
        own.setdefault(f, []).append(s)
    ratios: dict = {}
    for f, ss in own.items():
        if bank.seconds.get(f):
            ratios.setdefault(bank.block[f], []).append(statistics.median(ss) / bank.seconds[f])
    allr = [x for v in ratios.values() for x in v]
    overall = statistics.median(allr) if allr else 1.0

    def cost(f: str) -> float:
        if f in own:
            return statistics.median(own[f])
        r = ratios.get(bank.block[f])
        return bank.seconds[f] * (statistics.median(r) if r else overall)
    return cost


def next_family(bank: Bank, th: float, done: set, slowness: float = 1.0, min_per_block: int = 0, counts: dict | None = None,
                cost=None) -> str | None:
    """The family with the most information per expected second; blocks below their minimum go first."""
    cands = [f for f in bank.a if f not in done]
    if not cands:
        return None
    if min_per_block and counts is not None:
        short = [f for f in cands if counts.get(bank.block[f], 0) < min_per_block]
        if short:
            cands = short
    cost = cost or (lambda f: bank.seconds[f] * slowness)
    return max(cands, key=lambda f: bank.info(f, th) / max(cost(f), 1.0))


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


# suite contents with the same tasks, mapped to the canonical one whose bank they share: a grader fix applied to the older
# runs by `llmbox regrade --reader`, or only the version string changing
SAME_TASKS = {"e9f84b50bd85": "8168e071e372",    # v0.10-dev7 (billing questions did not state the uptime; regraded)
              "8713ad469067": "8168e071e372",    # v0.10-dev7.1-7.3 -> v0.10 (the version string only)
              "19bbcf6ca73c": "53fbc86d524d"}    # v0.11 = v0.11-dev4 (the version string only)


# released suite contents (canonical hash -> version): runs of an older tag with the same tasks count as that release
# even after the working tree has moved on to the next version
RELEASES = {"8168e071e372": "0.10", "53fbc86d524d": "0.11"}


def canonical(content_hash: str | None) -> str | None:
    return SAME_TASKS.get(content_hash, content_hash)


def equivalent(content_hash: str) -> set:
    """Every suite content with the same tasks as this one (itself included)."""
    c = canonical(content_hash)
    return {c} | {h for h, t in SAME_TASKS.items() if t == c}


def bank_for(content_hash: str | None) -> Bank | None:
    if not content_hash:
        return None
    return load(canonical(content_hash))


def score_rows(bank: Bank, rows: list[dict], prior: tuple | None = None) -> dict:
    """The IRT estimate from a run's graded rows (any tier): capability with its 95% interval and the block scores.
    Rows of families the bank does not know, pending rows and errors are left out."""
    obs = [(family_of(r["id"]), max(0.0, min(1.0, float(r["score"])))) for r in rows
           if not r.get("pending") and not r.get("error") and family_of(r["id"]) in bank.a]
    est = block_estimate(bank, obs, prior)
    return {"capability": round(est["capability"], 1), "ci95": [round(est["lo"], 1), round(est["hi"], 1)],
            "blocks": {b: round(100 * v["score"], 1) for b, v in est["blocks"].items()}, "theta": round(est["theta"], 3),
            "n": len(obs)}


# ---- persistence --------------------------------------------------------------------------------------------------

def with_provisional(bank: Bank, families: list[str], step: float = 0.6) -> Bank:
    """A copy of the bank that also knows `families` it has no answers for (new levels 7-8, a rewritten kind), with
    guessed parameters so an adaptive run can pick them: difficulty extrapolated from the kind's calibrated levels
    (+step per level; the block's median at the kind's median level when the kind has none), discrimination and seconds
    from the kind (or block). They stay out of the capability scale until a recalibration has answers from enough models."""
    import copy
    nb = copy.deepcopy(bank)
    for f in families:
        if f in nb.a:
            continue
        blk, kind, lv = f.rsplit(".", 2)
        L = int(lv[1:])
        same = [(int(g.rsplit(".", 1)[1][1:]), g) for g in bank.a if g.startswith(f"{blk}.{kind}.L")]
        inblk = [g for g in bank.a if bank.block[g] == blk] or list(bank.a)
        if same:
            near = min(same, key=lambda x: abs(x[0] - L))
            b0, L0, ref = bank.b[near[1]], near[0], [g for _, g in same]
        else:
            b0, L0, ref = statistics.median(bank.b[g] for g in inblk), statistics.median(int(g.rsplit(".", 1)[1][1:]) for g in inblk), inblk
        nb.b[f] = b0 + step * (L - L0)
        nb.a[f] = statistics.median(bank.a[g] for g in ref)
        nb.seconds[f] = statistics.median(bank.seconds[g] for g in ref) * (1 + 0.25 * max(0, L - L0))
        nb.block[f] = blk
        nb.neff[f] = 1.0
        nb.provisional.append(f)
    if not nb.scale:
        nb.scale = sorted(f for f in bank.a)
    return nb


def subset(bank: Bank, fams: set) -> Bank:
    """The bank restricted to some families (the calibrated ones, during an adaptive run with provisional families)."""
    import copy
    nb = copy.copy(bank)
    for k in ("a", "b", "seconds", "block", "neff"):
        setattr(nb, k, {f: v for f, v in getattr(bank, k).items() if f in fams})
    nb.provisional = []
    nb.scale = [f for f in bank.scale if f in fams]
    return nb


def bank_path(content_hash: str) -> str:
    return os.path.join(HOME, "irt", f"bank-{content_hash}.json")


def save(bank: Bank, content_hash: str, n_models: int) -> str:
    p = bank_path(content_hash)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump({"content_hash": content_hash, "n_models": n_models, "a": bank.a, "b": bank.b, "seconds": bank.seconds,
               "block": bank.block, "theta": bank.theta, "weights": bank.weights, "tau": bank.tau, "dev": bank.dev,
               "neff": bank.neff, "prior": list(bank.prior), "scale": bank.scale},
              open(p, "w"), indent=1)
    return p


def load(content_hash: str) -> Bank | None:
    p = bank_path(content_hash)
    if not os.path.exists(p):
        return None
    d = json.load(open(p))
    return Bank(a=d["a"], b=d["b"], seconds=d["seconds"], block=d["block"], theta=d["theta"], weights=d["weights"],
                tau=d.get("tau", TAU), dev=d.get("dev", {}), neff=d.get("neff", {}), prior=tuple(d.get("prior", PRIOR)),
                scale=d.get("scale") or [])


# ---- block offsets: one capability plus a shrunk per-block deviation ---------------------------------------------------
#
# A model can be much better at one kind of work than its overall level (Nex: agentic 100, writing 59). With one theta,
# the few agentic tasks of a short run would be read through the other blocks. So each block b gets eta_b = theta + d_b,
# d_b ~ N(0, tau^2): with no tasks in a block its estimate is theta (wide); every task there pulls it toward the data.
# The next task is the one that shrinks the 95% interval of the weighted capability the most per expected second.

TAU = 1.0   # the real deviations are large (Nex: agentic 100, writing 59)
# a family enters the capability scale once this many models answered it: before that its difficulty is a guess from a
# few answers, and the scale (the average expected score over the block's families) would move with every new run
MIN_SCALE_MODELS = 3


def _grid_post(bank: Bank, obs: list[tuple[str, float]], mu: float, sd: float) -> list[float]:
    logw = []
    for t in GRID:
        lw = -0.5 * ((t - mu) / sd) ** 2
        for f, x in obs:
            p = bank.p(f, t)
            lw += bank.n(f) * (x * math.log(max(p, 1e-12)) + (1 - x) * math.log(max(1 - p, 1e-12)))
        logw.append(lw)
    mx = max(logw)
    w = [math.exp(v - mx) for v in logw]
    s = sum(w)
    return [v / s for v in w]


def block_estimate(bank: Bank, obs: list[tuple[str, float]], prior: tuple | None = None, tau: float | None = None, draws: int = 60) -> dict:
    """Posterior of the weighted capability under theta + per-block offsets, by drawing theta from its posterior and,
    for each draw, eta_b = theta + d_b from its block posterior (prior N(theta, tau^2), the block's own answers).
    Blocks share theta, so their errors are correlated exactly as much as the data say. Returns per block the
    expected score (mean, var, eta, eta_var) and the capability with its 95% interval."""
    import random as _r
    tau = tau or bank.tau or TAU
    rnd = _r.Random(len(obs) * 7919 + 17)
    th, sdth, wth = posterior(bank, obs, prior)
    cdf, acc = [], 0.0
    for v in wth:
        acc += v
        cdf.append(acc)
    thetas = [GRID[min(len(GRID) - 1, next(i for i, c in enumerate(cdf) if c >= (k + 0.5) / draws))] for k in range(draws)]
    coarse = [(-4.0 + 0.1 * i) for i in range(101)]
    on_scale = set(bank.scale) if bank.scale else {f for f in bank.a if f not in bank.provisional}
    fams = {blk: [f for f in bank.a if bank.block[f] == blk and f in on_scale] for blk in bank.weights}
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
                    v += bank.n(f) * (x * math.log(max(p, 1e-12)) + (1 - x) * math.log(max(1 - p, 1e-12)))
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


def next_family_blocks(bank: Bank, est: dict, used: dict, slowness: float = 1.0, max_per_family: int = 2, cost=None) -> str | None:
    """The family whose answer would shrink the capability's variance the most per expected second."""
    cost = cost or (lambda f: bank.seconds[f] * slowness)
    best, best_v = None, -1.0
    wsum = sum(bank.weights.values())
    for f in bank.a:
        if used.get(f, 0) >= max_per_family:
            continue
        fresh = 1.5 if not used.get(f) else 1.0   # coverage: a family not yet tried beats a repeat of the same value
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
        v = fresh * gain / cost(f)
        if v > best_v:
            best, best_v = f, v
    return best
