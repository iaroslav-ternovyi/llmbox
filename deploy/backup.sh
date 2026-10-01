#!/usr/bin/env bash
# The server's state, backed up daily with restic (encrypted, deduplicated) to a Hetzner Storage Box or any restic
# repository; run by llmbox-backup.timer as root. Settings in /etc/llmbox-backup.env (root only):
#   RESTIC_REPOSITORY=sftp:uXXXXX@uXXXXX.your-storagebox.de:llmbox   (or s3:..., with AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)
#   RESTIC_PASSWORD=...   (kept somewhere else too: without it the backup cannot be read)
# What: the intake (accounts, submissions, the salt the handles are made with - lose it and every handle changes),
# people's accepted results, and the visit counts. SQLite files are copied with SQLite's own backup first, so a copy
# taken while the server writes is still whole.
#   backup.sh            back up, then keep 14 daily, 8 weekly, 12 monthly
#   backup.sh check      restore the latest into a temporary folder and check every database in it (run it once a month)
set -euo pipefail
. "${LLMBOX_BACKUP_ENV:-/etc/llmbox-backup.env}"
export RESTIC_REPOSITORY RESTIC_PASSWORD
STAGE="${LLMBOX_BACKUP_STAGE:-/var/backups/llmbox-stage}"
DATA="${LLMBOX_DATA:-/home/llmbox/.llmbox}"
GOAT="${LLMBOX_GOATCOUNTER_DB:-/var/lib/goatcounter/db.sqlite3}"

snapshot_dbs() {   # consistent copies of the live databases
  rm -rf "$STAGE" && mkdir -p "$STAGE"
  python3 - "$STAGE" "$DATA/intake/intake.db" "$GOAT" <<'EOF'
import os, sqlite3, sys
stage = sys.argv[1]
for src in sys.argv[2:]:
    if os.path.exists(src):
        dst = os.path.join(stage, os.path.basename(os.path.dirname(src)) + "-" + os.path.basename(src))
        with sqlite3.connect(src) as s, sqlite3.connect(dst) as d:
            s.backup(d)
EOF
}

case "${1:-backup}" in
  backup)
    restic cat config >/dev/null 2>&1 || restic init
    snapshot_dbs
    restic backup --tag llmbox --exclude "$DATA/intake/intake.db*" --exclude "$DATA/intake/inbox" \
      "$STAGE" "$DATA/intake" "$DATA/results/community"
    restic forget --tag llmbox --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --prune
    ;;
  check)
    T=$(mktemp -d)
    trap 'rm -rf "$T"' EXIT
    restic restore latest --tag llmbox --target "$T" >/dev/null
    found=0
    for db in $(find "$T" -name "*.db" -o -name "*.sqlite3"); do
      python3 -c "import sqlite3, sys; r = sqlite3.connect(sys.argv[1]).execute('PRAGMA integrity_check').fetchone()[0]; print(sys.argv[1].rsplit('/', 1)[-1], r); sys.exit(r != 'ok')" "$db"
      found=$((found + 1))
    done
    [ -f "$(find "$T" -path "*intake/salt" | head -1)" ] || { echo "the salt is missing from the backup"; exit 1; }
    echo "restored the latest backup: $found database(s) whole, the salt there ($(du -sh "$T" | cut -f1))"
    ;;
  *) echo "usage: backup.sh [backup|check]" >&2; exit 2 ;;
esac
