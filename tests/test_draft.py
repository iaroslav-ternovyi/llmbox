"""`llmbox recipe new` pieces that need no network. Run: python3 tests/test_draft.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import draft as D  # noqa: E402
from llmbox import estimate as E  # noqa: E402

failed = 0


def check(name, ok, detail=""):
    global failed
    failed += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {name}{f'  ({detail})' if detail and not ok else ''}")


# markers: bare and leading-space forms, capitalized only, in any tokenizer convention
gpt2 = ["x"] * 10 + ["Wait", "ĠWait", "wait", "Ġwait", "Hmm", "ĠHmm", "Alternatively", "ĠAlternatively", "Actually", "ĠActually", "Ġactually"]
m = D.marker_ids(gpt2)
check("GPT-2 vocab: 8 capitalized markers", sorted(m.values()) == [10, 11, 14, 15, 16, 17, 18, 19], str(m))
spm = ["▁Wait", "Wait", "▁Hmm", "▁Alternatively", "▁Actually", "▁wait"]
check("SentencePiece vocab", set(D.marker_ids(spm)) == {"▁Wait", "Wait", "▁Hmm", "▁Alternatively", "▁Actually"})
check("lowercase never biased (occurs in code)", not any(k.lower() == k for k in D.marker_ids(gpt2)))

# quant classes: 4-bit first, then 3, 2, then bigger, 16-bit last
check("bits", [D._bits(q) for q in ("UD-Q4_K_XL", "IQ3_XXS", "Q2_K", "Q8_0", "BF16", "F16", "IQ1_M", "PQ2_0")] == [4, 3, 2, 8, 16, 16, 1, 2])

# sliding-window layers: KV of windowed layers stops at the window
s = E.ModelShape(arch="gemma3", n_layers=48, n_mtp_layers=0, attn_layers=48, kv_heads=8, k_len=256, v_len=256, swa_layers=40, swa_window=1024)
full = E.ModelShape(arch="x", n_layers=48, n_mtp_layers=0, attn_layers=48, kv_heads=8, k_len=256, v_len=256)
kv = lambda sh, c: sh.kv_bytes_per_token("q8_0") * c + sh.kv_swa_bytes("q8_0", c)
check("SWA: 128k KV about 1/6 of full attention", 0.15 < kv(s, 131072) / kv(full, 131072) < 0.2, f"{kv(s, 131072) / kv(full, 131072):.3f}")
check("SWA: below the window it equals full attention", kv(s, 512) == kv(full, 512))

print(f"\n{'all passed' if not failed else f'{failed} FAILED'}")
sys.exit(1 if failed else 0)
