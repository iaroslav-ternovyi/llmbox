"""Explanations: can someone who reads only the model's explanation get things right?

The model gets the documentation of a made-up system - how a CLI tool resolves its settings, how an API service bills,
who may do what in a file-sharing workspace - written the way real docs are: rules spread over sections, a changelog
that overrides the main text, filler, a support thread. It must explain the system to a colleague in at most N words.
Then a fixed small reader model (reader.py) gets only that explanation and a quiz of new cases the explaining model
never saw, and answers them. The quiz is graded by the rule engine below, so no judge rates anything: the score is how
many cases the reader got right. The systems are generated from a seed, so neither model can know them beforehand, and
a wrong, vague or incomplete explanation shows up as the reader's wrong answers. Past the word limit the text is cut.

This is teacher-student simulatability (Pruthi et al., TACL 2022; ALMANACS 2023) with a fixed student. Checks
(`llmbox validate`): the reference explanation written from the rules scores ~1, no explanation scores ~0.
"""
from __future__ import annotations

import math
import re

from .common import Item, rng, strip_think

BLOCK = "explain"
LIMIT = {1: 100, 2: 130, 3: 160, 4: 190, 5: 220}   # words the explanation may use; the docs are 3-4x longer
TASK = ("A colleague has to {goal} on their own from now on. They have never seen {thing} and will not read the "
        "documentation: they will only get your explanation. Write it so they get every case right, in at most {n} words "
        "(anything past {n} words is cut off). Plain text or markdown; no need to repeat things that don't affect the result.")


def words(text: str) -> list[str]:
    return re.findall(r"\S+", text or "")


def cut(text: str, n: int) -> str:
    """The first n words, keeping the line structure."""
    out, k = [], 0
    for line in (text or "").splitlines():
        ws = words(line)
        if k + len(ws) > n:
            out.append(" ".join(ws[: n - k]))
            break
        out.append(line)
        k += len(ws)
    return "\n".join(out).strip()


def _answers(text: str, n: int) -> dict[int, str]:
    from .knowledge import answers
    return answers(text, n)


def reader_prompt(topic: str, explanation: str, setup: str, questions: list[str], fmt: str) -> str:
    body = "\n\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1))
    return (f"A colleague wrote you this explanation of {topic}. You have never seen it before and have nothing else to go on.\n\n"
            f"--- explanation ---\n{explanation.strip() or '(empty)'}\n--- end of explanation ---\n\n"
            f"Use the explanation to answer the questions below. {fmt}\n\n" + (f"{setup}\n\n" if setup else "") + body +
            "\n\nWork through each case briefly, then finish with exactly:\nANSWERS\n" + "\n".join(f"{i}. <answer>" for i in range(1, len(questions) + 1)))


def _item(kind: str, level: int, seed: int, doc: str, goal: str, thing: str, topic: str, setup: str,
          quiz: list[tuple], fmt: str, oracle: str) -> Item:
    """quiz: (question, expected, mode[, choices]) with mode text / list / num / set; choices: every value a text answer
    could name."""
    n = LIMIT[level]
    prompt = doc.strip() + "\n\n---\n\n" + TASK.format(goal=goal, thing=thing, n=n)

    def check(text: str, _t=None) -> float:
        from .. import reader
        expl = cut(strip_think(text), n)
        got = _answers(reader.ask(reader_prompt(topic, expl, setup, [x[0] for x in quiz], fmt)), len(quiz))
        return sum(match(q[1], got.get(i), q[2], q[3] if len(q) > 3 else None) for i, q in enumerate(quiz, 1)) / len(quiz)
    return Item(f"{BLOCK}.{kind}.L{level}.{seed}", BLOCK, kind, [{"role": "user", "content": prompt}], check, max_tokens=16000,
                meta={"level": level, "deferred": "reader", "limit": n, "topic": topic, "setup": setup, "fmt": fmt,
                      "quiz": [list(x) for x in quiz], "expected": [x[1] for x in quiz], "oracle": oracle,
                      "doc_words": len(words(doc))})


def reader_prompt_of(it: Item, explanation: str) -> str:
    return reader_prompt(it.meta["topic"], cut(explanation, it.meta["limit"]), it.meta["setup"], [x[0] for x in it.meta["quiz"]], it.meta["fmt"])


def _toks(s: str) -> list[str]:
    s = re.sub(r"[`*\"'\[\]()]", " ", s.lower())
    return [t for t in re.split(r"[\s,;]+", s) if t and t not in ("and", "the", "only", "actions:", "is", "=")]


def match(expected: str, got: str | None, mode: str, choices: list[str] | None = None) -> float:
    if got is None:
        return 0.0
    g = got.strip()
    if mode == "num":
        m = re.search(r"-?\d[\d,]*(?:\.\d+)?", g.replace("€", " "))
        return float(m is not None and abs(float(m.group(0).replace(",", "")) - float(expected)) < 0.006)
    if mode == "set":
        want = set(_toks(expected)) - {"none"}
        have = set(_toks(g)) - {"none", "can", "they", "user"}
        return float(have == want)
    if mode == "list":
        return float(_toks(g) == _toks(expected) or (not _toks(expected) and re.search(r"empty|none|nothing", g, re.I) is not None))
    # text: exactly one of the possible values appears, and it is the right one ("6 (the flag)" is right; "6 or 3" is not)
    found = {c for c in (choices or [expected]) if re.search(rf"(?<![\w/.~-]){re.escape(c.lower())}(?![\w/.-])", g.lower())}
    return float(found == {expected.lower()})


FILLER = {
    "config": [
        "## Output\n\n`{t}` prints one line per finished task. `--json` switches to one JSON object per line with the task "
        "name, duration in milliseconds and exit code. Colours are disabled automatically when the output is not a terminal.",
        "## Caching\n\nTask results are cached by the hash of their inputs. `{t} clean` empties the cache; `{t} cache stats` "
        "shows its size. The cache is safe to share between machines over NFS, but not between different versions of `{t}`.",
        "## Exit codes\n\n0 means every task succeeded, 1 that a task failed, 2 a problem with the configuration file, and "
        "130 that the run was interrupted. With `--keep-going` the exit code is that of the first failed task.",
        "## Migrating from 1.x\n\nIn 1.x there was a single configuration file and flags always won over it. 1.x files are "
        "still read, with a deprecation warning; `{t} migrate` converts them. None of the 1.x rules apply to 2.x or later.",
        "## Windows\n\nOn Windows the user file lives in `%APPDATA%\\{t}\\config.toml` and paths in settings may use either "
        "slash. Long paths need `LongPathsEnabled` in the registry.",
        "## Writing plugins\n\nA plugin is an executable named `{t}-<name>` on the PATH. It receives the task graph as JSON "
        "on stdin and may print extra tasks to stdout. Plugins run in the order they are listed.",
        "## Troubleshooting\n\n`{t} config show` prints every setting with the value it ended up with, but it does not say "
        "where the value came from, and it is slow on large projects because it loads every plugin.",
    ],
    "billing": [
        "## Authentication\n\nEvery request carries an API key in the `Authorization: Bearer` header. Keys are per project; "
        "you can have up to 20. Revoked keys stop working within a minute.",
        "## SDKs\n\nOfficial SDKs exist for Python, TypeScript, Go and Java. They retry failed requests up to three times "
        "with exponential backoff; each retry is a separate request.",
        "## Invoices and payment\n\nInvoices are issued on the first working day of the next month and paid by card or SEPA "
        "direct debit. Invoices in another currency are converted at the ECB rate of the invoice date.",
        "## Refunds\n\nWe refund the plan fee of a month in which our uptime fell below 99.5%. Extra requests are never refunded.",
        "## Legacy pricing (before 2025)\n\nCustomers who signed up before 2025 used to pay per request with no plan fee; "
        "these accounts were all moved to the current plans in January 2025, so this pricing no longer applies to anyone.",
        "## Enterprise\n\nAbove 50 million requests a month, talk to sales about a custom contract with a dedicated region "
        "and an SLA. Custom contracts are not covered by this page.",
        "## Changing plans\n\nUpgrades take effect immediately; downgrades at the end of the month. A month is always billed "
        "on the plan you were on at its end.",
        "## Usage dashboard\n\nThe dashboard shows requests per day and per API key, updated every ten minutes. Usage alerts "
        "can email you at 50%, 80% and 100% of the included requests. The dashboard's numbers are estimates; the invoice counts.",
        "## Data retention\n\nRequest logs are kept for 30 days on every plan. Tracking results are cached for 24 hours; "
        "cached answers are served faster but are still requests.",
        "## Regions\n\nThe API is served from Frankfurt and Virginia. Requests go to the nearest region; the price is the "
        "same everywhere, and there is no charge for traffic between regions.",
    ],
    "access": [
        "## Activity log\n\nEvery read, edit and share is recorded in the activity log for 180 days. Workspace owners can "
        "export it as CSV. Guests do not appear in exports made before they joined.",
        "## Trash\n\nDeleted files stay in the trash for 30 days, and anyone who could delete them can restore them. Emptying "
        "the trash is permanent.",
        "## Two-factor sign-in\n\nOwners can require two-factor sign-in for everyone. People without it are signed out and "
        "asked to set it up at their next sign-in; their roles do not change.",
        "## Legacy permissions (before 2024)\n\nBefore 2024 every member could read every folder by default. That setting was "
        "removed for all workspaces in 2024; today nobody gets anything they were not given.",
        "## Moving files\n\nA file moved to another folder takes on the permissions of its new folder. Moving needs edit on "
        "both folders.",
        "## Transferring ownership\n\nThe owner can transfer the workspace to another member. The old owner becomes a manager "
        "of every top-level folder.",
        "## Notifications\n\nPeople get an email when someone shares a folder with them, mentions them in a comment or "
        "replies to their comment. Notifications can be muted per folder; muting does not change anyone's access.",
        "## Offline access\n\nThe desktop app keeps files people open recently available offline for 14 days. Offline "
        "copies follow the same permissions and are removed on the next sync after access is taken away.",
        "## Storage limits\n\nFiles up to 20 GB can be uploaded. Every workspace gets 2 TB; version history counts against "
        "it, the trash does not. Owners can buy more storage in blocks of 1 TB.",
        "## Single sign-on\n\nWorkspaces on the business plan can require sign-in through the company's identity provider. "
        "Groups can be synced from the identity provider once a day; synced groups cannot be edited by hand.",
    ],
}


def _filler(r, kind: str, level: int, **fmt) -> list[str]:
    return [x.format(**fmt) for x in r.sample(FILLER[kind], min(len(FILLER[kind]), 4 + level))]


def _join(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


# ---- 1. config: how a CLI tool decides the value of a setting --------------------------------------------------------

TOOLS = ["brisk", "tallow", "quern", "marl", "fennel", "sorrel", "ketch", "wren"]
DEFAULTS = {"jobs": "4", "region": "eu-west", "log_level": "info", "cache.dir": "~/.cache/{t}", "plugins": [], "color": "true"}
POOL = {"jobs": ["1", "2", "3", "6", "8", "12", "16"], "region": ["us-east", "ap-south", "eu-north", "sa-east", "us-west"],
        "log_level": ["debug", "warn", "error", "trace"], "cache.dir": ["/tmp/{t}", "/var/cache/{t}", "./.cache", "/data/{t}-cache"],
        "plugins": ["lint", "fmt", "docs", "audit", "bench", "cover", "deploy"], "color": ["true", "false"]}
NAMES = {"user": "the user file (`~/.config/{t}/config.toml`)", "project": "the project file (`./{t}.toml`)",
         "env": "environment variables", "flag": "command-line flags", "system": "the system file (`/etc/{t}/config.toml`)"}
SHORT = {"user": "the user file", "project": "the project file", "env": "environment variables", "flag": "command-line flags"}
FILES = {"system": "/etc/{t}/config.toml", "user": "~/.config/{t}/config.toml", "project": "./{t}.toml", "base": "./base.toml"}


def _cfg_rules(r, level: int) -> dict:
    srcs = ["user", "env", "flag"] if level == 1 else ["user", "project", "env", "flag"]
    order = srcs[:]
    while order == srcs:   # never the conventional order: the reader has to be told
        r.shuffle(order)
    return {"order": order, "conv": srcs, "lists": level >= 2, "reset": level >= 3, "nested": level >= 3, "locked": level >= 4,
            "profiles": level >= 4, "extends": level >= 5, "bools": level >= 5, "sep": ";" if level >= 5 else ","}


def _chain(ru: dict, has_base: bool) -> list[str]:
    ch = (["system"] if ru["locked"] else []) + ru["order"]
    if has_base:
        ch.insert(ch.index("project"), "base")   # an extended file sits right below the project file
    return ch


def _env_name(t: str, key: str) -> str:
    return f"{t.upper()}_" + key.upper().replace(".", "__")


def _flag_name(key: str) -> str:
    return "--" + key.replace(".", "-").replace("_", "-")


def _parse_bool(v: str) -> str | None:
    v = v.lower()
    return "true" if v in ("1", "true", "yes", "on") else "false" if v in ("0", "false", "no", "off") else None


def _cfg_value(ru, t, case, src, key, profile):
    if src in ("system", "user", "project", "base"):
        f = case.get(src) or {}
        prof = (f.get("profiles") or {}).get(profile) or {}
        if ru["profiles"] and profile and key in prof:
            return prof[key]
        return (f.get("values") or {}).get(key)
    if src == "env":
        v = (case.get("env") or {}).get(_env_name(t, key))
        if v is None:
            return None
        if key == "plugins":
            return [x for x in v.split(ru["sep"]) if x]
        if key == "color":
            return _parse_bool(v)
        return v
    if src == "flag":
        fl = case.get("flag") or {}
        if key == "color":
            if "--no-color" in fl:
                return "false"
            if "--color" in fl:
                return "true"
            return None
        v = fl.get(_flag_name(key))
        if v is None:
            return None
        return [x for x in v.split(",") if x] if key == "plugins" else v
    return None


def _cfg_profile(ru, t, case) -> str | None:
    env = (case.get("env") or {}).get(f"{t.upper()}_PROFILE")
    flag = (case.get("flag") or {}).get("--profile")
    if env and flag:
        return flag if ru["order"].index("flag") > ru["order"].index("env") else env
    return env or flag


def cfg_resolve(ru: dict, t: str, case: dict, key: str):
    profile = _cfg_profile(ru, t, case) if ru["profiles"] else None
    locked = ((case.get("system") or {}).get("locked") or {}).get(key)
    if ru["locked"] and locked is not None:
        return locked
    vals = [v for src in _chain(ru, bool(case.get("base"))) if (v := _cfg_value(ru, t, case, src, key, profile)) is not None]
    if key == "plugins":
        out: list[str] = []
        for v in vals:
            items = list(v)
            if ru["reset"] and items[:1] == ["!"]:
                out, items = [], items[1:]
            for x in items:
                if x not in out:
                    out.append(x)
        return out
    return vals[-1] if vals else DEFAULTS[key].format(t=t)


def _naive(ru, t, case, key):
    """What 'the usual order, the last source replaces lists' gives: a quiz case where this is also right says little."""
    nr = dict(ru, order=ru["conv"], lists=False, reset=False, locked=False, profiles=False)
    v = cfg_resolve(nr, t, case, key)
    if key == "plugins":   # without merging: the strongest list replaces the rest
        vals = [x for src in _chain(nr, bool(case.get("base"))) if (x := _cfg_value(nr, t, case, src, key, None)) is not None]
        v = [x for x in (vals[-1] if vals else []) if x != "!"]
    return v


def _fmt_val(key, v, t):
    if key == "plugins":
        return "[" + ", ".join(f'"{x}"' for x in v) + "]"
    if key in ("jobs",) or key == "color":
        return v
    return f'"{v.format(t=t)}"'


def _render_case(ru, t, case) -> str:
    lines = []
    for src in ("system", "user", "base", "project"):
        f = case.get(src)
        if not f:
            continue
        body = []
        if src == "project" and case.get("base"):
            body.append('extends = "base.toml"')
        body += [f"{k} = {_fmt_val(k, v, t)}" for k, v in (f.get("values") or {}).items()]
        for p, kv in (f.get("profiles") or {}).items():
            body.append(f"[profile.{p}]")
            body += [f"{k} = {_fmt_val(k, v, t)}" for k, v in kv.items()]
        if f.get("locked"):
            body.append("[locked]")
            body += [f"{k} = {_fmt_val(k, v, t)}" for k, v in f["locked"].items()]
        lines.append(f"- `{FILES[src].format(t=t)}`:\n" + "\n".join("      " + b for b in body))
    env = case.get("env") or {}
    lines.append("- environment: " + (" ".join(f"{k}={v}" for k, v in env.items()) if env else "(nothing relevant)"))
    fl = case.get("flag") or {}
    parts = [k if v is True else f"{k}={v}" for k, v in fl.items()]
    lines.append(f"- command: `{t} build" + (" " + " ".join(parts) if parts else "") + "`")
    return "\n".join(lines)


def _cfg_case(r, ru, t, level):
    keys = ["jobs", "region", "log_level"] + (["plugins"] if ru["lists"] else []) + (["cache.dir"] if ru["nested"] else []) + \
           (["color"] if ru["bools"] else [])
    key = r.choice(keys)
    srcs = list(ru["order"])
    setters = r.sample(srcs, r.randint(2, min(4, len(srcs))))
    case: dict = {}
    pool = [x.format(t=t) for x in POOL[key]]

    def val():
        if key == "plugins":
            v = r.sample(pool, r.randint(1, 2))
            if ru["reset"] and r.random() < 0.3:
                v = ["!"] + v
            return v
        return r.choice(pool)
    for s in setters:
        v = val()
        if s in ("user", "project"):
            case.setdefault(s, {}).setdefault("values", {})[key] = v
        elif s == "env":
            ev = ru["sep"].join(v) if key == "plugins" else ("0" if v == "false" else "1") if key == "color" and r.random() < 0.5 else v
            case.setdefault("env", {})[_env_name(t, key)] = ev
        else:
            if key == "color":
                case.setdefault("flag", {})["--no-color" if v == "false" else "--color"] = True
            else:
                case.setdefault("flag", {})[_flag_name(key)] = ",".join(v) if key == "plugins" else v
    other = r.choice([k for k in keys if k != key])   # noise: another setting somewhere
    case.setdefault(r.choice(["user", "project"] if "project" in srcs else ["user"]), {}).setdefault("values", {})[other] = \
        (r.sample(POOL["plugins"], 1) if other == "plugins" else r.choice(POOL[other]).format(t=t))
    if ru["profiles"] and r.random() < 0.45 and key != "color":
        p = r.choice(["ci", "prod", "local"])
        f = r.choice([s for s in ("user", "project") if s in srcs])
        case.setdefault(f, {}).setdefault("profiles", {})[p] = {key: val()}
        how = r.choice(["env", "flag", "both"])
        if how in ("env", "both"):
            case.setdefault("env", {})[f"{t.upper()}_PROFILE"] = p if how == "env" or r.random() < 0.5 else "staging"
        if how in ("flag", "both"):
            case.setdefault("flag", {})["--profile"] = p if how == "flag" or case["env"][f"{t.upper()}_PROFILE"] != p else "staging"
    if ru["locked"] and r.random() < 0.3 and key not in ("plugins", "color"):
        case.setdefault("system", {}).setdefault("locked", {})[key] = val()
    elif ru["locked"] and r.random() < 0.4:
        case.setdefault("system", {}).setdefault("values", {})[key] = val()
    if ru["extends"] and "project" in case and r.random() < 0.5:
        case["base"] = {"values": {key: val()}}
    if ru["nested"] and key == "cache.dir" and r.random() < 0.5:   # the wrong spelling is ignored
        case.setdefault("env", {})[f"{t.upper()}_CACHE_DIR"] = r.choice(pool)
    return key, case


def _cfg_doc(r, ru, t, level) -> str:
    T = t.upper()
    order_hi = list(reversed(ru["order"]))   # strongest first
    doc_order = order_hi[:]
    amend = None
    if level >= 3:   # the main text still describes the old order of one adjacent pair; the changelog has the change
        i = r.randrange(len(doc_order) - 1)
        doc_order[i], doc_order[i + 1] = doc_order[i + 1], doc_order[i]
        amend = (order_hi[i], order_hi[i + 1])
    ver = f"{r.randint(2, 4)}.{r.randint(0, 9)}"
    p = [f"# {t}\n\n`{t}` runs build, test and release tasks defined in `{t}.toml`. It is a single static binary with no "
         f"runtime dependencies and works on Linux, macOS and Windows.",
         f"## Installation\n\nDownload the binary for your platform from the releases page, or `brew install {t}`. Shell "
         f"completions: `{t} completions bash > /etc/bash_completion.d/{t}`. Nightly builds are unsupported.",
         "## Configuration\n\nEvery setting can come from several places: " + ", ".join(NAMES[s].format(t=t) for s in ru["order"])
         + (", and at the bottom the system file (`/etc/" + t + "/config.toml`)" if ru["locked"] else "")
         + ". If nothing sets a setting, its default applies. When the same setting is set in several places, the "
         + "strongest one wins; from strongest to weakest: " + ", ".join(SHORT[s] for s in doc_order)
         + (", then the system file" if ru["locked"] else "") + ".",
         "## Settings\n\n| setting | default | meaning |\n|---|---|---|\n"
         + "\n".join(f"| `{k}` | `{(', '.join(v) if isinstance(v, list) else v).format(t=t) or '(empty list)'}` | {m} |"
                     for k, v, m in [("jobs", DEFAULTS["jobs"], "parallel tasks"), ("region", DEFAULTS["region"], "artifact region"),
                                     ("log_level", DEFAULTS["log_level"], "log verbosity")]
                     + ([("plugins", [], "plugins to load (a list)")] if ru["lists"] else [])
                     + ([("cache.dir", DEFAULTS["cache.dir"], "cache location (in the `[cache]` table: `dir`)")] if ru["nested"] else [])
                     + ([("color", "true", "coloured output")] if ru["bools"] else [])),
         f"## Environment variables and flags\n\nA setting `name` is read from the variable `{T}_NAME` (upper case) and from "
         f"the flag `--name=value`, where an underscore in the name is written as a dash (`log_level` is `--log-level`)" + (f"; in a nested setting like `cache.dir` the dot becomes a double underscore in the variable "
                                       f"(`{T}_CACHE__DIR`) and a dash in the flag (`--cache-dir`). Variables spelled any other way "
                                       f"are ignored" if ru["nested"] else "") + "."
         + (f" Booleans: `--color` / `--no-color`; in a variable, `1/true/yes/on` and `0/false/no/off`." if ru["bools"] else "")]
    if ru["lists"]:
        p.append("## Lists\n\nList settings are not replaced by a stronger source. All the places that set the list are "
                 "combined: the weakest source's items first, then the next stronger one's, and so on; an item that is "
                 "already in the list is not added again. In a variable or a flag, list items are separated by commas."
                 + (" A list that starts with the item `\"!\"` throws away everything the weaker sources contributed and "
                    "starts over with its own items." if ru["reset"] else ""))
    if ru["profiles"]:
        p.append(f"## Profiles\n\nA file can have `[profile.<name>]` tables. When a profile is active, a setting in that "
                 f"file's profile table replaces the same setting in the rest of that file (it does not change which source "
                 f"is stronger). The profile is chosen with `--profile=<name>` or `{T}_PROFILE`; if both are given, the "
                 f"stronger of the two sources wins, as for any setting.")
    if ru["locked"]:
        p.append("## Locked settings\n\nAdministrators can put settings in a `[locked]` table in the system file. A locked "
                 "setting always has that value; nothing can override it.")
    if ru["extends"]:
        p.append('## Sharing settings\n\nA project file may contain `extends = "<path>"`. The named file counts as a source '
                 "of its own, just below the project file that extends it (so the project file beats it, and it beats "
                 "everything the project file beats).")
    p += _filler(r, "config", level, t=t)
    p.append(f"## Telemetry\n\n`{t}` sends no telemetry. The `{T}_NO_UPDATE_CHECK=1` variable disables the weekly "
             f"version check, which only fetches a JSON file with the latest version number.")
    log = [f"- {ver}.1: faster task graph; `{t} graph` prints it as DOT."]
    if amend:
        log.insert(0, f"- {ver}.0: {SHORT[amend[0]].capitalize()} now take precedence over {SHORT[amend[1]]}; before "
                      f"{ver}.0 it was the other way round. (The configuration section above has not been updated yet.)")
    if ru["sep"] == ";":
        log.insert(0, f"- {int(ver[0]) + 1}.0: list items in environment variables are now separated by `;` instead of "
                      f"`,` (commas can appear in paths). Flags still use commas.")
    p.append("## Changelog\n\n" + "\n".join(log))
    if level >= 5:
        a, b = order_hi[0], order_hi[-1]
        p.append(f"## From the issue tracker\n\n> **#{r.randint(300, 900)}** I set a value in {SHORT[b]} and in "
                 f"{SHORT[a]} and {SHORT[a]} won. Surely {SHORT[b]} should win?\n\n> **maintainer:** Working as "
                 f"intended: {SHORT[a]} are the strongest source of all (apart from locked settings).")
    return "\n\n".join(p)


def _cfg_oracle(ru, t) -> str:
    T = t.upper()
    s = [f"Strongest to weakest: " + ", ".join(SHORT[x] for x in reversed(ru["order"])) + (", system file" if ru["locked"] else "")
         + ", then the default. The strongest source that sets a setting wins.",
         f"Defaults: jobs 4, region eu-west, log_level info" + (f", cache.dir ~/.cache/{t}" if ru["nested"] else "")
         + (", plugins empty" if ru["lists"] else "") + (", color true" if ru["bools"] else "") + ".",
         f"Variables: {T}_ plus the name in capitals" + (", a dot becomes __ (cache.dir is " + T + "_CACHE__DIR); other spellings are ignored" if ru["nested"] else "")
         + f". Flags: --name=value, _ becomes - (--log-level)" + (", and so does a dot (--cache-dir)" if ru["nested"] else "") + "."]
    if ru["lists"]:
        s.append("Lists (plugins) are combined, not replaced: weakest source's items first, then stronger ones; skip items "
                 "already present." + (" A list starting with \"!\" drops everything from weaker sources." if ru["reset"] else "")
                 + (" In variables items are separated by ;, in flags by commas." if ru["sep"] == ";" else " Items are comma-separated in variables and flags."))
    if ru["profiles"]:
        s.append(f"Profiles: --profile or {T}_PROFILE picks one (the stronger source wins if both are set). In each file, "
                 "values in [profile.<active>] replace that file's own values; precedence between sources is unchanged.")
    if ru["locked"]:
        s.append("A [locked] value in the system file always wins.")
    if ru["extends"]:
        s.append("extends = \"x.toml\" in the project file makes x.toml a source just below the project file.")
    if ru["bools"]:
        s.append(f"color: --color / --no-color; in the variable 1/true/yes/on or 0/false/no/off.")
    return "\n".join(s)


def config(seed: int, level: int = 3) -> Item:
    r = rng(BLOCK, f"config{level}", seed)
    t = r.choice(TOOLS)
    ru = _cfg_rules(r, level)
    quiz = []
    tries = 0
    while len(quiz) < 5 and tries < 400:
        tries += 1
        key, case = _cfg_case(r, ru, t, level)
        ans = cfg_resolve(ru, t, case, key)
        if _naive(ru, t, case, key) == ans and r.random() < 0.8:
            continue   # mostly cases where the usual assumptions give the wrong value
        q = f"Case:\n{_render_case(ru, t, case)}\n\nWhat is the effective value of `{key}`?"
        universe = sorted({x.format(t=t) for x in POOL[key]} | {DEFAULTS[key].format(t=t)}) if key != "plugins" else None
        quiz.append((q, ", ".join(ans) if key == "plugins" else ans, "list" if key == "plugins" else "text", universe))
    return _item("config", level, seed, _cfg_doc(r, ru, t, level), "work out which value each setting of `" + t + "` ends up with",
                 f"`{t}`", f"how the `{t}` command-line tool decides the value of each setting", "", quiz,
                 "Give each effective value exactly (a list as its items in order, comma-separated, or `empty`).", _cfg_oracle(ru, t))


# ---- 2. billing: what a month of API use costs -----------------------------------------------------------------------

SERVICES = ["Parcelgate", "Ledgerly", "Mapforge", "Pingwell", "Snapmeter", "Routewise"]


def _bill_rules(r, level: int) -> dict:
    plans = {}
    for name, fee, inc in [("Starter", r.choice([15, 19, 25]), r.choice([50, 100])), ("Team", r.choice([49, 59, 79]), r.choice([250, 300, 400])),
                           ("Business", r.choice([199, 249]), r.choice([1000, 1500]))][: 2 if level < 5 else 3]:
        plans[name] = {"fee": fee, "inc": inc * 1000, "rate": r.choice([0.40, 0.50, 0.60]) if name == "Starter" else r.choice([0.25, 0.30, 0.35])}
    ru = {"plans": plans, "errors_free": level >= 2, "tiers": None, "annual": None, "nonprofit": None, "volume": None, "change": None}
    if level >= 3:   # graduated overage: the first N thousand over the quota at the plan rate, the rest cheaper
        ru["tiers"] = {"first": r.choice([100, 200, 500]), "later": r.choice([0.15, 0.20])}
    if level >= 4:
        ru["annual"] = r.choice([15, 20, 25])
        ru["change"] = {"plan": "Team", "month": r.choice(["March", "April", "May"]), "inc": plans["Team"]["inc"] + r.choice([100, 150, 200]) * 1000}
    if level >= 5:
        ru["volume"] = "Business"
        ru["nonprofit"] = r.choice([30, 40])
    return ru


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def bill(ru: dict, plan: str, month: str, requests: int, errors: int, annual: bool, nonprofit: bool) -> float:
    p = ru["plans"][plan]
    inc = p["inc"]
    if ru["change"] and plan == ru["change"]["plan"] and MONTHS.index(month) >= MONTHS.index(ru["change"]["month"]):
        inc = ru["change"]["inc"]
    billable = requests - (errors if ru["errors_free"] else 0)
    units = math.ceil(max(0, billable - inc) / 1000)
    if ru["tiers"]:
        first, later = ru["tiers"]["first"], ru["tiers"]["later"]
        if ru["volume"] == plan:   # volume pricing: every unit at the rate of the tier reached
            over = units * (later if units > first else p["rate"])
        else:
            over = min(units, first) * p["rate"] + max(0, units - first) * later
    else:
        over = units * p["rate"]
    fee = p["fee"]
    opts = [fee + over]
    if annual and ru["annual"]:
        opts = [fee * (1 - ru["annual"] / 100) + over]
    if nonprofit and ru["nonprofit"]:
        opts.append((fee + over) * (1 - ru["nonprofit"] / 100))   # never both: the larger saving applies
    return round(min(opts) + 1e-9, 2)


def _eur(x: float) -> str:
    return f"€{x:,.2f}".replace(".00", "") if float(x).is_integer() else f"€{x:,.2f}"


def _bill_doc(r, ru, svc, level) -> str:
    pl = ru["plans"]
    p = [f"# {svc} pricing\n\n{svc} is a REST API for parcel tracking and address validation. You pay a monthly plan fee "
         f"that includes a number of requests, plus a charge for requests above it. Prices are in euros and exclude VAT.",
         "## Plans\n\n| plan | monthly fee | included requests | extra requests |\n|---|---|---|---|\n"
         + "\n".join(f"| {n} | {_eur(v['fee'])} | {v['inc']:,} | {_eur(v['rate'])} per 1,000 |" for n, v in pl.items()),
         "## How extra requests are counted\n\nRequests above the included amount are billed per started block of 1,000: "
         "1,001 extra requests are billed as 2,000. The bill is rounded to the cent." + (" Requests that fail with a server error (HTTP 5xx) do not count at all, "
                                                        "neither against the included amount nor as extra." if ru["errors_free"] else "")]
    if ru["tiers"]:
        t = ru["tiers"]
        p.append(f"## Volume discount\n\nThe first {t['first']:,} blocks of 1,000 extra requests in a month cost the plan's "
                 f"rate; every block after that costs {_eur(t['later'])}."
                 + (f" Exception: the {ru['volume']} plan uses volume pricing instead - if a month has more than {t['first']:,} "
                    f"extra blocks, all of them cost {_eur(t['later'])}, including the first {t['first']:,}." if ru["volume"] else ""))
    if ru["annual"]:
        p.append(f"## Annual billing\n\nPaying yearly saves {ru['annual']}% on the plan fee. Extra requests are not discounted.")
    if ru["nonprofit"]:
        p.append(f"## Non-profits\n\nRegistered non-profits get {ru['nonprofit']}% off the whole bill (fee and extra requests). "
                 "It cannot be combined with the annual discount: a non-profit on annual billing gets whichever of the two "
                 "saves more that month.")
    p += _filler(r, "billing", level)
    p.append(f"## Rate limits\n\nAll plans allow 50 requests per second; bursts up to 200 are queued. Rate-limited requests "
             f"(HTTP 429) are not billed. The sandbox environment is free and does not count.")
    log = [f"- Webhooks are now included in every plan at no extra cost."]
    if ru["change"]:
        c = ru["change"]
        log.insert(0, f"- From {c['month']} 2026 the {c['plan']} plan includes {c['inc']:,} requests a month (was "
                      f"{ru['plans'][c['plan']]['inc']:,}). The table above still shows the old number.")
    p.append("## What's new\n\n" + "\n".join(log))
    if level >= 5:
        p.append("## Community forum\n\n> I thought errors were free, but my invoice charged the 5xx requests as extra. Anyone?\n\n"
                 "> **Support:** They are not billed; the invoice you saw included 4xx client errors, which are billed like "
                 "any other request. Only 5xx server errors are free.")
    return "\n\n".join(p)


def _bill_oracle(ru) -> str:
    s = ["Plans (fee, included requests, price per 1,000 extra): " + "; ".join(f"{n} {_eur(v['fee'])}, {v['inc']:,}, {_eur(v['rate'])}" for n, v in ru["plans"].items()) + "."]
    if ru["change"]:
        c = ru["change"]
        s.append(f"From {c['month']} 2026 on, {c['plan']} includes {c['inc']:,} instead.")
    s.append(("Take the month's requests minus 5xx errors (4xx still count). " if ru["errors_free"] else "")
             + "Extra = requests above the included amount, billed per started 1,000 (round up).")
    if ru["tiers"]:
        t = ru["tiers"]
        s.append(f"The first {t['first']} extra blocks cost the plan rate, blocks after that {_eur(t['later'])} each."
                 + (f" {ru['volume']} instead: if more than {t['first']} blocks, ALL blocks cost {_eur(t['later'])}." if ru["volume"] else ""))
    s.append("Bill = fee + extra.")
    if ru["annual"]:
        s.append(f"Annual billing: fee minus {ru['annual']}%, extra not discounted.")
    if ru["nonprofit"]:
        s.append(f"Non-profit: {ru['nonprofit']}% off the whole bill; if also annual, use whichever discount gives the lower bill.")
    s.append("Round the total to cents.")
    return "\n".join(s)


def billing(seed: int, level: int = 3) -> Item:
    r = rng(BLOCK, f"billing{level}", seed)
    svc = r.choice(SERVICES)
    ru = _bill_rules(r, level)
    quiz = []
    for _ in range(4):
        plan = r.choice(list(ru["plans"]))
        month = r.choice(MONTHS[1:8])
        inc = ru["plans"][plan]["inc"]
        req = r.randrange(int(inc * 0.9), int(inc * 3.5), 250) + r.choice([0, 17, 480, 999])
        err = r.randrange(0, max(1, req // 25), 10) if ru["errors_free"] else 0
        annual = bool(ru["annual"]) and r.random() < 0.5
        npf = bool(ru["nonprofit"]) and r.random() < 0.5
        total = bill(ru, plan, month, req, err, annual, npf)
        who = ("a registered non-profit " if npf else "a customer ") + f"on the {plan} plan, " + ("billed yearly" if annual else "billed monthly")
        q = (f"{who[0].upper()}{who[1:]}, made {req:,} requests in {month} 2026" + (f", {err:,} of which failed with HTTP 5xx errors" if err else "")
             + ". What is that month's bill in euros?")
        quiz.append((q, f"{total:.2f}", "num"))
    return _item("billing", level, seed, _bill_doc(r, ru, svc, level), f"work out what a month of {svc} costs a customer",
                 svc, f"how {svc} bills its customers each month", "", quiz, "Give each bill in euros with cents.", _bill_oracle(ru))


# ---- 3. access: who may do what in a shared workspace ----------------------------------------------------------------

PRODUCTS = ["Cabinet", "Driftbox", "Foldr", "Stackroom", "Paperlane"]
ACTIONS = ["read", "comment", "edit", "share", "delete"]
ROLES = {"viewer": {"read"}, "commenter": {"read", "comment"}, "editor": {"read", "comment", "edit"},
         "manager": {"read", "comment", "edit", "share"}}
PEOPLE = ["ana", "ben", "chen", "dara", "eli", "femi", "gus", "hana", "ivo", "jun"]
TREE = ["/Projects", "/Projects/Apollo", "/Projects/Apollo/Notes", "/Projects/Hermes", "/HR", "/HR/Contracts", "/Shared", "/Shared/Old"]


def _acc_rules(r, level: int) -> dict:
    roles = {k: set(v) for k, v in ROLES.items()}
    tweak = None
    if level >= 2:
        tweak = r.choice(["editor_share", "manager_delete", None])
        if tweak == "editor_share":
            roles["editor"].add("share")
        elif tweak == "manager_delete":
            roles["manager"].add("delete")
    return {"roles": roles, "tweak": tweak, "groups": level >= 2, "private": level >= 3, "deny": level >= 4, "guests": level >= 4,
            "link": level >= 5, "archived": level >= 5}


def _ancestors(path: str) -> list[str]:
    parts = path.strip("/").split("/")
    return ["/" + "/".join(parts[:i]) for i in range(1, len(parts) + 1)]


def acc_actions(ru, ws, user, path) -> set[str]:
    if user == ws["owner"]:
        return set(ACTIONS)
    chain = _ancestors(path)
    if ru["private"]:
        priv = [f for f in chain if f in ws["private"]]
        if priv:
            chain = chain[chain.index(priv[-1]):]   # a private folder ignores everything granted above it
    who = {user} | ({g for g, ms in ws["groups"].items() if user in ms} if ru["groups"] else set())
    acts: set[str] = set()
    for p, role, f in ws["grants"]:
        if p in who and f in chain:
            acts |= ru["roles"][role]
    if ru["link"] and any(f in ws["link"] for f in chain):
        acts.add("read")
    if ru["deny"]:
        for p, a, f in ws["denies"]:
            if p in who and f in chain:
                acts.discard(a)
    if ru["guests"] and user in ws["guests"]:
        acts -= {"share", "delete"}
    if ru["archived"] and any(f in ws["archived"] for f in _ancestors(path)):
        acts &= {"read"}
    return acts


def _acc_workspace(r, ru) -> dict:
    people = r.sample(PEOPLE, 7)
    ws = {"owner": people[0], "groups": {}, "grants": [], "denies": [], "guests": set(), "private": set(), "link": set(), "archived": set()}
    if ru["groups"]:
        ws["groups"] = {"design": set(r.sample(people[1:], 3)), "legal": set(r.sample(people[1:], 2))}
    principals = people[1:] + list(ws["groups"])
    for _ in range(6):
        ws["grants"].append((r.choice(principals), r.choice(list(ROLES)), r.choice(TREE)))
    ws["grants"].append((r.choice(people[1:]), r.choice(["editor", "manager"]), "/Projects"))
    if ru["private"]:
        ws["private"] = {r.choice(["/Projects/Apollo/Notes", "/HR/Contracts"])}
        ws["grants"].append((r.choice(principals), r.choice(["viewer", "editor"]), next(iter(ws["private"]))))
    if ru["deny"]:
        for _ in range(2):
            ws["denies"].append((r.choice(principals), r.choice(["edit", "share", "delete", "comment"]), r.choice(TREE)))
    if ru["guests"]:
        ws["guests"] = set(r.sample(people[1:], 2))
    if ru["link"]:
        ws["link"] = {r.choice(["/Shared", "/Projects/Hermes"])}
    if ru["archived"]:
        ws["archived"] = {"/Shared/Old"}
    return ws


def _acc_setup(ru, ws, prod) -> str:
    s = [f"The {prod} workspace belongs to {ws['owner']} (the workspace owner). Folders: " + ", ".join(
        f + (" (private)" if f in ws["private"] else "") + (" (archived)" if f in ws["archived"] else "")
        + (" (link sharing on)" if f in ws["link"] else "") for f in TREE) + "."]
    if ws["groups"]:
        s.append("Groups: " + "; ".join(f"{g} = {', '.join(sorted(m))}" for g, m in ws["groups"].items()) + ".")
    if ws["guests"]:
        s.append("Guests (people from outside the company): " + ", ".join(sorted(ws["guests"])) + ".")
    s.append("Grants:\n" + "\n".join(f"- {p}: {role} on {f}" for p, role, f in ws["grants"]))
    if ws["denies"]:
        s.append("Deny entries:\n" + "\n".join(f"- {p}: deny {a} on {f}" for p, a, f in ws["denies"]))
    return "Workspace:\n" + "\n".join(s)


def _acc_doc(r, ru, prod, level) -> str:
    roles = ru["roles"]
    p = [f"# {prod} help center: sharing and permissions\n\n{prod} keeps a team's files in folders inside a workspace. This "
         f"article explains who can do what. The five actions are read, comment, edit, share (invite others or change "
         f"their access) and delete.",
         "## Roles\n\nYou can give a person" + (" or a group" if ru["groups"] else "") + " one of four roles on a folder: "
         + "; ".join(f"**{k}** can {_join(sorted(v, key=ACTIONS.index))}" for k, v in ROLES.items())
         + ". The workspace owner can do everything everywhere, and nothing below applies to the owner.",
         "## Inheritance\n\nA role on a folder applies to every file and subfolder inside it. If you have several roles "
         "that reach a file (on the folder itself and on folders above it" + (", or through your groups" if ru["groups"] else "")
         + "), you can do everything any of them allows.",
         "## Storage\n\nEach workspace has 2 TB. Version history keeps 90 days of changes; restoring an old version counts "
         "as an edit."]
    if ru["private"]:
        p.append("## Private folders\n\nA folder marked private does not inherit anything from the folders above it - no "
                 "roles, no link sharing, no deny entries: only what is set on the private folder itself (or on folders "
                 "inside it) counts there.")
    if ru["deny"]:
        p.append("## Deny entries\n\nAn admin can deny a single action to a person or group on a folder. A deny applies "
                 "to everything inside that folder the way roles do, and it beats any role.")
    if ru["guests"]:
        p.append("## Guests\n\nGuests are people from outside the company.")
    if ru["link"]:
        p.append("## Link sharing\n\nWith link sharing on, anyone with the link can read the folder's contents (it gives read, "
                 "nothing else). Like roles, it does not reach into private folders below it.")
    if ru["archived"]:
        p.append("## Archiving\n\nArchived folders are frozen: everything in them is read-only for everyone except the "
                 "workspace owner, whatever their roles.")
    p += _filler(r, "access", level)
    log = ["- Comments now support @mentions."]
    if ru["tweak"] == "editor_share":
        log.insert(0, "- Editors can now also share (invite others and change their access).")
    elif ru["tweak"] == "manager_delete":
        log.insert(0, "- Managers can now also delete files and folders.")
    if ru["guests"]:
        log.insert(0, "- Security update: guests can no longer share or delete anything, whatever their role.")
    p.append("## Recent changes\n\n" + "\n".join(log))
    if level >= 5:
        p.append("## Community\n\n> Does a deny on a folder stop me from reading the files inside?\n\n> **Staff:** Only if "
                 "the deny is for `read`. A deny removes just the one action it names.")
    return "\n\n".join(p)


def _acc_oracle(ru) -> str:
    s = ["Workspace owner: everything, always; nothing else applies to them.",
         "Roles: " + "; ".join(f"{k} = {', '.join(sorted(v, key=ACTIONS.index))}" for k, v in ru["roles"].items()) + ".",
         "A role on a folder covers everything inside it. Add up everything any role that reaches the file allows"
         + (" (your own roles and your groups' roles)" if ru["groups"] else "") + "."]
    if ru["private"]:
        s.append("Private folder: nothing set on folders above it (roles, link sharing, denies) counts inside it; only what is set on it or below.")
    if ru["link"]:
        s.append("Link sharing on a folder: everyone gets read inside it (not inside a private subfolder).")
    if ru["deny"]:
        s.append("Deny entries (for you or your group, on the folder or above) remove that one action, whatever the roles.")
    if ru["guests"]:
        s.append("Guests never get share or delete.")
    if ru["archived"]:
        s.append("Inside an archived folder everyone except the owner can only read (if they could read before).")
    s.append("Order: collect roles (and link read), remove denied actions, then the guest limit, then archiving.")
    return "\n".join(s)


def access(seed: int, level: int = 3) -> Item:
    r = rng(BLOCK, f"access{level}", seed)
    prod = r.choice(PRODUCTS)
    ru = _acc_rules(r, level)
    ws = _acc_workspace(r, ru)
    files = {f: f"{f}/{r.choice(['plan', 'notes', 'budget', 'draft', 'spec', 'minutes'])}.doc" for f in TREE}
    naive = dict(ru, roles={k: set(v) for k, v in ROLES.items()}, private=False, deny=False, guests=False, archived=False)
    people = sorted({p for p, _r, _f in ws["grants"] if p in PEOPLE} | set(ws["guests"]) | {m for ms in ws["groups"].values() for m in ms})
    cand = [(u, files[f]) for u in people for f in TREE]
    r.shuffle(cand)
    good = [(u, fp) for u, fp in cand if acc_actions(ru, ws, u, fp.rsplit("/", 1)[0]) != acc_actions(naive, ws, u, fp.rsplit("/", 1)[0])]
    plain = [(u, fp) for u, fp in cand if (u, fp) not in good and acc_actions(ru, ws, u, fp.rsplit("/", 1)[0])]
    picks = good[:4] + plain[:1] if len(good) >= 4 else (good + plain)[:5]
    quiz = []
    for u, fp in picks:
        a = acc_actions(ru, ws, u, fp.rsplit("/", 1)[0])
        quiz.append((f"Which actions can {u} perform on the file {fp}?", ", ".join(x for x in ACTIONS if x in a) or "none", "set"))
    return _item("access", level, seed, _acc_doc(r, ru, prod, level), f"work out who can do what in {prod} workspaces", prod,
                 f"who can do what in {prod} workspaces", _acc_setup(ru, ws, prod), quiz,
                 "For each question list the actions (from: read, comment, edit, share, delete) comma-separated, or `none`.",
                 _acc_oracle(ru))


KINDS = {"config": config, "billing": billing, "access": access}
QUICK = list(KINDS)
MAX_LEVEL = 5


def oracle(it: Item) -> str:
    return it.meta["oracle"]
