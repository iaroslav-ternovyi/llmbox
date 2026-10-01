#!/usr/bin/env bash
# One-time setup of the llmbox server on a fresh Ubuntu 24.04 VPS (Hetzner CX23 or alike), as root:
#   curl -fsSL https://raw.githubusercontent.com/iaroslav-ternovyi/llmbox/main/deploy/vps-setup.sh | bash -s -- https://github.com/iaroslav-ternovyi/llmbox [<ssh-pubkey>]
# It creates the user llmbox, installs Python 3.12, Caddy (HTTPS), bubblewrap (the sandbox) and Node 22 (wrangler), clones llmbox, and starts
#   llmbox-intake.service   the intake (llmbox serve) on 127.0.0.1:8767; accepted submissions rebuild and publish the site
#   caddy                   https://<this ip, dashed>.sslip.io -> /api/* of the intake (a certificate without a domain)
#   goatcounter.service     the site's visit counts (no cookies, no ids) on 127.0.0.1:8081, https://stats.<that host>
# Secrets (Cloudflare token) go in /etc/llmbox.env by hand afterwards; nothing secret is in this script or the repo.
# Safe to re-run: every step checks what is there.
set -euo pipefail
REPO="${1:?usage: vps-setup.sh <repo-url> [<ssh public key for the llmbox user>]}"
PUBKEY="${2:-}"

apt-get update -q
apt-get install -y -q python3 python3-venv git rsync ufw bubblewrap apparmor debian-keyring debian-archive-keyring apt-transport-https curl gnupg restic
# Node 22+ for wrangler (the site upload); Ubuntu 24.04 ships 18
if ! node -e 'process.exit(parseInt(process.versions.node) >= 22 ? 0 : 1)' 2>/dev/null; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash - >/dev/null
  apt-get install -y -q nodejs
fi
if ! command -v caddy >/dev/null; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q && apt-get install -y -q caddy
fi
# Ubuntu 24.04 lets only profiled programs make user namespaces: allow bubblewrap (the re-grading sandbox)
if [ -d /etc/apparmor.d ] && [ ! -f /etc/apparmor.d/bwrap-llmbox ]; then
  printf 'abi <abi/4.0>,\ninclude <tunables/global>\nprofile bwrap-llmbox /usr/bin/bwrap flags=(unconfined) {\n  userns,\n}\n' > /etc/apparmor.d/bwrap-llmbox
  apparmor_parser -r /etc/apparmor.d/bwrap-llmbox || true
fi

id llmbox >/dev/null 2>&1 || useradd -m -s /bin/bash llmbox
if [ -n "$PUBKEY" ]; then   # the author's machine pushes results with rsync over ssh as this user
  install -d -m 700 -o llmbox -g llmbox /home/llmbox/.ssh
  grep -qF "$PUBKEY" /home/llmbox/.ssh/authorized_keys 2>/dev/null || echo "$PUBKEY" >> /home/llmbox/.ssh/authorized_keys
  chown llmbox:llmbox /home/llmbox/.ssh/authorized_keys && chmod 600 /home/llmbox/.ssh/authorized_keys
fi
sudo -u llmbox bash -c "
  set -e
  [ -d ~/llmbox ] || git clone -q '$REPO' ~/llmbox
  cd ~/llmbox && git pull -q --ff-only
  [ -d ~/venv ] || python3 -m venv ~/venv
  ~/venv/bin/pip install -q -e ~/llmbox
  mkdir -p ~/.llmbox ~/site
  # the code of every released suite version: a person's run is re-graded with the tasks it ran
  for v in \$(~/venv/bin/python -c 'from llmbox import irt; print(\" \".join(sorted(set(irt.RELEASES.values()))))'); do
    [ -d ~/.llmbox/snapshots/suite-v\$v ] || ~/venv/bin/llmbox snapshot suite-v\$v --no-validate >/dev/null
  done
"
touch /etc/llmbox.env && chmod 600 /etc/llmbox.env   # CLOUDFLARE_API_TOKEN=..., CLOUDFLARE_ACCOUNT_ID=... (by hand)

# GoatCounter: a pinned release, checked against the sha256 GitHub publishes for it
GC_VER=v2.7.0
GC_SHA=98d221cb9c8ef2bf76d8daa9cca647839f8d8b0bb5bc7400ff9337c5da834511
if ! /usr/local/bin/goatcounter version 2>/dev/null | grep -q "${GC_VER#v}"; then
  curl -fsSL -o /tmp/goatcounter.gz "https://github.com/arp242/goatcounter/releases/download/$GC_VER/goatcounter-$GC_VER-linux-amd64.gz"
  echo "$GC_SHA  /tmp/goatcounter.gz" | sha256sum -c - >/dev/null
  gunzip -c /tmp/goatcounter.gz > /usr/local/bin/goatcounter && chmod 755 /usr/local/bin/goatcounter && rm -f /tmp/goatcounter.gz
fi
id goatcounter >/dev/null 2>&1 || useradd --system --home-dir /var/lib/goatcounter --create-home --shell /usr/sbin/nologin goatcounter
install -m 644 /home/llmbox/llmbox/deploy/goatcounter.service /etc/systemd/system/goatcounter.service

IP=$(curl -fsS4 https://api.ipify.org)
HOST="${IP//./-}.sslip.io"
cat > /etc/caddy/Caddyfile <<EOF
$HOST {
    encode gzip
    handle /api/* {
        reverse_proxy 127.0.0.1:8767
    }
    handle {
        respond "llmbox intake: POST /api/v1/runs (llmbox submit). The site is on Cloudflare Pages." 200
    }
}
stats.$HOST {
    encode gzip
    reverse_proxy 127.0.0.1:8081
}
EOF
install -m 644 /home/llmbox/llmbox/deploy/llmbox-intake.service /etc/systemd/system/llmbox-intake.service
install -m 644 /home/llmbox/llmbox/deploy/llmbox-backup.service /home/llmbox/llmbox/deploy/llmbox-backup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now llmbox-intake goatcounter caddy llmbox-backup.timer   # the backup skips until /etc/llmbox-backup.env exists
systemctl reload caddy

ufw allow 22/tcp >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ufw --force enable >/dev/null
echo "intake: https://$HOST/api/v1/health"
echo "stats:  https://stats.$HOST"
echo "next:"
echo "  1. CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID in /etc/llmbox.env"
echo "  2. the counter's site and your login (asks for a password):"
echo "       sudo -u goatcounter goatcounter db create site -db sqlite+/var/lib/goatcounter/db.sqlite3 -createdb -vhost stats.$HOST -user.email <you>"
echo "     then in https://stats.$HOST settings: public dashboard on, data retention 760 days, an API token with 'count'"
echo "  3. LLMBOX_STATS=https://stats.$HOST in /etc/llmbox.env; in the Pages project: GOATCOUNTER_URL=https://stats.$HOST and"
echo "     the secret GOATCOUNTER_TOKEN (npx wrangler pages secret put GOATCOUNTER_TOKEN --project-name llmbox)"
echo "  4. systemctl restart llmbox-intake"
echo "  5. backups: RESTIC_REPOSITORY and RESTIC_PASSWORD in /etc/llmbox-backup.env (chmod 600), then"
echo "       /home/llmbox/llmbox/deploy/backup.sh && /home/llmbox/llmbox/deploy/backup.sh check   (a restore, checked)"
