"""
Friend gifts between players on this server.

The game's gift list (Bm1WNDQ0 GiftItemMst, from the recordings): eDj82kMN gift id, 2bvT9hKt type
(1 zel, 2 honor points, 3 item kixHbe54, 4 karma), wgV86x1q amount.

FixGiftInfoRequest Gg5c3eM6: s2WnRw9N = the 3 gifts you want (shown to your friends),
qD74LPXb = gifts to send ("<user id>:<gift id>,..."), 23Xi0jom = gifts to take ("<serial>,...").
Reply 30uygM9m (UserGiftInfo): gifts waiting for you, plus the ones you sent today (the game
greys those friends out for the day). gNE76SLp serial, yDiG31Fe from, 1px2dbX3 to, fa6WwE9u 1 =
taken. The game adds a taken gift to your zel / karma / honor points itself; the server adds the
same to its own balances so they stay equal.

One gift per friend per day. Gifts are kept 30 days. saves/friend_gifts.json.
"""
import datetime
import json
import os
import threading
import time

KEEP_DAYS = 30
G_GIFTS, G_GIFT_MST = '30uygM9m', 'Bm1WNDQ0'


class GiftBox:
    def __init__(self, srv):
        self.S = srv
        self.file = os.path.join(srv.SAVE_DIR, 'friend_gifts.json')
        self.mu = threading.RLock()
        try:
            self.data = json.load(open(self.file, encoding='utf-8'))
        except Exception:
            self.data = {'next': 1000, 'gifts': []}
        self.mst = {}
        for a in srv.T['action']:
            b = srv.BLOBS[a['resp']]
            if isinstance(b, dict) and b.get(G_GIFT_MST):
                self.mst = {str(r.get('eDj82kMN')): r for r in b[G_GIFT_MST] if isinstance(r, dict)}
                break

    def save(self):
        cut = time.time() - KEEP_DAYS * 86400
        self.data['gifts'] = [g for g in self.data['gifts'] if g['time'] >= cut]
        tmp = self.file + '.tmp'
        json.dump(self.data, open(tmp, 'w', encoding='utf-8'), indent=1)
        os.replace(tmp, self.file)

    @staticmethod
    def day(t):
        return datetime.datetime.fromtimestamp(t).strftime('%Y-%m-%d')

    # ------------------------------------------------------------------ rows the game reads
    def row(self, g, people):
        p = people.get(g['from']) or {}
        F = self.S.friend_engine()
        u = F.leader(p) if p else {}
        uf = F._unit_fields(u) if u else {}
        return {'gNE76SLp': str(g['id']), 'yDiG31Fe': g['from'], '1px2dbX3': g['to'],
                'eDj82kMN': str(g['gift']), '2bvT9hKt': str(g['type']), 'kixHbe54': str(g['item']),
                'wgV86x1q': str(g['count']), 'aHSvC81N': self.day(g['time']) + ' 00:00:00', 'q5Y4gUnb': '0',
                'fa6WwE9u': '1' if g.get('taken') else '0', 'pn16CNah': uf.get('pn16CNah', '0'),
                'B5JQyV8j': self.S.shown_name(p) if p else g.get('name', ''), '2pAyFjmZ': '0', 'jkldTrhL': 0,
                '9i2xhMaJ': '0', 'iNy0ZU5M': uf.get('iNy0ZU5M', '0'), 'btZizNep': '', 'RVVgyuor': ''}

    def rows(self, pl):
        me, today = pl['user_id'], self.day(time.time())
        people = {p['user_id']: p for p in self.S.all_players()}
        out = []
        for g in self.data['gifts']:
            if g['to'] == me and not g.get('taken') and g['from'] in people:
                out.append(self.row(g, people))
            elif g['from'] == me and self.day(g['time']) == today:
                out.append(self.row(g, people))
        return out

    def waiting(self, pl):
        return sum(1 for g in self.data['gifts'] if g['to'] == pl['user_id'] and not g.get('taken'))

    def sent_today(self, me, other):
        today = self.day(time.time())
        return any(g['from'] == me and g['to'] == other and self.day(g['time']) == today
                   for g in self.data['gifts'])

    # ------------------------------------------------------------------ actions
    def fix(self, pl, req):
        q = (req.get('Gg5c3eM6') or [{}])[0] or {}
        me = pl['user_id']
        out = {}
        with self.mu:
            want = str(q.get('s2WnRw9N') or '').strip()
            t = self.S.team_row(pl['state'])
            if want and t is not None and all(x in self.mst for x in want.split(',') if x):
                t['s2WnRw9N'] = want
            # take gifts
            serials = {x.strip() for x in str(q.get('23Xi0jom') or '').split(',') if x.strip()}
            got = []
            for g in self.data['gifts']:
                if str(g['id']) in serials and g['to'] == me and not g.get('taken'):
                    g['taken'] = time.time()
                    self.give(pl, g, out)
                    got.append(f"{g['count']} {self.kind(g)}")
            # send gifts
            sent = []
            F = self.S.friend_engine()
            for part in str(q.get('qD74LPXb') or '').split(','):
                if ':' not in part:
                    continue
                other, gid = (x.strip() for x in part.split(':', 1))
                m = self.mst.get(gid)
                if not m or F.status(me, other) != 'friend' or self.sent_today(me, other):
                    continue
                self.data['next'] = int(self.data.get('next', 1000)) + 1
                self.data['gifts'].append({'id': self.data['next'], 'from': me, 'to': other, 'gift': int(gid),
                                           'type': int(m.get('2bvT9hKt') or 0), 'item': str(m.get('kixHbe54') or '0'),
                                           'count': int(m.get('wgV86x1q') or 0), 'time': time.time(),
                                           'name': pl.get('handle', '')})
                sent.append(other)
            if got or sent:
                self.save()
                self.S.log.info('friend gifts: %s took %s; sent to %s', pl.get('handle'),
                                ', '.join(got) or 'nothing', ', '.join(sent) or 'nobody')
        out[G_GIFTS] = self.rows(pl)
        return out

    def kind(self, g):
        return {1: 'zel', 2: 'honor points', 3: f"x item {g['item']}", 4: 'karma'}.get(g['type'], '?')

    def give(self, pl, g, out):
        w = pl.setdefault('wallet', {})
        if g['type'] == 1:
            w['zel'] = w.get('zel', 0) + g['count']
        elif g['type'] == 2:
            w['friendpoints'] = w.get('friendpoints', 0) + g['count']
        elif g['type'] == 4:
            w['karma'] = w.get('karma', 0) + g['count']
        elif g['type'] == 3 and g['item'] not in ('', '0'):
            self.S.add_item(pl, g['item'], max(1, g['count']))
            out[self.S.G_WAREHOUSE] = pl['state'].get(self.S.G_WAREHOUSE, [])
            out['bd5Rj6pN'] = pl['state'].get('bd5Rj6pN', [])
