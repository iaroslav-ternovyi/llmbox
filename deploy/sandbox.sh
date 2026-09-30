#!/usr/bin/env bash
# The sandbox a stranger's answers are re-graded in (LLMBOX_SANDBOX for llmbox verify; the server sets it):
#   sandbox.sh --read <path> [--read <path> ...] -- <command...>
# bubblewrap: the system read-only, /home and /root hidden except the --read paths (read-only), a fresh /tmp, no
# network and no other namespace shared, killed with its parent; at most 15 CPU-minutes and 256 processes. Memory is
# capped by the service's cgroup (MemoryMax in llmbox-intake.service).
set -euo pipefail
binds=()
while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do
  case "$1" in
    --read) binds+=(--ro-bind "$2" "$2"); shift 2 ;;
    *) echo "sandbox.sh: unknown option $1" >&2; exit 2 ;;
  esac
done
shift
exec prlimit --cpu=900 --nproc=256 -- bwrap --ro-bind / / --dev /dev --proc /proc --tmpfs /tmp --tmpfs /home --tmpfs /root \
  "${binds[@]}" --unshare-all --die-with-parent --new-session --setenv HOME /tmp --chdir "$PWD" "$@"
