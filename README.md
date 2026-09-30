# llmbox

Get the most quality × speed out of local LLMs on your own hardware: pick a model and quantization that fits, tune
llama.cpp for your box, measure it on real work, and publish the results as a static site.

- **Real work, graded by programs.** Coding in real repositories, tool calls on messy data, questions about your
  own machine, long documents, writing under constraints. Hidden tests and checkers grade every task; no model grades
  another.
- **Fresh tasks every run.** Tasks are generated from a seed, so a model cannot have seen the answers.
- **A score with its margin.** Item response theory pools every answer of every run. The score is a share of
  Claude Opus 5.5's score on the same tasks, with a 95% range, and the site says when two models are not
  measurably apart.
- **Speed on a real PC, predicted for yours.** Speed is timed on the reference box with the settings on each model
  page, and predicted for other GPUs and Macs from the model file and memory speeds.

Requires Python 3.12. It has no dependencies outside the standard library. The models run on a box with llama.cpp or
ik_llama.cpp behind [llama-swap](https://github.com/mostlygeek/llama-swap).

## Quick start

```bash
pip install -e .
llmbox host add box --ssh me@gpu-box     # register the machine that runs models: GPU, RAM, measured RAM speed
llmbox scout unsloth/Qwen3.6-35B-A3B-GGUF --host box   # which quants fit and how fast, no download
llmbox recipe new unsloth/Qwen3.6-35B-A3B-GGUF --file Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf --write
llmbox install qwen36-al --apply         # download, fit to the box, write the launcher and the llama-swap entry
llmbox optimize qwen36-al                # tune speed knobs, compare against stock llama.cpp, re-measure
llmbox queue add qwen36-al --suite suite-v0.11 --bench-args "--recipe qwen36-al --adaptive --budget 40"
llmbox queue run                         # the worker: runs queued jobs one at a time, resumes after a crash
llmbox site --out ~/.llmbox/site         # the static site from every saved result
```

`llmbox --help` lists the commands grouped by what you want to do. `llmbox <command> --help` shows a command's
options.

## Where things live

| Path | What |
|---|---|
| `~/.llmbox/hosts/` | Registered machines: hardware, memory speeds, SSH access |
| `~/.llmbox/recipes/<host>/<id>.toml` | A model file plus the settings it is measured with, one recipe per model and quant |
| `~/.llmbox/results/<host>/*.json` | Every run: answers, scores, speed, the exact server command line, telemetry |
| `~/.llmbox/llmbox.db`, `bundles/` | The results database built from those files; readers go through it (`LLMBOX_NO_DB=1` bypasses it) |
| `~/.llmbox/irt/bank-<hash>.json` | The calibrated task bank of a suite version (`llmbox irt calibrate`) |
| `~/.llmbox/queue.db`, `queue/` | Benchmark jobs and their logs |
| `~/.llmbox/snapshots/<tag>/` | Frozen code of a suite tag: a whole campaign runs one version of the tasks |
| `~/.llmbox/traces/` | Saved thinking of every task, used for the loop and budget audits |
| `~/.llmbox/cache/` | Slow, pure results kept between builds (loop scans, bootstrap intervals) |
| `~/.llmbox/site/` | The built site |

## The code

| Module | What |
|---|---|
| `llmbox/suite/` | The tasks: nine blocks, each kind with levels 1–10, generators and graders (`suite.VERSION`, content hash) |
| `bench.py`, `client.py`, `frontier.py` | Running the suite against a served model or against Claude (reference runs) |
| `irt.py`, `famfp.py`, `report.py` | Calibration, adaptive task selection, answers pooled across runs and versions, the ranking |
| `recipe.py`, `fit.py`, `estimate.py`, `install.py`, `tune.py`, `optimize.py` | Recipes, fitting them to a box, speed prediction, installing, tuning |
| `queue.py`, `runinfo.py`, `pending.py` | The job queue, what a run records, explanations graded after cloud runs |
| `results.py`, `db.py`, `verify.py` | Result files, the results database, re-grading a run from its answers |
| `candidates.py`, `watch.py`, `eci.py` | New models on Hugging Face, daily watch, expected scores from public benchmarks |
| `site/` | The static site. `words`: names and texts. `stats`: ties and places. `components`: shared pieces. `data`: records and pools. `layout`: the page frame. One module per page, plus `build`. Styles and scripts are in `site/assets/`. |

Tests: `python3 tests/run_all.py` runs every test in parallel (~1.5 min), `--quick` skips the two slow ones. Each is also a
plain script (`python3 tests/test_site.py`).

## Docs

- [docs/fast-test.md](docs/fast-test.md): how the test works and why. Covers adaptive runs, calibration, every suite
  version's changes and the ranking at each release. In Russian.
- [docs/results-db.md](docs/results-db.md): the results database and the plan for results from other people's hardware.
- [docs/product.md](docs/product.md): who it is for and how they use it.
- [docs/usage-research.md](docs/usage-research.md): what predicts everyday usefulness (sources).
- The site's own [method page](http://127.0.0.1:8766/method.html) explains the numbers for visitors. It is served
  locally by the `com.llmbox.site` launch agent.
