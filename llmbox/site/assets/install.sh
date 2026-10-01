#!/bin/sh
# llmbox installer: a private Python environment in ~/.llmbox/venv and the `llmbox` command in ~/.local/bin.
# No sudo, no background service; everything llmbox keeps is under ~/.llmbox. Remove: rm -rf ~/.llmbox/venv ~/.local/bin/llmbox
# (and the "# llmbox" PATH line it adds to the shell's startup file, when ~/.local/bin was not on the PATH)
#   curl --proto '=https' --tlsv1.2 -fsSL https://llmbox.pages.dev/install.sh | sh             then the guided start
#   curl --proto '=https' --tlsv1.2 -fsSL https://llmbox.pages.dev/install.sh | sh -s -- <id>   the same with that model
# Or read it first: curl -fsSLO https://llmbox.pages.dev/install.sh && less install.sh && sh install.sh
set -eu

# everything in main, called on the last line: a download cut off half-way defines a function and runs nothing
main() {
  REPO="${LLMBOX_REPO:-https://github.com/iaroslav-ternovyi/llmbox}"
  PY=""
  for c in python3.14 python3.13 python3.12 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then PY="$c"; break; fi
  done
  if [ -z "$PY" ]; then
    echo "llmbox needs Python 3.12 or newer: sudo apt install python3.12 python3.12-venv (Ubuntu), or brew install python@3.12 (Mac)" >&2
    exit 1
  fi
  command -v git >/dev/null 2>&1 || { echo "llmbox installs from GitHub and needs git: sudo apt install git" >&2; exit 1; }
  V="$HOME/.llmbox/venv"
  echo "installing llmbox with $PY into $V ..."
  "$PY" -m venv "$V" || { echo "no venv module: sudo apt install python3-venv (or python3.12-venv)" >&2; exit 1; }
  "$V/bin/pip" install -q --upgrade pip
  # the repository until llmbox-bench is on PyPI; --force-reinstall because pip compares only version numbers from a
  # repository (running the installer again then updates), and llmbox has no dependencies for --no-deps to skip
  "$V/bin/pip" install -q --upgrade --force-reinstall --no-deps "git+$REPO"
  mkdir -p "$HOME/.local/bin"
  ln -sf "$V/bin/llmbox" "$HOME/.local/bin/llmbox"
  echo "installed: $("$V/bin/llmbox" --version)"
  # ~/.local/bin on the PATH the way uv and rustup do it: one line in the shell's startup file, once
  # (LLMBOX_NO_MODIFY_PATH=1 leaves the startup files alone and only says what to add)
  case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *)
      case "$(basename "${SHELL:-sh}")" in
        zsh) RC="$HOME/.zshrc"; LINE='export PATH="$HOME/.local/bin:$PATH"' ;;
        bash) RC="$HOME/.bashrc"; LINE='export PATH="$HOME/.local/bin:$PATH"' ;;
        fish) RC="$HOME/.config/fish/conf.d/llmbox.fish"; LINE='fish_add_path -g $HOME/.local/bin'; NOW='set -gx PATH $HOME/.local/bin $PATH' ;;
        *) RC="$HOME/.profile"; LINE='export PATH="$HOME/.local/bin:$PATH"' ;;
      esac
      if [ -n "${LLMBOX_NO_MODIFY_PATH:-}" ]; then
        echo "add ~/.local/bin to your PATH: $LINE"
      else
        mkdir -p "$(dirname "$RC")"
        grep -qsF "$LINE" "$RC" || printf '\n# llmbox\n%s\n' "$LINE" >> "$RC"
        echo "llmbox is on the PATH of new terminals ($RC); in this one: ${NOW:-export PATH=\"\$HOME/.local/bin:\$PATH\"}"
      fi ;;
  esac
  # straight on to the guided start when a person is at the terminal (the script itself came through a pipe: ask /dev/tty)
  if [ -t 1 ] && [ -r /dev/tty ] && [ -z "${LLMBOX_NO_START:-}" ]; then
    exec "$V/bin/llmbox" start "$@" </dev/tty
  fi
  echo "next:  llmbox    (the guided start: this computer, the best model for it, install, run)"
}

main "$@"
