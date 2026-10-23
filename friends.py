"""
Friends and quest helpers on the private server - only players on this server (no recorded
bfrevive players or bots), plus generated helpers so a quest never has an empty helper list.

Friend statuses the game uses (tojMy68W.96Nxs2WQ): 1 friend, 2 request sent, 3 request received,
4 search result. Helpers (xZH6EIQ7): q9Arz8Jv 1 = friend; J3stQ7jd = honor points for using them
(50 for a friend, 10 otherwise, as in bfrevive).
"""
import json
import os
import random
import time

MAX_FRIENDS = 50
HELPERS = 12


OWN_PREFIX = 'MYUNIT'           # helper user ID for your own unit (read by the v7 app)


def _i(x, d=0):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return d


class Friends:
    def __init__(self, srv):
        self.S = srv
        self.file = os.path.join(srv.SAVE_DIR, 'friends.json')
        self.links = json.load(open(self.file)) if os.path.exists(self.file) else {}
        self.friend_tmpl = self.helper_tmpl = None
        self.limits = [{'4vamo28W': str(MAX_FRIENDS), 'KgbM81wd': '10'}]
        for a in sorted(srv.T['action'], key=lambda a: -a['n']):
            b = srv.BLOBS[a['resp']]
            if not isinstance(b, dict):
                continue
            if self.friend_tmpl is None and b.get('tojMy68W'):
                self.friend_tmpl = dict(b['tojMy68W'][0])
            if self.helper_tmpl is None and b.get('xZH6EIQ7'):
                self.helper_tmpl = dict(b['xZH6EIQ7'][0])

    def save(self):
        tmp = self.file + '.tmp'
        json.dump(self.links, open(tmp, 'w'), indent=1)
        os.replace(tmp, self.file)

    # ------------------------------------------------------------------ people
    def players(self):
        return {p['user_id']: p for p in self.S.all_players() if p.get('user_id')}

    def status(self, me, other):
        return (self.links.get(me) or {}).get(other)

    def set(self, a, b, st_a, st_b):
        for x, y, st in ((a, b, st_a), (b, a, st_b)):
            d = self.links.setdefault(x, {})
            if st:
                d[y] = st
            else:
                d.pop(y, None)
        self.save()

    def leader(self, p):
        """(unit row, level) a player lends as a helper: the leader of their quest squad."""
        sq = self.S.active_squad(p)
        rows = [r for r in self.S._rows(p['state'].get('dX7S2Lc1')) if isinstance(r, dict)]
        rows.sort(key=lambda r: (str(r.get('U9ABSYEp')) != '1', str(r.get('zsiAn9P1')) != sq,
                                 str(r.get('gr48vsdJ')) != '0'))
        for r in rows:
            u = p['units'].get(r.get('edy7fq3L'))
            if u:
                return u
        return next(iter(p['units'].values()), {})

    def _unit_fields(self, u):
        keys = ('pn16CNah', 'e7DK0FQT', '67CApcti', 'q08xLEsy', 'PWXu25cg', 'cuIWp89g', 'TokWs1B3', 'RT4CtH5d',
                't4m1RH6Y', 'GcMD0hy6', 'e6mY8Z0k', 'C1HZr3pb', 'X6jf8DUw', 'nj9Lw7mV', '3NbeC8AB', 'nBTx56W9',
                'iEFZ6H19', 'RQ5GnFE2', 'iNy0ZU5M', 'Ge8Yo32T', 'mZA7fH2v')     # last two: the spheres
        out = {k: str(u.get(k, '0') if u.get(k) not in (None, '', '-1') else '0') for k in keys}
        out['4A6LzBxr'] = str(u.get('D9wXQI2V', '1'))
        if not out['iNy0ZU5M'] or out['iNy0ZU5M'] == '0':
            out['iNy0ZU5M'] = str((self.S.unit_mst().get(out['pn16CNah']) or {}).get('iNy0ZU5M', '1'))
        return out

    def _person(self, p):
        t = self.S.team_row(p['state']) or {}
        ui = (p['state'].get(self.S.G_USERINFO) or [{}])[0]
        try:
            seen = int(os.path.getmtime(self.S.player_path(p['user_id'])))
        except OSError:
            seen = int(time.time())
        return {self.S.F_USERID: p['user_id'], 'B5JQyV8j': self.S.shown_name(p), '2Fh3J7ng': str(t.get('D9wXQI2V', '1')),
                '98WfKiyA': str(p.get('friend_id', '')), 'bM7RLu5K': str(ui.get('bM7RLu5K', '') or ''),
                '0CAQ6wUe': str(max(0, int(time.time()) - seen)),      # seconds since last seen (not a date)
                's2WnRw9N': str(t.get('s2WnRw9N') or '100,200,300')}    # the gifts they want

    def friend_row(self, me, p, st):
        row = dict(self.friend_tmpl or {})
        row.update(self._unit_fields(self.leader(p)))
        row.update(self._person(p))
        fav = p['user_id'] in (self.links.get(me) or {}).get('_fav', [])
        row.update({'96Nxs2WQ': str(st), '5JbjC3Pp': '1' if fav else '0', 'a1Jp3TVb': '0', '8DtoZdXE': '0',
                    'JmFn3g9t': '', 'U4pMNjy0': '0'})
        return row

    def helper_row(self, p, is_friend, n):
        row = dict(self.helper_tmpl or {})
        row.update(self._unit_fields(self.leader(p)))
        row.update(self._person(p))
        row.update({'q9Arz8Jv': '1' if is_friend else '0', 'J3stQ7jd': '50' if is_friend else '10', 'w28VsQ7h': '10',
                    'edy7fq3L': str(992000 + n), 'JmFn3g9t': '0'})
        return row

    def own_helper(self, pl):
        """Your own squad leader offered as a helper. The user ID 'MYUNIT<unit id>' tells the app (v7) to
        show and use that unit's real level, stats and spheres. Marked as a friend helper (q9Arz8Jv 1):
        its leader skill works as a Friend Skill and the game lists it with the friends, at the top.
        No honor points."""
        u = self.leader(pl)
        uuid = str(u.get(self.S.F_UNIT_ID) or '') if u else ''
        if not uuid:
            return None
        row = dict(self.helper_tmpl or {})
        row.update(self._unit_fields(u))
        row.update(self._person(pl))
        row.update({self.S.F_USERID: OWN_PREFIX + uuid, 'q9Arz8Jv': '1', 'J3stQ7jd': '0', 'w28VsQ7h': '0',
                    '0CAQ6wUe': '0',
                    'edy7fq3L': uuid, 'JmFn3g9t': '0'})
        return row

    def npc_helper(self, pl, n, used_names):
        """A generated helper, as strong as the player's own squad."""
        A = self.S.arena_engine()
        rar, lv = A.strength(pl)
        pool = A.unit_pool()
        r = max(1, min(max(pool), rar + random.choice([0, 0, 1])))
        while r not in pool and r > 1:
            r -= 1
        uid = random.choice(pool.get(r) or pool[min(pool)])
        u = A.unit_json(uid, int(max(1, lv) * random.uniform(0.9, 1.15)), 0)
        name = random.choice([x for x in A_NAMES() if x not in used_names] or A_NAMES())
        used_names.add(name)
        row = dict(self.helper_tmpl or {})
        row.update({self.S.F_USERID: f'npc{n:05d}', 'B5JQyV8j': name, '2Fh3J7ng': str(max(1, lv + random.randint(0, 20))),
                    '98WfKiyA': str(100000000 + n), 'bM7RLu5K': '', '0CAQ6wUe': str(random.randint(60, 6 * 3600)),
                    'pn16CNah': uid, '4A6LzBxr': u['P_LV'], 'e7DK0FQT': u['P_HP'], '67CApcti': u['P_ATK'],
                    'q08xLEsy': u['P_DEF'], 'PWXu25cg': u['P_HEL'], 'nj9Lw7mV': u['P_SKILL_ID'], '3NbeC8AB': u['P_SKILL_LV'],
                    'nBTx56W9': u['P_UNIT_TYPE_ID'], 'iEFZ6H19': '0', 'RQ5GnFE2': '0',
                    'iNy0ZU5M': str((self.S.unit_mst().get(uid) or {}).get('iNy0ZU5M', '1')),
                    'q9Arz8Jv': '0', 'J3stQ7jd': '10', 'w28VsQ7h': '10', 'edy7fq3L': str(992000 + n),
                    'Ge8Yo32T': '0', 'mZA7fH2v': '0', 'JmFn3g9t': '0'})
        for k in ('cuIWp89g', 'TokWs1B3', 'RT4CtH5d', 't4m1RH6Y', 'GcMD0hy6', 'e6mY8Z0k', 'C1HZr3pb', 'X6jf8DUw'):
            row[k] = '0'
        return row

    # ------------------------------------------------------------------ lists
    def own_friend(self, pl):
        """Your own squad leader in the friend list as a friend (status 1): trials only take helpers
        from friends, so this keeps one there even with no friends on the server. Same user ID as the
        own helper ('MYUNIT<unit id>'); befriending, unfriending and favourites do nothing for it."""
        u = self.leader(pl)
        uuid = str(u.get(self.S.F_UNIT_ID) or '') if u else ''
        if not uuid:
            return None
        row = dict(self.friend_tmpl or {})
        row.update(self._unit_fields(u))
        row.update(self._person(pl))
        row.update({self.S.F_USERID: OWN_PREFIX + uuid, '0CAQ6wUe': '0',
                    '96Nxs2WQ': '1', '5JbjC3Pp': '0', 'a1Jp3TVb': '0', '8DtoZdXE': '0',
                    'JmFn3g9t': '', 'U4pMNjy0': '0'})
        return row

    def friend_list(self, pl, extra=None):
        me = pl['user_id']
        ps = self.players()
        code = {'friend': 1, 'sent': 2, 'received': 3}
        rows = []
        own = self.own_friend(pl)
        if own:
            rows.append(own)
        for other, st in sorted((self.links.get(me) or {}).items(), key=lambda x: (x[1] != 'friend', x[0])):
            if other.startswith('_') or other not in ps:
                continue
            rows.append(self.friend_row(me, ps[other], code[st]))
        if extra is not None:
            rows.append(extra)
        return rows

    def get(self, pl, req):
        me = pl['user_id']
        ps = self.players()
        helpers, n = [], 0
        own = self.own_helper(pl)
        if own:
            helpers.append(own)
        mine = self.links.get(me) or {}
        friends = [o for o, st in mine.items() if st == 'friend' and o in ps]
        others = [o for o in ps if o != me and o not in friends]
        random.shuffle(others)
        for o in friends + others[:max(0, 8 - len(friends))]:
            helpers.append(self.helper_row(ps[o], o in friends, n))
            n += 1
        used = {h['B5JQyV8j'] for h in helpers}
        while len(helpers) < HELPERS:
            helpers.append(self.npc_helper(pl, n, used))
            n += 1
        pl['_helpers'] = {h[self.S.F_USERID]: _i(h['J3stQ7jd']) for h in helpers}
        return {'xZH6EIQ7': helpers, 'tojMy68W': self.friend_list(pl), '6e4b7sQt': self.limits}

    def _target(self, req):
        q = (req.get('BPz1e5tU') or [{}])[0]
        return q, str(q.get(self.S.F_USERID) or '')

    @staticmethod
    def is_own(uid):
        return str(uid or '').startswith(OWN_PREFIX)

    def search(self, pl, req):
        q, _ = self._target(req)
        fid = str(q.get('98WfKiyA') or '').strip()
        # The game shows its own messages from the reply, and takes an error reply as a lost
        # connection - so never answer with an error here. It looks for a row of friend type 4:
        # none = "User not found."; your own user ID = "this is your ID".
        lst = {'tojMy68W': self.friend_list(pl), '6e4b7sQt': self.limits}
        if fid and fid == str(pl.get('friend_id')):
            lst['tojMy68W'].append(dict(self.friend_row(pl['user_id'], pl, 4)))
            return lst, None
        found = next((p for p in self.players().values() if str(p.get('friend_id')) == fid and p['user_id'] != pl['user_id']), None)
        if not found:
            self.S.log.info('friends: %s searched for %s - no such player', pl.get('handle'), fid)
            return lst, None
        # already friends / already asked: still shown, so the game finds them (applying again
        # does no harm: it accepts their request, or changes nothing)
        lst['tojMy68W'] = [r for r in lst['tojMy68W'] if r.get(self.S.F_USERID) != found['user_id']]
        lst['tojMy68W'].append(self.friend_row(pl['user_id'], found, 4))
        return lst, None

    def waiting(self, pl):
        """Friend requests sent to this player and not answered yet (the friend badge)."""
        ps = self.players()
        return sum(1 for o, st in (self.links.get(pl['user_id']) or {}).items()
                   if st == 'received' and not o.startswith('_') and o in ps)

    def random_search(self, pl, req):
        me = pl['user_id']
        rows = [self.friend_row(me, p, 4) for o, p in self.players().items()
                if o != me and not self.status(me, o)]
        random.shuffle(rows)
        return {'tojMy68W': self.friend_list(pl) + rows[:10], '6e4b7sQt': self.limits}, None

    def count(self, uid):
        return sum(1 for o, st in (self.links.get(uid) or {}).items() if not o.startswith('_') and st == 'friend')

    def apply(self, pl, req):
        _, other = self._target(req)
        me = pl['user_id']
        if self.is_own(other):                   # your own unit used as helper: nothing to befriend
            return {'tojMy68W': self.friend_list(pl), '6e4b7sQt': self.limits}, None
        same = {'tojMy68W': self.friend_list(pl), '6e4b7sQt': self.limits}
        if other.startswith('npc') or other not in self.players() or other == me:
            # computer helpers are not players (and errors show as a lost connection): nothing to do
            self.S.log.info('friends: %s asked %s - not a player, nothing sent', pl.get('handle'), other)
            return same, None
        st = self.status(me, other)
        if st in ('friend', 'sent'):
            return same, None
        if self.count(me) >= MAX_FRIENDS - 1:          # one place is your own unit
            self.S.log.info('friends: %s friend list is full - request not sent', pl.get('handle'))
            return same, None
        if st == 'received':                     # they asked first: just become friends
            self.set(me, other, 'friend', 'friend')
        else:
            self.set(me, other, 'sent', 'received')
        self.S.log.info('friends: %s -> %s (%s)', pl.get('handle'), other, self.status(me, other))
        return {'tojMy68W': self.friend_list(pl), '6e4b7sQt': self.limits}, None

    def agree(self, pl, req):
        _, other = self._target(req)
        me = pl['user_id']
        same = {'tojMy68W': self.friend_list(pl), '6e4b7sQt': self.limits}
        if self.status(me, other) != 'received':
            return same, None                      # gone already (errors show as a lost connection)
        if self.count(me) >= MAX_FRIENDS or self.count(other) >= MAX_FRIENDS:
            self.S.log.info('friends: %s could not accept %s - a friend list is full', pl.get('handle'), other)
            return same, None
        self.set(me, other, 'friend', 'friend')
        self.S.log.info('friends: %s and %s are now friends', pl.get('handle'), other)
        return {'tojMy68W': self.friend_list(pl), '6e4b7sQt': self.limits}, None

    def remove(self, pl, req):
        """Refuse a request, cancel one you sent, or unfriend."""
        _, other = self._target(req)
        if self.is_own(other):                   # your own unit: stays
            return {'tojMy68W': self.friend_list(pl), '6e4b7sQt': self.limits}, None
        if self.status(pl['user_id'], other):
            self.set(pl['user_id'], other, None, None)
        return {'tojMy68W': self.friend_list(pl), '6e4b7sQt': self.limits}, None

    def favorite(self, pl, req):
        q, other = self._target(req)
        if self.is_own(other):
            return {'tojMy68W': self.friend_list(pl), '6e4b7sQt': self.limits}, None
        d = self.links.setdefault(pl['user_id'], {})
        fav = set(d.get('_fav', []))
        flag = str(q.get('5JbjC3Pp', q.get('mZA7fH2v', '')))
        if flag == '0' or (flag == '' and other in fav):
            fav.discard(other)
        else:
            fav.add(other)
        d['_fav'] = sorted(fav)
        self.save()
        return {'tojMy68W': self.friend_list(pl), '6e4b7sQt': self.limits}, None


def A_NAMES():
    import arena
    return arena.NAMES
