#!/bin/sh
# llmbox installer: a private Python environment in ~/.llmbox/venv and the `llmbox` command in ~/.local/bin.
# No sudo, no background service; everything llmbox keeps is under ~/.llmbox. Remove: rm -rf ~/.llmbox/venv ~/.local/bin/llmbox
#   curl -fsSL https://llmbox.pages.dev/install.sh | sh
set -eu
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
"$V/bin/pip" install -q --upgrade "git+$REPO"
mkdir -p "$HOME/.local/bin"
ln -sf "$V/bin/llmbox" "$HOME/.local/bin/llmbox"
echo "installed: $("$V/bin/llmbox" --version)"
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo "add ~/.local/bin to your PATH:  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc && . ~/.bashrc" ;;
esac
echo "next:  llmbox host add me   then   llmbox pick"
