# llmbox

[![tests](https://github.com/iaroslav-ternovyi/llmbox/actions/workflows/tests.yml/badge.svg)](https://github.com/iaroslav-ternovyi/llmbox/actions/workflows/tests.yml)

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

On the machine that runs models (or anywhere, with `--ssh` to it):

```bash
curl -fsSL https://llmbox.pages.dev/install.sh | sh    # installs llmbox, then the guided start (or: pip install git+https://github.com/iaroslav-ternovyi/llmbox, then llmbox)
```

The guided start (`llmbox`, no arguments) looks at this computer, gets llama.cpp built for its card if it is missing (the
official release build, sha256-checked), shows the best model for it with the numbers and the reason, and after one yes
downloads it, fits the settings, optionally times it against machines like it, and leaves it running with its address.
Then, as you need them:

```bash
llmbox doctor                            # is this computer ready? each problem with the command that fixes it
llmbox pick                              # every measured model fitted to this computer, and the pick
llmbox start <model>                     # the guided start for a model of your choice
llmbox test <model>                      # 3 min speed + 10 min quality: where you stand against machines like yours
                                         # and against the model's score; sent only on a yes (--full: 40 min)
llmbox run <model> -d / llmbox stop      # serve a model in the background / stop it
llmbox login                             # GitHub: a profile page, and your quality runs count toward the scores
llmbox tune <model>                      # measure the speed knobs on this machine, keep what wins
```

`llmbox pick --gpu "RTX 4090" --ram-gb 64 --ram-bw 60` answers for a machine you do not have yet.

Running the benchmark and the site (the reference box):

```bash
llmbox recipe new unsloth/Qwen3.6-35B-A3B-GGUF --file Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf --write
llmbox queue add qwen36-al --suite suite-v0.11 --bench-args "--recipe qwen36-al --adaptive --budget 40"
llmbox queue run                         # the worker: runs queued jobs one at a time, resumes after a crash
llmbox site --out ~/.llmbox/site         # the static site from every saved result, with the recipe registry
llmbox serve --rebuild ~/.llmbox/site    # the intake for `llmbox submit`; accepted measurements rebuild the site
```

`llmbox --help` lists the commands grouped by what you want to do. `llmbox <command> --help` shows a command's
options.

## Where things live

| Path | What |
|---|---|
| `~/.llmbox/hosts/` | Registered machines: hardware, memory speeds, SSH access |
| `~/.llmbox/recipes/<host>/<id>.toml` | A model file plus the settings it is measured with, one recipe per model and quant; `registry/` holds the site's published ones (`llmbox recipe pull`) |
| `~/.llmbox/results/<host>/*.json` | Every run: answers, scores, speed, the exact server command line, telemetry |
| `~/.llmbox/llmbox.db`, `bundles/` | The results database built from those files; readers go through it (`LLMBOX_NO_DB=1` bypasses it) |
| `~/.llmbox/irt/bank-<hash>.json` | The calibrated task bank of a suite version (`llmbox irt calibrate`) |
| `~/.llmbox/queue.db`, `queue/` | Benchmark jobs and their logs |
| `~/.llmbox/snapshots/<tag>/` | Frozen code of a suite tag: a whole campaign runs one version of the tasks |
| `~/.llmbox/traces/` | Saved thinking of every task, used for the loop and budget audits |
| `~/.llmbox/cache/` | Slow, pure results kept between builds (loop scans, bootstrap intervals) |
| `~/.llmbox/site/` | The built site; `recipes/` in it is the registry that `llmbox recipe pull` and `llmbox pick` read |
| `~/.llmbox/intake/`, `results/community/` | `llmbox serve`: submitted bundles and their checks; accepted measurements from people's machines |
| `~/.llmbox/install-id`, `submitted.json` | `llmbox submit`: this machine's random id, what was sent |

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
| `wizard.py`, `doctor.py`, `engine.py` | The guided start, the readiness check, the official llama.cpp build for this computer |
| `serving.py` | A model served by llmbox itself (`run`, and `test` for its run): no llama-swap needed |
| `registry.py`, `pick.py` | The published recipes (export with the site, pull anywhere), the ranking fitted to a machine |
| `hwclass.py`, `submit.py`, `server.py` | Hardware classes, sending measurements, the intake that takes them in |
| `account.py` | Signing in with GitHub's device flow, the llmbox key, profile visibility, erasure |
| `site/` | The static site. `words`: names and texts. `stats`: ties and places. `components`: shared pieces. `data`: records and pools. `layout`: the page frame. One module per page, plus `build`. Styles and scripts are in `site/assets/`. |

Tests: `python3 tests/run_all.py` runs every test in parallel (~1.5 min), `--quick` skips the two slow ones, `--portable`
runs only those that need no saved results of the reference box (what CI runs). Each is also a
plain script (`python3 tests/test_site.py`).

## Docs

- [docs/fast-test.md](docs/fast-test.md): how the test works and why. Covers adaptive runs, calibration, every suite
  version's changes and the ranking at each release. In Russian.
- [docs/results-db.md](docs/results-db.md): the results database and the plan for results from other people's hardware.
- [docs/roadmap.md](docs/roadmap.md): what is left before launch, competitors, how comparison across people works.
- [docs/product.md](docs/product.md): who it is for and how they use it.
- [docs/usage-research.md](docs/usage-research.md): what predicts everyday usefulness (sources).
- The site's own [method page](http://127.0.0.1:8766/method.html) explains the numbers for visitors. It is served
  locally by the `com.llmbox.site` launch agent.

## License

Apache-2.0 (LICENSE).
