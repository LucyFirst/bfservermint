"""
Quest battle generator.

Every quest start is generated fresh from a *profile* built out of references:
  - recorded bfrevive runs (the capture recordings and probe_bfrevive fetches),
  - datamined battle lists (data/battles.json, src "datamine"),
  - the wiki's zone data (enemy pools, rare drops, boss notes).
References are never replayed 1:1 - they only tell the generator which enemies a quest uses,
how strong they are, how many appear, and how often things drop.

Story quests share one model: enemies from the quest's own area, strength scaled from the
nearest referenced story quests by quest exp, battle sizes and boss fights shaped like the
references. Vortex quests each get their own profile, with tiers of the same dungeon (family)
standing in for each other when a tier has no references of its own.
"""
import math
import random
import statistics
from collections import Counter, defaultdict

FILLER = ('1000', '290', '50')          # bfrevive's placeholder enemies (hp, atk, def)
MIMIC = '60142'
BAT_MIMIC, DRAGON_MIMIC, METAL_MIMIC = '60143', '60144', '60224'
MIMICS = {MIMIC, BAT_MIMIC, DRAGON_MIMIC, METAL_MIMIC}
MIMIC_CHANCE = {'story': 0.015, 'vortex': 0.025}     # per battle (not the boss), at most once per quest
METAL_MIMIC_QUESTS = {'100200': 0.05, '100201': 0.05}  # Box of Desires Lv1/Lv2: a random wave encounter
STORY_EXP_POWER = 0.43                  # enemy HP ~ exp^0.43 across 113 datamined story quests


def _i(x, d=0):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return d


def _med(xs, d=0):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else d


# set by the server from the admin page: unit drop / item chest chance multipliers, closed units
RARE_ENEMY_WEIGHT = 0.12      # story quests: how often a rare enemy shows up compared with an ordinary one
SETTINGS = {'drop': 1.0, 'item': 1.0, 'off_units': set(), 'unit_drop': {}}
# Fixed capture chance (per time the enemy is beaten) for units that otherwise drop too often -
# story quests only: Vortex quests (e.g. the Idol / evolution farming dungeons) keep their own rates.
# admin_settings.json "unit_drop": {"<unit id>": percent} overrides these.
UNIT_DROP = {'10021': 0.10,   # Beast Zegar
             '20021': 0.10,   # Zephu
             '30021': 0.10,   # Archer Lario
             '40021': 0.10,   # Advisor Weiss
             '50021': 0.10,   # Luna
             '60021': 0.10,   # Mifune
             # evolution Idols (rare drops - they came out of St. Lamia almost every run)
             '10132': 0.06, '20132': 0.06, '30132': 0.06, '40132': 0.06, '50122': 0.06, '60122': 0.06}

# Vortex quests whose only reference is a sister quest: swap that quest's enemies for the right ones
# (Garden of Nymphs / Spirits borrow Garden of Idols' fights). Stats follow the units' own stats.
IDOLS = {'10132': '1', '20132': '2', '30132': '3', '40132': '4', '50122': '5', '60122': '6'}
NYMPHS = {'1': '10130', '2': '20130', '3': '30130', '4': '40130', '5': '50120', '6': '60120'}
SPIRITS = {'1': '10131', '2': '20131', '3': '30131', '4': '40131', '5': '50121', '6': '60121'}
VORTEX_SWAP = {'100100': {i: NYMPHS[e] for i, e in IDOLS.items()},     # Garden of Nymphs
               '100101': {i: SPIRITS[e] for i, e in IDOLS.items()}}    # Garden of Spirits

def unit_drop_override(u):
    custom = SETTINGS.get('unit_drop') or {}
    if str(u) in custom:
        return max(0.0, min(100.0, float(custom[str(u)]))) / 100
    return UNIT_DROP.get(str(u))

def _chance(p, k):
    return min(0.95, p * SETTINGS.get(k, 1.0)) if p < 1 else p

class Battlegen:
    def __init__(self, recorded, datamine, wiki, missions, unit_mst, log=None):
        """recorded: {mid: [MissionStart response, ...]}; datamine/wiki: data/battles.json specs;
        missions: mission_mst; unit_mst: unit master."""
        self.U, self.M, self.W, self.log = unit_mst, missions, wiki, log
        self.refs = defaultdict(list)            # mid -> [run]
        for mid, resps in recorded.items():
            for r in resps:
                run = self._from_recording(r)
                if run:
                    self.refs[mid].append(run)
        for mid, spec in datamine.items():
            if spec.get('src') == 'datamine' and spec.get('battles'):
                self.refs[mid].append(self._from_datamine(spec))
        self.story_ids = sorted((int(m) for m, rs in self.refs.items()
                                 if int(m) < 100000 and any(self._real_normals(r) for r in rs)))
        by_dun = defaultdict(list)
        for mid in missions:
            by_dun[missions[mid].get('dungeon')].append(mid)
        self.family = {mid: sorted(by_dun[missions[mid].get('dungeon')], key=int) for mid in missions}
        # units the real game let you capture from ordinary enemies: the datamine's drop rates and
        # what recorded runs actually dropped, over every quest (Goblin 25%, Thief Leon 15%...)
        rates, seen, got = defaultdict(list), Counter(), Counter()
        for runs in self.refs.values():
            for run in runs:
                for bi, b in enumerate(run['battles']):
                    for m in b:
                        if m.get('boss') or m.get('filler'):
                            continue
                        seen[m['u']] += 1
                        if run['drops'] is None and (m.get('drop') or {}).get('u') == m['u'] and m['drop'].get('pct'):
                            rates[m['u']].append(m['drop']['pct'])
                    # a recorded drop counts only when an ordinary enemy of that unit was in that
                    # battle (boss drops are boss captures, handled separately)
                    if run['drops'] is not None and bi < len(run['drops']) and bi < len(run['battles']) - 1:
                        here = {m['u'] for m in b if not m.get('filler')}
                        for u, c in run['drops'][bi].items():
                            if u in here:
                                got[u] += c
        self.capturable = {u: _med(ps) / 100 for u, ps in rates.items()}
        for u, c in got.items():
            if u not in self.capturable and seen[u]:
                self.capturable[u] = min(0.5, c / seen[u])

    # ------------------------------------------------------------------ references
    def _from_recording(self, r):
        groups = r.get('pj41dy9g') or []
        mons = {m['o49dYfpH']: m for m in r.get('U0v5IeJo') or []}
        if not groups or not mons:
            return None
        order = {g['Qzhp8B40']: _i(g['VETu07N6']) for g in groups}
        n = len(order)
        battles = [[] for _ in range(n)]
        for b in r.get('75t0sx9z') or []:
            bi = order.get(b['Qzhp8B40'])
            m = mons.get(b['o49dYfpH'])
            if not bi or not m or m.get('pn16CNah') == MIMIC:
                continue
            battles[bi - 1].append((_i(b.get('hZtF1s8B')), {
                'u': m['pn16CNah'], 'name': m.get('3evIn0zZ'), 'hp': _i(m['e7DK0FQT']), 'atk': _i(m['67CApcti']),
                'def': _i(m['q08xLEsy']), 'el': m.get('iNy0ZU5M'), 'ai': m.get('i74vGUFa', '1'),
                'act': [_i(m.get('m4EK7Gt6'), 1), _i(m.get('oMGC3hW0'), 1)],
                'zel': _i(m.get('Najhr8m6')), 'karma': _i(m.get('HTVh8a65')), 'row': m,
                'filler': (m.get('e7DK0FQT'), m.get('67CApcti'), m.get('q08xLEsy')) == FILLER}))
        battles = [[m for _, m in sorted(b, key=lambda x: x[0])] for b in battles]
        # what this run dropped: unit drops per position and chests per battle
        drops, chests = [Counter() for _ in range(n)], [[] for _ in range(n)]
        for seg in ((r.get('Kz7qfSs5') or [{}])[0].get('LE6JkUp7') or '').split('@'):
            f = seg.split('|')
            if len(f) < 3 or not f[0].strip().isdigit():
                continue
            bi = _i(f[0]) - 1
            if not 0 <= bi < n:
                continue
            for d in f[1].split('-'):
                p = d.split(':')
                if len(p) >= 3 and p[2].isdigit():
                    drops[bi][p[2]] += 1
            for c in f[2].split('-'):
                c = c.strip()
                if c and '/' in c:
                    chests[bi].append(c.split('/', 1)[1])        # keep "box/type:..." and drop the position
        return {'src': 'recording', 'battles': battles, 'drops': drops, 'chests': chests,
                'skills': r.get('8aiBoHg5') or [], 'ai': r.get('89ausgc4') or []}

    def _from_datamine(self, spec):
        battles = [[dict(m, act=list(m.get('act') or [1, 1]), filler=False) for m in b] for b in spec['battles']]
        return {'src': 'datamine', 'battles': battles, 'drops': None, 'chests': None, 'skills': [], 'ai': []}

    @staticmethod
    def _real_normals(run):
        return [m for b in run['battles'][:-1] for m in b if not m['filler']]

    @staticmethod
    def _real_boss(run):
        b = run['battles'][-1] if run['battles'] else []
        return b if b and not any(m['filler'] for m in b) else []

    # ------------------------------------------------------------------ helpers
    def exp(self, mid):
        return _i((self.M.get(mid) or {}).get('exp'), 1)

    def kind(self, u):
        return str((self.U.get(u) or {}).get('xP9oi5Z2', '0'))

    def may_drop_self(self, u, wiki):
        """Ordinary enemies drop as themselves when they are materials (crystals, frogs, imps, nymphs,
        ghosts...), units the wiki lists as this quest's captures, or units the real game let you
        capture from enemies (datamine drop rates / recorded drops) - e.g. Goblin, Burny, Thief Leon.
        Enemies that never dropped anywhere (bosses' units, high-rarity enemies) still don't."""
        return u != MIMIC and (self.kind(u) != '0' or u in (wiki.get('captures') or []) or u in self.capturable)

    def difficulty(self, mid):
        """Quest exp is the difficulty scale; a quest with no known exp (JP-only arcs) gets the exp
        its wiki strength curve corresponds to among the referenced story quests."""
        e = self.exp(mid)
        if e > 0:
            return e + 1
        if not hasattr(self, '_k'):
            ks = [self._curve_hp(str(x)) / (self.exp(str(x)) + 1) ** STORY_EXP_POWER for x in self.story_ids
                  if self._curve_hp(str(x)) and self.exp(str(x)) > 0]
            self._k = _med(ks, 300)
        hp = self._curve_hp(mid)
        return (hp / self._k) ** (1 / STORY_EXP_POWER) if hp else 1

    def _curve_hp(self, mid):
        c = (self.W.get(mid) or {}).get('curve')
        return _i(c[0]) if c else 0

    def nearest_story_refs(self, mid, k=3):
        q = int(mid)
        if not self.story_ids:
            return []
        dun = (self.M.get(mid) or {}).get('dungeon')
        same = [x for x in self.story_ids if (self.M.get(str(x)) or {}).get('dungeon') == dun and x != q]
        pool = sorted(self.story_ids, key=lambda x: (abs(math.log(self.difficulty(str(x)) / self.difficulty(mid))), abs(x - q)))
        out = ([str(q)] if self.refs.get(str(q)) else []) + [str(x) for x in same if x != q]
        for x in pool:
            if str(x) not in out:
                out.append(str(x))
        return out[:k]

    def scale(self, mid, ref, power=STORY_EXP_POWER):
        if mid == ref:
            return 1.0
        return max(0.25, min(4.0, (self.difficulty(mid) / self.difficulty(ref)) ** power))

    def mimic_for(self, mid, wiki):
        """Which mimic hides in this quest's chests: the one the wiki lists for the area, else by how far
        into the story the quest is (Mimic early, Bat Mimic from Land of the Light, Dragon Mimic from 600)."""
        mobs = wiki.get('mobs') or []
        q = int(mid)
        if DRAGON_MIMIC in mobs:
            u = DRAGON_MIMIC
        elif BAT_MIMIC in mobs:
            u = BAT_MIMIC
        elif q >= 100000:
            u = MIMIC
        elif q >= 20000:
            u = BAT_MIMIC
        elif q >= 600:
            u = DRAGON_MIMIC
        elif q >= 240:
            u = BAT_MIMIC
        else:
            u = MIMIC
        d = self.difficulty(mid) if q < 100000 else max(self.exp(mid), 1)
        lv = 1 if d < 200 else 2 if d < 600 else 3 if d < 1500 else 4 if d < 3000 else 5
        return {'u': u, 'lv': lv, 'name': (self.U.get(u) or {}).get('utP1c0CD', 'Mimic')}

    # ------------------------------------------------------------------ public
    def category(self, mid):
        return 'story' if int(mid) < 100000 else 'vortex'

    def plan(self, mid, rng=random):
        mid = str(mid)
        wiki = self.W.get(mid) or {}
        if self.category(mid) == 'vortex':
            alias = str(wiki.get('src', ''))
            if alias.startswith('same quest as '):       # a re-run of another quest: use that quest's profile
                other = alias.split()[3]
                if self.refs.get(other):
                    return self._vortex(mid, other, self.W.get(other) or wiki, rng)
            src = [mid] if self.refs.get(mid) else [m for m in sorted(self.family.get(mid, []), key=lambda x: abs(int(x) - int(mid)))
                                                      if self.refs.get(m)][:1]
            if src:
                return self._vortex(mid, src[0], wiki, rng)
        return self._story(mid, wiki, rng)

    # ------------------------------------------------------------------ story
    def _story(self, mid, wiki, rng):
        refs = self.nearest_story_refs(mid)
        runs = [(ref, run) for ref in refs for run in self.refs.get(ref, [])]
        n = _i((self.M.get(mid) or {}).get('battles')) or _i(wiki.get('n')) or 3
        n = max(2, min(12, n))
        # strength: median of real reference enemies, scaled to this quest by exp
        normal = defaultdict(list)
        boss = defaultdict(list)
        sizes, zk = [], []
        by_unit = {}
        for ref, run in runs:
            s = self.scale(mid, ref)
            for m in self._real_normals(run):
                for f in ('hp', 'atk', 'def'):
                    normal[f].append(m[f] * s)
                zk.append((m['zel'] * s, m['karma'] * s))
                by_unit.setdefault(m['u'], m)
            for b in run['battles'][:-1]:
                if b and not any(m['filler'] for m in b):
                    sizes.append(len(b))
            for m in self._real_boss(run):
                for f in ('hp', 'atk', 'def'):
                    boss[f].append(m[f] * s)
        fallback = wiki.get('curve') or (1000, 300, 50, 10000, 500, 80)
        lvl = {f: _med(normal[f], fallback[i]) for i, f in enumerate(('hp', 'atk', 'def'))}
        blvl = {f: max(_med(boss[f], fallback[3 + i]), lvl[f]) for i, f in enumerate(('hp', 'atk', 'def'))}
        zel, karma = _med([z for z, _ in zk], lvl['hp'] * 0.04), _med([k for _, k in zk], lvl['hp'] * 0.025)
        sizes = sizes or [1, 2, 2, 3, 3, 4]
        # enemy pool: the wiki's list for this area + enemies the area's references use
        same_dun = [r for r in refs if (self.M.get(r) or {}).get('dungeon') == (self.M.get(mid) or {}).get('dungeon')]
        seen = [m['u'] for r in same_dun for run in self.refs.get(r, []) for m in self._real_normals(run)]
        pool = [u for u in dict.fromkeys(list(wiki.get('mobs') or []) + seen) if u in self.U and u not in MIMICS]
        pool = pool or list(by_unit) or ['10030', '20030', '30030', '40030']
        unit_lvl = {}

        def enemy(u, base, spread=0.1):
            key = (u, base is blvl)
            if key not in unit_lvl:      # one strength per enemy type per run, like the real game
                ref = by_unit.get(u)
                unit_lvl[key] = {f: max(1, int(base[f] * rng.uniform(1 - spread, 1 + spread))) for f in ('hp', 'atk', 'def')}
                unit_lvl[key].update({'ai': (ref or {}).get('ai', '1'), 'act': (ref or {}).get('act', [1, 1]),
                                    'row': (ref or {}).get('row'), 'name': (ref or {}).get('name')})
            m = dict(unit_lvl[key], u=u, zel=int(zel * rng.uniform(.8, 1.2)), karma=int(karma * rng.uniform(.8, 1.2)))
            return m

        # rare enemies (nymphs, spirits, idols, totems... and the wiki's rare captures) turn up now and
        # then, not as often as the area's ordinary enemies - unless real runs of this area show them a lot
        seen_n = Counter(seen)
        common = max([c for u, c in seen_n.items() if self.kind(u) == '0'] or [1])
        caps = set(wiki.get('captures') or [])

        def weight(u):
            if seen_n.get(u):
                return max(0.15, seen_n[u] / common)
            return RARE_ENEMY_WEIGHT if (self.kind(u) != '0' or u in caps) else 1.0
        weights = [weight(u) for u in pool]
        battles = []
        for _ in range(n - 1):
            k = max(1, min(5, rng.choice(sizes)))
            battles.append([enemy(u, lvl) for u in rng.choices(pool, weights=weights, k=k)])
        battles.append(self._story_boss(mid, wiki, runs, pool, enemy, lvl, blvl, rng))
        return self._finish(mid, wiki, battles, runs, rng, story=True)

    def _story_boss(self, mid, wiki, runs, pool, enemy, lvl, blvl, rng):
        own = [run for ref, run in runs if ref == mid and self._real_boss(run)]
        if own:          # this quest's own boss fight is known: same boss, varied a little
            src = rng.choice(own)
            out = []
            for m in self._real_boss(src):
                out.append(dict(m, hp=max(1, int(m['hp'] * rng.uniform(.95, 1.05))),
                                atk=max(1, int(m['atk'] * rng.uniform(.95, 1.05))), boss=True, filler=False))
            return out
        bosses = [b for b in wiki.get('bosses') or [] if b.get('u') in self.U][:3]
        if bosses:
            out = [dict(u=b['u'], name=b.get('name'), el=b.get('el'), ai='1', act=[1, 2], row=None, boss=True,
                        hp=int((b.get('hp') or blvl['hp'] / (1 if len(bosses) == 1 else 1.6)) * rng.uniform(.95, 1.05)),
                        atk=int(blvl['atk'] * rng.uniform(.95, 1.05)), **{'def': int(blvl['def'])},
                        zel=int(blvl['hp'] * 0.03), karma=int(blvl['hp'] * 0.02)) for b in bosses]
            return out
        # an ordinary quest ends with a stronger leader and a couple of normal enemies
        leader = max(pool, key=lambda u: _i((self.U.get(u) or {}).get('7ofj5xa1')))
        lead = enemy(leader, blvl, 0.05)
        lead.update(boss=True, act=[1, 2])
        return [lead] + [enemy(rng.choice(pool), lvl) for _ in range(rng.choice([0, 1, 2]))]

    # ------------------------------------------------------------------ vortex
    def _vortex(self, mid, ref, wiki, rng):
        runs = [(ref, run) for run in self.refs[ref]]
        s = 1.0 if ref == mid else self.scale(mid, ref, 0.7)
        nb = Counter(len(run['battles']) for _, run in runs)
        n = nb.most_common(1)[0][0]
        idx = defaultdict(list)          # battle index -> [lineup]
        for _, run in runs:
            if len(run['battles']) != n:
                continue
            for bi, b in enumerate(run['battles']):
                if b and not any(m['filler'] for m in b):
                    idx[bi].append(b)
        stats = defaultdict(lambda: defaultdict(list))
        proto = {}
        for b in (m for bs in idx.values() for lineup in bs for m in lineup):
            for f in ('hp', 'atk', 'def', 'zel', 'karma'):
                stats[(b['u'], b.get('name'))][f].append(b[f])
            proto.setdefault((b['u'], b.get('name')), b)
        per_run = {}
        for key, p in proto.items():
            st = stats[key]
            per_run[key] = dict(p, **{f: max(1 if f in ('hp', 'atk') else 0, int(_med(st[f]) * (s if f in ('hp', 'atk', 'def') else 1)
                                                                               * rng.uniform(.95, 1.05)))
                                      for f in ('hp', 'atk', 'def', 'zel', 'karma')})
        battles = []
        for bi in range(n):
            lineups = idx.get(bi) or idx.get(min(idx, key=lambda x: abs(x - bi))) if idx else []
            if not lineups:
                return self._story(mid, wiki, rng)
            k = max(1, min(5, len(rng.choice(lineups))))
            freq = Counter((m['u'], m.get('name')) for lu in lineups for m in lu)
            keys, w = zip(*freq.items())
            b = [dict(per_run[kk]) for kk in rng.choices(keys, weights=w, k=k)]
            if bi == n - 1:
                for m in b:
                    m['boss'] = True
            battles.append(b)
        swap = VORTEX_SWAP.get(mid) if ref != mid else None
        if swap:
            for b in battles:
                for i, m in enumerate(b):
                    new = swap.get(str(m['u']))
                    if not new:
                        continue
                    old_u, new_u = self.U.get(str(m['u'])) or {}, self.U.get(new) or {}
                    r_hp = _i(new_u.get('UZ1Bj7w2'), 1) / max(1, _i(old_u.get('UZ1Bj7w2'), 1))
                    r_atk = _i(new_u.get('i9Tn7kYr'), 1) / max(1, _i(old_u.get('i9Tn7kYr'), 1))
                    row = dict(m['row']) if m.get('row') else None
                    if row is not None:
                        row.update({'pn16CNah': new, '3evIn0zZ': new_u.get('utP1c0CD', row.get('3evIn0zZ')),
                                    'eyUo6a8c': new_u.get('eyUo6a8c', row.get('eyUo6a8c')),
                                    '6Aou5M9r': new_u.get('6Aou5M9r', row.get('6Aou5M9r'))})
                    b[i] = dict(m, u=new, name=new_u.get('utP1c0CD'), row=row, hp=max(1, int(m['hp'] * r_hp)),
                                atk=max(1, int(m['atk'] * r_atk)), drop=None)
        chance = METAL_MIMIC_QUESTS.get(mid, 0)
        if chance and len(battles) > 1 and rng.random() < chance:
            # it turns up in one of the normal waves (never the boss) and is caught when beaten
            b = battles[rng.randrange(len(battles) - 1)]
            mm = {'u': METAL_MIMIC, 'name': 'Metal Mimic', 'hp': 60, 'atk': 400, 'def': 99999, 'ai': '1',
                  'act': [1, 1], 'zel': 0, 'karma': 0, 'row': None, 'filler': False, 'force_drop': True}
            if len(b) < 5:
                b.insert(rng.randrange(len(b) + 1), mm)
            else:
                b[rng.randrange(len(b))] = mm
        plan = self._finish(mid, wiki, battles, runs, rng, story=False)
        return plan

    # ------------------------------------------------------------------ drops, chests, skills
    def _finish(self, mid, wiki, battles, runs, rng, story):
        caps = [u for u in wiki.get('captures') or [] if u in self.U]
        boss_ok, boss_as = set(), {}
        for nn in wiki.get('cap_notes') or []:
            for who in nn.get('who') or []:
                for tgt in nn.get('yes') or []:
                    boss_as[who] = tgt
            boss_ok |= set(nn.get('yes') or [])
        boss_no = {u for nn in wiki.get('cap_notes') or [] for u in nn.get('no') or []}
        # observed unit drop rates: recorded runs (drops / appearances) and datamine percentages
        seen, dropped, pct = Counter(), Counter(), defaultdict(list)
        chest_obs = defaultdict(list)
        n_runs = defaultdict(int)
        for _, run in runs:
            for bi, b in enumerate(run['battles']):
                for m in b:
                    seen[m['u']] += 1
                    if run['drops'] is None and m.get('drop'):
                        pct[m['u']].append((m['drop'].get('u'), m['drop'].get('pct', 0)))
                if run['drops'] is not None:
                    for u, c in run['drops'][bi].items():
                        dropped[u] += c
            if run['chests'] is not None:
                for bi, cs in enumerate(run['chests']):
                    n_runs[bi] += 1
                    chest_obs[bi].extend(c for c in cs if not c.startswith('2/'))
        vortex = self.category(mid) == 'vortex'     # Vortex quests keep their own drop rates

        def fixed_rate(u):
            return None if vortex else unit_drop_override(u)

        def unit_rate(u):
            fixed = fixed_rate(u)
            if fixed is not None:
                return fixed
            if pct.get(u):
                return _med([p for _, p in pct[u]]) / 100
            if seen[u] and dropped[u]:
                return min(0.95, dropped[u] / seen[u])
            if u in self.capturable:
                return self.capturable[u]
            return 0.2 if self.kind(u) != '0' else 0.08
        items = [i for i in (wiki.get('qitems') or []) + (wiki.get('items') or [])]
        rare = wiki.get('rare') or []
        dm_items = defaultdict(list)
        for _, run in runs:
            if run['src'] == 'datamine':
                for b in run['battles']:
                    for m in b:
                        for it in m.get('items') or []:
                            dm_items[m['u']].append(it)
        present = {m['u'] for b in battles for m in b}
        extra_caps = [u for u in caps if u not in present and u != MIMIC]
        out_b = []
        for bi, b in enumerate(battles):
            mons, unit_drops, chests = [], [], []
            for pos, m in enumerate(b[:5]):
                drop = None
                if m.get('force_drop'):
                    drop = m['u']
                elif m.get('boss'):
                    tgt = boss_as.get(m['u']) or (m['u'] if m['u'] in boss_ok else None)
                    if not tgt and m['u'] in caps:
                        tgt = m['u']
                    if tgt and m['u'] not in boss_no and tgt in self.U and rng.random() < _chance(unit_rate(m['u']) if pct.get(m['u']) or fixed_rate(m['u']) is not None else 0.2, 'drop'):
                        drop = tgt
                elif self.may_drop_self(m['u'], wiki) and rng.random() < _chance(unit_rate(m['u']), 'drop'):
                    drop = m['u']
                elif extra_caps and story and rng.random() < _chance(0.01, 'drop'):
                    drop = rng.choice(extra_caps)  # a wiki-listed rare drop now and then (one not fought here)
                if drop and str(drop) in SETTINGS.get('off_units', ()):
                    drop = None                   # closed on the admin page
                mons.append(dict(m, drop=drop))
                if drop:
                    unit_drops.append(f'{pos}:0:{drop}:1:{rng.randint(1, 5)}')
                for it in ([] if chest_obs.get(bi) else dm_items.get(m['u'], [])[:4]):   # recorded chests already cover this battle
                    if rng.random() < _chance(float(it.get('pct', 0)) / 100, 'item'):
                        chests.append(f'{pos}/1/4:1000:{it["i"]}:1')
                        break
            k = len(mons)
            if chest_obs.get(bi) and n_runs.get(bi):
                # recorded chests at this point of the quest: how often, and what they held
                rate = len(chest_obs[bi]) / n_runs[bi] * SETTINGS.get('item', 1.0)
                for _ in range(int(rate) + (1 if rng.random() < rate - int(rate) else 0)):
                    c = rng.choice(chest_obs[bi])
                    chests.append(f'{rng.randrange(k)}/{c}')
            elif story:
                if items and not dm_items and rng.random() < _chance(0.3, 'item'):
                    chests.append(f'{rng.randrange(k)}/1/4:1000:{rng.choice(items)}:1')
                if rare and rng.random() < _chance(0.04, 'item'):
                    chests.append(f'{rng.randrange(k)}/1/4:1000:{rng.choice(rare)}:1')
                if rng.random() < 0.25:
                    kind = rng.choice((1, 2))
                    base = max(50, int((self.exp(mid) + 20) ** 0.5 * (60 if kind == 1 else 30)))
                    chests.append(f'{rng.randrange(k)}/1/{kind}:25:{int(base * rng.uniform(.5, 1.5))}')
            out_b.append({'mons': mons, 'unit_drops': unit_drops, 'chests': chests})
        # a mimic chest: a small chance in each normal battle, never the boss, at most once per quest.
        # "2/<mimic battle group>:0:0:<mimic unit>:1:<lv>" - the group is the one after the last battle
        mimic = self.mimic_for(mid, wiki)
        for bi in range(len(out_b) - 1):
            if rng.random() < MIMIC_CHANCE['story' if story else 'vortex']:
                k = len(out_b[bi]['mons'])
                out_b[bi]['chests'].append(f"{rng.randrange(k)}/2/{len(out_b) + 1}:0:0:{mimic['u']}:1:{mimic['lv']}")
                break
        skills, ai = {}, {}
        used = {(m.get('row') or {}).get('o49dYfpH') for b in out_b for m in b['mons']}
        for _, run in runs:
            if run['src'] == 'recording' and any(m.get('row', {}).get('o49dYfpH') in used for b in run['battles'] for m in b):
                for row in run['skills']:
                    skills.setdefault(row.get('nj9Lw7mV'), row)
                for row in run['ai']:
                    ai.setdefault((row.get('4eEVw5hL'), row.get('yu18xScw')), row)
        src = sorted({ref for ref, _ in runs})
        return {'battles': out_b, 'mimic': mimic, 'skills': list(skills.values()), 'ai': list(ai.values()),
                'label': ('story' if story else 'vortex') + (' refs ' + ','.join(src) if src else ' (wiki only)')}
