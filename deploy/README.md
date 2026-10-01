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
     curl -fsSL https://raw.githubusercontent.com/iaroslav-ternovyi/llmbox/main/deploy/vps-setup.sh | bash -s -- https://github.com/iaroslav-ternovyi/llmbox "$(cat your_key.pub)"
     ```
     The script prints the intake address `https://<ip-dashed>.sslip.io`. Caddy gets it a certificate, so no domain is needed.
   - Put the Cloudflare secrets in `/etc/llmbox.env` (root only), then run `systemctl restart llmbox-intake`:
     ```
     CLOUDFLARE_API_TOKEN=...
     CLOUDFLARE_ACCOUNT_ID=...
     ```
3. **GitHub sign-in** (`llmbox login` in a terminal, SIGN IN on the site)
   - At github.com → Settings → Developer settings → OAuth Apps → New OAuth App, fill in:
     - name `llmbox`;
     - homepage `https://llmbox.pages.dev`;
     - callback URL `https://<ip-dashed>.sslip.io/api/v1/login/web/callback` (the site's sign-in comes back there).
   - Tick **Enable Device Flow** and register the app.
   - Copy the **Client ID** into `account.GITHUB_CLIENT_ID` (it is public: the terminal's device flow needs no secret).
   - Generate a **client secret** for the site's sign-in and put both in `/etc/llmbox.env` on the server, never in the repo:
     ```
     LLMBOX_GITHUB_CLIENT_ID=...
     LLMBOX_GITHUB_SECRET=...
     LLMBOX_SERVER=https://<ip-dashed>.sslip.io
     ```
     `LLMBOX_SERVER` also tells the site build where the account page calls the API.
4. **The author's machine**
   - Write `llmbox@<server ip>` to `~/.llmbox/vps`.
   - Run `deploy/sync.sh` once. The server gets the reference results and publishes the first site.
   - From then on, `~/.llmbox/bin/site-on-done.sh` runs `deploy/sync.sh` after every finished benchmark job.

## Releasing to PyPI

The package is `llmbox-bench` on PyPI (the command stays `llmbox`). Publishing uses Trusted Publishing, so no token is stored:

1. Once, on pypi.org (your account): Publishing → add a pending publisher with project `llmbox-bench`, owner
   `iaroslav-ternovyi`, repository `llmbox`, workflow `release.yml`, environment `pypi`.
2. Bump `version` in `pyproject.toml`, then `git tag v0.1.0 && git push --tags`. The release workflow builds the
   package, checks that its suite is a released one, and publishes it.
3. After the first release only: switch `llmbox update` (`llmbox/cli.py`) and `install.sh` to `pip install llmbox-bench`
   with the repository as the fallback. Until then they install from GitHub, because taking PyPI first before the name
   is ours would install whoever registers it.

## What goes where

- **Sent to the server:** reference results (box and cloud), recipes, calibration banks, model shapes, thinking traces
  (for the loop flags), the watch feed, and the reference box's hardware profile. The profile is sent without its ssh
  address or endpoints: the server never reaches the box.
- **Kept on the server:** people's accepted measurements (`~/.llmbox/results/community`) and the intake log
  (`~/.llmbox/intake`). `sync.sh` copies the measurements back so the local site shows them too.
- **Secrets:** the Cloudflare token lives only in `/etc/llmbox.env`. There is none in the repo or in these scripts.
