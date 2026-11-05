"""
Frontier Hunter (the Akras Summoners' Hall Survey Office on the Randall map).

Every season of the game's own Frontier Hunter tables (URLMst: mQC4s5ka seasons, 5M8jI4cP missions,
zW1i02aG grades, 4C1Wt8sS score rewards, nUmaEC41 ranking kinds, P8V71kbw ranking rewards, h09mEvDR
Hunter Rank rewards) runs here in order, from season 1, one season after another. Each season lasts
SEASON_DAYS (admin setting fh_days); when it ends the rankings are settled and the rewards go to the
gift box. After season 97 it starts again from 1.

Requests (from the game's code):
  ChallengeBaseRequest            lobby: season, Hunter Rank / Survey energy, your points and rank,
                                  your best score per mission
  ChallengeUserInfoRequest        a7HQ9sEB {c5yZnpB4 season, j28VNcUW mission}: the mission screen
  ChallengeMissionStartRequest    a7HQ9sEB: pay one Survey energy (Aube, max SziK4Xg1, one back every
                                  3xAsgHL8 s) - then the game sends a normal MissionStart carrying
                                  cvg8hzp9 {c5yZnpB4}
  MissionEndRequest + cvg8hzp9    the run's score, worked out by the game (ZYSWU9V1 TPR79fyI)
  ChallengeRankingRequest         Hmiwj75u: overall (NH65Wj0f 1), per mission / MVP top 30 (2), friends (0)
  ChallengeRankRewardRequest      p73SJiWv: what the last season's ranking gave you
  ShopUse 60IsqxDt 5 (in the lobby) refill Survey energy for gems
Reply groups: cvg8hzp9 ChallengeInfo, kN2i7qds header (Sv80kL5r HR, 38sHatGk energy, RHKA30s5 seconds
to the next one), s7A3bGLe user info, cv9rC2KN best per mission, 7B5Ue9kH start, 21BqKn8b end
(Cdv07KEU grade, Najhr8m6 zel, HTVh8a65 karma, BSY9X6Mo rank before, vV4P53m7 rank now, TPR79fyI
points), ZSC3t4Fv rewards, ZYSWU9V1 result.
"""
import datetime
import json
import os
import threading
import time

DAY = 86400
SEASON_DAYS = 7                  # default length of a season (admin page: fh_days)
STRENGTH = 2.5                   # enemies' HP / ATK / DEF against the story references (admin: fh_strength)
SEARCH_DIV = 100                 # survey points a run gives = its score / this
TOP = 30                         # rows per ranking list (as the game's)
# Hunter Rank by overall place at the end of a season (this server's, for every season - instead of the
# game's 1-500 / 501-3000 / ... brackets, made for millions of players): (from, to (0 = and below), +HR, comment)
HR_TIERS = [(1, 1, 7, "You're in first place! Hold on to it and your Hunter Rank goes up by 7!"),
            (2, 4, 3, "You're in the top 4! Keep this up and your Hunter Rank goes up by 3. First place gets 7!"),
            (5, 8, 2, "You're in the top 8! Keep this up and your Hunter Rank goes up by 2. Can you reach the top 4?"),
            (9, 0, 1, "Every Summoner who scores gets a Hunter Rank! Climb into the top 8 for more!")]
INTRO = 'You\'re here! The area to be surveyed this time is full of tough enemies, but I\'m sure you can take care of them!'


def _i(x, d=0):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return d


def _ts(t):
    return datetime.datetime.utcfromtimestamp(t).strftime('%Y-%m-%d %H:%M:%S')


class FrontierHunter:
    def __init__(self, srv):
        self.S = srv
        self.mu = threading.RLock()
        self.path = os.path.join(srv.SAVE_DIR, 'frontier_hunter.json')
        try:
            self.data = json.load(open(self.path, encoding='utf-8'))
        except Exception:
            self.data = {}
        self.data.setdefault('runs', {})
        cap = srv.BY_ID[srv.RID['URLMstRequest']][-1]
        m = srv.BLOBS[cap['resp']]
        rows = lambda g: [r for r in (m.get(g) or []) if isinstance(r, dict)]
        self.seasons = {r['c5yZnpB4']: r for r in rows('mQC4s5ka')}
        self.order = sorted(self.seasons, key=int)
        self.missions = {}
        for r in rows('5M8jI4cP'):
            self.missions.setdefault(r['c5yZnpB4'], []).append(r)
        self.mis = {r['j28VNcUW']: r for r in rows('5M8jI4cP')}
        self.grades = {}
        for r in rows('zW1i02aG'):
            self.grades.setdefault(r['j28VNcUW'], []).append((_i(r['TPR79fyI']), r['Cdv07KEU']))
        self.score_rewards = {}
        for r in rows('4C1Wt8sS'):
            self.score_rewards.setdefault(r['j28VNcUW'], []).append(r)
        self.mvp = {}
        for r in rows('nUmaEC41'):
            self.mvp.setdefault(r['c5yZnpB4'], []).append(r)
        self.rank_rewards = {}
        for r in rows('P8V71kbw'):
            self.rank_rewards.setdefault(r['c5yZnpB4'], []).append(r)
        self.hr = {_i(r['Sv80kL5r']): r for r in rows('h09mEvDR')}
        d = m.get('VkoZ5t3K')
        d = d[0] if isinstance(d, list) and d else (d or {})
        self.max_energy = max(1, _i(d.get('SziK4Xg1'), 3))
        self.energy_time = max(60, _i(d.get('3xAsgHL8'), 10800))

    # ------------------------------------------------------------------ settings / schedule
    def settings(self):
        s = self.S.admin_settings()
        return {'on': bool((s.get('features') or {}).get('frontierhunter')),
                'days': max(1, _i(s.get('fh_days'), SEASON_DAYS)),
                'strength': float(s.get('fh_strength') or STRENGTH)}

    def save(self):
        tmp = self.path + '.tmp'
        json.dump(self.data, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False)
        os.replace(tmp, self.path)

    def start(self):
        """When season 1 began on this server: the first time a player logs in or opens Frontier Hunter
        after it is switched on (counted from 00:00 UTC that day)."""
        if not self.data.get('start'):
            if not self.settings()['on']:
                return int(time.time() // DAY * DAY)      # not switched on yet: nothing starts
            self.data['start'] = int(time.time() // DAY * DAY)
            self.save()
            self.S.log.info('Frontier Hunter: season 1 starts %s', _ts(self.data['start']))
        return self.data['start']

    def period(self):
        return self.settings()['days'] * DAY

    def run_at(self, now=None):
        """(run number from 0, season id, start, end) of the season running at now."""
        now = now or time.time()
        n = max(0, int((now - self.start()) // self.period()))
        st = self.start() + n * self.period()
        return n, self.order[n % len(self.order)], st, st + self.period()

    def run(self, n):
        r = self.data['runs'].setdefault(str(n), {'users': {}, 'settled': False})
        r.setdefault('season', self.order[n % len(self.order)])
        return r

    def term_text(self, st, en):
        f = lambda t: datetime.datetime.utcfromtimestamp(t).strftime('%b %d').replace(' 0', ' ')
        return f'{f(st)} - {f(en - 1)} (UTC)'

    def patch_master(self, resp):
        """URLMst mQC4s5ka: every season's dates follow this server's schedule (season 1 first)."""
        rows = resp.get('mQC4s5ka')
        if not isinstance(rows, list):
            return
        n, _, st, _ = self.run_at()
        cycle = n // len(self.order)
        p = self.period()
        for r in rows:
            if not isinstance(r, dict) or r.get('c5yZnpB4') not in self.seasons:
                continue
            k = cycle * len(self.order) + self.order.index(r['c5yZnpB4'])
            s0 = self.start() + k * p
            e0 = s0 + p
            r.update({'qA7M9EjP': _ts(s0), 'SzV0Nps7': _ts(e0 - 1), '7S5tL21M': _ts(e0 - 1),
                      '6dWR3qrQ': _ts(e0), '1JZuvmo7': _ts(e0 + p), '4nq0Zoau': _ts(e0 + p)})

    # ------------------------------------------------------------------ the player's side
    def me(self, pl):
        f = pl.setdefault('fh', {})
        f.setdefault('hr', 1)
        f.setdefault('energy', self.max_energy)
        f.setdefault('energy_t', 0)
        if f['energy'] < self.max_energy and f['energy_t']:
            k = int((time.time() - f['energy_t']) // self.energy_time)
            if k > 0:
                f['energy'] = min(self.max_energy, f['energy'] + k)
                f['energy_t'] += k * self.energy_time
        if f['energy'] >= self.max_energy:
            f['energy_t'] = 0
        return f

    def entry(self, pl, n):
        u = self.run(n)['users'].setdefault(pl['user_id'], {'search': 0, 'best': {}, 'mvp': {}, 'paid': {}})
        t = self.S.team_row(pl['state']) or {}
        try:
            lead = next((x for x, ld in self.S.arena_engine().squad_units(pl) if ld), {})
        except Exception:
            lead = {}
        u.update({'name': self.S.shown_name(pl), 'lv': str(t.get('D9wXQI2V', '1')),
                  'unit': str(lead.get('pn16CNah', '10011')), 'unit_lv': str(lead.get('D9wXQI2V', '1')),
                  'hr': self.me(pl)['hr']})
        return u

    def header(self, pl):
        f = self.me(pl)
        nxt = 0 if f['energy'] >= self.max_energy or not f['energy_t'] else \
            max(0, int(self.energy_time - (time.time() - f['energy_t'])))
        return {'Sv80kL5r': str(f['hr']), '38sHatGk': str(f['energy']), '6mh0jiKJ': '0',
                'RHKA30s5': str(nxt), 'vvXp7Uek': '0'}

    def place(self, n, uid, kind='1', mission=None):
        """Your place in a ranking (0 = not in it)."""
        rows = self.ranked(n, kind, mission)
        return next((i for i, (u, _) in enumerate(rows, 1) if u == uid), 0)

    def value(self, u, kind, mission=None):
        if kind == '1':
            return _i(u.get('search'))
        if kind.startswith('1'):
            return _i((u.get('best') or {}).get(mission))
        return _i((u.get('mvp') or {}).get(kind))

    def ranked(self, n, kind='1', mission=None):
        users = self.run(n)['users']
        rows = [(uid, self.value(u, kind, mission)) for uid, u in users.items()]
        rows = [r for r in rows if r[1] > 0]
        rows.sort(key=lambda r: -r[1])
        return rows

    def comment(self, season, place, search):
        rr = [r for r in self.rank_rewards.get(season, []) if r.get('9w5e3ZJB') == '1']
        if not search:
            return next((r['WDUyI85C'] for r in rr if r.get('u7Ijep5o') == '0'), INTRO)
        for lo, hi, up, text in HR_TIERS:
            if place >= lo and (not hi or place <= hi):
                return text
        return INTRO

    @staticmethod
    def hr_up(place):
        return next((up for lo, hi, up, _ in HR_TIERS if place >= lo and (not hi or place <= hi)), 0)

    def user_info(self, pl):
        n, season, st, en = self.run_at()
        u = self.entry(pl, n)
        place = self.place(n, pl['user_id'])
        f = self.me(pl)
        return {'TPR79fyI': str(u['search']), 'QG5kcBz6': '1' if f.get('news') else '0', 'c5yZnpB4': season,
                'j0Uszek2': '1', 's2gM3deu': self.term_text(st, en), '2Cn0bWt9': '0',
                'vV4P53m7': str(place), 'WDUyI85C': self.comment(season, place, u['search'])}

    def mission_rows(self, pl):
        n, season, _, _ = self.run_at()
        u = self.entry(pl, n)
        return [{'j28VNcUW': r['j28VNcUW'], 'TPR79fyI': str(_i(u['best'].get(r['j28VNcUW']))), 'QG5kcBz6': '1'}
                for r in self.missions.get(season, [])]

    def lobby(self, pl):
        self.settle(pl)
        n, season, _, _ = self.run_at()
        pl['fh_ctx'] = time.time()
        return {'cvg8hzp9': {'c5yZnpB4': season}, 'kN2i7qds': self.header(pl), 's7A3bGLe': self.user_info(pl),
                'cv9rC2KN': self.mission_rows(pl)}

    # ------------------------------------------------------------------ requests
    def handle(self, pl, cls, req):
        with self.mu:
            out = self._handle(pl, cls, req)
            self.save()
            return out

    def _handle(self, pl, cls, req):
        S = self.S
        if not self.settings()['on']:
            return S.error_response('The Survey Office is closed right now.')
        row = ((req.get('a7HQ9sEB') or req.get('cvg8hzp9') or [{}])[0]) or {}
        if cls in ('ChallengeBaseRequest', 'ChallengeUserInfoRequest', 'ChallengeStartRequest'):
            pl.pop('fh_run', None)          # back in the office: a survey started and left is over
            return self.lobby(pl)
        if cls == 'ChallengeMissionStartRequest':
            n, season, _, _ = self.run_at()
            mid = str(row.get('j28VNcUW') or '')
            m = self.mis.get(mid)
            if not m or m['c5yZnpB4'] != season:
                return S.error_response('This survey is over. Go back to the Survey Office for the new one.')
            f = self.me(pl)
            cost = max(0, _i(m.get('d9RLTqo7'), 1))
            if f['energy'] < cost:
                return S.error_response('You have no Survey energy left. Wait for it to come back, or refill it.')
            if f['energy'] >= self.max_energy:
                f['energy_t'] = time.time()
            f['energy'] -= cost
            pl['fh_run'] = {'n': n, 'season': season, 'mission': mid, 't': time.time()}
            u = self.entry(pl, n)
            S.log.info('Frontier Hunter: %s starts %s (season %s) - energy left %s', pl.get('handle'),
                       (S.MISSIONS.get(mid) or {}).get('name', mid), season, f['energy'])
            out = self.lobby(pl)
            out['7B5Ue9kH'] = {'TPR79fyI': str(_i(u['best'].get(mid)))}
            return out
        if cls == 'ChallengeRankingRequest':
            return {'Hmiwj75u': self.ranking_rows(pl)}
        if cls == 'ChallengeRankRewardRequest':
            f = self.me(pl)
            rows = f.pop('news', None) or []
            out = self.lobby(pl)
            out['p73SJiWv'] = rows
            return out
        return self.lobby(pl)

    def refill(self, pl, gems):
        """ShopUse type 5 in the Survey Office: energy back to full."""
        f = self.me(pl)
        f['energy'], f['energy_t'] = self.max_energy, 0
        self.S.log.info('Frontier Hunter: %s refilled Survey energy for %s gems', pl.get('handle'), gems)
        return {'kN2i7qds': self.header(pl)}

    # ------------------------------------------------------------------ battles
    def adjust_plan(self, pl, mid, plan):
        """A survey's battles: tougher than the story fights they're built from, and no drops (the
        rewards are the score, the ranking and Hunter Rank)."""
        run = pl.get('fh_run') or {}
        if run.get('mission') != str(mid):
            return plan
        n = _i(run.get('n'))
        k = self.settings()['strength'] * (1 + 0.02 * (self.order.index(run['season']) if run['season'] in self.order else 0))
        for b in plan.get('battles') or []:
            b['unit_drops'], b['chests'] = [], []
            for m in b.get('mons') or []:
                m['drop'] = None
                for s in ('hp', 'atk', 'def'):
                    if m.get(s):
                        m[s] = int(m[s] * k)
        return plan

    def mission_end(self, pl, req, resp):
        run = pl.pop('fh_run', None)
        if not run:
            return
        with self.mu:
            n, mid = _i(run['n']), run['mission']
            u = self.entry(pl, n)
            res = (req.get('ZYSWU9V1') or [{}])[0] or {}
            br = (req.get('rXvA1E5y') or [{}])[0] or {}
            score = max(0, _i(res.get('TPR79fyI')))
            before = self.place(n, pl['user_id'])
            gained = score // SEARCH_DIV
            u['search'] = _i(u['search']) + gained
            old = _i(u['best'].get(mid))
            u['best'][mid] = max(old, score)
            mv = u['mvp']
            turns = [x for x in str(br.get('Lu0pAk1R') or '').split(',') if x.strip()]
            for k, v in (('201', _i(br.get('U8uZLA34'))), ('202', _i(br.get('EcDR3f6M'))), ('203', _i(br.get('xFH5i4o8'))),
                         ('204', _i(br.get('GRCVD1i8')) + _i(br.get('Uy15wSJd'))),
                         ('205', sum(1 for x in turns if _i(x) == 1)), ('206', 1)):
                mv[k] = _i(mv.get(k)) + max(0, v)
            helper = str(pl.pop('fh_helper', '') or '')
            if helper and helper in self.run(n)['users']:
                hm = self.run(n)['users'][helper].setdefault('mvp', {})
                hm['207'] = _i(hm.get('207')) + 1
            # score rewards: each threshold once per season and mission
            paid = u.setdefault('paid', {}).setdefault(mid, [])
            rew = []
            for r in sorted(self.score_rewards.get(mid, []), key=lambda r: _i(r['TPR79fyI'])):
                if score >= _i(r['TPR79fyI']) and r['N1b6SUW4'] not in paid:
                    paid.append(r['N1b6SUW4'])
                    self.give(pl, r.get('30Kw4WBa'), r.get('TdDHf59J'), r.get('wJsB35iH'),
                              f"Frontier Hunter: {self.S.MISSIONS.get(mid, {}).get('name', mid)} - {_i(r['TPR79fyI']):,} points")
                    rew.append({'IkmC8gG2': '1', 'empaR60j': r['TPR79fyI'], '30Kw4WBa': r.get('30Kw4WBa'),
                                'TdDHf59J': r.get('TdDHf59J'), 'wJsB35iH': r.get('wJsB35iH'), '37moriMq': r.get('37moriMq', '0')})
            grade = '1'
            for need, g in sorted(self.grades.get(mid, [])):
                if score >= need:
                    grade = g
            self.save()
            resp['ZYSWU9V1'] = {'TPR79fyI': str(score), '7cev0fzQ': str(res.get('7cev0fzQ') or ''),
                                'pG2n1A28': str(res.get('pG2n1A28') or '0')}
            resp['21BqKn8b'] = {'Cdv07KEU': grade, 'Najhr8m6': str(_i(br.get('Najhr8m6'))),
                                'HTVh8a65': str(_i(br.get('HTVh8a65'))), 'BSY9X6Mo': str(before),
                                'vV4P53m7': str(self.place(n, pl['user_id'])), 'TPR79fyI': str(gained), '5Z1LNoyH': '0'}
            resp['ZSC3t4Fv'] = rew
            resp['kN2i7qds'] = self.header(pl)
            resp['s7A3bGLe'] = self.user_info(pl)
            resp['cvg8hzp9'] = {'c5yZnpB4': run['season']}
            self.S.log.info('Frontier Hunter: %s scored %s on %s (best %s, +%s points, rank %s -> %s)',
                            pl.get('handle'), f'{score:,}', mid, f'{max(old, score):,}', gained, before,
                            resp['21BqKn8b']['vV4P53m7'])

    def give(self, pl, ptype, target, count, note):
        ptype = str(ptype)
        if ptype == '10':
            return
        try:
            self.S.add_present(pl, int(ptype), str(target or ''), _i(count, 1), note)
        except Exception:
            self.S.log.exception('Frontier Hunter: could not give a reward')

    # ------------------------------------------------------------------ rankings
    def ranking_rows(self, pl):
        n, season, _, _ = self.run_at()
        me = pl['user_id']
        users = self.run(n)['users']
        self.entry(pl, n)
        try:
            friends = {u for u, st in (self.S.friend_engine().links.get(me) or {}).items() if str(st) in ('friend', '1')}
        except Exception:
            friends = set()

        def row(uid, val, place, info, kind, mission):
            u = users.get(uid) or {}
            return {'h7eY3sAK': uid, 'B5JQyV8j': u.get('name', ''), '2Fh3J7ng': u.get('lv', '1'),
                    'pn16CNah': u.get('unit', '10011'), '4A6LzBxr': u.get('unit_lv', '1'), 'U4pMNjy0': str(val),
                    'pG2n1A28': '0', '2Cn0bWt9': str(u.get('hr', 1)), 'vV4P53m7': str(place), 'NH65Wj0f': info,
                    '9w5e3ZJB': kind, 'j28VNcUW': mission or '0', '2pAyFjmZ': '1', 'jkldTrhL': '0',
                    '9i2xhMaJ': '1', 'iNy0ZU5M': '1', 'btZizNep': '1', 'RVVgyuor': '1'}
        out = []
        overall = self.ranked(n, '1')
        for i, (uid, v) in enumerate(overall[:TOP], 1):
            out.append(row(uid, v, i, '1', '0', None))
        mine = next(((i, v) for i, (u, v) in enumerate(overall, 1) if u == me), None)
        if not mine or mine[0] > TOP:
            out.append(row(me, mine[1] if mine else 0, mine[0] if mine else 9999, '1', '0', None))
        for r in self.mvp.get(season, []):
            kind = r['9w5e3ZJB']
            mission = r.get('j28VNcUW') if r.get('j28VNcUW') not in ('', '0') else None
            rk = self.ranked(n, kind, mission)
            for i, (uid, v) in enumerate(rk[:TOP], 1):
                out.append(row(uid, v, i, '2', kind, mission))
            mine = next(((i, v) for i, (u, v) in enumerate(rk, 1) if u == me), None)
            if not mine or mine[0] > TOP:
                out.append(row(me, mine[1] if mine else 0, mine[0] if mine else 9999, '2', kind, mission))
        fr = [(uid, v) for uid, v in overall if uid in friends or uid == me]
        for i, (uid, v) in enumerate(fr, 1):
            out.append(row(uid, v, i, '0', '0', None))
        return out

    # ------------------------------------------------------------------ end of a season
    def settle(self, current=None):
        """Seasons that are over: hand out the ranking rewards (gift box) and Hunter Rank. current = the
        player whose request is running (their save is changed in memory, not reloaded)."""
        with self.mu:
            cur = self.run_at()[0]
            for key, r in list(self.data['runs'].items()):
                if r.get('settled') or _i(key) >= cur:
                    continue
                try:
                    self._settle(_i(key), r, current)
                except Exception:
                    self.S.log.exception('Frontier Hunter: could not settle run %s', key)
                r['settled'] = True
                self.save()

    def _settle(self, n, run, current=None):
        S = self.S
        season = run['season']
        rr = self.rank_rewards.get(season, [])
        kinds = {'1': self.ranked(n, '1')}
        for m in self.mvp.get(season, []):
            mission = m.get('j28VNcUW') if m.get('j28VNcUW') not in ('', '0') else None
            kinds[m['9w5e3ZJB']] = self.ranked(n, m['9w5e3ZJB'], mission)
        wins = {}
        for place, (uid, _) in enumerate(kinds['1'], 1):      # Hunter Rank: this server's tiers
            up = self.hr_up(place)
            if up:
                wins.setdefault(uid, []).append(('1', place, {'30Kw4WBa': '10', 'TdDHf59J': '0', 'wJsB35iH': str(up),
                                                              '37moriMq': '0', 'j28VNcUW': '0'}))
        for kind, rows in kinds.items():
            for place, (uid, _) in enumerate(rows, 1):
                for r in rr:
                    if r.get('9w5e3ZJB') != kind or str(r.get('30Kw4WBa')) == '10':
                        continue
                    lo, hi = _i(r.get('u7Ijep5o')), _i(r.get('4BtYr6Eg'))
                    if lo and place >= lo and (not hi or place <= hi):
                        wins.setdefault(uid, []).append((kind, place, r))
        for uid, got in wins.items():
            pl = current if current is not None and current.get('user_id') == uid else S.load_player(uid)
            if not pl:
                continue
            f = self.me(pl)
            news = []
            for kind, place, r in got:
                if str(r.get('30Kw4WBa')) == '10':
                    up = _i(r.get('wJsB35iH'))
                    for _ in range(up):
                        f['hr'] += 1
                        h = self.hr.get(f['hr'])
                        if h:
                            self.give(pl, h.get('30Kw4WBa'), h.get('TdDHf59J'), h.get('wJsB35iH'),
                                      f"Frontier Hunter: Hunter Rank {f['hr']}")
                else:
                    self.give(pl, r.get('30Kw4WBa'), r.get('TdDHf59J'), r.get('wJsB35iH'),
                              f"Frontier Hunter season {season}: rank {place}")
                news.append({'c5yZnpB4': season, 'NH65Wj0f': '1', '9w5e3ZJB': kind, 'j28VNcUW': r.get('j28VNcUW', '0'),
                             'Sv80kL5r': str(f['hr']), '30Kw4WBa': r.get('30Kw4WBa'), 'TdDHf59J': r.get('TdDHf59J'),
                             'wJsB35iH': r.get('wJsB35iH'), '37moriMq': r.get('37moriMq', '0')})
            f['news'] = news
            if pl is not current:
                S.save_player(pl)
            S.log.info('Frontier Hunter: season %s rewards for %s - %d, Hunter Rank %s', season, pl.get('handle'),
                       len(got), f['hr'])

    # ------------------------------------------------------------------ the game's info page (WebView)
    def info_page(self):
        import html
        try:
            n, season, st, en = self.run_at()
            ms = self.missions.get(season, [])
            names = ''.join(f"<li>{html.escape(self.S.MISSIONS.get(m['j28VNcUW'], {}).get('name', m['j28VNcUW']))}</li>" for m in ms)
            top = self.ranked(n, '1')[:10]
            users = self.run(n)['users']
            rank = ''.join(f"<tr><td>{i}</td><td>{html.escape(users[u].get('name', ''))}</td><td>{v:,}</td></tr>"
                           for i, (u, v) in enumerate(top, 1)) or '<tr><td colspan=3>No scores yet - be the first!</td></tr>'
            left = max(0, en - time.time())
            body = (f"<h2>Frontier Hunter - Season {season}</h2><p>{self.term_text(st, en)} · ends in "
                    f"{int(left // 86400)}d {int(left % 86400 // 3600)}h</p><h3>Survey areas</h3><ul>{names}</ul>"
                    f"<h3>How it works</h3><ul><li>Each survey costs 1 Survey energy ({self.max_energy} max, one comes back every "
                    f"{self.energy_time // 3600} hours). You fight with the survey's own item set.</li>"
                    f"<li>Your score in a survey adds Survey points. The overall ranking is by Survey points; each survey and "
                    f"each MVP category (sparks, overkills, multi-kills, BB kills, one-turn kills, times played, times helped) "
                    f"has its own ranking.</li><li>Reaching score targets in a survey gives rewards straight away.</li>"
                    f"<li>When the season ends, ranking rewards and Hunter Rank go to your gift box, and the next season starts.</li></ul>"
                    f"<h3>Top 10</h3><table>{rank}</table>")
        except Exception as e:
            body = f'<h2>Frontier Hunter</h2><p>Not open yet ({html.escape(str(e))}).</p>'
        return ('<html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>'
                'body{background:#141018;color:#eee;font-family:sans-serif;padding:12px}h2{color:#f2d27a}'
                'table{border-collapse:collapse;width:100%}td{border-bottom:1px solid #333;padding:4px}</style></head>'
                f'<body>{body}</body></html>')
