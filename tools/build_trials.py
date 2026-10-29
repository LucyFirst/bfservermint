"""
Builds data/trial_specs.json - the boss fights for the Summoners' Research Lab trials.

What comes from the game itself (client masters, decrypted into /home/claude/mst):
  - the trial scripts (dialogue, form changes, "boss appears" banners) keyed by monster id,
  - every boss move (skill rows + their level params), incl. the summon moves and which
    summon group / slot they call.
What comes from the datamine (bfdata/ai.json): the real AI scripts of the X trials (X2, X3, X4).
What is ours: HP (wiki numbers where the wiki gives them), ATK/DEF, act counts, and the AI of the
numbered trials, written from the wiki's move descriptions (when each move is used).

Run:  python3 tools/build_trials.py   (writes data/trial_specs.json)
"""
import glob
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MST = '/home/claude/mst'
SCRATCH = '/tmp/claude-0/trials'

S, L = {}, {}
for f in glob.glob(f'{MST}/Ver1535_wkCyV73D_*.json'):
    for r in json.load(open(f)):
        S[r['nj9Lw7mV']] = r
for f in glob.glob(f'{MST}/Ver1503_zLIvD5o2_*.json'):
    for r in json.load(open(f)):
        L[r['nj9Lw7mV']] = r
NAMES = json.load(open(f'{SCRATCH}/skillnames.json'))
AI_DM = json.load(open('/home/claude/bfdata/ai.json'))
U = json.load(open(os.path.join(HERE, 'data', 'unit_mst.json')))
MNAMES = {k: v.get('name') for k, v in json.load(open(os.path.join(HERE, 'data', 'mission_mst.json'))).items()}

# the recorded Trial No. 001 boss row (bfrevive) - field layout for a trial boss
TEMPLATE = {"o49dYfpH": "", "3evIn0zZ": "", "e7DK0FQT": "0", "67CApcti": "0", "q08xLEsy": "0", "iNy0ZU5M": "1",
            "eyUo6a8c": "", "6Aou5M9r": "", "n9h7p02P": "3", "2EF0d6ue": "", "QqfI9mM4": "", "pn16CNah": "",
            "qp37xTDh": "", "J2hPXGo5": "4", "mv4o39Uz": "1", "h2L1YI90": "1", "i74vGUFa": "1", "D4Y5bWK7": "1",
            "oMGC3hW0": "2", "m4EK7Gt6": "1", "6fwL59FT": "50", "F4bQ7r8C": "", "XE7Yi5c3": "1",
            "CEeqs63b": "0:100:100:100:100:100", "Najhr8m6": "0", "9FN0GAei": "0", "HTVh8a65": "0", "vNk2sI4X": "0",
            "3BpHN6VD": "", "hjAy9St3": "", "2Smu5Mtq": "", "CYk84E3W": "0,0", "Y6bd4fXp": "0,0,0", "jm6JSK2D": "",
            "9pXSKmn1": "5", "Lkh6gYkT": "0,0"}
# resist order (CEeqs63b): poison, weak, sick, injury, curse, paralysis - 100 = immune
IMMUNE = '100:100:100:100:100:100'
LAYOUT = {1: ['180:302'], 2: ['180:302', '120:248'], 3: ['180:302', '120:248', '96:352'],
          4: ['160:240', '215:300', '160:360', '105:300'], 5: ['215:250', '215:350', '160:300', '105:240', '105:360']}


def skill_row(sid):
    r = dict(S[sid])
    r['0nxpBDz2'] = (NAMES.get(sid) or '').strip() or 'Attack'
    r['qp37xTDh'] = 'None'
    return r


# ------------------------------------------------------------------ AI rows
def _pad_params(p):
    vals = [str(x) for x in (p or [])]
    vals += ['-1'] * (12 - len(vals))
    return ','.join(vals) + ','


def ai_row(aid, label, prio, act, conds=(), pct=100, tc='random', tt=2, params=(), party=()):
    party = list(party) + ['0:non:non:non'] * (4 - len(party))
    conds = list(conds)
    assert len(conds) <= 5, (label, conds)
    conds += ['non:0'] * (5 - len(conds))
    return {'4eEVw5hL': str(aid), 'yu18xScw': str(prio), 'L8PCsu0K': label,
            'q7Nit8JW': '@'.join(party) + '@#' + '@'.join(conds) + '@',
            '4xctV8gF': str(tt), 'VBj9u0ot': tc,
            'Hhgi79M1': f'{act}@{_pad_params(params)}@1@0@0', 'ug9xV4Fz': str(max(1, round(float(pct))))}


def ai_from_rules(aid, label, rules):
    """rules: list of tuples
       ('s', n, [conds], pct=100, tc='random')   use skill n (1-based in the monster's skill list)
       ('a', pct, tc)                              normal attack
    """
    rows = []
    for i, r in enumerate(rules, 1):
        if r[0] == 's':
            n, conds = r[1], list(r[2] if len(r) > 2 else [])
            pct = r[3] if len(r) > 3 else 100
            tc = r[4] if len(r) > 4 else 'random'
            prm = r[5] if len(r) > 5 else ()
            rows.append(ai_row(aid, label, i, 'skill', [f'skill:{n}'] + conds, pct, tc, params=prm))
        else:
            pct = r[1] if len(r) > 1 else 100
            tc = r[2] if len(r) > 2 else 'random'
            rows.append(ai_row(aid, label, i, 'attack', [], pct, tc))
    return rows


def ai_from_datamine(dm_id, aid, label):
    """The datamined AI script, as game rows (party terms: target type:target param:term:param)."""
    rows = []
    for a in sorted(AI_DM[dm_id]['actions'], key=lambda x: int(x['priority'])):
        conds = [f"{c['type']}:{c['parameters']}" for c in a['conditions/set parameters']]
        party = [f"{c['target type']}:{c['target param']}:{c['type']}:{c['parameters']}"
                 for c in a['party conditions/set parameters']]
        params = [p for p in a['action']['parameters'].split(',') if p != '']
        pct = a['percent']
        rows.append(ai_row(aid, label, a['priority'], a['action']['type'], conds, pct,
                           a['target conditions'], a['target type'], params, party))
    return rows


# shorthand for rules
def every(n, s, extra=()):
    return ('s', s, [f'actbetween:{n}', 'turn_limited_act:1'] + list(extra))


def at(hp, s, times=1, extra=(), flag=()):
    return ('s', s, [f'hp_pr_under:{hp}', f'limited_act:{times}'] + list(extra), 100, 'random', flag)


def after(flag, s):
    """use skill s once the flag is on (set by an earlier move), and clear the flag"""
    return ('s', s, [f'flg_on:{flag}'], 100, 'random', (flag, 0))


def first_turn(s):
    return ('s', s, ['act:1', 'limited_act:1'])


def rand(s, pct, extra=(), tc='random'):
    return ('s', s, list(extra), pct, tc)


ATTACK = ('a', 100, 'random')


# ------------------------------------------------------------------ the trials
# a monster: name, unit (look), hp, atk, def, act (min,max), skills [ids], ai (rules or 'dm:<id>'), resist, el
def M(name, unit, hp, atk, df, skills, ai, act=(2, 3), resist='0:100:100:100:100:100', el=None, rate=50):
    return dict(name=name, unit=unit, hp=hp, atk=atk, df=df, skills=[str(s) for s in skills], ai=ai,
                act=act, resist=resist, el=el, rate=rate)


def rng(a, b):
    return list(range(a, b + 1))


TRIALS = {}

# ---- Trial No. 003: Juggernaut -> Demon Abaddon -> Creator Maxwell (one battle, the script swaps forms)
TRIALS['2000002'] = dict(battles=[['1002']], groups={}, mons={
    '1002': M('Juggernaut', '40334', 600000, 2300, 1200, [1902, 1903, 1904],
              [every(4, 2), rand(1, 40), rand(3, 30), ATTACK], resist='0:0:100:0:100:0'),
    '1102': M('Demon Abaddon', '60103', 750000, 2400, 1100, [1952, 1953, 1954, 1955, 1956],
              [at(30, 5, 4), at(50, 5, 2), every(4, 2), rand(3, 30), rand(4, 30), rand(1, 30), ATTACK],
              resist='60:0:0:0:0:0'),
    '54501': M('Creator Maxwell', '50525', 1000000, 2500, 1300, [2050, 2051, 2054, 2055, 2056, 2057, 2058, 2059],
               [at(20, 7), every(4, 3), every(4, 5), every(5, 8), at(30, 8, 2), ('s', 4, ['hp_pr_under:70'], 25),
                ('s', 6, ['hp_pr_under:50'], 25), rand(1, 40), ATTACK],
               resist='100:80:100:0:100:100'),
})

# ---- Trial No. 004: Mad God Zebra -> Cardes (calls Luther and Phee)
TRIALS['2000003'] = dict(battles=[['63653']], groups={'3000004': ['65251', '65252']}, mons={
    '63653': M('Mad God Zebra', '60234', 1500000, 2500, 1200, [2101510, 2101511, 2101512, 2101513, 2101514],
               [at(50, 4), at(25, 4), every(3, 3), rand(5, 25), rand(1, 35), rand(2, 35), ATTACK],
               resist='100:100:100:0:100:100'),
    '65201': M('Cardes the Malevolent', '60425', 2000000, 2600, 1300,
               [2101540, 2101541, 2101542, 2101543, 2101544, 2101545, 2101546, 2101547],
               [first_turn(6), at(20, 3), at(30, 7, 2), at(50, 2), at(50, 7), at(70, 2), at(70, 7),
                every(5, 6), every(4, 8), rand(1, 40), ATTACK],
               resist='100:80:100:80:100:100'),
    '65251': M('Defiant God Luther', '10445', 300000, 1900, 900, [2101520, 2101521],
               [rand(2, 30), rand(1, 50), ATTACK], act=(1, 2), resist='100:0:100:100:100:0'),
    '65252': M('Tyrant Goddess Phee', '20435', 50000, 1500, 800, [2101530, 2101531],
               [every(2, 2), rand(1, 40), ATTACK], act=(1, 1), resist='0:100:100:100:0:100'),
})

# ---- The Green Menace: Gravion (+Farvnil) -> Lugina -> Gaia King Lugina, with summons
TRIALS['2000004'] = dict(battles=[['33952', '33953']],
                         groups={'3000006': ['32755', '32756', '32757'], '3000007': ['32805', '32806', '32807']},
                         mons={
    '33952': M('Gravion', '30275', 700000, 2300, 1300, [2101610, 2101611, 2101612, 2101613, 2101614],
               [after(1, 4), at(70, 3, flag=(1, 1)), at(30, 3, flag=(1, 1)), at(50, 5), rand(2, 35), rand(1, 40), ATTACK],
               resist='100:0:100:0:100:0'),
    '33953': M('Royal Dragon Farvnil', '30514', 300000, 1900, 1000, [2101620],
               [rand(1, 40), ATTACK], act=(1, 2)),
    '32754': M('Lugina', '30233', 800000, 2400, 1300, [2101630, 2101631, 2101632, 2101633, 2101634, 2101635],
               [at(85, 3), at(80, 6, extra=()), at(70, 4), at(60, 6), at(55, 2), at(50, 6),
                rand(5, 30), rand(1, 40), ATTACK], resist='0:100:100:100:100:100'),
    '32755': M('Twin Arms Rickel', '20215', 150000, 1800, 900, [2101650, 2101651],
               [rand(2, 30), rand(1, 40), ATTACK], act=(1, 2), resist='100:100:100:100:100:0'),
    '32756': M('Ivy Goddess Nalmika', '30425', 150000, 1800, 900, [2101660, 2101661],
               [rand(1, 40), rand(2, 20), ATTACK], act=(1, 2)),
    '32757': M('Blade Emperor Zelban', '30255', 200000, 1800, 1100, [2101670, 2101671],
               [every(4, 1), rand(2, 35), ATTACK], act=(1, 2), resist='100:0:100:100:0:100'),
    '32804': M('Gaia King Lugina', '30234', 1200000, 2600, 1400,
               [2101680, 2101681, 2101682, 2101683, 2101684, 2101685, 2101686, 2101687],
               [first_turn(6), at(10, 6), at(70, 4), at(60, 3), at(50, 2), at(50, 7),
                every(6, 7, ['hp_pr_under:50']), every(4, 5), ('s', 8, ['hp_pr_under:55'], 30),
                rand(1, 40), ATTACK], resist='100:100:100:100:100:100'),
    '32805': M('Thief God Zelnite', '30445', 200000, 1800, 900, [2101700, 2101701, 2101702],
               [every(4, 2), rand(1, 40), rand(3, 25), ATTACK], act=(1, 2), resist='100:100:100:100:0:100'),
    '32806': M('Alpha Tree Altri', '30115', 200000, 1600, 1000, [2101710, 2101711],
               [every(3, 2), rand(1, 30), ATTACK], act=(1, 2), resist='100:100:100:100:100:0'),
    '32807': M('Deity Ruler Lucca', '30505', 250000, 1900, 1000, [2101720, 2101721],
               [every(3, 2), rand(1, 40), ATTACK], act=(1, 2), resist='100:100:100:100:100:0'),
})

# ---- Trial No. 005: Zevalhua, three forms (700k / 1.2M / 1.8M)
TRIALS['2000005'] = dict(battles=[['66051']], groups={}, mons={
    '66051': M('Zevalhua the Supreme', '60435', 700000, 2500, 1300, [2101730, 2101731, 2101732, 2101733, 2101735],
               [at(80, 3), at(60, 3), at(40, 3), every(4, 5, ()), every(5, 4), rand(2, 35), rand(1, 40), ATTACK],
               resist=IMMUNE),
    '66052': M('Zevalhua the Supreme', '60435', 1200000, 2600, 1400,
               [2101730, 2101731, 2101732, 2101734, 2101735, 2101736],
               [at(50, 6), at(30, 4), every(4, 5), every(5, 6), rand(2, 40), rand(1, 40), ATTACK],
               resist='100:50:100:100:100:100'),
    '66053': M('Zevalhua the Supreme', '60435', 1800000, 2700, 1500,
               [2101730, 2101731, 2101760, 2101761, 2101762, 2101763, 2101764, 2101765, 2101766],
               [after(1, 9), at(20, 8, flag=(1, 1)), at(40, 3), every(4, 3), every(5, 4),
                ('s', 6, ['hp_pr_over:50', 'actbetween:5', 'turn_limited_act:1']),
                ('s', 7, ['hp_pr_under:50', 'actbetween:5', 'turn_limited_act:1']),
                at(70, 5), at(30, 5), every(3, 2), rand(1, 40), ATTACK],
               resist='100:50:100:100:100:100'),
})

# ---- The Crimson Terror: Seria's three forms, each with friends
TRIALS['2000006'] = dict(battles=[['12651', '12652', '12653']],
                         groups={'3000010': ['12706', '12707'], '3000011': ['16111', '16112']}, mons={
    '12651': M('Swordswoman Seria', '10233', 700000, 2300, 1200, [3000000, 3000001, 3000002, 3000003, 3000004],
               [at(50, 4), at(25, 2), every(4, 3), rand(5, 30), rand(1, 40), ATTACK],
               resist='100:100:100:0:100:100'),
    '12652': M('Phoenix Reborn', '10115', 800000, 2000, 1100, [3000010, 3000011],
               [every(2, 2), every(3, 1), ATTACK], act=(1, 2), resist='0:100:100:100:100:0'),
    '12653': M('Drake Queen Aisha', '10214', 300000, 2000, 1000, [3000020],
               [rand(1, 40), ATTACK], act=(1, 2), resist='100:100:100:0:100:100'),
    '12705': M('Blade God Seria', '10234', 900000, 2500, 1300,
               [3000030, 3000031, 3000032, 3000033, 3000034, 3000035, 3000036],
               [at(60, 6), at(30, 7), at(50, 3), at(20, 4), every(4, 5), rand(2, 25), rand(1, 40), ATTACK],
               resist='0:100:100:0:100:100'),
    '12706': M('Guardian Darvanshel', '10465', 250000, 1800, 1300, [3000040, 3000041],
               [every(3, 2), rand(1, 40), ATTACK], act=(1, 2), resist='100:0:100:100:100:0'),
    '12707': M('Ember Charm Fiora', '10545', 200000, 1900, 1000, [3000045, 3000046],
               [every(3, 2), rand(1, 40), ATTACK], act=(1, 2), resist='100:100:100:100:100:0'),
    '16101': M('Flare Goddess Seria', '10235', 1200000, 2700, 1400,
               [3000050, 3000051, 3000052, 3000053, 3000054, 3000055, 3000056],
               [first_turn(4), at(90, 6), at(50, 7), ('s', 5, ['hp_pr_under:15', 'actbetween:5', 'turn_limited_act:1']),
                every(4, 2), rand(3, 45), rand(1, 40), ATTACK], resist='100:100:100:0:100:100'),
    '16111': M('Fire Goddess Ulkina', '10505', 250000, 1800, 1100, [3000060, 3000061],
               [every(3, 1), rand(2, 40), ATTACK], act=(1, 2)),
    '16112': M('Flame Legend Vargas', '10016', 300000, 2100, 1100, [3000070, 3000071, 3000072, 3000073],
               [every(4, 4), ('s', 3, ['actbetween:6', 'turn_limited_act:1']), rand(2, 30), rand(1, 40), ATTACK],
               act=(1, 2), resist='100:0:100:100:0:100'),
})

# ---- Trial No. 006: Beast God Afla Dilith, calls God of Decay Zurg
TRIALS['2000007'] = dict(battles=[['66451']], groups={'3000013': ['65653']}, mons={
    '66451': M('Beast God Afla Dilith', '60445', 5000000, 2700, 1500,
               [3000080, 3000081, 3000093, 3000095, 3000096, 3000091, 3000092, 3000094],
               [at(70, 4), at(50, 5), at(25, 8), at(10, 8), every(5, 6), every(4, 3), every(3, 7),
                rand(1, 35), rand(2, 40), ATTACK], resist='100:0:100:100:100:100'),
    '65653': M('God of Decay Zurg', '60544', 700000, 2300, 1200, [3000097],
               [rand(1, 50), ATTACK], act=(1, 2), resist='100:100:100:100:100:0'),
})

# ---- The Cobalt Impact: round 1 Quake God Lugina + Flare Goddess Seria; round 2 Blizzard God Karl
TRIALS['2000008'] = dict(battles=[['36402', '16103'], ['26452']],
                         groups={'3000016': ['16104', '36403'], '3000017': ['26454', '26455'], '3000018': ['66751']},
                         mons={
    '36402': M('Quake God Lugina', '30235', 1100000, 2600, 1400, [5000010, 5000011, 5000012, 5000013, 5000014, 5000015, 5000016],
               [at(10, 4), at(35, 1), at(60, 7), at(50, 6, 2), at(75, 6), every(6, 5), every(4, 3), every(2, 2),
                ATTACK], resist='0:100:0:100:100:100'),
    '16103': M('Flare Goddess Seria', '10235', 800000, 2500, 1300, [5000000, 5000001, 5000002, 5000003, 5000004, 5000005],
               [at(50, 6), at(50, 4), ('s', 5, ['hp_pr_under:10', 'actbetween:5', 'turn_limited_act:1']),
                every(4, 2), rand(3, 40), rand(1, 40), ATTACK], resist='100:100:0:0:100:100'),
    '16104': M('Phoenix God Arus', '10555', 250000, 2000, 1000, [5000020, 5000021],
               [every(3, 2), rand(1, 40), ATTACK], act=(1, 2), resist='100:100:100:100:100:0'),
    '36403': M('Noble Fist Dilma', '30285', 300000, 2000, 1100, [5000030, 5000031],
               [every(3, 2), rand(1, 40), ATTACK], act=(1, 2), resist='0:100:100:100:100:100'),
    '26452': M('Blizzard God Karl', '20235', 1500000, 2700, 1400,
               [5000040, 5000041, 5000042, 5000043, 5000044, 5000045, 5000046, 5000047, 5000048, 4003104],
               [at(50, 8), at(50, 9), at(75, 2), every(3, 10), every(4, 3), every(5, 7), rand(4, 25), rand(5, 25),
                rand(6, 20), rand(1, 30), ATTACK], resist='0:100:100:100:100:100'),
    '26453': M('Blizzard God Karl', '20235', 1500000, 2800, 1500,
               [5000080, 5000086, 5000087, 5000088, 5000089, 5000090, 5000091, 5000082, 4003104],
               [first_turn(7), at(50, 6), at(30, 5), at(10, 6), ('s', 8, ['hp_pr_under:50', 'actbetween:3', 'turn_limited_act:1']),
                every(5, 5), every(3, 9), rand(2, 25), rand(3, 25), rand(4, 20), rand(1, 30), ATTACK],
               resist='0:100:100:100:100:100'),
    '26454': M('Ice Legend Selena', '20016', 300000, 2000, 1000, [5000050, 5000051, 5000052, 5000053],
               [every(4, 4), every(5, 3), rand(2, 30), rand(1, 40), ATTACK], act=(1, 2)),
    '26455': M('Beast God Exvehl', '40445', 300000, 2000, 1000, [5000060, 5000061],
               [every(3, 1), rand(2, 40), ATTACK], act=(1, 2)),
    '66751': M('Twilight God Grahdens', '60325', 600000, 2300, 1300, [5000062, 5000063, 5000064, 5000065, 5000066, 5000067, 5000068, 5000069],
               [at(80, 7), at(40, 8), at(60, 6), at(20, 7), every(4, 2), rand(4, 25), rand(1, 40), ATTACK],
               act=(1, 2), resist='0:100:0:100:0:100'),
})

# ---- Trial No. 007: round 1 Ark (calls his friends); round 2 Ark -> Oracle Knight Ark
TRIALS['2000009'] = dict(battles=[['57351'], ['57352']],
                         groups={'3000021': ['57361', '57362', '57363', '57364', '57365', '57366']}, mons={
    '57351': M('Ark', '50285', 1500000, 2600, 1300, [5000160, 5000161, 5000162, 5000163, 5000164, 5000165, 5000166, 5000167, 5000168],
               [first_turn(3), at(80, 4), at(60, 5), at(40, 7, 2), at(20, 9), every(3, 6), every(4, 2),
                rand(7, 30), rand(1, 40), ATTACK], resist='100:50:100:100:100:100'),
    '57361': M('Hurricane Cutter Dion', '30605', 120000, 1700, 900, [5000120, 5000121], [rand(2, 30), rand(1, 40), ATTACK], act=(1, 2)),
    '57362': M('Eerie Possession Yuura', '60735', 120000, 1700, 900, [5000150, 5000151], [rand(2, 30), rand(1, 40), ATTACK], act=(1, 2)),
    '57363': M('Pyro Revolution Ruby', '10645', 120000, 1700, 900, [5000100, 5000101], [rand(2, 30), rand(1, 40), ATTACK], act=(1, 2)),
    '57364': M('Crushing Thunder Balgran', '40585', 120000, 1700, 900, [5000130, 5000131], [rand(2, 30), rand(1, 40), ATTACK], act=(1, 2)),
    '57365': M('Ocean Pearl Medina', '20615', 120000, 1700, 900, [5000110, 5000111], [rand(2, 30), rand(1, 40), ATTACK], act=(1, 2)),
    '57366': M('Sacred Advocate Rinon', '50735', 120000, 1700, 900, [5000140, 5000141], [every(3, 1), rand(2, 30), ATTACK], act=(1, 2)),
    '57352': M('Ark', '50285', 1500000, 2700, 1400, [5000180, 5000181, 5000182, 5000183, 5000184],
               [at(75, 5), every(3, 4), every(2, 2), rand(3, 35), rand(1, 40), ATTACK], resist='100:50:100:100:100:100'),
    '57401': M('Oracle Knight Ark', '50286', 2500000, 2800, 1500,
               [5000190, 5000191, 5000192, 5000193, 5000194, 5000195, 5000198, 5000199, 5000200, 5000202, 5000203, 5000204],
               [at(10, 12), ('s', 12, ['hp_pr_under:10', 'actbetween:5', 'turn_limited_act:1']), after(1, 11), at(30, 10, flag=(1, 1)),
                at(40, 8), at(40, 9), at(75, 6), at(60, 7),
                every(4, 3), every(3, 4), every(5, 2), rand(5, 25), rand(1, 40), ATTACK],
               resist='100:50:100:100:100:100'),
})

# ---- Imperial Ambition: round 1 Eriole; round 2 Blind Justice Tesla, who calls Winged Lord Eriole
TRIALS['2000010'] = dict(battles=[['55352'], ['27851']], groups={'3000024': ['57451']}, mons={
    '55352': M('Eriole', '50444', 2000000, 2700, 1400, [5000210, 5000211, 5000212, 5000213, 5000214, 5000215, 5000216],
               [at(70, 4), at(50, 4), at(40, 7), at(30, 7), at(20, 7), at(10, 7), at(80, 5), every(4, 6),
                rand(1, 35), rand(3, 30), rand(2, 30), ATTACK], resist='0:100:0:100:100:100'),
    '27851': M('Blind Justice Tesla', '20525', 3000000, 2800, 1500,
               [5000230, 5000231, 5000232, 5000233, 5000234, 5000235, 5000236, 5000237, 5000238, 5000239, 5000240,
                5000241, 5000242, 5000243, 5000244, 5000245],
               [at(90, 14), at(90, 15), at(60, 9), at(30, 13), ('s', 10, ['hp_pr_under:30'], 30),
                first_turn(12), every(5, 11), ('s', 3, [], 17), ('s', 4, [], 20), ('s', 5, [], 25), ('s', 6, [], 33),
                ('s', 7, [], 50), ('s', 8, []), every(3, 1), rand(2, 30), rand(16, 40), ATTACK],
               resist='0:100:100:100:100:0'),
    '57451': M('Winged Lord Eriole', '50445', 3500000, 2800, 1500, [5000250, 5000251, 5000252, 5000253, 5000254, 5000255, 5000256, 5000257, 5000258],
               [at(10, 5), at(77, 7), at(50, 2), at(40, 9), at(30, 9), at(20, 9), every(4, 6), every(3, 3),
                rand(1, 40), ATTACK], resist='100:100:0:100:100:100'),
})

# ---- Trial No. 008: Multifarious Lucius -> Sealed God Lucius (two forms)
TRIALS['2000011'] = dict(battles=[['3007']], groups={}, mons={
    '3007': M('Multifarious Lucius', '50786', 4000000, 2800, 1500, [5001100, 5001071, 5001077, 5001074, 5001073],
              [first_turn(2), every(6, 4), every(3, 3), rand(5, 30), rand(1, 50), ATTACK],
              resist='100:50:50:50:100:100'),
    '57651': M('Sealed God Lucius', '50786', 4000000, 2900, 1500,
               [5001100, 5001111, 5001102, 5001101, 5001103, 5001104, 5001105, 5001108],
               [first_turn(2), every(5, 5), every(3, 4), every(2, 3), at(50, 6), at(25, 6), every(6, 8),
                rand(7, 15), rand(1, 40), ATTACK], resist='100:50:50:50:100:100'),
    '57652': M('Sealed God Lucius', '50786', 2000000, 3000, 1600,
               [5000310, 5001126, 5000313, 5001107, 5001122, 5001127, 5001110],
               [first_turn(2), at(80, 5, 3), at(50, 7), ('s', 7, ['hp_pr_under:25', 'actbetween:4', 'turn_limited_act:1']),
                every(4, 3), every(4, 4), every(5, 5), every(6, 6), rand(1, 50), ATTACK],
               resist='100:50:50:50:100:100'),
})

# ---- X trials: the datamined AI scripts
TRIALS['8300031'] = dict(battles=[['87400300', '87100300', '87600300'], ['87500300', '87300300', '87200300']], groups={}, mons={
    '87400300': M('Raid Bolt Fennia', '840005', 650000, 2200, 900, rng(8400641, 8400646), 'dm:8100094', act=(2, 3)),
    '87100300': M('War Rocket Cayena', '810005', 650000, 2200, 900, rng(8400611, 8400615), 'dm:8100095', act=(2, 3)),
    '87600300': M('Demon Blade Lico', '860005', 650000, 2200, 900, rng(8400661, 8400665), 'dm:8100096', act=(2, 3)),
    '87500300': M('Sky Angel Vanila', '850005', 650000, 2300, 900, rng(8400651, 8400656), 'dm:8100097', act=(2, 3)),
    '87300300': M('Wild Slash Bayley', '830005', 650000, 2300, 900, rng(8400631, 8400636), 'dm:8100098', act=(2, 3)),
    '87200300': M('Gun Goddess Serin', '820005', 650000, 2300, 900, rng(8400621, 8400626), 'dm:8100099', act=(2, 3)),
})
FEI = rng(8400718, 8400724)
TRIALS['8360041'] = dict(battles=[['87600400']],
                         groups={'8600304': ['87600401'], '8600305': ['87600402'], '8600306': ['87600403'],
                                 '8600307': ['87600404', '87600405']}, mons={
    '87600400': M("Temptress Xie'Jing", '860156', 4250000, 2600, 1300, rng(8400701, 8400717), 'dm:8100121', act=(3, 4),
                  resist='100:90:90:100:100:100'),
    '87600401': M('Cursed Fei', '820504', 500000, 2200, 1100, FEI, 'dm:8100122', act=(1, 2), resist='70:100:100:100:100:100'),
    '87600402': M('Blighted Fei', '820505', 750000, 2300, 1200, FEI, 'dm:8100123', act=(1, 2), resist='80:100:100:100:100:100'),
    '87600403': M('Execrated Fei', '820506', 1000000, 2400, 1300, FEI, 'dm:8100124', act=(1, 2), resist='90:100:100:100:100:100'),
    '87600404': M('Bozdell', '10261', 500, 1500, 99999, rng(8400725, 8400730), 'dm:8100125', act=(1, 1), resist=IMMUNE),
    '87600405': M('Bozdell', '10261', 500, 1500, 99999, rng(8400725, 8400730), 'dm:8100126', act=(1, 1), resist=IMMUNE),
})
TRIALS['8360051'] = dict(battles=[['87500501']],
                         groups={'8600403': ['87500503'], '8600404': ['87500504'], '8600405': ['87500505']}, mons={
    '87500501': M('Gazia', '850346', 3000000, 2700, 1400, rng(8400801, 8400821), 'dm:8100142', act=(3, 4), resist=IMMUNE),
    '87500502': M('Mad Heretic Gazia', '850347', 3500000, 2800, 1500, rng(8400801, 8400822), 'dm:8100143', act=(3, 4), resist=IMMUNE),
    '87500503': M('Turbo Cyclaw', '810125', 150000, 2000, 1000, rng(8400823, 8400825), 'dm:8100144', act=(1, 2)),
    '87500504': M('Turbo Euryalis', '830125', 150000, 2000, 1000, rng(8400826, 8400828), 'dm:8100145', act=(1, 2)),
    '87500505': M('Turbo Grievesnare', '840125', 150000, 2000, 1000, rng(8400829, 8400831), 'dm:8100146', act=(1, 2)),
})


# ------------------------------------------------------------------ build
def summon_spots(skill_ids):
    """group id -> {slot: 'x:y'} from the summon moves' own params."""
    out = {}
    for sid in skill_ids:
        if '37' not in S[sid]['hjAy9St3'].split('@'):
            continue
        first = (L.get(sid, {}).get('Kn51uR4Y') or '').split('|')[0]
        parts = first.split(':', 2)
        if len(parts) < 3:
            continue
        for proc, prm in zip(S[sid]['hjAy9St3'].split('@'), parts[2].split('@')):
            if proc != '37':
                continue
            f = prm.split(',')
            out.setdefault(f[0], {})[int(f[1])] = f'{f[2]}:{f[3]}'
    return out


# trials bfrevive has properly built (every form in the answer, the game's own monster ids):
# taken whole from the recorded answer - all forms, AI, moves and monster sprites.
# The other trials bfrevive answers with rough stand-ins (all forms on the field at once, wrong
# monster ids so the game's trial script can't run), so those use the build above.
RECORDED = ['2000000', '2000001', '2000002', '8360021']


def recorded_answer(mid):
    import gzip
    p = os.path.join(HERE, 'quest_recordings', f'mission_{mid}.json')
    if os.path.exists(p):
        return json.load(open(p, encoding='utf-8'))['response']
    T = json.loads(gzip.open(os.path.join(HERE, 'data', 'templates.json.gz')).read())
    for a in T['action']:
        if a['cls'] == 'MissionStartRequest' and str(((a['req'].get('9Q1Lq5FS') or [{}])[0]).get('j28VNcUW')) == mid:
            return T['blobs'][a['resp']]
    raise SystemExit(f'no recording for {mid}')


# status resistances the recorded answer has wrong (resist order: poison, weak, sick, injury, curse,
# paralysis - 100 = immune). Trial No. 002: Grahdens only takes weak and injury.
RESIST_FIX = {'2000001': {'Grahdens': {0: '100', 1: '0', 2: '100', 3: '0', 4: '100', 5: '100'}}}


def from_recording(mid):
    r = recorded_answer(mid)
    mons = {m['o49dYfpH']: m for m in r['U0v5IeJo']}
    nb = len(r['pj41dy9g'])
    names = [[mons[b['o49dYfpH']]['3evIn0zZ'] for b in r['75t0sx9z'] if b['Qzhp8B40'] == str(bi)] for bi in range(1, nb + 1)]
    skills = list(r.get('8aiBoHg5') or [])
    have = {k['nj9Lw7mV'] for k in skills}
    for m in r['U0v5IeJo']:            # a move the answer names but didn't send: from the game's own list
        for part in filter(None, (m.get('F4bQ7r8C') or '').split('@')):
            sid = part.split(':')[0]
            if sid not in have and sid in S:
                skills.append(skill_row(sid)); have.add(sid)
    for m in r['U0v5IeJo']:
        for i, v in (RESIST_FIX.get(mid, {}).get(m.get('3evIn0zZ')) or {}).items():
            f = (m.get('CEeqs63b') or '0:0:0:0:0:0').split(':')
            f[i] = v
            m['CEeqs63b'] = ':'.join(f)
    return {'battles': nb, 'monsters': r['U0v5IeJo'], 'groups': r['75t0sx9z'], 'ai': r.get('89ausgc4') or [],
            'skills': skills, 'cgs': r.get('8hoyIF9Q') or [], 'names': names, 'src': 'bfrevive'}


# recorded trials whose enemy AI is replaced by the official script from the datamine (monster -> AI)
OFFICIAL_AI = {'8360021': {'87500128': '8150004',     # Cursed Estia: "AI Cursed Estia 4 Skills"
                           '87500129': '8100088',     # Princess Estia: "TX1_Estia_AI"
                           '87500130': '8150005'}}    # Xenon & Estia: "AI XenonEstia Couple"


def use_official_ai(mid, spec):
    """Keep the recorded monsters (stats, moves, forms) but give each the official AI script, under the
    AI id the monster already points at."""
    rows = []
    for m in spec['monsters']:
        dm = OFFICIAL_AI[mid].get(m['o49dYfpH'])
        aid = m['i74vGUFa']
        if dm:
            rows += ai_from_datamine(dm, aid, f"{MNAMES.get(mid) or mid} - {m['3evIn0zZ']} ({AI_DM[dm]['name']})")
        else:
            rows += [r for r in spec['ai'] if r['4eEVw5hL'] == aid]
    spec['ai'] = rows
    spec['ai_src'] = 'official'
    return spec


# recorded monsters shown with the game's own monster sprite instead of a unit's look (monster -> sprite no.)
MONSTER_SPRITE = {'8360021': {'87500128': '87600100'}}   # Trial X1 phase 1: Cursed Estia (cdn/content/monster)


def use_monster_sprite(mid, spec):
    cgs = [r for r in spec.get('cgs') or []]
    for m in spec['monsters']:
        n = MONSTER_SPRITE[mid].get(m['o49dYfpH'])
        if not n:
            continue
        m.update({'2EF0d6ue': f'monster_anime_{n}.png', 'QqfI9mM4': f'monster_cgg_{n}.csv', 'pn16CNah': '0'})
        cgs = [r for r in cgs if r['o49dYfpH'] != m['o49dYfpH']]
        for k, kind in ((1, 'idle'), (2, 'move'), (3, 'atk'), (4, 'atk')):
            cgs.append({'o49dYfpH': m['o49dYfpH'], 'fpc3rbs7': str(k), '0QxL2is7': f'monster_{kind}_cgs_{n}.csv'})
    spec['cgs'] = cgs
    return spec


# Trial No. 003, Creator Maxwell, as the wiki has it: Endless once when she first acts under 20%, and
# also once for every dialogue threshold (70/50/30/20% HP) when several are crossed between two of her
# actions. Flags 15-18: threshold 70/50/30/20 seen; flag 19: an Endless chain is running. (The game only
# reads flags 1-20 - a first version used 31-39, which always read as off.)
# The "seen" flags are set by her own moves: each of her moves comes in HP bands (70-50, 50-30, 30-20)
# and the banded copy marks every threshold above her HP as seen. (A first try used a plain attack to
# mark them; the game ignored the flags on that attack and she used Endless on a single crossing.)
MAXWELL_AI = '20000023'
# Off: in the game the "threshold seen" flags don't survive her dialogue (or are never kept), so she
# used Endless on single crossings in three recorded runs. Back to the recorded AI: Endless once,
# the first time she acts under 20%.
MAXWELL_THRESHOLDS = False
MAXWELL_SKILL = {'endless': 7}
BANDS = [(70, 50, (15, 1)), (50, 30, (15, 1, 16, 1)), (30, 20, (15, 1, 16, 1, 17, 1))]


def _conds(r):
    return [c for c in r['q7Nit8JW'].split('#')[1].split('@') if c and c != 'non:0']


def _banded(r, label):
    """Copies of a recorded AI row for each HP band, marking the thresholds above it as seen."""
    conds = _conds(r)
    if any(c.startswith('limited_act') for c in conds) or not r['Hhgi79M1'].startswith(('skill@', 'attack@')):
        return []
    if any(p != '-1' for p in r['Hhgi79M1'].split('@')[1].split(',') if p):
        return []                                   # already sets flags of its own
    hi = min([int(c.split(':')[1]) for c in conds if c.startswith('hp_pr_under:')] or [101])
    lo = max([int(c.split(':')[1]) for c in conds if c.startswith('hp_pr_over:')] or [-1])
    rest = [c for c in conds if not c.startswith(('hp_pr_under:', 'hp_pr_over:'))]
    act = r['Hhgi79M1'].split('@')[0]
    out = []
    for top, bottom, flags in BANDS:
        u, o = min(hi, top), max(lo, bottom)
        if u <= o:
            continue
        c = rest + [f'hp_pr_under:{u}', f'hp_pr_over:{o}']
        if len(c) > 5:
            continue
        skill = [x for x in c if x.startswith('skill:')]
        other = [x for x in c if not x.startswith('skill:')]
        row = ai_row(MAXWELL_AI, label, 0, act, other if act == 'attack' else other, int(r['ug9xV4Fz']),
                     r['VBj9u0ot'], int(r['4xctV8gF']), params=flags)
        if skill:                                   # keep the skill term first, like the game's rows
            row = ai_row(MAXWELL_AI, label, 0, act, skill + other, int(r['ug9xV4Fz']), r['VBj9u0ot'],
                         int(r['4xctV8gF']), params=flags)
        out.append(row)
    return out


def _outside(r):
    """A chance-based row whose banded copies already roll inside 70-20%: the original only rolls
    outside the bands, so each move keeps its own chance (one roll per action, not two)."""
    conds = _conds(r)
    hi = min([int(c.split(':')[1]) for c in conds if c.startswith('hp_pr_under:')] or [101])
    lo = max([int(c.split(':')[1]) for c in conds if c.startswith('hp_pr_over:')] or [-1])
    rest = [c for c in conds if not c.startswith(('hp_pr_under:', 'hp_pr_over:'))]
    out = []
    for u, o in ((hi, max(lo, 70)), (min(hi, 20), lo)):
        if u <= o:
            continue
        c = rest + ([f'hp_pr_under:{u}'] if u < 101 else []) + ([f'hp_pr_over:{o}'] if o >= 0 else [])
        cs = c + ['non:0'] * (5 - len(c))
        party = r['q7Nit8JW'].split('#')[0]
        out.append(dict(r, q7Nit8JW=party + '#' + '@'.join(cs) + '@'))
    return out


def maxwell_thresholds(spec):
    rows = [r for r in spec['ai'] if r['4eEVw5hL'] == MAXWELL_AI]
    rest = [r for r in spec['ai'] if r['4eEVw5hL'] != MAXWELL_AI]
    rows.sort(key=lambda r: int(r['yu18xScw']))
    label = rows[0]['L8PCsu0K']
    e = MAXWELL_SKILL['endless']
    endless = [                                     # (conditions, flags set)
        (['flg_off:15', 'hp_pr_under:50'], (15, 1, 19, 1)),     # 70% and 50% crossed together
        (['flg_off:16', 'hp_pr_under:30'], (16, 1, 19, 1)),     # 50% and 30%
        (['flg_off:17', 'hp_pr_under:20'], (17, 1, 19, 1)),     # 30% and 20%
        (['flg_on:19', 'flg_off:16', 'hp_pr_under:50'], (16, 1, 19, 0)),   # last of a chain
        (['flg_on:19', 'flg_off:17', 'hp_pr_under:30'], (17, 1, 19, 0)),
        (['flg_on:19', 'flg_off:18', 'hp_pr_under:20'], (18, 1, 19, 0)),
        (['flg_off:18', 'hp_pr_under:20'], (18, 1)),            # 20% crossed on its own
    ]
    old = [r for r in rows if f'skill:{e}@' in r['q7Nit8JW']]
    assert len(old) == 1, 'Maxwell: expected one Endless row'
    out = []
    for r in rows:
        if r is old[0]:
            out += [ai_row(MAXWELL_AI, label, 0, 'skill', [f'skill:{e}'] + c, params=f) for c, f in endless]
        else:
            band = _banded(r, label)
            out += band + (_outside(r) if band and int(r['ug9xV4Fz']) < 100 else [r])
    spec['ai'] = rest + [dict(r, yu18xScw=str(i)) for i, r in enumerate(out, 1)]
    return spec


# Death Gate (Abaddon) deals 120% HP like the wiki says: that number lives in the game's skill-level
# table, not here - see data/skill_level_fix.json and skilllevel.py.


def build():
    out = {mid: from_recording(mid) for mid in RECORDED}
    if MAXWELL_THRESHOLDS:
        out['2000002'] = maxwell_thresholds(out['2000002'])
    for mid in OFFICIAL_AI:
        out[mid] = use_official_ai(mid, out[mid])
    for mid in MONSTER_SPRITE:
        out[mid] = use_monster_sprite(mid, out[mid])
    next_ai = 21000000
    for mid, t in TRIALS.items():
        if mid in RECORDED:
            continue
        mons, ai_rows, skills = [], [], {}
        all_skills = [s for m in t['mons'].values() for s in m['skills']]
        for s in all_skills:
            assert s in S, (mid, s, 'skill not in client master')
            assert s in L, (mid, s, 'no level row')
            skills[s] = skill_row(s)
        for mon_id, m in t['mons'].items():
            u = U[m['unit']]
            next_ai += 1
            aid = str(next_ai)
            label = f"{MNAMES.get(mid) or mid} - {m['name']}"
            if isinstance(m['ai'], str) and m['ai'].startswith('dm:'):
                rows = ai_from_datamine(m['ai'][3:], aid, label)
            else:
                rows = ai_from_rules(aid, label, m['ai'])
            for r in rows:            # every skill an AI row names must exist
                for c in r['q7Nit8JW'].split('#')[1].split('@'):
                    if c.startswith('skill:'):
                        n = int(c.split(':')[1])
                        assert 1 <= n <= len(m['skills']), (mid, mon_id, m['name'], n, len(m['skills']))
            ai_rows += rows
            row = dict(TEMPLATE)
            row.update({'o49dYfpH': mon_id, '3evIn0zZ': m['name'], 'e7DK0FQT': str(m['hp']),
                        '67CApcti': str(m['atk']), 'q08xLEsy': str(m['df']),
                        'iNy0ZU5M': str(m['el'] or u.get('iNy0ZU5M', '1')),
                        'eyUo6a8c': u.get('eyUo6a8c', ''), '6Aou5M9r': u.get('6Aou5M9r', ''),
                        'pn16CNah': m['unit'], 'J2hPXGo5': u.get('J2hPXGo5', '3'), 'mv4o39Uz': u.get('mv4o39Uz', '1'),
                        'h2L1YI90': u.get('h2L1YI90', '1'), 'XE7Yi5c3': u.get('XE7Yi5c3', '1'),
                        'D4Y5bWK7': u.get('D4Y5bWK7', '1'), 'i74vGUFa': aid,
                        'm4EK7Gt6': str(m['act'][0]), 'oMGC3hW0': str(m['act'][1]), '6fwL59FT': str(m['rate']),
                        'F4bQ7r8C': '@'.join(f'{s}:1' for s in m['skills']), 'CEeqs63b': m['resist']})
            mons.append(row)
        bmon = []
        for bi, b in enumerate(t['battles'], 1):
            lay = LAYOUT[len(b)]
            for pos, mon_id in enumerate(b):
                bmon.append({'Qzhp8B40': str(bi), 'o49dYfpH': mon_id, 'hZtF1s8B': str(pos), '3g8PW6x0': lay[pos],
                             'hw3L0uVj': '0:0:0:0'})
        spots = summon_spots(all_skills)
        for gid, members in t['groups'].items():
            assert gid in spots, (mid, gid, 'no summon move calls this group')
            for slot, mon_id in enumerate(members):
                assert mon_id in t['mons'], (mid, mon_id)
                bmon.append({'Qzhp8B40': gid, 'o49dYfpH': mon_id, 'hZtF1s8B': str(slot),
                             '3g8PW6x0': spots[gid].get(slot, '120:248'), 'hw3L0uVj': '0:0:0:0'})
        for gid in spots:
            assert gid in t['groups'], (mid, gid, 'summon group without members')
        out[mid] = {'battles': len(t['battles']), 'monsters': mons, 'groups': bmon, 'ai': ai_rows,
                    'skills': list(skills.values()),
                    'names': [[t['mons'][x]['name'] for x in b] for b in t['battles']], 'src': 'built'}
    out['_hidden'] = ['2000012', '2000013', '2000014', '2000015']    # JP-only trials, not in Global
    out['_how'] = 'built by tools/build_trials.py from the game\'s own trial scripts and boss moves'
    path = os.path.join(HERE, 'data', 'trial_specs.json')
    json.dump(out, open(path, 'w', encoding='utf-8'), ensure_ascii=False)
    for mid in sorted(k for k in out if not k.startswith('_')):
        o = out[mid]
        print(mid, o['src'], o['battles'], 'battles', len(o['monsters']), 'monsters', len(o['ai']), 'AI rows',
              len(o['skills']), 'skills', o['names'])
    print('wrote', path)


if __name__ == '__main__':
    build()
