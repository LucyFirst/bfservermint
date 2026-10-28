"""
Arena on the private server.

Opponents are never the recorded bfrevive players: each match is either another player on this
server (their own squad, as it is now) or a challenger built on the spot to match your squad's
strength. Ranks and rank-up rewards follow the game's own arena table (ArenaRankMst, sent by the
game data); points (ABP) follow the wiki's description of the original (see ABP_* below), plus a
flat bonus for wins in a row; karma = 100 x your rank for a win; one orb per battle.
"""
import json
import random
import time

ORB_SECONDS = 3600          # an orb comes back every hour (the recordings show a 3600 s timer)
# Arena Battle Points, as the wiki describes the original game: a win gives 10..200 before bonuses -
# more for beating a higher-ABP opponent (200 for one 3000 above you), and more the better you
# fought (units left standing, HP left, damage done); a loss costs more against a lower-ABP
# opponent, and less the better you fought.
ABP_MIN, ABP_MAX, ABP_EVEN, ABP_SPAN = 10, 200, 105, 3000
# consecutive wins: a flat bonus on every win once the streak is long enough (a loss resets it)
STREAK_BONUS = [(10, 60), (5, 40), (3, 20)]       # (wins in a row incl. this one, bonus points)
NAMES = ['Aldric', 'Brynn', 'Caelum', 'Dara', 'Eamon', 'Fenna', 'Galen', 'Hesper', 'Ilsa', 'Joren', 'Kaela',
         'Lorcan', 'Maren', 'Niall', 'Orla', 'Perrin', 'Quill', 'Rhea', 'Soren', 'Tamsin', 'Ulric', 'Vesna',
         'Wren', 'Xander', 'Yara', 'Zephyr', 'Ashe', 'Bastian', 'Corin', 'Delia', 'Elric', 'Fiora', 'Gideon',
         'Halia', 'Ivo', 'Juno', 'Kestrel', 'Lys', 'Mirel', 'Nox', 'Oriel', 'Pell', 'Roan', 'Sable', 'Tiber']
INFO_DEFAULT = {'JmFn3g9t': '1', 'U4pMNjy0': '0', '69vnphig': '0', '8CEu9Kcm': '0', '20iEWRCV': '0',
                '0rAkzg7L': '0', '8uJ1XGwx': '1', '2rqxaZ6K': '0', 'xL0p2MkY': ''}


def _i(x, d=0):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return d


class Arena:
    def __init__(self, srv):
        """srv: the server module (players, unit data, helpers)."""
        self.S = srv
        self.ranks = []          # [(rank, name, lo, hi, reward type, reward)]
        self.ai_rows = []
        for a in sorted(srv.T['action'], key=lambda a: a['n']):
            b = srv.BLOBS[a['resp']]
            if not self.ranks and isinstance(b, dict) and b.get('6kWq78zx'):
                self.ranks = [(_i(r['JmFn3g9t']), r.get('rGm09bav'), _i(r['w0aTd94Y']), _i(r['1U3eBCyY']),
                               str(r.get('IkmC8gG2') or ''), str(r.get('empaR60j') or '')) for r in b['6kWq78zx']]
            if a['cls'] == 'ArenaBattleStartRequest' and b.get('89ausgc4'):
                self.ai_rows = b['89ausgc4']
        self.ranks.sort()

    # ------------------------------------------------------------------ player state
    def info(self, pl):
        rows = pl['state'].get('8jBJ7uKR')
        row = (rows[0] if isinstance(rows, list) and rows else rows) if rows else None
        if not isinstance(row, dict):
            row = dict(INFO_DEFAULT)
        row[self.S.F_USERID] = pl['user_id']
        pl['state']['8jBJ7uKR'] = [row]
        return row

    def rank_of(self, points):
        r = 1
        for rank, _, lo, _, _, _ in self.ranks:
            if points >= lo:
                r = rank
        return r

    def regen_orbs(self, pl):
        t = self.S.team_row(pl['state'])
        if t is None:
            return
        mx = _i(t.get('9m5FWR8q'), 3) or 3
        now = time.time()
        if 'orb_time' not in pl:            # first time the server tracks orbs: start full
            t['YS2JG9no'] = str(mx)
        orbs = _i(t.get('YS2JG9no'))
        if orbs >= mx:
            pl['orb_time'] = now
            t['jp9s8IyY'] = '0'
            return
        last = pl.get('orb_time') or now
        got = int((now - last) // ORB_SECONDS)
        if got:
            orbs = min(mx, orbs + got)
            last += got * ORB_SECONDS
            t['YS2JG9no'] = str(orbs)
        pl['orb_time'] = now if orbs >= mx else last
        # The game's timer (FightRestTimer) is the time until ALL orbs are back: it counts it down and
        # adds an orb each time it passes a whole hour (DefineMst RecoverTimeFight 0BPn68DG = 3600).
        nxt = int(ORB_SECONDS - (now - pl['orb_time']))
        t['jp9s8IyY'] = '0' if orbs >= mx else str((mx - orbs - 1) * ORB_SECONDS + max(1, nxt))

    def position(self, pl, points):
        """Ranking place among everyone on this server."""
        better = 0
        for other in self.S.all_players():
            if other.get('user_id') == pl['user_id']:
                continue
            info = other.get('state', {}).get('8jBJ7uKR')
            info = info[0] if isinstance(info, list) and info else info
            if isinstance(info, dict) and _i(info.get('U4pMNjy0')) > points:
                better += 1
        return better + 1

    # ------------------------------------------------------------------ opponents
    def squad_units(self, pl):
        """The player's arena squad (or their quest squad if they never set one): [(unit row, leader?)].
        Squad rows: U9ABSYEp kind (1 quests, 2 arena), zsiAn9P1 squad no., gr48vsdJ 0 = leader."""
        rows = [r for r in self.S._rows(pl['state'].get('dX7S2Lc1')) if isinstance(r, dict)]
        if not rows:
            return []
        kinds = {str(r.get('U9ABSYEp')) for r in rows}
        kind = '2' if '2' in kinds else '1' if '1' in kinds else sorted(kinds)[0]
        rows = [r for r in rows if str(r.get('U9ABSYEp')) == kind]
        nums = sorted({str(r.get('zsiAn9P1')) for r in rows}, key=lambda x: _i(x))
        if kind == '1':
            sq = self.S.active_squad(pl)
        else:                                   # the Arena squad chosen in the game (gKNfIZiA)
            t = self.S.team_row(pl['state']) or {}
            sq = str(_i(t.get('gKNfIZiA')))
        sq = sq if sq in nums else nums[0]
        out, seen = [], set()
        for r in sorted((r for r in rows if str(r.get('zsiAn9P1')) == sq), key=lambda r: _i(r.get('XuJL4pc5'))):
            u = pl['units'].get(r.get('edy7fq3L'))
            if u and r.get('edy7fq3L') not in seen:
                seen.add(r.get('edy7fq3L'))
                out.append((u, str(r.get('gr48vsdJ')) == '0'))
        return out[:6]

    def strength(self, pl):
        um = self.S.unit_mst()
        sq = self.squad_units(pl)
        if not sq:
            return 2, 1
        rar = sorted(_i(um.get(u.get('pn16CNah'), {}).get('7ofj5xa1'), 2) for u, _ in sq)
        lv = sorted(_i(u.get('D9wXQI2V'), 1) for u, _ in sq)
        return rar[len(rar) // 2], lv[len(lv) // 2]

    def unit_pool(self):
        """Units NPC opponents and NPC helpers may use: only ones players can actually get right now -
        units on summons that are showing and capturable units from quests that are open (minus units
        switched off on the admin page). Unreleased units (e.g. collab units like Deemo) can crash the
        game's unit pages, so they never show up. Rebuilt every 5 minutes so admin changes count."""
        now = time.time()
        if getattr(self, '_pool', None) is not None and now - getattr(self, '_pool_at', 0) < 300:
            return self._pool
        um = self.S.unit_mst()
        ids = set()
        try:
            sm = self.S.summon_editor()
            for gid in list(sm.order) + [g for g in (self.S.POOLS or {}) if g not in sm.order]:
                if gid == 'default' or sm.hidden(gid):
                    continue
                for lst in (sm.pool(gid).get('units') or {}).values():
                    ids.update(str(x) for x in lst)
            for mid, spec in self.S.BATTLES.items():
                if spec.get('captures') and str(mid) in self.S.MISSIONS and not self.S.mission_closed(mid):
                    ids.update(str(x) for x in spec['captures'])
            ids -= {str(x) for x in self.S.admin_settings()['off'].get('units', [])}
        except Exception:
            self.S.log.exception('arena: could not work out the live unit pool')
            ids = set()
        if not ids:                                  # fall back to every known unit
            for p in (self.S.POOLS or {}).values():
                for lst in (p.get('units') or {}).values():
                    ids.update(lst)
        pool = {}
        for u in ids:
            m = um.get(u)
            if m and str(m.get('xP9oi5Z2', '0')) == '0':
                pool.setdefault(_i(m.get('7ofj5xa1'), 1), []).append(u)
        for v in pool.values():
            v.sort()
        self._pool, self._pool_at = pool, now
        return pool

    def unit_json(self, uid, lv, n):
        """One opponent unit, stats from the game's unit data at that level."""
        m = self.S.unit_mst().get(uid, {})
        mx = max(1, _i(m.get('EI1DF8Yt'), 1))
        lv = max(1, min(mx, lv))
        f = (lv - 1) / max(1, mx - 1)

        def stat(lo, hi):
            a, b = _i(m.get(lo)), _i(m.get(hi))
            return str(int(a + (b - a) * f))
        return {'P_ADD_ATK': '0', 'P_ADD_DEF': '0', 'P_ADD_HEL': '0', 'P_ADD_HP': '0',
                'P_ATK': stat('i9Tn7kYr', 'omuyP54D'), 'P_DEF': stat('q78KoWsg', '32INDST4'),
                'P_EQP_ITEM_ID': '0', 'P_EQP_ITEM_ID2': '0', 'P_EXTRA_PASSIVE_SKILL_ID': '0',
                'P_EXTRA_PASSIVE_SKILL_ID2': '0', 'P_EXTRA_SKILL_ID': '0', 'P_EXTRA_SKILL_LV': '0', 'P_EXT_ATK': '0',
                'P_EXT_DEF': '0', 'P_EXT_HEL': '0', 'P_EXT_HP': '0', 'P_FE_INFO': '',
                'P_HEL': stat('92ij6UGB', 'X9P3AN5d'), 'P_HP': stat('UZ1Bj7w2', '3WMz78t6'), 'P_IMG_TYPE': '0',
                'P_LV': str(lv), 'P_OMNI_LV': '0', 'P_SKILL_ID': str(m.get('nj9Lw7mV') or '0'),
                'P_SKILL_LV': str(random.randint(1, 10) if lv > 1 else 1), 'P_UNIT_ID': uid,
                'P_UNIT_TYPE_ID': str(random.randint(1, 5)), 'P_USER_UNIT_ID': str(n)}

    def from_unit_row(self, u, n):
        """A real player's unit as the arena shows it - with its unit-type extra stats (P_ADD_*), imps
        (P_EXT_*, as the real server sends them), both spheres, SBB and its level, and SP enhancements. The game adds the sphere effects
        itself from the sphere ids."""
        z = lambda v: '0' if str(v or '').strip() in ('', '-1') else str(v)
        slot2_open = str(u.get('RXfC31FA')) not in ('-1', '') or u.get('5gXxT7LZ')
        return {'P_ADD_ATK': z(u.get('RT4CtH5d')), 'P_ADD_DEF': z(u.get('GcMD0hy6')),
                'P_ADD_HEL': z(u.get('C1HZr3pb')), 'P_ADD_HP': z(u.get('cuIWp89g')),
                'P_ATK': str(u.get('67CApcti', '0')), 'P_DEF': str(u.get('q08xLEsy', '0')),
                'P_EQP_ITEM_ID': z(u.get('Ge8Yo32T')), 'P_EQP_ITEM_ID2': z(u.get('mZA7fH2v')) if slot2_open else '0',
                'P_EXTRA_PASSIVE_SKILL_ID': '0', 'P_EXTRA_PASSIVE_SKILL_ID2': '0',
                'P_EXTRA_SKILL_ID': z(u.get('iEFZ6H19')), 'P_EXTRA_SKILL_LV': z(u.get('RQ5GnFE2')),
                'P_EXT_ATK': z(u.get('t4m1RH6Y')), 'P_EXT_DEF': z(u.get('e6mY8Z0k')),
                'P_EXT_HEL': z(u.get('X6jf8DUw')), 'P_EXT_HP': z(u.get('TokWs1B3')),
                'P_FE_INFO': str(u.get('Fnxab5CN') or ''), 'P_HEL': str(u.get('PWXu25cg', '0')),
                'P_HP': str(u.get('e7DK0FQT', '0')),
                'P_IMG_TYPE': '0', 'P_LV': str(u.get('D9wXQI2V', '1')), 'P_OMNI_LV': '0',
                'P_SKILL_ID': str(u.get('nj9Lw7mV') or '0'), 'P_SKILL_LV': str(u.get('3NbeC8AB') or '1'),
                'P_UNIT_ID': str(u.get('pn16CNah')), 'P_UNIT_TYPE_ID': str(u.get('nBTx56W9') or '1'),
                'P_USER_UNIT_ID': str(n)}

    def make_opponent(self, pl):
        me = self.info(pl)
        my_pts = _i(me.get('U4pMNjy0'))
        others = []
        for o in self.S.all_players():
            if o.get('user_id') != pl['user_id'] and self.squad_units(o):
                others.append(o)
        seq = pl.get('arena_seq', 900000)
        if others and random.random() < 0.5:
            o = random.choice(others)
            oi = o.get('state', {}).get('8jBJ7uKR')
            oi = oi[0] if isinstance(oi, list) and oi else (oi or {})
            sq = self.squad_units(o)
            units = [self.from_unit_row(u, seq + i) for i, (u, _) in enumerate(sq)]
            leader = next((i for i, (_, lead) in enumerate(sq) if lead), 0)
            t = self.S.team_row(o['state']) or {}
            opp = {'id': o['user_id'], 'name': self.S.shown_name(o), 'lv': str(t.get('D9wXQI2V', '1')),
                   'points': _i(oi.get('U4pMNjy0')), 'battles': _i(oi.get('69vnphig')), 'wins': _i(oi.get('8CEu9Kcm')),
                   'units': units, 'leader': leader, 'real': True}
        else:
            rar, lv = self.strength(pl)
            pool = self.unit_pool()
            units = []
            for i in range(5):
                r = max(1, min(max(pool), rar + random.choice([-1, 0, 0, 0, 1])))
                while r not in pool and r > 1:
                    r -= 1
                uid = random.choice(pool.get(r) or pool[min(pool)])
                units.append(self.unit_json(uid, int(lv * random.uniform(0.8, 1.1)), seq + i))
            pts = max(0, int(my_pts * random.uniform(0.85, 1.15)) + random.randint(-50, 50))
            battles = random.randint(5, 40)
            t = self.S.team_row(pl['state']) or {}
            opp = {'id': 'npc' + self.S.rand_id(5), 'name': random.choice(NAMES),
                   'lv': str(max(1, _i(t.get('D9wXQI2V'), 10) + random.randint(-5, 5))), 'points': pts,
                   'battles': battles, 'wins': random.randint(0, battles), 'units': units,
                   'leader': random.randrange(5), 'real': False}
        pl['arena_seq'] = seq + 10
        pl['arena_opp'] = opp
        # the game asks for opponents twice in a row and then fights the one the player picked
        # (ArenaBattleStart names it) - keep the last few, so the fight is against that one
        recent = [o for o in (pl.get('arena_opps') or []) if isinstance(o, dict) and o.get('id') != opp['id']]
        pl['arena_opps'] = (recent + [opp])[-6:]
        return opp

    # ------------------------------------------------------------------ requests
    def matching(self, pl):
        o = self.make_opponent(pl)
        lead = o['units'][o['leader']]['P_UNIT_ID'] if o['units'] else '10011'
        row = {self.S.F_USERID: o['id'], 'B5JQyV8j': o['name'], 'D9wXQI2V': o['lv'],
               'JmFn3g9t': str(self.rank_of(o['points'])), 'pn16CNah': lead, '69vnphig': str(o['battles']),
               '8CEu9Kcm': str(o['wins']), '20iEWRCV': '0', '0rAkzg7L': '0', 'U4pMNjy0': str(o['points']),
               '98WfKiyA': '', '2pAyFjmZ': '0'}
        self.S.log.info('arena: matched against %s (%s, %d points)', o['name'], 'player' if o['real'] else 'challenger',
                        o['points'])
        return {'fQ3XImM2': [row]}

    def start(self, pl, req):
        o = pl.get('arena_opp')
        want = next((str(r.get(self.S.F_USERID)) for r in self.S._rows(req.get('9NX83YVe'))
                     if isinstance(r, dict) and r.get(self.S.F_USERID)), None)
        if want:
            o = next((x for x in reversed(pl.get('arena_opps') or []) if str(x.get('id')) == want), o)
            pl['arena_opp'] = o
        if not o:
            return None, 'No opponent - go back and search again.'
        self.regen_orbs(pl)
        t = self.S.team_row(pl['state'])
        if t is not None:
            if _i(t.get('YS2JG9no')) < 1:
                return None, 'No arena orbs left. One comes back every hour.'
            if _i(t.get('YS2JG9no')) >= (_i(t.get('9m5FWR8q'), 3) or 3):
                pl['orb_time'] = time.time()
            t['YS2JG9no'] = str(_i(t.get('YS2JG9no')) - 1)
        self.regen_orbs(pl)
        bid = pl['arena_battle'] = pl.get('arena_battle', 0) + 1
        o['battle'] = str(bid)
        deck = [{'P_DISPORDER': str(i), 'P_MEMBER_TYPE': '0' if i == o['leader'] else '1',
                 'P_USER_UNIT_ID': u['P_USER_UNIT_ID']} for i, u in enumerate(o['units'])]
        row = {self.S.F_USERID: o['id'], 'xL0p2MkY': str(bid),
               'INT1K9Pf': json.dumps({'P_FRIEND_MESSAGE': '', 'P_GUILD_ID': '', 'P_LV': o['lv'], 'P_USER_ID': o['id']},
                                      separators=(',', ':')),
               'nZc1Ip3N': json.dumps(deck, separators=(',', ':')),
               'U42YcFNu': json.dumps(o['units'], separators=(',', ':')), 'bM7RLu5K': '', 'IvuccUuD': '0'}
        return {'9NX83YVe': [row], self.S.G_TEAM: [dict(t or {})], '89ausgc4': self.ai_rows}, None

    @staticmethod
    def performance(res):
        """0..1: how well the fight went - units left standing, HP left, damage done to the other side
        (the game's own end-of-battle report: WDYR11jJ / chy3HnIm = each unit's 'HP left:max HP')."""
        def hp(text):
            out = []
            for part in str(text or '').split(','):
                a, _, b = part.partition(':')
                if _i(b) > 0:
                    out.append((max(0, _i(a)), _i(b)))
            return out
        mine, foe = hp(res.get('WDYR11jJ')), hp(res.get('chy3HnIm'))
        if not mine or not foe:
            return 0.5
        alive = sum(1 for now, _ in mine if now > 0) / len(mine)
        left = sum(now for now, _ in mine) / sum(m for _, m in mine)
        dealt = 1 - sum(now for now, _ in foe) / sum(m for _, m in foe)
        return max(0.0, min(1.0, (alive + left + dealt) / 3))

    @staticmethod
    def streak_bonus(streak):
        return next((b for n, b in STREAK_BONUS if streak >= n), 0)

    def end(self, pl, req, badge):
        o = pl.pop('arena_opp', None) or {}
        res = ((req.get('yTEvm85f') or [{}])[0])
        rule = ((req.get('fq2C03jh') or [{}])[0])
        win = str(res.get('3pAQY5KW')) == '1'
        me = self.info(pl)
        before = _i(me.get('U4pMNjy0'))
        diff = _i(o.get('points'), before) - before            # opponent's ABP above (+) or below (-) yours
        perf = self.performance(res)
        streak = pl.get('arena_streak', 0) + 1 if win else 0
        bonus = 0
        if win:
            base = max(ABP_MIN, min(ABP_MAX, ABP_EVEN + diff * (ABP_MAX - ABP_EVEN) / ABP_SPAN))
            gain = max(ABP_MIN, round(base * (0.5 + 0.5 * perf)))
            bonus = self.streak_bonus(streak)
            delta = gain + bonus
        else:
            base = max(ABP_MIN, min(ABP_MAX, ABP_EVEN - diff * (ABP_MAX - ABP_EVEN) / ABP_SPAN))
            delta = -min(before, max(ABP_MIN, round(base * (1 - 0.5 * perf))))
        after = max(0, before + delta)
        old_rank, new_rank = self.rank_of(before), self.rank_of(after)
        karma = 100 * new_rank if win else 0
        if karma:
            self.S.check_and_apply(pl, {'karma': karma})
        # Rank-up cutscenes are an event flag: each rank's cutscene (and its reward) comes once per account.
        # The game plays a cutscene for every rank between "rank before" and "rank after", so after a
        # drop and climb back, "rank before" is sent as the highest rank already seen.
        seen = max(_i(pl.get('arena_best_rank'), 1), _i(me.get('8uJ1XGwx'), 1))
        shown_from = max(old_rank, min(seen, new_rank))
        # a cutscene someone missed (e.g. the game crashed) can be shown again once: pl['arena_show_from']
        # = the rank to show from. Rewards already given are only shown, not given twice.
        again = _i(pl.get('arena_show_from'), 0)
        if again and new_rank > again:
            shown_from = min(shown_from, again)
            pl.pop('arena_show_from', None)
        rewards = []
        for rank, name, lo, hi, kind, reward in self.ranks:
            if shown_from < rank <= new_rank:
                what, _, n = reward.partition(':')
                n = _i(n, 1)
                if rank <= seen:
                    pass                  # replayed cutscene: the reward was given the first time
                elif kind == '1':
                    self.S.add_present(pl, 8, '', n, f'Arena rank up: {name}')
                elif kind == '2' and what not in ('', '0'):
                    self.S.add_present(pl, 4, what, n, f'Arena rank up: {name}')
                # 1ef7JNSC = gems given, 4T0Q2Bh5 = item given (as bfrevive sends a 1-gem rank up)
                rewards.append({'1ef7JNSC': str(n) if kind != '2' else '0',
                                '4T0Q2Bh5': f'{what}:{n}' if kind == '2' else '', 'ZBwx5ct3': str(n)})
                pl['arena_best_rank'] = max(rank, _i(pl.get('arena_best_rank'), 1))
        best = max(new_rank, seen)
        me.update({'JmFn3g9t': str(new_rank), '8uJ1XGwx': str(best),       # highest rank ever (doesn't drop)
                   'U4pMNjy0': str(after),
                   '69vnphig': str(_i(me.get('69vnphig')) + 1),
                   '8CEu9Kcm': str(_i(me.get('8CEu9Kcm')) + (1 if win else 0)),
                   '2rqxaZ6K': str(self.position(pl, after)), 'xL0p2MkY': ''})
        try:
            self.S.achievement_engine().record_arena(pl, req)
        except Exception:
            self.S.log.exception('could not count arena stats')
        if win:
            try:
                self.S.daily_engine().event(pl, 'arena')
            except Exception:
                self.S.log.exception('could not count daily tasks')
        pl['arena_streak'] = streak
        pl['arena_best_streak'] = max(pl.get('arena_best_streak', 0), pl['arena_streak'])
        self.regen_orbs(pl)
        self.S.log.info('arena: %s vs %s (%+d ABP apart, fought %.0f%%) - %+d points%s (%d), karma +%d%s',
                        'win' if win else 'loss', o.get('name', '?'), diff, perf * 100, after - before,
                        f' incl. {bonus} for {streak} wins in a row' if bonus else '', after, karma,
                        f', rank up to {new_rank}' if new_rank > old_rank else '')
        out = {'yTEvm85f': [{'xL0p2MkY': str(res.get('xL0p2MkY') or o.get('battle', '')), '3pAQY5KW': str(res.get('3pAQY5KW', '0')),
                             's07yWuZR': str(shown_from), 'C1w4H62i': str(new_rank), 'jPoM1UV8': str(before),
                             'TJ9cQ8AY': str(after), 'HTVh8a65': str(karma), 'Sx5H0Ikn': '0', 'tvWWL9wI': str(bonus),
                             'ciDqCjAF': str(rule.get('ciDqCjAF', '3')), '59b1eE6N': str(rule.get('59b1eE6N', '100')),
                             '69vnphig': me['69vnphig']}],
               'okd0y3Ir': rewards, '8jBJ7uKR': [me], self.S.G_TEAM: [dict(self.S.team_row(pl['state']) or {})]}
        if pl['state'].get('PQ56vbkI'):
            out['PQ56vbkI'] = pl['state']['PQ56vbkI']
        if badge is not None:
            out[self.S.G_BADGE] = badge
        return out

    def ranking(self, pl):
        rows = []
        for o in self.S.all_players():
            if o.get('user_id') == pl['user_id']:
                continue                 # the game adds your own row itself - listing you too made a twin
            oi = o.get('state', {}).get('8jBJ7uKR')
            oi = oi[0] if isinstance(oi, list) and oi else oi
            if not isinstance(oi, dict):
                continue
            sq = self.squad_units(o)
            lead = next((u for u, lead in sq if lead), sq[0][0] if sq else {})
            t = self.S.team_row(o['state']) or {}
            rows.append({self.S.F_USERID: o['user_id'], 'B5JQyV8j': self.S.shown_name(o), '2Fh3J7ng': str(t.get('D9wXQI2V', '1')),
                         'pn16CNah': str(lead.get('pn16CNah', '10011')), '4A6LzBxr': str(t.get('D9wXQI2V', '1')),
                         'JmFn3g9t': str(oi.get('JmFn3g9t', '1')), 'U4pMNjy0': str(oi.get('U4pMNjy0', '0')),
                         '69vnphig': str(oi.get('69vnphig', '0')), '8CEu9Kcm': str(oi.get('8CEu9Kcm', '0')),
                         '20iEWRCV': str(oi.get('20iEWRCV', '0')), '0rAkzg7L': '0', 'NH65Wj0f': '0', '2pAyFjmZ': '0'})
        rows.sort(key=lambda r: -_i(r['U4pMNjy0']))
        for i, r in enumerate(rows, 1):
            r['2rqxaZ6K'] = str(i)
        return {'BYaF62TE': rows[:50]}
