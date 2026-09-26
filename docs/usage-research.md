# What predicts everyday usefulness (research, 2026-09-27)

Collected by a research agent for llmbox v0.10; every figure links to its source, estimates are marked (est.), unchecked numbers (unverified).

I've finished the research. Most figures were checked against primary sources: papers, official reports and dataset cards. Anything I couldn't confirm is marked **(unverified)**, and my own estimates are marked **(est.)**.

# What predicts everyday usefulness for local LLMs (as of 2026-09-27)

## 1. What users actually do

| Source (population) | Coding | Writing/editing | Advice/how-to/tutoring | Info seeking | Agents/tools | Other |
|---|---|---|---|---|---|---|
| ChatGPT consumers, ~1.1M sampled conversations, to Jun 2025 ([NBER w34255](https://www.nber.org/papers/w34255)) | 4.2% (Technical Help ~5%, incl. math 3%) | 24%; about ⅔ of it edits, translates or summarizes text the user supplied; 40% of work messages | 29% (tutoring 10.2%, how-to 8.5%) | 24% | – | multimedia ~7%, relationships 1.9%, role-play 0.4%; more than 70% non-work |
| WildChat, same taxonomy ([arXiv 2609.28990](https://arxiv.org/html/2609.28990v1)) | Tech help 8.0% | 28.1% | 28.8% | 17.8% | – | self-expression 7.5% |
| Claude.ai, Feb 2026 ([AEI Mar-26](https://www.anthropic.com/research/economic-index-march-2026-report)) | computer & math occupations 35% | – | coursework 12% | – | – | 46% work / 42% personal |
| Claude.ai outputs, Apr–Jun 2026 ([AEI Jun-26](https://www.anthropic.com/research/economic-index-june-2026-report)) | code ~17% | documents/reports 15% | explanations 17%, guidance 11% | | | |
| OpenRouter, 100T tokens, 2025 ([arXiv 2601.10088](https://arxiv.org/abs/2601.10088)) | programming grew from 11% to over 50% of tokens; 15–20% of open-weight tokens | | | | tool calling rising (no %) | role-play ≈52% of open-weight tokens; average prompt grew from 1.5k to over 6k tokens |
| Local-LLM Discord, 10,133 use cases, Jul 2025–Jul 2026 ([BenchmarkList](https://benchmarklist.com/research/local-llm-use-cases/)) | 20.4% | media/translation 4.4%, creative/RP 4.3% | education 2.5% | chat/assistant 4.8% | 20.0% | RAG 4.7%, homelab 18.5%, fine-tuning 6.6% |

- **Consumers:** about half of what an average user does is advice plus information. About a quarter is writing, mostly transforming text they supplied. Coding is 5–8% or less.
- **Local users:** they skew toward developer and agent work. That evidence is weak: the Discord data counts what enthusiasts talk about, not how much they use each thing.
- **Language:** Arena prompts come from 100+ languages, and only slightly more than half are English ([Arena](https://arena.ai/blog/opendata-july2025)).
- **Mobile:** on phones, the top Copilot use is looking up health information ([Microsoft](https://microsoft.ai/news/its-about-time-the-copilot-usage-report-2025/)).
- **Gap:** I found no data on how much consumer usage involves 30k–200k-token documents.

## 2. Benchmarks that predict human preference

| Benchmark | Items | Judge needed | Agreement with Arena |
|---|---|---|---|
| Arena-Hard-Auto ([paper](https://arxiv.org/abs/2406.11939)) | 500 | yes | Spearman 0.932, Kendall 0.80, confidence agreement 90.9%, separability 87.4%. With style control: agreement 98.6%, Spearman 0.944. ~$20 per model (2024 judge prices) |
| Arena-Hard v2 ([repo](https://github.com/lmarena/arena-hard-auto)) | 500 hard + 250 creative | yes (Gemini-2.5 / GPT-4.1 ensemble) | no numbers published in the README (unverified) |
| AlpacaEval 2 length-controlled ([paper](https://arxiv.org/abs/2404.04475)) | 805 | yes | Spearman 0.98 (0.94 before length control), under $10 and under 3 min. Arena-Hard's own comparison measured it at Spearman 0.919, agreement 82.5% |
| WildBench ([paper](https://arxiv.org/abs/2406.04770)) | 1,024 real-user tasks with checklists | yes | Pearson 0.98 for WB-Reward on top models; 0.95 for WB-Score |
| MixEval-Hard ([paper](https://arxiv.org/abs/2406.06565)) | 1,000 ground-truth questions | only a parser | 0.96; about $0.6 per run |
| MT-Bench | 80 | yes | Spearman 0.899, but confidence agreement only 26.6% |
| LiveBench ([paper](https://arxiv.org/abs/2406.19314)) | n/a | no | 0.91 |
| MMLU / GSM8K / MBPP ([MixEval](https://arxiv.org/abs/2406.06565)) | large | no | 0.65 / 0.78 / 0.28 |
| MMLU / GPQA, only 9 models ([Auto-Arena](https://arxiv.org/abs/2405.20267)) | | no | 0.56 / 0.37 |

**Cautions:**
- **Spearman flatters benchmarks.** MT-Bench has ρ≈0.90 yet only 26.6% confidence agreement. Separability and confidence agreement are the numbers to watch.
- **Correlations shrink in a narrow band.** Across benchmarks the median correlation is 0.73, and a wide release-date range inflates it ([Epoch](https://epoch.ai/data-insights/benchmark-correlations)). Local 30B-class models sit in a narrow band, so expect weaker correlations there.
- **Arena itself is a biased target:**
  - Voters reward length, markdown and a positive tone. Style control has been the default view since May 2025 ([Arena](https://x.com/arena/status/1923398953468678529), [sentiment](https://arena.ai/blog/sentiment-control)).
  - Private testing and data access skew rankings. Extra Arena data gave up to a 112% relative gain on Arena-Hard ([Leaderboard Illusion](https://arxiv.org/abs/2504.20879)).
- **A judge alone is not enough.** What LLM judges prefer does not track knowledge, instruction following or safety ([SOS-Bench](https://arxiv.org/abs/2409.15268)).
- **IFEval is overfit.** Models fail to generalize to IFBench's 58 new constraints ([IFBench](https://arxiv.org/abs/2507.02833)).
- **Takeaway:** MixEval shows that program-graded questions sampled to match real user queries can reach 0.96 without a judge. That is the cheapest valid route for the information-seeking share of usage.

## 3. Agentic coding and tool use

| Benchmark | Size | Problems |
|---|---|---|
| SWE-bench Verified | 500 | Contaminated. 59.4% of the 138 audited tasks had flawed tests; OpenAI stopped using it in Feb 2026 ([Epoch review](https://epoch.ai/benchmarks/swe-bench-verified/review)) |
| SWE-bench Pro | 731 public | About 30% broken; OpenAI withdrew its recommendation on 2026-07-08 ([OpenAI](https://openai.com/index/separating-signal-from-noise-coding-evaluations/); I read this via [secondary coverage](https://alphasignal.ai/news/openai-retracts-swe-bench-pro-after-finding-30-of-tasks-broken)) |
| SWE-rebench | fresh monthly GitHub slices | Tasks created before a model's release are flagged, which makes it the best option for contamination ([paper](https://arxiv.org/abs/2505.20411)) |
| Terminal-Bench 2.0 / 4.0 | 89 / 66 tasks | 4.0 has 8-hour limits and a median expert time of 4 h; Epoch found 45.5% of tasks defective ([paper](https://arxiv.org/abs/2601.11868), [vals](https://www.vals.ai/benchmarks/terminal-bench-4), [review](https://epoch.ai/benchmarks/terminal-bench-4/review)) |
| Aider Polyglot | 225 exercises, 6 languages, 2 attempts | Correlates 0.93 with Epoch's general capability index ([Epoch](https://epoch.ai/data-insights/benchmark-correlations)) |
| τ²-bench | 50 airline + 115 retail + 114 telecom tasks | Needs a simulated-user LLM; about $40 per trial with GPT-4.1 ([paper](https://arxiv.org/abs/2506.07982)) |
| BFCL v4 | 5,088 items (40% agentic, 30% multi-turn) | 48% of 50 sampled items defective; rated "Flawed" ([Epoch](https://epoch.ai/benchmarks/berkeley-function-calling-leaderboard/review)) |

- **Scale of the problem:** Epoch rated 9 of its first 15 reviewed benchmarks "Flawed" ([summary](https://www.theneuron.ai/news/epoch-ai-benchmark-reviews-nine-flawed/)).
- **Link to practical usefulness is weak:**
  - Static coding benchmarks correlate at ρ≤0.1 with what people prefer inside a real IDE. Arena's coding category does better, at 0.62 ([Copilot Arena](https://arxiv.org/abs/2502.09328)).
  - In METR's randomized trial, experienced developers were 19% *slower* with AI tools ([METR](https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/)).
- **Cost:** about $0.30 per SWE-bench instance with frontier APIs, with gains flattening near 50 steps ([swebench](https://www.swebench.com/post-250820-mini-roulette.html)). Running all 500 locally is not feasible **(est.)**.

Your own generated tasks with hidden tests and state-graded outcomes are the right call, given these contamination and defect rates.

## 4. Long context

- **Synthetic tests predict poorly.** In HELMET, no synthetic task averages ρ>0.8 against downstream tasks, and needle-in-a-haystack does not predict them. RAG-style tasks are cheap and predict other downstream tasks best ([HELMET](https://arxiv.org/abs/2410.02694)).
- **NoLiMa** removes literal word overlap between question and answer. At 32K, 11 of 13 models fell below 50% of their short-context score ([NoLiMa](https://arxiv.org/abs/2502.05167)).
- **LongBench v2** is realistic but expensive: 503 multiple-choice questions over 8k–2M words. Human experts scored 53.7% in 15 minutes ([paper](https://arxiv.org/abs/2412.15204)).
- **MRCR** has 2,400 items, 100 per length bin from 4k to 1M, graded by a program. About 15% of items were fixed in Dec 2025 ([card](https://huggingface.co/datasets/openai/mrcr)).
- **Fiction.LiveBench** is 36 questions over 30 stories and hard to reproduce ([Epoch](https://epoch.ai/benchmarks/fictionlivebench)).
- **All models degrade as input grows:** all 18 models tested got worse with longer input ([Chroma](https://www.trychroma.com/research/context-rot)).
- **No study links any long-context test to user satisfaction.** Cheapest valid option: RAG/NoLiMa-style questions at 16–64k, several questions per document so the cached prefix is reused.

## 5. Efficient evaluation

- **tinyBenchmarks:** 100 items give about 2% error, including 100 of AlpacaEval's 800 ([paper](https://arxiv.org/abs/2402.14992)).
- **metabench:** 858 of 28,632 items reproduce scores with 1.24% RMSE ([ICLR'25](https://proceedings.iclr.cc/paper_files/paper/2025/file/4ebc26584810a189ef1e4f173aba4319-Paper-Conference.pdf)).
- **Fluid Benchmarking:** 50× fewer MMLU items than the full set, with better validity; the item model was fit on 102 LMs ([paper](https://arxiv.org/abs/2509.11106)).
- **ATLAS:** up to 90% fewer items ([paper](https://arxiv.org/abs/2511.04689)).
- **The skeptical counterpoint:** no subset method reliably ranks models 3.5 points apart on MMLU-Pro. About 250 items are needed for close pairs, and at that size random sampling is just as good ([ICLR'26](https://arxiv.org/abs/2510.08730)).
- **Generative tasks:** adaptive testing on continuous scores (including judge scores) needed 52–101 items, about 2% of the budget, with a mean Kendall τ of 0.75 (range 0.52–0.96). Adaptive stopping saved another 32% of items. It also has a cost-aware selection rule ([paper](https://arxiv.org/abs/2601.13885)).
- **Agent benchmarks:** keeping only tasks with a 30–70% historical pass rate cuts 44–70% of tasks. Rank correlation stays at 0.90–0.96 (worst case 0.87), and random subsets are much less stable ([paper](https://arxiv.org/abs/2603.23749)).
- **Fresh-seed items:** you can't calibrate each item individually. Instead, predict difficulty from the item's content or generator settings ("amortized calibration", 172 LMs in the study) ([paper](https://arxiv.org/abs/2503.13335)).
- **Where your ±12 comes from (arithmetic):** the 95% half-width of the total is about √Σ(wᵢ·98/√nᵢ)². A component with weight 0.25 and 6 pass/fail tasks alone contributes about ±10. Two fixes:
  - Use partial credit and more, cheaper items in the heavily weighted components.
  - Run the same seeds on every model in a round, so model-vs-model differences are paired and much tighter.

## 6. LLM-as-judge

- **Agreement with humans:** GPT-4 agrees with humans 85% on non-tied votes (66% counting ties); human-human agreement is 81% (63%). GPT-4 favors its own answers by about +10% win rate, Claude-v1 by +25%, and judges show position bias ([MT-Bench](https://arxiv.org/abs/2306.05685)).
- **Judge choice matters:** confidence agreement was 90.9% with GPT-4-Turbo, 84.8% with Gemini-1.5-Pro and 66.7% with Claude-3-Opus. An ensemble reached 91.5% and reduced self-bias ([Arena-Hard](https://arxiv.org/abs/2406.11939)).
- **Pairwise judgments are fragile:** they flip in 35% of cases when a distractor feature is added, versus 9% for pointwise scores ([COLM'25](https://arxiv.org/abs/2504.14716)).
- **Percent agreement misleads:** judges with 80%+ agreement can be 20 points apart. Cohen's κ is 0.84 for GPT-4-Turbo against 0.96 human-human ([paper](https://arxiv.org/abs/2406.12624)).
- **Length control** cut AlpacaEval's length gameability from 21% to 6% ([README](https://github.com/tatsu-lab/alpaca_eval)).
- **What reaches ≥0.9:** only agreement at the level of *rankings*: Arena-Hard (with style control), AlpacaEval length-controlled, WildBench, and a two-family ensemble. No setup reaches 0.9 agreement on individual answers.
- **Cost:** about $3–15 per model for 300 prompts judged in both positions at current mid-tier API prices **(est.)**.

## Recommendation: minimal battery

Time assumptions: 60 tok/s generation, prefill at about 1k tok/s **(unverified; measure it on your box)**, thinking set to low.

| Component | What it measures | Weight | Items | Time (est.) | Grading | Evidence |
|---|---|---|---|---|---|---|
| Everyday help chat, ~40% non-English | advice, how-to, explanation, tutoring (~29% + part of 24% of usage) | **25%** | 50, adaptive from a pool of about 500 real-user-style prompts | 11 min | Judge: 2 model families not under test, both positions, length/markdown control; checklist pointwise preferred | Arena-Hard 90.9% / 98.6%; WildBench 0.98; tinyAlpacaEval |
| Knowledge + abstaining when unsure | factual lookup (24% of usage; local models have no web) | **15%** | 100 (from [SimpleQA Verified](https://arxiv.org/abs/2509.07968), 1,000 items) | 4 min | Program + cheap equivalence checker; confident wrong answers penalized | MixEval 0.96; passed Epoch review |
| Editing user-supplied text | edit, summarize, translate, rewrite under constraints (24%; 40% of work) | **20%** | 40 | 8 min | Program checks unseen-type constraints + judge checks quality | IFBench, SOS-Bench |
| Coding | function-level with test feedback + small repo tasks | **20%** | 15 function + 5 repo tasks at 30–70% pass rate, partial credit | 26 min | Program, hidden tests | Aider–capability index 0.93; mid-difficulty filter ρ 0.90–0.96 |
| Tool use | multi-turn with simulated user, graded on final state | **10%** | 10 | 7 min | Program; simulated user runs in the cloud | τ²-bench design; BFCL flawed |
| Long-document QA, 16–64k | RAG-style, non-literal questions | **5%** | 4 documents × 3 questions | 4 min | Program | HELMET, NoLiMa |
| Multi-step reasoning | exact-answer math | **5%** | 20 | 8 min | Program | math is 3% of ChatGPT messages |

- **Time:** about 68 min at fixed length, or about 45 min with adaptive stopping **(est.)**, against 107 min now.
- **Precision:** worst-case 95% interval about ±7.5 points, against ±12 now, and tighter still on paired model-vs-model differences.
- **Developer profile (optional):** raise coding to 30% and tool use to 15%, and take the 15 points out of chat and knowledge.

**What your current suite is missing:** open-ended help and advice (about half of real usage), factual knowledge and hallucination, editing of text the user provides, multilingual prompts, and judge-graded quality with bias controls.

**What it over-weights for an average user:**
- Coding: 35% against 4–8% of consumer usage.
- CRM tool calling: 20%.
- 30k–200k documents: 15%, when typical prompts are about 6k tokens, and they cost the most prefill time on local hardware.
- Exact-answer reasoning: 15%.
