#!/usr/bin/env bash
# One-time setup of the llmbox server on a fresh Ubuntu 24.04 VPS (Hetzner CX23 or alike), as root:
#   curl -fsSL https://raw.githubusercontent.com/iaroslav-ternovyi/llmbox/main/deploy/vps-setup.sh | bash -s -- https://github.com/iaroslav-ternovyi/llmbox [<ssh-pubkey>]
# It creates the user llmbox, installs Python 3.12, Caddy (HTTPS), bubblewrap (the sandbox) and Node 22 (wrangler), clones llmbox, and starts
#   llmbox-intake.service   the intake (llmbox serve) on 127.0.0.1:8767; accepted submissions rebuild and publish the site
#   caddy                   https://<this ip, dashed>.sslip.io -> /api/* of the intake (a certificate without a domain)
# Secrets (Cloudflare token) go in /etc/llmbox.env by hand afterwards; nothing secret is in this script or the repo.
# Safe to re-run: every step checks what is there.
set -euo pipefail
REPO="${1:?usage: vps-setup.sh <repo-url> [<ssh public key for the llmbox user>]}"
PUBKEY="${2:-}"

apt-get update -q
apt-get install -y -q python3 python3-venv git rsync ufw bubblewrap apparmor debian-keyring debian-archive-keyring apt-transport-https curl gnupg
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
"
touch /etc/llmbox.env && chmod 600 /etc/llmbox.env   # CLOUDFLARE_API_TOKEN=..., CLOUDFLARE_ACCOUNT_ID=... (by hand)

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
EOF
install -m 644 /home/llmbox/llmbox/deploy/llmbox-intake.service /etc/systemd/system/llmbox-intake.service
systemctl daemon-reload
systemctl enable --now llmbox-intake caddy
systemctl reload caddy

ufw allow 22/tcp >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ufw --force enable >/dev/null
echo "intake: https://$HOST/api/v1/health"
echo "next: put CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID in /etc/llmbox.env, then systemctl restart llmbox-intake"
