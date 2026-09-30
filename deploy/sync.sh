#!/usr/bin/env bash
# From the author's machine (where the reference runs are): bring people's accepted measurements home, send the
# reference results to the server, and have it rebuild and publish the site. Run after each finished job
# (~/.llmbox/bin/site-on-done.sh calls it when LLMBOX_VPS is set) or by hand:
#   LLMBOX_VPS=llmbox@<ip> deploy/sync.sh
set -euo pipefail
VPS="${LLMBOX_VPS:-$(cat "$HOME/.llmbox/vps" 2>/dev/null)}"; [ -n "$VPS" ] || { echo "set LLMBOX_VPS=llmbox@<server> or write it to ~/.llmbox/vps"; exit 1; }
L="$HOME/.llmbox"
mkdir -p "$L/results/community"
# people's measurements: the server's are the truth, this machine keeps a copy for its local site
rsync -a "$VPS:.llmbox/results/community/" "$L/results/community/"
# what the site is built from; the server never writes these (no --delete on results/: community lives there)
ssh "$VPS" 'mkdir -p ~/.llmbox/results ~/.llmbox/recipes ~/.llmbox/hosts'
for d in results/box results/cloud; do rsync -a --delete "$L/$d/" "$VPS:.llmbox/$d/"; done
for d in recipes/box irt watch shapes traces cache tuned hf epoch; do
  [ -d "$L/$d" ] && rsync -a --delete "$L/$d/" "$VPS:.llmbox/$d/"
done
for f in candidates.json queue.db; do [ -f "$L/$f" ] && rsync -a "$L/$f" "$VPS:.llmbox/$f"; done
# the reference box's profile without its ssh address and endpoints (the server never reaches the box)
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); [d.pop(k, None) for k in ('ssh','endpoint','agent_endpoint')]; d.get('hw',{}).pop('hostname',None); print(json.dumps(d))" \
  "$L/hosts/box.json" | ssh "$VPS" 'cat > ~/.llmbox/hosts/box.json'
ssh "$VPS" 'cd ~/llmbox && git pull -q --ff-only && ~/venv/bin/llmbox serve --ingest-once --rebuild ~/site --deploy "npx --yes wrangler pages deploy {site} --project-name llmbox --branch main --commit-dirty=true" --force-rebuild'
