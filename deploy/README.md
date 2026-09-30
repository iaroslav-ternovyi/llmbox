# Running the public llmbox

Three parts (docs/roadmap.md §5, stage 2):

| Part | Where | What |
|---|---|---|
| Site | Cloudflare Pages (free, CDN) | the static site, rebuilt and uploaded by the server with `wrangler pages deploy` |
| Intake | a small VPS (Hetzner CX23, ~€6 a month) | `llmbox serve`: takes `llmbox submit`, checks, files people's measurements, rebuilds the site |
| Reference runs | the reference box + the author's machine | benchmark runs, then `deploy/sync.sh` sends the results to the server |

## One-time setup

1. **Cloudflare**
   - Make an account and create a Pages project named `llmbox` (Direct Upload). The site will be at `https://llmbox.pages.dev`,
     or at the next free name if that one is taken.
   - Create an API token with the "Cloudflare Pages: Edit" permission, and note the account id.
2. **Server**
   - Create a Hetzner CX23 with Ubuntu 24.04 and your ssh key. Then, as root on it:
     ```bash
     curl -fsSL https://raw.githubusercontent.com/<owner>/llmbox/main/deploy/vps-setup.sh | bash -s -- https://github.com/<owner>/llmbox "$(cat your_key.pub)"
     ```
     The script prints the intake address `https://<ip-dashed>.sslip.io`. Caddy gets it a certificate, so no domain is needed.
   - Put the Cloudflare secrets in `/etc/llmbox.env` (root only), then run `systemctl restart llmbox-intake`:
     ```
     CLOUDFLARE_API_TOKEN=...
     CLOUDFLARE_ACCOUNT_ID=...
     ```
3. **The author's machine**
   - Write `llmbox@<server ip>` to `~/.llmbox/vps`.
   - Run `deploy/sync.sh` once. The server gets the reference results and publishes the first site.
   - From then on, `~/.llmbox/bin/site-on-done.sh` runs `deploy/sync.sh` after every finished benchmark job.

## What goes where

- **Sent to the server:** reference results (box and cloud), recipes, calibration banks, model shapes, thinking traces
  (for the loop flags), the watch feed, and the reference box's hardware profile. The profile is sent without its ssh
  address or endpoints: the server never reaches the box.
- **Kept on the server:** people's accepted measurements (`~/.llmbox/results/community`) and the intake log
  (`~/.llmbox/intake`). `sync.sh` copies the measurements back so the local site shows them too.
- **Secrets:** the Cloudflare token lives only in `/etc/llmbox.env`. There is none in the repo or in these scripts.
