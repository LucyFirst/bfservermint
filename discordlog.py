"""
Posts what the server does to a Discord channel through a webhook.
Set "discord_webhook" in config.json (python3 tools/set_config.py discord_webhook <link>).
Nothing is posted when it is not set. The link stays on the server - never in GitHub.

Posts: server started (and which code version, with what changed since the last start), a new
app in the app folder, and server errors (each kind at most once every 30 minutes).
"""
import json
import logging
import os
import subprocess
import threading
import time
import urllib.request

COLOR = {'start': 0x2ecc71, 'update': 0x3498db, 'app': 0xf1c40f, 'error': 0xe74c3c}


class Discord:
    def __init__(self, srv):
        self.S = srv
        self.url = str(srv.CFG.get('discord_webhook') or '').strip()
        self.state_file = os.path.join(srv.SAVE_DIR, 'discord_state.json')
        try:
            self.state = json.load(open(self.state_file))
        except Exception:
            self.state = {}
        self.last_err = {}

    def save(self):
        tmp = self.state_file + '.tmp'
        json.dump(self.state, open(tmp, 'w'))
        os.replace(tmp, self.state_file)

    def post(self, title, text, kind='update'):
        if not self.url:
            return
        body = json.dumps({'username': 'BF Mint', 'embeds': [{
            'title': title[:250], 'description': text[:3900], 'color': COLOR.get(kind, 0x95a5a6),
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}]}).encode()

        def send():
            try:
                req = urllib.request.Request(self.url, data=body, headers={
                    'Content-Type': 'application/json', 'User-Agent': 'BFServer (bravefmint, 1.0)'})
                urllib.request.urlopen(req, timeout=15).read()
            except Exception as e:
                self.S.log.warning('discord: could not post (%s)', e)
        threading.Thread(target=send, daemon=True).start()

    # ------------------------------------------------------------------ events
    def git(self, *args):
        try:
            return subprocess.run(['git', *args], cwd=self.S.HERE, capture_output=True, text=True,
                                  timeout=10).stdout.strip()
        except Exception:
            return ''

    def started(self):
        head = self.git('rev-parse', '--short', 'HEAD')
        prev = self.state.get('version')
        if head and prev and head != prev:
            changes = self.git('log', '--format=• %s', f'{prev}..{head}') or '(no details)'
            self.post(f'Server updated ({prev} → {head})', changes, 'update')
        else:
            self.post('Server started', f'Code version {head or "?"} on {self.S.PUBLIC}', 'start')
        if head:
            self.state['version'] = head
            self.save()
        threading.Thread(target=self.watch_apps, daemon=True).start()

    def watch_apps(self):
        while True:
            try:
                best = self.S.app_update().latest()
                if best and best[0] != self.state.get('app'):
                    if self.state.get('app'):           # not on the very first look
                        self.post(f'New app: {best[1]}',
                                  f'Players on older versions get the update prompt.\n'
                                  f'Download: http://{self.S.HOST}/app/{best[1]}', 'app')
                    self.state['app'] = best[0]
                    self.save()
            except Exception:
                pass
            time.sleep(60)

    def error(self, msg):
        key = msg.split('\n')[0][:120]
        now = time.time()
        if now - self.last_err.get(key, 0) < 1800:
            return
        self.last_err[key] = now
        self.post('Server error', msg[:1800], 'error')


class ErrorHandler(logging.Handler):
    """Sends ERROR log lines (with their traceback) to Discord."""
    def __init__(self, d):
        super().__init__(logging.ERROR)
        self.d = d

    def emit(self, record):
        try:
            text = record.getMessage()
            if record.exc_info:
                import traceback
                text += '\n```' + ''.join(traceback.format_exception(*record.exc_info))[-1500:] + '```'
            self.d.error(text)
        except Exception:
            pass
