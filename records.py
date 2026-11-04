"""
Player records (UserTeamArchive, group zI2tJB7R): the numbers on the game's records / trophy screens.

The game only shows what the server sends, so the server keeps every one of them up to date here.
Keys and what they count (from the game's own UserTeamArchiveResponse):
  dD64grYH LoginCnt         days played               3w6YDS4z SerialLoginDay   days in a row
  d96tuT2E Exp              player exp                pThS5FE3 ZelGet / mn5Tj3fz ZelUse
  jG91JRxN ZelSale          zel from selling          06phPeqv ZelUnitSale / Zq8ej5IN ZelItemSale
  isRx41jy KarmaGet / 20qd9shE KarmaUse               Sf95jez7 FriendPGet / I29Qgxot FriendPUse
  WMC6rNF1 UnitMixCnt       fusions                   Z93pUQhG UnitMixElemCnt   same-element materials fused
  Rc6St9h1 UnitEvoCnt       evolutions                k5Sjn9Zq UnitSumCnt       units ever obtained
  c3Bo97kI ItemMixCnt       items crafted             Gt2msFb1 ItemMixElemCnt   kinds of item crafted
  07HgoLtC SphereMixCnt     spheres crafted           AEz43gai FightCnt / 8CEu9Kcm WinCnt  arena battles / wins
  3DBVLY8H TownHarvestCnt   town spot taps            c4im6B2v QuestChallengeCnt quests started
  UCN04WxE QuestClearCnt    different quests cleared  ovFJ6Hp0 QuestWinCnt      quests won
  TW1Mrtp5 QuestMimicCnt    mimics met                e6BKoYy9 TurnMaxUnitDamage  best one-unit turn damage
  5NRJQ1LU BattleTurnMaxDamage / XP06YWdT BattleTurnMaxSpark   best turn damage / sparks
  U8uZLA34 BattleSparkCnt / rZQJF5G9 BattleSkillCnt   sparks / Brave Bursts used (quests)
  hoG2ieT5 BCrystal / 6PLsn8xo HCrystal               BC / HC picked up (quests)
  0LwvAF3H BCrystalMax / rQ3TAy6I HCrystalMax          most BC / HC in one quest
  84BC2kXw GiftPresentCnt / 5pg7MYCQ GiftReceiveCnt   friend gifts sent / received
  mFID53JZ ArenaDefeatUnitCnt (kept by arena.py in PQ56vbkI, copied here)
Records start counting from when this file went live; what happened before isn't known.
"""
import json
import os
import time

G = 'zI2tJB7R'
KEYS = ['dD64grYH', '3w6YDS4z', 'd96tuT2E', 'pThS5FE3', 'mn5Tj3fz', 'jG91JRxN', '06phPeqv', 'Zq8ej5IN',
        'isRx41jy', '20qd9shE', 'Sf95jez7', 'I29Qgxot', 'WMC6rNF1', 'Z93pUQhG', 'Rc6St9h1', 'k5Sjn9Zq',
        'c3Bo97kI', 'Gt2msFb1', '07HgoLtC', 'AEz43gai', '8CEu9Kcm', '3DBVLY8H', 'c4im6B2v', 'UCN04WxE',
        'ovFJ6Hp0', 'TW1Mrtp5', 'e6BKoYy9', '5NRJQ1LU', 'XP06YWdT', 'U8uZLA34', 'rZQJF5G9', '0LwvAF3H',
        'rQ3TAy6I', 'hoG2ieT5', '6PLsn8xo', '84BC2kXw', '5pg7MYCQ', 'mFID53JZ']
MONEY = {'zel': ('pThS5FE3', 'mn5Tj3fz'), 'karma': ('isRx41jy', '20qd9shE'), 'friendpoints': ('Sf95jez7', 'I29Qgxot')}
QUEST_SUM = {'hoG2ieT5': 'hoG2ieT5', '6PLsn8xo': '6PLsn8xo', 'U8uZLA34': 'U8uZLA34', 'rZQJF5G9': 'rZQJF5G9',
             'TW1Mrtp5': 'TW1Mrtp5'}
QUEST_MAX = {'0LwvAF3H': 'hoG2ieT5', 'rQ3TAy6I': '6PLsn8xo', '5NRJQ1LU': '5NRJQ1LU', 'XP06YWdT': 'XP06YWdT',
             'e6BKoYy9': 'e6BKoYy9'}
NOT_NEW_UNITS = {'UnitEvoRequest', 'UnitOmniEvoRequest'}      # an evolved unit isn't a new one


def _i(x, d=0):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return d


class Records:
    def __init__(self, srv):
        self.S = srv

    def row(self, pl):
        st = pl.setdefault('state', {})
        rows = st.get(G)
        row = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else \
            (rows if isinstance(rows, dict) else {})
        row[self.S.F_USERID] = pl['user_id']
        for k in KEYS:
            row.setdefault(k, '0')
        st[G] = [row]
        return row

    # ------------------------------------------------------------------ before the request runs
    def before(self, pl, cls, req):
        w = pl.get('wallet') or {}
        snap = {'cls': cls, 'rec_day': pl.get('rec_day'), 'money': {k: _i(w.get(k)) for k in MONEY}, 'units': set(pl.get('units') or {})}
        if cls == 'UnitMixRequest':
            um = self.S.unit_mst()
            rows = req.get(self.S.G_SELL_UNITS) or []
            base = next((r.get(self.S.F_UNIT_ID) for r in rows if str(r.get('mnZ5K4Ii')) == '1'), None)
            el = lambda uid: str((um.get((pl['units'].get(uid) or {}).get('pn16CNah')) or {}).get('iNy0ZU5M') or '')
            mats = [r.get(self.S.F_UNIT_ID) for r in rows if str(r.get('mnZ5K4Ii')) == '2']
            snap['same_el'] = sum(1 for m in mats if base and el(m) and el(m) == el(base))
        if cls == 'MissionEndRequest':
            mid = str(pl.get('current_mission') or '')
            snap['mid'] = mid
            snap['first'] = bool(mid) and not self.S.cleared_before(pl, mid)
        return snap

    # ------------------------------------------------------------------ after it ran
    def after(self, pl, snap, req, resp):
        if not isinstance(resp, dict) or self.S.G_ERROR in resp:
            return False
        r = self.row(pl)
        before = dict(r)
        cls = snap['cls']

        def add(k, n=1):
            if n:
                r[k] = str(_i(r.get(k)) + int(n))

        def best(k, v):
            if _i(v) > _i(r.get(k)):
                r[k] = str(_i(v))

        # days played / in a row (the game's day starts 00:00 UTC, like the daily reset)
        day = int(time.time() // 86400)
        last = pl.get('rec_day')
        if last is None and _i(pl.get('created')) // 86400 == day:
            pl['rec_day'], pl['rec_streak'] = day, 1         # a new account: its first day is already counted
            last = day
        if last != day:
            add('dD64grYH')
            pl['rec_streak'] = pl.get('rec_streak', 0) + 1 if last == day - 1 else 1
            pl['rec_day'] = day
        r['3w6YDS4z'] = str(max(1, pl.get('rec_streak', 1)))
        # money in and out
        w = pl.get('wallet') or {}
        for k, (got, used) in MONEY.items():
            d = _i(w.get(k)) - snap['money'][k]
            if d > 0:
                add(got, d)
                if k == 'zel' and cls == 'UnitSellRequest':
                    add('jG91JRxN', d), add('06phPeqv', d)
                elif k == 'zel' and cls == 'ItemSellRequest':
                    add('jG91JRxN', d), add('Zq8ej5IN', d)
            elif d < 0:
                add(used, -d)
        # units ever obtained (anything new in the unit box, except an evolution's new form)
        new = len(set(pl.get('units') or {}) - snap['units'])
        if cls not in NOT_NEW_UNITS:
            add('k5Sjn9Zq', new)
        if _i(r.get('k5Sjn9Zq')) < len(pl.get('units') or {}):
            r['k5Sjn9Zq'] = str(len(pl.get('units') or {}))
        # unit and item work
        if cls == 'UnitMixRequest':
            add('WMC6rNF1'), add('Z93pUQhG', snap.get('same_el', 0))
        elif cls in ('UnitEvoRequest', 'UnitOmniEvoRequest'):
            add('Rc6St9h1')
        elif cls == 'ItemMixRequest':
            self._crafts(pl, r, req, add)
        elif cls == 'TownUpdateRequest':
            taps = 0
            for row in self.S._rows(req.get('EuY6L7AX')):
                for part in str((row or {}).get('0mRaAo39') or '').split(','):
                    taps += max(0, _i(part.partition(':')[2]))
            add('3DBVLY8H', taps)
        elif cls == 'FixGiftInfoRequest':
            got, sent = pl.pop('_gift_counts', (0, 0))
            add('5pg7MYCQ', got), add('84BC2kXw', sent)
        # quests
        elif cls == 'MissionStartRequest':
            add('c4im6B2v')
        elif cls == 'MissionEndRequest':
            res = (req.get('rXvA1E5y') or [{}])[0]
            won = str(((req.get('Kz7qfSs5') or [{}])[0]).get('j3g5P4cq')) != '3'
            if won:
                add('ovFJ6Hp0')
                if snap.get('first'):
                    add('UCN04WxE')
            for k, f in QUEST_SUM.items():
                add(k, max(0, _i(res.get(f))))
            for k, f in QUEST_MAX.items():
                best(k, res.get(f))
        # copies of what other screens keep
        t = self.S.team_row(pl['state']) or {}
        if t.get('d96tuT2E') is not None:
            r['d96tuT2E'] = str(_i(t.get('d96tuT2E')))
        ai = (pl['state'].get('8jBJ7uKR') or [{}])
        ai = ai[0] if isinstance(ai, list) and ai else ai
        if isinstance(ai, dict):
            r['AEz43gai'] = str(_i(ai.get('69vnphig')))
            r['8CEu9Kcm'] = str(_i(ai.get('8CEu9Kcm')))
        pa = (pl['state'].get('PQ56vbkI') or [{}])
        pa = pa[0] if isinstance(pa, list) and pa else pa
        if isinstance(pa, dict):
            r['mFID53JZ'] = str(_i(pa.get('mFID53JZ')))
        if r != before:
            resp[G] = [dict(r)]
            return True
        return pl.get('rec_day') != snap.get('rec_day')      # nothing to show, but the day must be kept

    _NAMES = None

    def _names(self):
        if Records._NAMES is None:
            try:
                Records._NAMES = json.load(open(os.path.join(self.S.HERE, 'data', 'item_names.json'), encoding='utf-8'))
            except Exception:
                Records._NAMES = {}
        return Records._NAMES

    def _crafts(self, pl, r, req, add):
        recipes = {str(x.get('4HqhTf3a')): x for x in self.S._town_mst('8f0bCciN') if isinstance(x, dict)}
        names = self._names()
        kinds = set(pl.get('rec_crafted') or [])
        for rid, n in self.S.mix_orders(req):
            rec = recipes.get(str(rid)) or {}
            item = str(rec.get('kixHbe54') or '')
            made = (_i(rec.get('wgV86x1q'), 1) or 1) * n
            kind = (names.get(item) or [None, ''])[1]
            if kind in ('sphere', 'ls_sphere'):
                add('07HgoLtC', made)
            else:
                add('c3Bo97kI', made)
            if item and item not in kinds:
                kinds.add(item)
                add('Gt2msFb1')
        pl['rec_crafted'] = sorted(kinds)
