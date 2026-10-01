# Changelog

## Unreleased (0.2)

For people running models:
- `llmbox` (no arguments) is a guided start. It finds the computer, gets llama.cpp if missing and shows the best model
  for it with the reason. On one yes it installs the model, optionally times it, and leaves it running.
  - `install.sh` hands over to it.
  - `curl … | sh -s -- <model>` starts it with a chosen model.
- llama.cpp is not compiled any more. llmbox downloads the official release build for the card and driver (NVIDIA
  CUDA with its runtime, Mac Metal, AMD Vulkan, CPU) and checks its sha256.
- `llmbox test` takes 3 minutes of speed plus 10 of quality (`--full` runs 40). It shows how the machine compares with
  others of its kind and whether this setup reproduces the model's published quality. It sends nothing without a yes.
- `llmbox doctor` lists each problem with the command that fixes it. `llmbox update` updates llmbox and the model list.
- `llmbox run -d` and `llmbox stop` run models in the background.
- `llmbox pick` works for any machine (`--gpu 'RTX 4090'`) and shows measured speeds from people with the same
  hardware.
- The installer puts `~/.local/bin` on the PATH itself, the way uv and rustup do: one marked line in the shell's startup
  file. `LLMBOX_NO_MODIFY_PATH=1` opts out.
- `llmbox update` and a second run of the installer really update. Before, pip kept the old code because the version
  number had not changed.
- On PyPI the package will be `llmbox-bench` (`llmbox` there is another project); the command stays `llmbox`. Until the
  first release, installs come from GitHub.

For the site:
- The home page opens with "best for this box", with one line to copy. The browser's own report of the graphics card
  preselects the box, and nothing is sent.
- Sign in with GitHub, on the site or with `llmbox login`. Profile pages show a handle unless the owner makes the name
  public. `llmbox forget` deletes everything the account sent.
- There are pages for people's machines, grouped by hardware class (median, spread, count of machines and people).

Scores:
- Quality runs from people are graded again by the server in a sandbox. A run whose range misses the model's range is
  held back, and only runs from signed-in people count.
- The 95% ranges use 400 draws, so their ends no longer jump between builds. `llmbox queue precision` spends runs where
  they narrow the ranking most.

## Suite v0.11 (2026-09-30)

See [docs/fast-test.md](docs/fast-test.md) §13.
