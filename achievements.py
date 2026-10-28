"""
Achievements, tracked by the server for each player.

The list of achievements (H9ATfJ38) is the game's own data from the recordings. Progress is counted
here from what each player actually does on this server - never copied from the recorded account.
Achievements whose condition the server can't measure yet stay at 0 until it can.

Row fields (YTRJLG65): pG2n1A28 progress, 9g4hdDJn target, 0B5bQgZh 0 locked / 1 in progress /
2 achieved, rPk8gtY5 1 reward waiting / 2 reward taken. Points total: Bnc4LpM8.idfCDG70.
Rewards (JQ23rIvk "type:id:count:..."): 6 unit, 7 item, 8 gems - they go to the gift box.
"""
import json
import os
import time

DAY = 86400
DAILY_COUNT = 5
MERIT_DAILY = 20000              # zel / karma trades a day (the game's own limit, shown on the trade screen)
# Exchange Hall: evolution materials and spheres restock every week (Monday 00:00 UTC, like the day's
# reset); everything else keeps its lifetime limit. Evolution materials = evolution units (Nymphs,
# Spirits, Idols, Totems, Pots, Bulbs, Mecha Gods, Mimics), Legend Stone / Shards / Elementum Tome
# and Rainbow Crystal (as joppe2001/bf-private-server's Merit Shop).
WEEK = 7 * DAY
WEEK_START = 4 * DAY             # Monday 1970-01-05 00:00 UTC (1970-01-01 was a Thursday)
WEEKLY_UNITS = {'750006'}        # Rainbow Crystal
UNTRACKABLE = {'9900'}           # raid quests: not on this server

# what the game reports at the end of a quest (MissionEndRequest rXvA1E5y), named from its own code
QUEST_STATS = {'bc': 'hoG2ieT5', 'hc': '6PLsn8xo', 'spark': 'U8uZLA34', 'bbuse': 'rZQJF5G9',
               'bbko': 'GRCVD1i8', 'sbbko': 'Uy15wSJd', 'overkill': 'EcDR3f6M', 'multikill': 'xFH5i4o8',
               'bad': 'J3EBd90b', 'crit': '2z8kTtBy', 'strongdmg': 'CwN87TyL', 'dmg': 'C2aU1feD'}


def _csv(s):
    out = {}
    for part in str(s or '').split(','):
        a, _, b = part.partition(':')
        if a.strip() and b.strip().lstrip('-').isdigit():
            out[a.strip()] = out.get(a.strip(), 0) + int(b)
    return out


COUNTED = {'UnitMixRequest': 'fusion', 'UnitEvoRequest': 'evo', 'UnitOmniEvoRequest': 'evo',
           'ItemMixRequest': 'synth', 'UnitFavoriteRequest': 'fav_unit', 'FriendAgreeRequest': 'friend',
           'FriendFavoriteRequest': 'fav_friend', 'ProfileUpdateRequest': 'comment'}


def _i(x, d=0):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return d


class Achievements:
    def __init__(self, srv):
        self.S = srv
        self.master, self.base = [], None
        for a in sorted(srv.T['action'], key=lambda a: -a['n']):        # newest recording first
            b = srv.BLOBS[a['resp']]
            if isinstance(b, dict) and b.get('H9ATfJ38'):
                self.master, self.base = b['H9ATfJ38'], b
                break
        self.by_id = {r['M7SXoc31']: r for r in self.master}
        self.trades = {r['Mdgsh04u']: r for r in (self.base or {}).get('82CcMZhp') or []}
        self.weekly = self._weekly_offers()

    def _weekly_offers(self):
        try:
            units = self.S.unit_mst()
            items = json.load(open(os.path.join(self.S.HERE, 'data', 'item_names.json'), encoding='utf-8'))
        except Exception:
            return set()
        out = set()
        for tid, r in self.trades.items():
            f = str(r.get('qBAb07rh', '')).split(':')
            kind, what = f[0], f[1] if len(f) > 1 else ''
            if kind == '6':
                if str((units.get(what) or {}).get('xP9oi5Z2')) == '2' or what in WEEKLY_UNITS:
                    out.add(tid)
            elif kind in ('4', '5', '7') and (items.get(what) or [None, ''])[1] in ('evomat', 'sphere'):
                out.add(tid)
        return out

    @staticmethod
    def week(now=None):
        return int(((now or time.time()) - WEEK_START) // WEEK)

    def bought(self, pl, tid):
        """How many of an offer count against its limit: this week's for the weekly ones."""
        if tid in self.weekly:
            w = pl.get('ach_trades_week') or {}
            return (w.get('n') or {}).get(tid, 0) if w.get('week') == self.week() else 0
        return (pl.get('ach_trades') or {}).get(tid, 0)

    @staticmethod
    def limit(r):
        """S8rdp9zk is the per-player limit (what the game itself checks and shows)."""
        return _i(r.get('S8rdp9zk'), 0) or _i(r.get('a0d2suLZ'), 999) or 999

    # ------------------------------------------------------------------ what the player has done
    def count(self, pl, cls):
        k = COUNTED.get(cls)
        if k:
            c = pl.setdefault('counts', {})
            c[k] = c.get(k, 0) + 1

    # ------------------------------------------------------------------ battle stats
    def quest_stats(self, req):
        r = (req.get('rXvA1E5y') or [{}])[0]
        st = {k: _i(r.get(f)) for k, f in QUEST_STATS.items()}
        st['kills'] = sum(_csv(r.get('L16VDrk5')).values())
        st['kills_el'] = _csv(r.get('Fw2dr06x'))
        st['won'] = str(((req.get('Kz7qfSs5') or [{}])[0]).get('j3g5P4cq')) != '3'
        return st

    def arena_stats(self, req):
        r = (req.get('yTEvm85f') or [{}])[0]
        um = self.S.unit_mst()
        el = {}
        for u in str(r.get('sQ2aMk8B') or '').split(','):
            e = str((um.get(u.strip()) or {}).get('iNy0ZU5M') or '')
            if e:
                el[e] = el.get(e, 0) + 1
        return {'win': str(r.get('3pAQY5KW')) == '1', 'bbko': _i(r.get('GRCVD1i8')) + _i(r.get('Uy15wSJd')),
                'kills_el': el}

    def record_quest(self, pl, req):
        mid = str(pl.get('current_mission') or '')
        won = str(((req.get('Kz7qfSs5') or [{}])[0]).get('j3g5P4cq')) != '3'
        if won and mid:
            r = (req.get('rXvA1E5y') or [{}])[0]
            turns = sum(_i(x) for x in str(r.get('Lu0pAk1R') or '').split(',') if x.strip())
            if turns:
                bt = pl.setdefault('best_turns', {})
                bt[mid] = min(bt.get(mid, turns), turns)
            if pl.get('_continued') != mid:
                nc = pl.setdefault('clear_nocont', [])
                if mid not in nc:
                    nc.append(mid)
        st = self.quest_stats(req)
        b = pl.setdefault('bstats', {})
        for k, v in st.items():
            if k != 'kills_el':
                b[k] = b.get(k, 0) + v
        for e, n in st['kills_el'].items():
            b['kills_el' + e] = b.get('kills_el' + e, 0) + n
        for k in ('bc', 'hc', 'spark'):
            b['max_' + k] = max(b.get('max_' + k, 0), st[k])
        self._daily_progress(pl, 'quest', st)

    def record_arena(self, pl, req):
        st = self.arena_stats(req)
        b = pl.setdefault('bstats', {})
        b['arena_bbko'] = b.get('arena_bbko', 0) + st['bbko']
        for e, n in st['kills_el'].items():
            b['arena_el' + e] = b.get('arena_el' + e, 0) + n
        self._daily_progress(pl, 'arena', st)

    def _gain(self, kind, param, src, st, cat):
        """How much one quest / arena battle moves an achievement of this kind."""
        p = str(param or '')
        if src == 'quest':
            simple = {'5400': 'spark', '5500': 'bc', '5600': 'hc', '5800': 'bbuse', '8300': 'overkill',
                      '5701': 'overkill', '6500': 'kills', '7401': 'crit', '7301': 'bad', '7201': 'multikill',
                      '7001': 'strongdmg'}
            if kind in simple:
                return st.get(simple[kind], 0)
            if kind == '8200' and cat != '2':
                return st.get('bbko', 0) + st.get('sbbko', 0)
            if kind == '6606':
                return st['kills_el'].get(p.split(':')[0], 0)
            if kind == '8500':
                return 1 if st.get('won', True) else 0
            if kind in ('6301', '6201', '6401'):          # best single quest
                return ('max', st.get({'6301': 'bc', '6201': 'spark', '6401': 'hc'}[kind], 0))
        if src == 'arena':
            if kind == '7600':
                return 1
            if kind == '7700':
                return 1 if st['win'] else 0
            if kind == '8000':
                return st['kills_el'].get(p.split(',')[0], 0)
            if kind == '8200':
                return st['bbko']
        return 0

    # ------------------------------------------------------------------ daily missions (category 2)
    def daily_pool(self):
        return [r for r in self.master if str(r.get('KT71m8Ae')) == '2' and r.get('8zaB7d6W') not in UNTRACKABLE]

    def daily(self, pl):
        """Today's 5 missions (new set at 00:00 UTC, like the keys)."""
        day = int(time.time() // DAY)
        d = pl.get('daily') or {}
        if d.get('day') != day:
            import random
            pool = self.daily_pool()
            rng = random.Random(f"{pl['user_id']}:{day}")
            pick = rng.sample(pool, min(DAILY_COUNT, len(pool))) if pool else []
            d = {'day': day, 'missions': {r['M7SXoc31']: {'acc': 0, 'p': 0} for r in pick},
                 'order': [r['M7SXoc31'] for r in pick]}
            pl['daily'] = d
        return d

    def _daily_progress(self, pl, src, st):
        d = self.daily(pl)
        for aid, m in d['missions'].items():
            r = self.by_id.get(aid)
            if not r or not m.get('acc') or m.get('done'):
                continue
            g = self._gain(r.get('8zaB7d6W'), r.get('t5R47iwj'), src, st, '2')
            if isinstance(g, tuple):
                m['p'] = max(m['p'], g[1])
            else:
                m['p'] += g
            if m['p'] >= self.target(r):
                m['done'] = int(time.time())

    def daily_rows(self, pl):
        d = self.daily(pl)
        left = str(int((d['day'] + 1) * DAY - time.time()))
        out = []
        for aid in d['order']:
            r, m = self.by_id.get(aid), d['missions'][aid]
            if not r:
                continue
            tgt = self.target(r)
            state = '2' if m.get('done') else ('1' if m.get('acc') else '0')
            taken = '2' if m.get('taken') else ('1' if m.get('done') else '0')
            out.append({'M7SXoc31': aid, 'pG2n1A28': str(min(m['p'], tgt)), '9g4hdDJn': str(tgt), '0B5bQgZh': state,
                        'VDKB0Y5h': left, 'rPk8gtY5': taken, 'dJNpLc81': '0', 'AKtVeaZ2': '', 'oJIEa3hU': ''})
        return out

    def metric(self, pl, kind, param):
        S = self.S
        st = pl['state']
        t = S.team_row(st) or {}
        clears = {r.get('j28VNcUW'): _i(r.get('HIQoGb10'), 1) for r in S._rows(st.get('UT1SVg59')) if isinstance(r, dict)}
        arena = st.get('8jBJ7uKR')
        arena = (arena[0] if isinstance(arena, list) and arena else arena) or {}
        c = pl.get('counts', {})
        first = param.split(',')[0].split(':')[0]
        if kind == '100':                                    # days logged in
            return max(_i((pl.get('login_campaign') or {}).get('total')), len(pl.get('login_days', [])))
        if kind == '200':                                    # honor points
            return _i(pl.get('wallet', {}).get('friendpoints'))
        if kind == '500':                                    # Merit Points earned all-time
            return self.earned_total(pl)
        if kind == '700':                                    # player level
            return _i(t.get('D9wXQI2V'), 1)
        if kind == '8500':                                   # quests cleared (all clears)
            return sum(clears.values())
        if kind == '6900':                                   # quest cleared within N turns
            best = (pl.get('best_turns') or {}).get(first)
            return 1 if best is not None and best <= _i(param.split(',')[-1], 999) else 0
        if kind == '8900':                                   # cleared without continuing
            return 1 if first in (pl.get('clear_nocont') or []) else 0
        if kind in ('8600', '11200'):                        # a given quest cleared
            return 1 if first in clears else 0
        if kind == '7700':
            return _i(arena.get('8CEu9Kcm'))
        if kind == '7600':
            return _i(arena.get('69vnphig'))
        if kind == '8400':
            return _i(arena.get('JmFn3g9t'), 1)
        if kind == '7800':
            return _i(pl.get('arena_best_streak'))
        b = pl.get('bstats', {})
        p = str(param or '')
        simple = {'5400': 'spark', '5500': 'bc', '5600': 'hc', '5800': 'bbuse', '8300': 'overkill',
                  '5701': 'overkill', '6500': 'kills', '7401': 'crit', '7301': 'bad', '7201': 'multikill',
                  '7001': 'strongdmg', '6301': 'max_bc', '6201': 'max_spark', '6401': 'max_hc'}
        if kind in simple:
            return b.get(simple[kind], 0)
        if kind == '8200':
            return b.get('bbko', 0) + b.get('sbbko', 0) + b.get('arena_bbko', 0)
        if kind == '6606':
            return b.get('kills_el' + p.split(':')[0], 0)
        if kind == '8000':
            return b.get('arena_el' + p.split(',')[0], 0)
        if kind == '3500':                                   # unit guide entries
            return len(S._rows(st.get(S.G_DICT)))
        if kind == '2800':                                   # item guide entries
            return len(S._rows(st.get('bd5Rj6pN')))
        if kind == '3600':
            return c.get('fusion', 0)
        merit = {'2100': 'merit_zel', '2400': 'merit_karma', '4200': 'merit_units', '3150': 'merit_spheres'}
        if kind in merit:                                    # traded to Randall for Merit Points
            return c.get(merit[kind], 0)
        if kind == '3700':
            return c.get('evo', 0)
        if kind == '2700':
            return c.get('synth', 0)
        if kind == '4500':
            return c.get('fav_unit', 0)
        if kind == '800':
            return c.get('friend', 0)
        if kind == '1000':
            return c.get('fav_friend', 0)
        if kind == '1100':
            return c.get('comment', 0)
        return None                                          # not tracked yet

    @staticmethod
    def target(r):
        kind, p = r.get('8zaB7d6W'), str(r.get('t5R47iwj') or '')
        if kind in ('8600', '11200', '8900', '6900'):
            return 1
        return _i(p.split(',')[-1].split(':')[-1], 1) or 1

    # ------------------------------------------------------------------ rows
    def row(self, pl, r):
        st = pl.setdefault('ach', {})
        aid = r['M7SXoc31']
        mine = st.get(aid, {})
        tgt = self.target(r)
        m = self.metric(pl, r.get('8zaB7d6W'), str(r.get('t5R47iwj') or ''))
        prog = mine.get('p', 0) if m is None else m
        pre = str(r.get('HSRhkf70') or '')
        if pre:                         # the condition is an achievement done or a quest cleared
            cleared = {c.get('j28VNcUW') for c in self.S._rows(pl['state'].get('UT1SVg59')) if isinstance(c, dict)}
            unlocked = any(st.get(x, {}).get('done') or x in cleared for x in pre.split(','))
        else:
            unlocked = True
        done = mine.get('done') or (unlocked and m is not None and prog >= tgt)
        if done and not mine.get('done'):
            st[aid] = dict(mine, done=int(time.time()))
        state = '2' if done else ('1' if unlocked else '0')
        taken = '2' if mine.get('taken') else ('1' if done else '0')
        kind = r.get('8zaB7d6W')
        if kind in ('2100', '2400'):
            # the trade screen reads this row as "times traded today" against the daily limit
            prog = self.merit_today(pl).get('zel' if kind == '2100' else 'karma', 0)
        return {'M7SXoc31': aid, 'pG2n1A28': str(min(prog, 10 ** 9)), '9g4hdDJn': str(tgt), '0B5bQgZh': state,
                'VDKB0Y5h': '-1', 'rPk8gtY5': taken, 'dJNpLc81': '0', 'AKtVeaZ2': '', 'oJIEa3hU': ''}

    def rows(self, pl, cat, sub):
        out = []
        for r in self.master:
            if str(r.get('KT71m8Ae')) != str(cat):
                continue
            if str(sub) not in ('-1', '') and str(r.get('3rhygS9K')) != str(sub):
                continue
            out.append(self.row(pl, r))
        return out

    def points(self, pl):
        return {'idfCDG70': str(pl.get('ach_points', 0)), 'HjgTfx8A': '0', 'CjaJUSrs': '0'}

    # ------------------------------------------------------------------ requests
    def info(self, pl, req):
        q = (req.get('1Q2pGAXE') or [{}])[0]
        resp = {k: v for k, v in (self.base or {}).items() if k in ('H9ATfJ38', '82CcMZhp', '1tJiqKgZ')}
        resp['Bnc4LpM8'] = self.points(pl)
        resp['9j3ALx8I'] = self.trade_rows(pl)
        cat = q.get('KT71m8Ae', '1')
        resp['YTRJLG65'] = self.daily_rows(pl) if str(cat) == '2' else self.rows(pl, cat, q.get('3rhygS9K', '-1'))
        return resp

    def claim(self, pl, req):
        q = (req.get('1Q2pGAXE') or [{}])[0]
        st = pl.setdefault('ach', {})
        daily = self.daily(pl)['missions']
        cat, sub = str(q.get('KT71m8Ae', '1')), str(q.get('3rhygS9K', '-1'))
        for aid in str(q.get('M7SXoc31', '')).split(','):
            r = self.by_id.get(aid)
            if not r:
                continue
            # only the section the button was pressed in (our achievement data says where each one lives)
            if str(r.get('KT71m8Ae')) != cat or (sub not in ('-1', '0', '') and str(r.get('3rhygS9K')) != sub):
                self.S.log.info('achievement %s is not in section %s/%s - not claimed', aid, cat, sub)
                continue
            if aid in daily:
                m = daily[aid]
                if not m.get('done') or m.get('taken'):
                    continue
                m['taken'] = int(time.time())
            else:
                row = self.row(pl, r)
                if row['0B5bQgZh'] != '2' or st.get(aid, {}).get('taken'):
                    continue
                st.setdefault(aid, {})['taken'] = int(time.time())
            pts = _i(r.get('9hH0neGa'))
            self.gain_points(pl, pts)
            title = str(r.get('xXdp1q0Y') or '').replace('<br>', ' ').strip()
            label = (f"Level Up Campaign: {title[len('[Level Up Campaign]'):].strip()}"
                     if title.startswith('[Level Up Campaign]') else f'Achievement: {title}')
            rew = str(r.get('JQ23rIvk') or '')
            if rew:
                f = rew.split(':')
                kind, what, n = f[0], f[1] if len(f) > 1 else '', _i(f[2] if len(f) > 2 else 1, 1)
                # same codes as gift-box presents: 2 karma, 3 zel, 6 unit, 7 item, 8 gems, 12 raid medals
                if kind in ('2', '3', '8', '12'):
                    self.S.add_present(pl, _i(kind), '', n, label)
                elif kind in ('5', '6', '7') and what not in ('', '0'):
                    self.S.add_present(pl, _i(kind), what, n, label)
            self.S.log.info('achievement claimed: %s (+%d points)%s', title, pts, f' + reward {rew}' if rew else '')
        cat = q.get('KT71m8Ae', '1')
        return {'Bnc4LpM8': self.points(pl),
                'YTRJLG65': self.daily_rows(pl) if str(cat) == '2' else self.rows(pl, cat, q.get('3rhygS9K', '-1'))}

    # ------------------------------------------------------------------ Exchange Hall (Merit shop)
    def trade_rows(self, pl):
        """9j3ALx8I: what the Exchange Hall offers this player - every item of the game's trade list
        (82CcMZhp) except alternate arts (not on this server). H6k1LIxC = times already bought."""
        return [{'Mdgsh04u': r['Mdgsh04u'], 'H6k1LIxC': str(self.bought(pl, r['Mdgsh04u'])), 'VDKB0Y5h': '-1',
                 'ypV81b7k': '0'} for r in self.trades.values() if not str(r.get('qBAb07rh', '')).startswith('15:')]

    def trade(self, pl, req):
        q = (req.get('rX74GNsm') or [{}])[0]
        tid, n = str(q.get('Mdgsh04u', '')), max(1, _i(q.get('H6k1LIxC'), 1))
        r = self.trades.get(tid)
        if not r or str(r.get('qBAb07rh', '')).startswith('15:'):
            return None, 'This item is not available.'
        bought = pl.setdefault('ach_trades', {})
        if self.bought(pl, tid) + n > self.limit(r):
            return None, ('Sold out for this week - it restocks on Monday.' if tid in self.weekly
                          else 'You cannot exchange for this item any more.')
        cost = _i(r.get('3EWLm0sA')) * n
        if pl.get('ach_points', 0) < cost:
            return None, 'Not enough Merit Points.'
        f = str(r.get('qBAb07rh')).split(':')
        kind, what, cnt = f[0], f[1] if len(f) > 1 else '', _i(f[2] if len(f) > 2 else 1, 1)
        if kind not in ('2', '3', '4', '5', '6', '7', '8', '12'):
            return None, 'This item is not available.'
        pl['ach_points'] -= cost
        bought[tid] = bought.get(tid, 0) + n                  # all-time (Merit Points spent)
        if tid in self.weekly:
            w = pl.get('ach_trades_week') or {}
            if w.get('week') != self.week():
                w = {'week': self.week(), 'n': {}}
            w['n'][tid] = w['n'].get(tid, 0) + n
            pl['ach_trades_week'] = w
        name = str(r.get('hQ1SJZU9') or tid)
        if kind in ('6',):
            for _ in range(n):
                self.S.add_present(pl, 6, what, cnt, f'Exchange Hall: {name}')
        else:
            self.S.add_present(pl, _i(kind), '' if kind in ('2', '3', '8', '12') else what, cnt * n,
                               f'Exchange Hall: {name}')
        self.S.log.info('exchange hall: %s x%d for %d Merit Points (%d left)', name, n, cost, pl['ach_points'])
        return {'Bnc4LpM8': self.points(pl), '9j3ALx8I': self.trade_rows(pl)}, None

    def earned_total(self, pl):
        """Merit Points earned all-time (the "Merit Points Accumulated" records). Saves from before
        this was kept: what is held now plus what was spent in the Exchange Hall."""
        if 'ach_points_total' not in pl:
            spent = sum(_i((self.trades.get(t) or {}).get('3EWLm0sA')) * n for t, n in (pl.get('ach_trades') or {}).items())
            pl['ach_points_total'] = pl.get('ach_points', 0) + spent
        return pl['ach_points_total']

    def gain_points(self, pl, n):
        self.earned_total(pl)
        pl['ach_points'] = pl.get('ach_points', 0) + n
        pl['ach_points_total'] += n

    def merit_today(self, pl):
        day = int(time.time() // DAY)
        m = pl.get('merit_day')
        if not m or m.get('day') != day:
            c = pl.get('counts', {}) if m is None else {}      # trades made before this was tracked
            m = pl['merit_day'] = {'day': day, 'zel': c.get('merit_zel', 0) // 10000,
                                   'karma': c.get('merit_karma', 0) // 10000}
        return m

    def deliver(self, pl, req):
        """Randall's Merit Point trade (AchievementDeliver, from the game's own request code): 1Q2pGAXE =
        the trade achievement (category 3, section 4 zel / 5 karma / 8 units / 6 spheres), dE79UwNE =
        Rs7bCE3t zel or karma given, idfCDG70 Merit Points the game worked out, edy7fq3L unit ids,
        n6E8iMf3 items."""
        S = self.S
        q = (req.get('1Q2pGAXE') or [{}])[0]
        d = (req.get('dE79UwNE') or [{}])[0]
        sub, amount, pts = str(q.get('3rhygS9K', '')), _i(d.get('Rs7bCE3t')), max(0, _i(d.get('idfCDG70')))
        S.log.info('merit trade request: section %s, %s', sub, d)
        c = pl.setdefault('counts', {})
        w = pl.setdefault('wallet', {})
        what = ''
        if sub in ('4', '5'):
            cur = 'zel' if sub == '4' else 'karma'
            if amount <= 0 or w.get(cur, 0) < amount:
                return None, f'Not enough {cur}.'
            pts = min(pts, amount)                       # never more points than what was given
            today = self.merit_today(pl)
            if today[cur] + pts > MERIT_DAILY:
                return None, f'You can only trade {MERIT_DAILY} times a day ({MERIT_DAILY - today[cur]} left today).'
            today[cur] += pts
            w[cur] -= amount
            c['merit_' + cur] = c.get('merit_' + cur, 0) + amount
            what = f'{amount} {cur}'
        elif sub == '8':
            ids = [x for x in str(d.get('edy7fq3L') or '').split(',') if x.strip()]
            if not ids or any(x not in pl['units'] for x in ids):
                return None, 'A unit is no longer in your unit box.'
            for x in ids:
                pl['units'].pop(x, None)
            c['merit_units'] = c.get('merit_units', 0) + len(ids)
            what = f'{len(ids)} unit(s)'
        elif sub == '6':
            wh = [r for r in S._rows(pl['state'].get(S.G_WAREHOUSE)) if isinstance(r, dict)]
            given = 0
            for part in str(d.get('n6E8iMf3') or '').split(','):
                key, _, n = part.strip().partition(':')
                if not key:
                    continue
                n = _i(n, 1) or 1
                row = next((r for r in wh if str(r.get('n6E8iMf3')) == key), None) or \
                    next((r for r in wh if str(r.get('kixHbe54')) == key), None)
                if not row or _i(row.get('wgV86x1q')) < n:
                    return None, 'You do not have that many of this item.'
                row['wgV86x1q'] = str(_i(row['wgV86x1q']) - n)
                given += n
            pl['state'][S.G_WAREHOUSE] = [r for r in wh if _i(r.get('wgV86x1q')) > 0]
            c['merit_spheres'] = c.get('merit_spheres', 0) + given
            what = f'{given} item(s)'
        else:
            return None, 'This trade is not available.'
        self.gain_points(pl, pts)
        S.log.info('merit trade: %s -> +%d Merit Points (%d)', what, pts, pl['ach_points'])
        resp = {'Bnc4LpM8': self.points(pl), 'YTRJLG65': self.rows(pl, '3', '-1')}
        t = S.team_row(pl['state'])
        if t is not None:
            resp[S.G_TEAM] = [t]
        resp[S.G_UNITS] = list(pl['units'].values())
        resp[S.G_WAREHOUSE] = list(S._rows(pl['state'].get(S.G_WAREHOUSE)))
        return resp, None

    def badges(self, pl):
        """BadgeInfo dUujjBBK: "category:section:count" for every section with rewards waiting."""
        n = {}
        for r in self.master:
            cat = str(r.get('KT71m8Ae'))
            if cat in ('2', '3', '5'):
                # 2 = daily missions (counted below); 3 = the Trade screen's trade rows (merit trades,
                # not rewards to claim); 5 = old "(SP)" event challenges - the game has no screen for
                # them. A badge for 3 or 5 could never be cleared.
                continue
            row = self.row(pl, r)
            if row['rPk8gtY5'] == '1':
                k = (cat, str(r.get('3rhygS9K')))
                n[k] = n.get(k, 0) + 1
        for m in self.daily(pl)['missions'].values():
            if m.get('done') and not m.get('taken'):
                n[('2', '0')] = n.get(('2', '0'), 0) + 1
        return ','.join(f'{c}:{s}:{v}' for (c, s), v in sorted(n.items(), key=lambda x: (int(x[0][0]), int(x[0][1]))))

    def level_campaign(self, pl):
        """Level Up Campaign rewards are handed out by the server as soon as the level is reached:
        they go to the gift box and show as already claimed here (so Claim All never takes them)."""
        luc = [r for r in self.master if str(r.get('xXdp1q0Y') or '').startswith('[Level Up Campaign]')]
        st = pl.setdefault('ach', {})
        for _ in range(len(luc)):                    # each level unlocks the next one in the chain
            ready = [r['M7SXoc31'] for r in luc if not st.get(r['M7SXoc31'], {}).get('taken')
                     and self.row(pl, r)['0B5bQgZh'] == '2']
            if not ready:
                break
            self.claim(pl, {'1Q2pGAXE': [{'KT71m8Ae': str(luc[0].get('KT71m8Ae')),
                                          '3rhygS9K': str(luc[0].get('3rhygS9K')), 'M7SXoc31': ','.join(ready)}]})

    def accept(self, pl, req):
        """Start counting a daily mission (the game sends one id, or several at once)."""
        q = (req.get('1Q2pGAXE') or [{}])[0]
        d = self.daily(pl)
        for aid in str(q.get('M7SXoc31', '')).split(','):
            m = d['missions'].get(aid)
            if m and not m.get('acc'):
                m['acc'] = int(time.time())
                r = self.by_id.get(aid) or {}
                self.S.log.info('daily mission accepted: %s', str(r.get('xXdp1q0Y', aid)).replace('<br>', ' '))
        return {'Bnc4LpM8': self.points(pl), 'YTRJLG65': self.daily_rows(pl)}
