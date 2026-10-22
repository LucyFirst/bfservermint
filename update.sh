#!/bin/bash
# Get the newest server code from GitHub and restart the server.  On the Droplet just type:  bf-update
set -e
cd /opt/bfserver
before=$(git rev-parse --short HEAD)
git pull -q --ff-only origin main
after=$(git rev-parse --short HEAD)
cp -f update.sh /usr/local/bin/bf-update
if [ "$before" = "$after" ]; then
  echo "Already up to date ($after)."
else
  echo "Updated $before -> $after:"
  git log --format='  %h  %s' "$before..$after"
  systemctl restart bfserver
  sleep 3
  systemctl is-active --quiet bfserver && echo "Server restarted." || journalctl -u bfserver -n 30 --no-pager
fi
