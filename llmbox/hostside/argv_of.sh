#!/usr/bin/env bash
# Prints the final llama-server argv (one arg per line) of a launcher chain WITHOUT starting anything: `exec` is replaced
# by a function that follows nested start-*.sh launchers and prints the final command; nvidia-smi / sleep are stubbed so
# "wait until the GPU is free" loops return at once. Usage: bash -s <launcher> <port> < argv_of.sh
exec() {
  case "$1" in
    */start-*.sh) local s="$1"; shift; source "$s" "$@"; builtin exit 0 ;;
    *) printf '%s\n' "$@"; builtin exit 0 ;;
  esac
}
nvidia-smi() { echo 0; }
sleep() { :; }
L="$1"; shift
source "$L" "$@"
echo "launcher did not exec a server: $L" >&2
builtin exit 4
