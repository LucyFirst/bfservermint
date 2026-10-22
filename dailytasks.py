"""
Daily Tasks and Brave Points, tracked by the server for each player.

Three tasks a day (UTC), picked from the task kinds the game knows. Finishing a task gives its brave
points (T4bV8aI9) at once, finishing all three gives the bonus (p283g07d.k3bD738b). Brave points:
bya9a67k = total ever earned, 22rqpZTo = points to spend (both also in the team row).

Prizes (a739yK18) are the game's own list from the recordings:
  milestone prizes (J3l5We66 1) unlock once the total reaches their cost and cost nothing;
  shop prizes (J3l5We66 0) spend points; D2BlS89M = how many times, jT3oB57e = times taken.
Prizes go to the gift box (summon tickets straight to the wallet).
"""
import json
import os
import random
import time

DAY = 86400
TASKS_PER_DAY = 3
BONUS_DEFAULT = 10

KINDS = {   # code: (title, description, target, points)
    'AV': ('Arena Victory', 'Achieve 3 Victories in The Arena ', 3, 20),
    'CM': ('Craftsman', 'Craft 5 Items/Spheres ', 5, 20),
    'PU': ('Power Up', 'Fuse 5 units', 5, 20),
    'QE': ('Quest Explorer', 'Complete 3 Missions ', 3, 20),
    'UU': ('Unit Upgrade', 'Evolve any unit 1 time', 1, 20),
    'VV': ('Vortex Venturer', 'Complete 1 Mission within the Vortex ', 1, 20),
}


def _i(x, d=0):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return d


class DailyTasks:
    def __init__(self, srv):
        self.S = srv
        self.prizes, self.bonus = [], {'k3bD738b': str(BONUS_DEFAULT)}
        for a in sorted(srv.T['action'], key=lambda a: -a['n']):        # newest recording first
            b = srv.BLOBS[a['resp']]
            if isinstance(b, dict) and b.get('a739yK18'):
                self.prizes = [dict(r) for r in b['a739yK18']]
                if isinstance(b.get('p283g07d'), dict):
                    self.bonus = dict(b['p283g07d'])
                break
        self.by_id = {str(r['d83aQ39U']): r for r in self.prizes}
        p = os.path.join(srv.HERE, 'data', 'dungeon_area.json')
        self.geo = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {'dungeon': {}, 'area': {}}

    # ------------------------------------------------------------------ brave points
    def points(self, pl):
        bp = pl.setdefault('bp', {'total': 0, 'cur': 0})
        return bp

    def sync_team(self, pl):
        bp, t = self.points(pl), self.S.team_row(pl['state'])
        if t is not None:
            t['bya9a67k'], t['22rqpZTo'] = str(bp['total']), str(bp['cur'])

    def add_points(self, pl, n, why):
        bp = self.points(pl)
        bp['total'] += n
        bp['cur'] += n
        self.sync_team(pl)
        self.S.log.info('brave points +%d (%s): %d to spend, %d total', n, why, bp['cur'], bp['total'])

    # ------------------------------------------------------------------ today's tasks
    def where(self, mid):
        """(area id, land id) of a quest."""
        m = self.S.MISSIONS.get(str(mid)) or {}
        a = self.geo['dungeon'].get(str(m.get('dungeon', '')))
        if a:
            return a[0], a[1]
        return '', ('99' if _i(mid) >= 100000 else '')

    def story_areas(self, pl):
        seen = []
        for mid in self.S.cleared_ids(pl):
            area, land = self.where(mid)
            info = self.geo['area'].get(area) or {}
            if area and land != '99' and info.get('type') == '0' and _i(area) < 10000 and area not in seen:
                seen.append(area)
        return seen

    def today(self, pl):
        day = int(time.time() // DAY)
        d = pl.get('dtask')
        if not d or d.get('day') != day:
            codes = random.sample(sorted(KINDS), TASKS_PER_DAY)
            tasks = []
            for c in codes:
                title, desc, target, pts = KINDS[c]
                t = {'code': c, 'title': title, 'desc': desc, 'target': target, 'pts': pts, 'n': 0, 'area': ''}
                if c == 'QE':
                    areas = self.story_areas(pl)
                    if areas and random.random() < 0.6:
                        pick = sorted(random.sample(areas, min(len(areas), random.choice((1, 1, 2)))), key=int)
                        names = [self.geo['area'][a]['name'] for a in pick]
                        t['area'] = ','.join(pick)
                        t['desc'] = 'Complete 3 Missions in ' + ' & '.join(names)
                tasks.append(t)
            d = pl['dtask'] = {'day': day, 'tasks': tasks, 'bonus': 0}
        return d

    def event(self, pl, kind, n=1, mid=None):
        """Something the player did: quest (mid), vortex, arena, craft, fuse, evolve."""
        d = self.today(pl)
        for t in d['tasks']:
            c = t['code']
            hit = ((c == 'AV' and kind == 'arena') or (c == 'CM' and kind == 'craft') or
                   (c == 'PU' and kind == 'fuse') or (c == 'UU' and kind == 'evolve'))
            if kind == 'quest' and mid:
                area, land = self.where(mid)
                if c == 'VV' and land == '99':
                    hit = True
                if c == 'QE' and land != '99' and (not t['area'] or area in t['area'].split(',')):
                    hit = True
            if not hit:
                continue
            was = t['n']
            t['n'] += n
            if was < t['target'] <= t['n']:
                self.add_points(pl, t['pts'], 'daily task: ' + t['title'])
        if not d['bonus'] and all(t['n'] >= t['target'] for t in d['tasks']):
            d['bonus'] = 1
            self.add_points(pl, _i(self.bonus.get('k3bD738b'), BONUS_DEFAULT), 'all daily tasks done')

    # ------------------------------------------------------------------ what the game is shown
    def task_rows(self, pl):
        bp = self.points(pl)
        return [{'O36Qv37k': t['code'], 'hd2Jf3nC': t['title'], 'M7yKr4c1': t['desc'], 'Y3DbX5ot': str(t['target']),
                 'T4bV8aI9': str(t['pts']), 'a3011F8b': t['area'], '9cKyb15U': t['n'],
                 'bya9a67k': str(bp['total']), '22rqpZTo': str(bp['cur'])} for t in self.today(pl)['tasks']]

    def prize_rows(self, pl):
        taken = pl.setdefault('bp_taken', {})
        return [dict(r, jT3oB57e=taken.get(str(r['d83aQ39U']), 0)) for r in self.prizes]

    def info(self, pl, req=None):
        self.sync_team(pl)
        resp = {'p283g07d': dict(self.bonus), 'a739yK18': self.prize_rows(pl), 'k23D7d43': self.task_rows(pl)}
        t = self.S.team_row(pl['state'])
        if t is not None:
            resp[self.S.G_TEAM] = [t]
        return resp

    def claim(self, pl, req):
        pid = str(((req.get('a739yK18') or [{}])[0]).get('d83aQ39U', ''))
        r = self.by_id.get(pid)
        if not r:
            return None, 'That prize is not available.'
        taken, bp = pl.setdefault('bp_taken', {}), self.points(pl)
        cost, most = _i(r.get('4NuIwm77')), _i(r.get('D2BlS89M'), 1)
        if taken.get(pid, 0) >= most:
            return None, 'You have already claimed this prize.'
        milestone = str(r.get('J3l5We66')) == '1'
        if milestone and bp['total'] < cost:
            return None, 'Not enough Brave Points yet.'
        if not milestone:
            if bp['cur'] < cost:
                return None, 'Not enough Brave Points.'
            bp['cur'] -= cost
        taken[pid] = taken.get(pid, 0) + 1
        kind, what, n = str(r.get('30Kw4WBa')), str(r.get('TdDHf59J') or ''), _i(r.get('wJsB35iH'), 1)
        title = str(r.get('T091Rsbe') or '')
        if kind == '8000':                                     # summon tickets: straight to the wallet
            pl['wallet']['summontickets'] = pl['wallet'].get('summontickets', 0) + n
            self.S.log.info('brave points prize: %d summon ticket(s)', n)
        elif kind in ('2', '3', '8') or (kind in ('4', '5', '6', '7') and what not in ('', '0')):
            self.S.add_present(pl, _i(kind), '' if kind in ('2', '3', '8') else what, n,
                               f'Brave Points reward: {title}')
        else:
            self.S.log.warning('brave points prize %s (%s, type %s) has no gift on this server yet', pid, title, kind)
        self.sync_team(pl)
        self.S.log.info('brave points prize claimed: %s x%d (%s, %d to spend, %d total)', title, n,
                        'milestone' if milestone else f'-{cost}', bp['cur'], bp['total'])
        return self.info(pl), None
