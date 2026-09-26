"""`llmbox recipe new <hf-repo>`: a draft recipe for a model nobody has set up yet, from its GGUF headers alone.

What can be read from the file is filled in and says where it came from:
  quant       the best 4-bit (else 3-, 2-bit) file that fits the host at the model's full context (4-bit is the usual
              sweet spot: bigger files cost speed linearly for little quality)
  sha256      Hugging Face's LFS id of the file
  anti-loop   the 8 overthinking markers (Wait / Hmm / Alternatively / Actually, capitalized, with and without a leading
              space) looked up in the model's own vocabulary; lowercase forms stay free because they occur in code
  thinking    from the chat template: a thinking model gets the 24k reasoning reserve under the 32k reply cap
  MTP         a draft head in the file turns on speculative decoding
  sampling    only when the GGUF carries general.sampling.*; otherwise the model card's lines are quoted as a TODO
  hardware    `llmbox fit` for the host
Anything else stays a TODO in the notes. A draft has no score until `llmbox bench` runs it.
"""
from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor

from . import estimate as E
from . import fit as F
from . import hf, recipe as rc

MARKER_WORDS = ("Wait", "Hmm", "Alternatively", "Actually")
TOKENS = "tokenizer.ggml.tokens"


def marker_ids(tokens: list[str]) -> dict[str, int]:
    """Token ids of the markers in this vocabulary: bare, and with the tokenizer's leading-space mark."""
    idx = {t: i for i, t in enumerate(tokens)}
    out = {}
    for w in MARKER_WORDS:
        for form in (w, "Ġ" + w, "▁" + w, " " + w):   # GPT-2 byte BPE, SentencePiece, literal space
            if form in idx:
                out[form] = idx[form]
    return out


def _bits(quant: str) -> int:
    m = re.search(r"(?:I?Q|BF|F)(\d+)", quant.replace("UD-", "").upper())
    return int(m.group(1)) if m else 16


def _rank(f: hf.GGUFFile) -> tuple:
    """Preference among files of one repo: 4-bit first, then 3, 2, then 5, 6, 8, 1-bit, 16-bit; within a class K-quants
    and imatrix I-quants before the legacy Q4_0 / Q4_1 / Q5_0 / Q5_1, then larger."""
    q = f.quant.replace("UD-", "").upper()
    legacy = bool(__import__("re").fullmatch(r"Q\d_[01]", q))
    return ([4, 3, 2, 5, 6, 8, 1, 16].index(_bits(f.quant)) if _bits(f.quant) in (4, 3, 2, 5, 6, 8, 1, 16) else 9, legacy, -f.size)


def _card_sampling(repo: str) -> list[str]:
    lines = []
    for ln in hf.model_card(repo).splitlines():
        if re.search(r"temperature|top[_ -]?p|top[_ -]?k|min[_ -]?p|presence[_ -]penalty", ln, re.I) and len(ln) < 240:
            lines.append(ln.strip().strip("|").strip())
    return lines[:6]


def draft(repo: str, hw: E.HostSpec, rid: str, file: str | None = None, models_dir: str = "", server: str = "",
          cores: int | None = None) -> tuple[dict, str]:
    files = hf.list_gguf(repo)
    if not files:
        raise SystemExit(f"{repo}: no GGUF files")
    if file:
        files = [f for f in files if file in f.name]
        if not files:
            raise SystemExit(f"{repo}: no file matching {file!r}")
    base = rc._merge(rc.DEFAULTS, {"id": rid, "model": {"hf_repo": repo, "file": "", "path": ""}})
    with ThreadPoolExecutor(max_workers=4) as ex:
        shapes = list(ex.map(lambda f: E.analyze(hf.read_headers(f)), files))
    table, pick = [], None
    for f, sh in sorted(zip(files, shapes), key=lambda x: _rank(x[0])):
        r = rc._merge(base, {"placement": {"ctx": sh.context_length}})
        ft = F.fit(r, sh, hw, cores=cores)
        full = ft.fits and ft.ctx >= (sh.context_length or ft.ctx)
        table.append(f"  {f.quant:14s} {f.size / 2**30:6.1f} GB  " + (f"fits at {ft.ctx // 1024}k, ~{ft.tps:.0f} tok/s" if ft.fits else "does not fit"))
        if pick is None and full:
            pick = (f, sh)
    shorter = None   # the preferred class that only fits with less context: offered, not picked
    if pick is not None and _rank(pick[0])[0] > 0:
        shorter = next(((f, sh) for f, sh in sorted(zip(files, shapes), key=lambda x: _rank(x[0])) if _rank(f)[0] < _rank(pick[0])[0]
                        and F.fit(rc._merge(base, {"placement": {"ctx": sh.context_length}}), sh, hw).fits), None)
    if pick is None:   # nothing fits at the full context: the best file that fits at all
        pick = next(((f, sh) for f, sh in sorted(zip(files, shapes), key=lambda x: _rank(x[0]))
                     if F.fit(rc._merge(base, {"placement": {"ctx": sh.context_length}}), sh, hw).fits), None)
    if pick is None:
        raise SystemExit(f"{repo}: no file fits this box\n" + "\n".join(table))
    f, _ = pick
    h0 = hf.read_headers(f, keep=frozenset({TOKENS}))
    sh = E.analyze(h0)
    hd = h0[0]
    tmpl = hd.kv.get("tokenizer.chat_template") or ""
    thinking = "<think>" in tmpl or "enable_thinking" in tmpl or "reasoning_content" in tmpl
    marks = marker_ids(hd.kv.get(TOKENS) or [])
    notes = [f"Draft by `llmbox recipe new` from {repo}: no score until `llmbox bench` runs it."]
    r = rc._merge(base, {"model": {"file": f.name, "sha256": f.sha256[0] or "", "path": os.path.join(models_dir, os.path.basename(f.name)) if models_dir else ""},
                         "placement": {"ctx": sh.context_length, "kv_type": "q8_0"},
                         "sampling": {"max_tokens": 32768}})
    notes.append("KV q8_0 by default. Some families break tool calls on a quantized KV (Ornith: f16); check tool use in the bench.")
    if sh.sampling:
        r["sampling"].update({k.replace("-", "_"): float(f"{v:.4g}") if isinstance(v, float) else v for k, v in sh.sampling.items()})
        notes.append("Sampling from the GGUF metadata: " + ", ".join(f"{k} {float(f'{v:.4g}') if isinstance(v, float) else v}" for k, v in sh.sampling.items())
                     + ". Fine-tunes often recommend other values on their card.")
    else:
        card, where = _card_sampling(repo), repo
        base_url = hd.kv.get("general.base_model.0.repo_url") or ""
        if not card and "huggingface.co/" in base_url:   # quantizers' cards rarely repeat it; the base model's card does
            where = base_url.split("huggingface.co/")[1].strip("/")
            card = _card_sampling(where)
        notes.append("TODO sampling: the GGUF has none; " + (f"{where}'s card says: " + " / ".join(card) if card else "no card mentions it"))
    if thinking:
        r["antiloop"].update(reasoning_budget=24576, marker_ids=list(marks.values()), marker_bias=0.5)
        notes.append(f"Thinking template: 24k reasoning reserve under the 32k reply cap; markers {', '.join(repr(k.replace("Ġ", " ").replace("▁", " ")) for k in marks)} at -0.5.")
        if "reasoning_effort" in tmpl:
            notes.append("TODO template: it takes reasoning_effort (low / medium / high); agents usually want low.")
    else:
        notes.append("No thinking in the chat template: no reasoning reserve, no marker bias.")
    if sh.n_mtp_layers:
        r["speculative"].update(type="draft-mtp", draft_max=2)
        notes.append("MTP head in the file: speculative decoding on, draft 2 (`llmbox tune` can check 3).")
    if server:
        r["runtime"]["server"] = server
    ft = F.fit(r, sh, hw, cores=cores)
    if ft.fits:
        r = F.apply(r, ft)
    full = sh.context_length or ft.ctx
    notes.insert(1, f"Quant {f.quant}: the best quant (4-bit preferred) that fits this box " + (f"at the full {full // 1024}k context." if ft.ctx >= full else
                    f"at all, and only at {ft.ctx // 1024}k of its {full // 1024}k context: long documents are limited on this box."))
    if shorter:
        sf = F.fit(rc._merge(base, {"placement": {"ctx": shorter[1].context_length}}), shorter[1], hw)
        notes.append(f"Choice: {shorter[0].quant} is a better quant but fits only at {sf.ctx // 1024}k context (`--file {shorter[0].quant}`); "
                     "a different quant is a different recipe with its own score.")
    r["notes"] = {"lines": notes}
    r["description"] = f"{os.path.basename(f.name)} (draft)"
    if len(table) > 10:
        table = table[:9] + [f"  ... {len(table) - 9} more"]
    report = "\n".join([f"{repo}: {len(files)} files (speed: formula only, no run yet)", *table, "", f"picked {f.quant}", *[f"  - {n}" for n in notes[1:]]])
    return r, report
