"""
Leaderboards for Discord: one message in a channel that the server keeps editing with the current
top players, so the channel always shows a live board instead of a stream of posts.

Which channel: "discord_leaderboard_webhook" in config.json (make a webhook on its own channel, e.g.
#leaderboards). Without it the boards are off.
    python3 tools/set_config.py discord_leaderboard_webhook <link>

Boards (top 5 each):
    Player Level            level, then exp
    Zel                     zel in the wallet
    Quests (last 24 h)      quests cleared in the last 24 hours (counted from when this went in)
    Unit Collection         different units ever owned (the unit list in the game; no materials)
    Quests Completed        different quests cleared at least once
    Units Summoned          summons made (counted from when this went in)

config.json, all optional:
    "leaderboard_every_min": 15     how often the boards are worked out (they are only edited when
                                    something changed)
    "leaderboard_top": 5            places shown per board
    "leaderboard_skip": ["userid"]  accounts never shown; "highlight_skip" accounts and the account
                                    named ADMIN are never shown either

The message id is kept in saves/leaderboard_state.json; if the message is deleted in Discord a new
one is posted. Nothing here can break the game: it runs on its own thread and catches its errors.
"""
import glob
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

COLOR = 0xf1c40f
DAY = 86400
MEDALS = ['🥇', '🥈', '🥉']


def _esc(s):
    s = re.sub(r'<[^>]+>', '', str(s or ''))
    return re.sub(r'([*_~`|>\\])', r'\\\1', s).strip() or 'Summoner'


def _n(n, word):
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def _num(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------- counters kept in the player saves
def note_clear(pl, now=None):
    """A quest was cleared: remember when (for "quests in the last 24 hours")."""
    now = int(now or time.time())
    pl['clears_24h'] = [t for t in (pl.get('clears_24h') or []) if now - int(t) < DAY] + [now]


def note_summon(pl, n=1):
    pl['summons_total'] = int(pl.get('summons_total') or 0) + n


class Leaderboards:
    def __init__(self, srv):
        self.S = srv
        cfg = srv.CFG
        self.url = str(cfg.get('discord_leaderboard_webhook') or '').strip()
        self.every = max(1, int(cfg.get('leaderboard_every_min', 15))) * 60
        self.top = max(1, min(25, int(cfg.get('leaderboard_top', 5))))
        self.skip = {str(x) for x in (cfg.get('leaderboard_skip') or []) + (cfg.get('highlight_skip') or [])}
        self.state_file = os.path.join(srv.SAVE_DIR, 'leaderboard_state.json')
        try:
            self.state = json.load(open(self.state_file))
        except Exception:
            self.state = {}
        if self.url:
            threading.Thread(target=self._loop, daemon=True).start()

    # ------------------------------------------------------------------ the numbers
    def _players(self):
        for f in glob.glob(os.path.join(self.S.SAVE_DIR, 'player_*.json')):
            try:
                pl = json.load(open(f, encoding='utf-8'))
            except Exception:
                continue
            handle = str(pl.get('handle') or '').strip()
            if not handle or handle.upper() == 'ADMIN' or str(pl.get('user_id')) in self.skip:
                continue
            yield pl

    def boards(self, now=None):
        now = int(now or time.time())
        units = self.S.unit_mst()
        rows = []
        for pl in self._players():
            st = pl.get('state') or {}
            t = self.S.team_row(st) or {}
            dic = st.get(self.S.G_DICT) or []
            rows.append({
                'name': _esc(pl.get('handle')),
                'level': _num(t.get('D9wXQI2V')), 'exp': _num(t.get('d96tuT2E')),
                'zel': _num((pl.get('wallet') or {}).get('zel', t.get('Najhr8m6'))),
                'day': sum(1 for x in pl.get('clears_24h') or [] if now - _num(x) < DAY),
                'units': len({r.get('pn16CNah') for r in dic if isinstance(r, dict)
                              and str((units.get(str(r.get('pn16CNah'))) or {}).get('xP9oi5Z2', '0')) == '0'}),
                'quests': len({r.get('j28VNcUW') for r in st.get('UT1SVg59') or [] if isinstance(r, dict)}),
                'summons': _num(pl.get('summons_total')),
            })
        spec = [('🏆 Player Level', 'level', lambda r: f"Lv {r['level']:,}", lambda r: (r['level'], r['exp'])),
                ('💰 Zel', 'zel', lambda r: f"{r['zel']:,} zel", None),
                ('⚔️ Quests Cleared (last 24 h)', 'day', lambda r: _n(r['day'], 'clear'), None),
                ('📖 Unit Collection', 'units', lambda r: _n(r['units'], 'unit'), None),
                ('🗺️ Quests Completed', 'quests', lambda r: _n(r['quests'], 'quest'), None),
                ('🎰 Units Summoned', 'summons', lambda r: _n(r['summons'], 'summon'), None)]
        out = []
        for title, key, show, sort in spec:
            ranked = sorted((r for r in rows if r[key] > 0), key=sort or (lambda r: r[key]), reverse=True)[:self.top]
            lines = [f"{MEDALS[i] if i < 3 else f'`{i + 1}.`'} **{r['name']}** — {show(r)}"
                     for i, r in enumerate(ranked)] or ['*nobody yet*']
            out.append((title, '\n'.join(lines)))
        return out

    # ------------------------------------------------------------------ Discord
    def _request(self, method, url, body):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method=method, headers={
            'Content-Type': 'application/json', 'User-Agent': 'BFServer (bravefmint, 1.0)'})
        with urllib.request.urlopen(req, timeout=15) as r:
            txt = r.read().decode() or '{}'
        return json.loads(txt)

    def _save(self):
        try:
            tmp = self.state_file + '.tmp'
            json.dump(self.state, open(tmp, 'w'))
            os.replace(tmp, self.state_file)
        except Exception:
            pass

    def update(self, force=False):
        boards = self.boards()
        sig = hashlib.md5(json.dumps(boards).encode()).hexdigest()
        if not force and sig == self.state.get('sig') and self.state.get('msg'):
            return False
        now = int(time.time())
        embeds = [{'title': t, 'description': d[:4000], 'color': COLOR} for t, d in boards]
        embeds[-1]['footer'] = {'text': 'Updates every few minutes · counts of clears in the last 24 h '
                                        'and of summons started when the boards went live'}
        embeds[-1]['timestamp'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))
        body = {'username': 'BF Mint', 'content': f'**Leaderboards** — updated <t:{now}:R>', 'embeds': embeds}
        msg = self.state.get('msg')
        if msg:
            try:
                self._request('PATCH', f'{self.url}/messages/{msg}', body)
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise
                msg = None                       # deleted in Discord: post a new one
        if not msg:
            sep = '&' if '?' in self.url else '?'
            msg = str(self._request('POST', f'{self.url}{sep}wait=true', body).get('id') or '')
        self.state.update(msg=msg, sig=sig, at=now)
        self._save()
        return True

    def _loop(self):
        time.sleep(30)                           # let the server finish starting
        while True:
            try:
                if self.update():
                    self.S.log.info('leaderboards: updated')
            except Exception as e:
                self.S.log.warning('leaderboards: could not update (%s)', e)
            time.sleep(self.every)
