"""What changed out there since the last look, for the site's "What's new" and the NEW tab.

`llmbox watch` (a daily launchd job, ~/.llmbox/bin/watch-daily.sh) compares the world with ~/.llmbox/watch/state.json
and appends what is new to ~/.llmbox/watch/events.jsonl:

- new_model: a model the NEW-tab discovery (llmbox/candidates.py) lists for the first time;
- new_files: new GGUF files (a new quant, a fixed upload) in the Hugging Face repo of a model llmbox has a recipe for;
- repo_update: other commits to such a repo (card, chat template), with their titles;
- runtime: a new release of a runtime a recipe needs (the Bonsai fork);
- pr: a watched pull request was merged or closed (K2 Horizon support in mainline llama.cpp).

The first run only records the present (no flood of "new" things that were there before); a source that cannot be
reached is skipped and looked at again next time.
"""
from __future__ import annotations

import glob
import json
import os
import time
import tomllib
import urllib.request

from .hosts import HOME

DIR = os.path.join(HOME, "watch")
STATE = os.path.join(DIR, "state.json")
EVENTS = os.path.join(DIR, "events.jsonl")
HF = "https://huggingface.co/api/models"
GH = "https://api.github.com/repos"

# runtimes with rare, meaningful releases (mainline llama.cpp releases several times a day: not news)
RUNTIMES = [("PrismML-Eng/llama.cpp", "PrismML llama.cpp fork (Bonsai 2 PQ2_0 / PTQ1_0)")]
# pull requests whose merge changes what runs on stock llama.cpp
PRS = [("ggml-org/llama.cpp", 29535, "K2 Horizon (MoVA) support in llama.cpp")]


def _json(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={"User-Agent": "llmbox-watch", "Accept": "application/json"})
    tok = os.environ.get("GITHUB_TOKEN") if url.startswith(GH) else None
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _tracked_repos() -> dict:
    """{hf_repo: [recipe ids]} of every recipe on every host."""
    out: dict = {}
    for f in glob.glob(os.path.join(HOME, "recipes", "*", "*.toml")):
        try:
            r = tomllib.load(open(f, "rb"))
        except (OSError, ValueError):
            continue
        repo = (r.get("model") or {}).get("hf_repo")
        if repo:
            out.setdefault(repo, []).append(os.path.splitext(os.path.basename(f))[0])
    return out


def events(n: int | None = None) -> list[dict]:
    """The newest events first."""
    if not os.path.exists(EVENTS):
        return []
    rows = [json.loads(line) for line in open(EVENTS) if line.strip()]
    rows.sort(key=lambda e: e["at"], reverse=True)
    return rows[:n] if n else rows


def first_seen() -> dict:
    """{model key: first-seen date} of the models discovery has listed (keys as candidates.key_of)."""
    try:
        return (json.load(open(STATE)).get("models") or {})
    except (OSError, ValueError):
        return {}


def run(progress=print) -> list[dict]:
    from . import candidates
    os.makedirs(DIR, exist_ok=True)
    st = json.load(open(STATE)) if os.path.exists(STATE) else {}
    first = not st
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    new: list = []

    def event(kind: str, title: str, detail: str = "", url: str = "", **kw):
        new.append(dict({"at": now, "type": kind, "title": title, "detail": detail, "url": url}, **kw))

    # 1. new models: what the NEW tab would list, keyed by the model (not the quantizer's repo)
    models = st.setdefault("models", {})
    try:
        for c in candidates.load():
            lin = c.get("lineage") or {}
            key = candidates._key(lin.get("model") or c["repo"])
            if key in models:
                continue
            models[key] = (c.get("released") or now[:10]) if first else now[:10]   # the first look: seen when released
            if not first:
                sh = c.get("shape") or {}
                size = f'{(sh.get("total_params") or 0) / 1e9:.0f}B' + (f' · {(sh.get("active_params") or 0) / 1e9:.0f}B active' if sh.get("n_expert") else "")
                event("new_model", lin.get("model") or c["repo"], f'{lin.get("kind", "release")} · {size} · released {c.get("released")}',
                      f"https://huggingface.co/{c['repo']}", repo=c["repo"])
    except Exception as e:   # offline or rate-limited: try again next time
        progress(f"  models: skipped ({type(e).__name__}: {str(e)[:120]})")

    # 2. the repos of models we have recipes for: new files, other commits
    repos = st.setdefault("repos", {})
    for repo, rids in sorted(_tracked_repos().items()):
        try:
            d = _json(f"{HF}/{repo}")
        except Exception as e:
            progress(f"  {repo}: skipped ({type(e).__name__})")
            continue
        files = sorted(s["rfilename"] for s in d.get("siblings") or [] if s["rfilename"].endswith(".gguf"))
        old = repos.get(repo)
        repos[repo] = {"sha": d.get("sha"), "files": files, "modified": d.get("lastModified")}
        if not old or old.get("sha") == d.get("sha"):
            continue
        added = [f for f in files if f not in old.get("files", [])]
        if added:
            event("new_files", repo.split("/")[-1], "new: " + ", ".join(os.path.basename(f) for f in added[:6])
                  + (f" and {len(added) - 6} more" if len(added) > 6 else ""), f"https://huggingface.co/{repo}/tree/main", rids=rids)
        else:
            titles = []
            try:   # what changed, in the repo's own words: the commits since the one seen last time
                for c in _json(f"{HF}/{repo}/commits/main")[:20]:
                    if c.get("id") == old.get("sha"):
                        break
                    titles.append(c.get("title") or "")
            except Exception:
                pass
            event("repo_update", repo.split("/")[-1], "; ".join(titles[:3]) or "files or card changed",
                  f"https://huggingface.co/{repo}/commits/main", rids=rids)

    # 3. runtime releases
    rts = st.setdefault("runtimes", {})
    for gh_repo, what in RUNTIMES:
        try:
            rel = _json(f"{GH}/{gh_repo}/releases?per_page=1")[0]
        except Exception as e:
            progress(f"  {gh_repo}: skipped ({type(e).__name__})")
            continue
        tag = rel.get("tag_name")
        if rts.get(gh_repo) not in (None, tag):
            event("runtime", f"{what}: {tag}", (rel.get("name") or "")[:200], rel.get("html_url") or "")
        rts[gh_repo] = tag

    # 4. watched pull requests
    prs = st.setdefault("prs", {})
    for gh_repo, num, what in PRS:
        try:
            pr = _json(f"{GH}/{gh_repo}/pulls/{num}")
        except Exception as e:
            progress(f"  {gh_repo}#{num}: skipped ({type(e).__name__})")
            continue
        state = "merged" if pr.get("merged") else pr.get("state")
        if prs.get(f"{gh_repo}#{num}") not in (None, state) and state != "open":
            event("pr", f"{what}: {state}", pr.get("title") or "", pr.get("html_url") or "")
        prs[f"{gh_repo}#{num}"] = state

    st["checked"] = now
    json.dump(st, open(STATE + ".tmp", "w"), indent=1, sort_keys=True)
    os.replace(STATE + ".tmp", STATE)
    if new:
        with open(EVENTS, "a") as f:
            for e in new:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
    progress(("first look: recorded " if first else "") + f"{len(models)} models, {len(repos)} repos, {len(rts)} runtimes, "
             f"{len(prs)} pull requests; {len(new)} new event(s)")
    for e in new:
        progress(f"  {e['type']:12s} {e['title']} - {e['detail'][:120]}")
    return new
