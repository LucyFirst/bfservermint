"""
Player highlights for Discord: big summons, rare captures, trial clears, areas finished, top
evolutions, level milestones and new summoners - posted to a Discord channel through a webhook.

Which channel: "discord_highlights_webhook" in config.json (a separate players' channel), or the
server's "discord_webhook" when that is not set. Turn highlights off with "highlights": false.
    python3 tools/set_config.py discord_highlights_webhook <link>

What counts (config.json, all optional):
    "highlight_summon_stars": 5      summons of this rarity or more
    "highlight_drop_stars": 4        units captured in quests of this rarity or more (never materials:
                                     crystals, idols, frogs, imps, golems and other evolution/fusion units)
    "highlight_evo_stars": 5         evolutions into this rarity or more
    "highlight_level_every": 10      player level milestones (every 10 levels)
    "highlight_skip": ["userid"]     accounts never posted (e.g. a test account)
    "highlight_no_firsts": ["userid"] accounts that never take a "first on the server" (their clears
                                     are still posted, without the "first"). The account named ADMIN
                                     never takes one either.

Several things happening close together go out as one message (at most one post every 15 s).
Nothing here can break the game: every hook catches its own errors.
"""
import json
import os
import re
import threading
import time
import urllib.request

SEND_EVERY = 15
COLOR = 0x9b59b6


def _esc(s):
    s = re.sub(r'<[^>]+>', '', str(s or ''))          # the game's <color=...> tags
    return re.sub(r'([*_~`|>\\])', r'\\\1', s).strip() or 'Summoner'


class Highlights:
    def __init__(self, srv):
        self.S = srv
        cfg = srv.CFG
        self.url = str(cfg.get('discord_highlights_webhook') or cfg.get('discord_webhook') or '').strip()
        if cfg.get('highlights') is False:
            self.url = ''
        self.summon_min = int(cfg.get('highlight_summon_stars', 5))
        self.drop_min = int(cfg.get('highlight_drop_stars', 4))
        self.evo_min = int(cfg.get('highlight_evo_stars', 5))
        self.level_every = int(cfg.get('highlight_level_every', 10))
        self.skip = {str(x) for x in cfg.get('highlight_skip') or []}
        self.no_firsts = {str(x) for x in cfg.get('highlight_no_firsts') or []}
        self.state_file = os.path.join(srv.SAVE_DIR, 'highlights_state.json')
        try:
            self.state = json.load(open(self.state_file))
        except Exception:
            self.state = {}
        if not self.state.get('_fix_admin_trial003'):    # ADMIN's Trial No. 003 clear was taken as the first
            if '2000002' in (self.state.get('trial') or []):
                self.state['trial'].remove('2000002')
            self.state['_fix_admin_trial003'] = 1
            self._save()
        self.lines = []
        self.lock = threading.Lock()
        if self.url:
            threading.Thread(target=self._sender, daemon=True).start()

    # ------------------------------------------------------------------ plumbing
    def _save(self):
        try:
            tmp = self.state_file + '.tmp'
            json.dump(self.state, open(tmp, 'w'))
            os.replace(tmp, self.state_file)
        except Exception:
            pass

    def _who(self, pl):
        return f"**{_esc(pl.get('handle'))}**"

    def _ok(self, pl):
        return bool(self.url) and pl is not None and str(pl.get('user_id')) not in self.skip

    def _add(self, line):
        with self.lock:
            self.lines.append(line)

    def _can_first(self, pl):
        return (str(pl.get('user_id')) not in self.no_firsts
                and str(pl.get('handle') or '').strip().upper() != 'ADMIN')

    def _server_first(self, kind, key, pl=None):
        """True the first time anyone on the server does this (remembered across restarts). Accounts
        that can't take firsts (ADMIN) never use one up."""
        if pl is not None and not self._can_first(pl):
            return False
        seen = self.state.setdefault(kind, [])
        if str(key) in seen:
            return False
        seen.append(str(key))
        self._save()
        return True

    def _sender(self):
        while True:
            time.sleep(SEND_EVERY)
            with self.lock:
                lines, self.lines = self.lines[:20], self.lines[20:]
            if not lines:
                continue
            body = json.dumps({'username': 'BF Mint', 'embeds': [{
                'description': '\n'.join(lines)[:3900], 'color': COLOR,
                'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}]}).encode()
            try:
                req = urllib.request.Request(self.url, data=body, headers={
                    'Content-Type': 'application/json', 'User-Agent': 'BFServer (bravefmint, 1.0)'})
                urllib.request.urlopen(req, timeout=15).read()
            except Exception as e:
                self.S.log.warning('highlights: could not post (%s)', e)

    def _unit(self, uid):
        m = self.S.unit_mst().get(str(uid)) or {}
        return m.get('utP1c0CD') or str(uid), int(m.get('7ofj5xa1') or 0)

    # ------------------------------------------------------------------ events
    def summon(self, pl, uid):
        if not self._ok(pl):
            return
        name, stars = self._unit(uid)
        if stars >= self.summon_min:
            self._add(f"🌟 {self._who(pl)} summoned **{_esc(name)}** ({stars}★)!")

    def drops(self, pl, uids, mid):
        if not self._ok(pl):
            return
        quest = (self.S.MISSIONS.get(str(mid)) or {}).get('name', '')
        for uid in uids:
            if str((self.S.unit_mst().get(str(uid)) or {}).get('xP9oi5Z2', '0')) != '0':
                continue        # materials (crystals, idols, frogs, imps, golems...) are never highlighted
            name, stars = self._unit(uid)
            if stars >= self.drop_min:
                first = ' — a first for the server!' if self._server_first('capture', uid, pl) else ''
                self._add(f"🎁 {self._who(pl)} captured **{_esc(name)}** ({stars}★)"
                          f"{f' in {_esc(quest)}' if quest else ''}{first}")

    def trial(self, pl, mid):
        if not self._ok(pl):
            return
        name = (self.S.MISSIONS.get(str(mid)) or {}).get('name', mid)
        first = self._server_first('trial', mid, pl)
        self._add(f"🏆 {self._who(pl)} cleared the trial **{_esc(name)}**"
                  + (' — first on the server!' if first else '!'))

    def area(self, pl, area_id):
        if not self._ok(pl):
            return
        try:
            self.S.mission_place('0')
            name = (self.S._GEO.get('area', {}).get(str(area_id)) or {}).get('name') or f'area {area_id}'
        except Exception:
            name = f'area {area_id}'
        first = self._server_first('area', area_id, pl)
        self._add(f"🗺️ {self._who(pl)} finished **{_esc(name)}**"
                  + (' — first on the server!' if first else '!'))

    def evo(self, pl, old, new):
        if not self._ok(pl):
            return
        name, stars = self._unit(new)
        if stars >= self.evo_min:
            self._add(f"✨ {self._who(pl)} evolved {_esc(self._unit(old)[0])} into **{_esc(name)}** ({stars}★)!")

    def level(self, pl, before, after):
        if not self._ok(pl) or self.level_every <= 0:
            return
        for lv in range(before + 1, after + 1):
            if lv % self.level_every == 0:
                self._add(f"⬆️ {self._who(pl)} reached player level **{lv}**!")

    def joined(self, total):
        if self.url:
            self._add(f"👋 A new summoner has joined! ({total} summoners on the server)")
