#!/bin/bash
# Brave Frontier private server - set up (or repair) the server on the DigitalOcean Droplet.
# Run as root from the Droplet console:   bash /opt/bfserver/setup-droplet.sh
# Safe to run again: it never deletes saves, art or settings.
set -e
DIR=/opt/bfserver
HOST=bravefmint.com
REPO=git@github.com:LucyFirst/bfservermint.git
cd "$DIR"

echo "== 1/7 Installing Python, its encryption library and git"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-cryptography git >/dev/null

echo "== 2/7 Server settings for $HOST (config.json)"
[ -f config.json ] || cp config.example.json config.json
python3 - <<PY
import json
p='$DIR/config.json'
c=json.load(open(p))
c['host']='$HOST'; c['port']=443; c['http_port']=80
c['admin_anywhere']=True           # admin page reachable from anywhere (password protected)
json.dump(c,open(p,'w'),indent=2)
print('   host =',c['host'])
PY

echo "== 3/7 Firewall: SSH, game (443) and content downloads (80)"
if command -v ufw >/dev/null; then
  ufw allow 22/tcp >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
  ufw --force enable >/dev/null
fi

echo "== 4/7 Run the server as a service (starts on boot, restarts if it stops)"
cat > /etc/systemd/system/bfserver.service <<UNIT
[Unit]
Description=Brave Frontier private server
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$DIR
ExecStart=/usr/bin/python3 $DIR/server.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable bfserver >/dev/null

echo "== 5/7 Daily backup of saves at 04:00 (to /opt/bf-backups, never deleted automatically)"
mkdir -p /opt/bf-backups
cat > /etc/cron.d/bfserver-backup <<CRON
0 4 * * * root tar -czf /opt/bf-backups/saves-\$(date +\%Y-\%m-\%d).tgz -C $DIR saves
CRON

echo "== 6/7 Link to the GitHub repository (code updates come from there)"
KEY=/root/.ssh/bfserver_deploy
mkdir -p /root/.ssh && chmod 700 /root/.ssh
[ -f $KEY ] || ssh-keygen -q -t ed25519 -N "" -C "bfserver droplet" -f $KEY
grep -q "bfserver_deploy" /root/.ssh/config 2>/dev/null || cat >> /root/.ssh/config <<SSHCFG
Host github.com
  IdentityFile $KEY
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
SSHCFG
until git ls-remote "$REPO" >/dev/null 2>&1; do
  echo
  echo "   GitHub does not know this server yet. Add this key to the repository:"
  echo "   GitHub > LucyFirst/bfservermint > Settings > Deploy keys > Add deploy key"
  echo "   Title: Droplet    Key: (the whole line below)    leave 'Allow write access' OFF"
  echo
  cat $KEY.pub
  echo
  read -p "   Press Enter once the key is added... " _
done
if [ ! -d .git ]; then
  git init -q -b main
  git remote add origin "$REPO"
fi
git fetch -q origin
git checkout -q -f -B main origin/main      # code from GitHub; saves, art and settings are untouched
cp -f update.sh /usr/local/bin/bf-update && chmod +x /usr/local/bin/bf-update
echo "   linked - code version $(git log -1 --format='%h  %s')"

echo "== 7/7 Starting the server"
systemctl restart bfserver
sleep 4
if systemctl is-active --quiet bfserver; then
  echo
  echo "Done. The server is running on https://$HOST"
  echo "Admin page:   https://$HOST/admin"
  echo "Live log:     journalctl -u bfserver -f      (Ctrl+C to stop watching)"
  echo "Get updates:  bf-update"
else
  echo
  echo "The server did not start. Last lines of its log:"
  journalctl -u bfserver -n 30 --no-pager
fi
