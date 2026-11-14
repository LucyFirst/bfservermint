"""
Builds data/custom_bosses.json - boss fights we design ourselves for custom quests (data/custom_dungeons.json).

A custom boss is played like a trial (one battle, the server sends the boss, its moves and its AI with
the quest). Its moves are moves the game already has (skill rows from the client masters); their
numbers come from data/skill_level_fix.json (the server hands the game an edited skill-level table,
see skilllevel.py). Its look can be a custom unit sprite (custom_cdn/, see server.sync_custom_cdn).

AI rows: the game runs them top to bottom on each of the boss's actions and uses the first row whose
conditions hold. Terms used here:
  turn_act:a,b      this is the boss's a-th..b-th action of the turn
  hp_pr_under:n     boss HP under n%
  limited_act:n     this row is used at most n times per battle
  actbetween:n      at least n turns since this row was last used
  flg_on:n/flg_off:n  battle flag n set / not set; an action's params "n,v,..." set flag n to v

Run:  python3 tools/build_custom_bosses.py   (writes data/custom_bosses.json)
"""
import glob
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MST = '/home/claude/mst'

S = {}
for f in glob.glob(f'{MST}/Ver1535_wkCyV73D_*.json'):
    for r in json.load(open(f)):
        S[r['nj9Lw7mV']] = r
U = json.load(open(os.path.join(HERE, 'data', 'unit_mst.json')))

# the trial boss row layout (tools/build_trials.py TEMPLATE)
TEMPLATE = {"o49dYfpH": "", "3evIn0zZ": "", "e7DK0FQT": "0", "67CApcti": "0", "q08xLEsy": "0", "iNy0ZU5M": "1",
            "eyUo6a8c": "", "6Aou5M9r": "", "n9h7p02P": "3", "2EF0d6ue": "", "QqfI9mM4": "", "pn16CNah": "",
            "qp37xTDh": "", "J2hPXGo5": "4", "mv4o39Uz": "1", "h2L1YI90": "1", "i74vGUFa": "1", "D4Y5bWK7": "1",
            "oMGC3hW0": "2", "m4EK7Gt6": "1", "6fwL59FT": "50", "F4bQ7r8C": "", "XE7Yi5c3": "1",
            "CEeqs63b": "0:100:100:100:100:100", "Najhr8m6": "0", "9FN0GAei": "0", "HTVh8a65": "0", "vNk2sI4X": "0",
            "3BpHN6VD": "", "hjAy9St3": "", "2Smu5Mtq": "", "CYk84E3W": "0,0", "Y6bd4fXp": "0,0,0", "jm6JSK2D": "",
            "9pXSKmn1": "5", "Lkh6gYkT": "0,0"}
IMMUNE = '100:100:100:100:100:100'      # poison, weak, sick, injury, curse, paralysis


def ai_row(aid, label, prio, act, conds=(), pct=100, tc='random', params=()):
    conds = list(conds)
    assert len(conds) <= 5, (label, conds)
    conds += ['non:0'] * (5 - len(conds))
    vals = [str(x) for x in params] + ['-1'] * (12 - len(params))
    return {'4eEVw5hL': str(aid), 'yu18xScw': str(prio), 'L8PCsu0K': label,
            'q7Nit8JW': '@'.join(['0:non:non:non'] * 4) + '@#' + '@'.join(conds) + '@',
            '4xctV8gF': '2', 'VBj9u0ot': tc,
            'Hhgi79M1': f'{act}@{",".join(vals)},@1@0@0', 'ug9xV4Fz': str(pct)}


def skill_row(sid, name, look=None, set_=None):
    """The game's own skill row, renamed; look: another skill whose animation it borrows; set_: fields."""
    r = dict(S[sid])
    if look:
        for f in ('eyUo6a8c', '6Aou5M9r', 'diGe6u21', 'I7cmtqW8', 'K2i8emcH', 'qYCx73y2', '9pXSKmn1', 'AR7y0sY4'):
            r[f] = S[look][f]
    r.update(set_ or {})
    r['0nxpBDz2'] = name
    r['qp37xTDh'] = 'None'
    return r


BOSSES = {}

# ---------------------------------------------------------------- Vargas of Mint (green recolor of Fencer Vargas)
# 3,000,000 HP / 3,500 ATK / 1,600 DEF, Fire, 3 actions a turn.
#  - the turn it first drops under 50%: 6 actions, each a normal attack on a random unit
#  - under 30%: 250,000 HP Earth shield (one of his actions), then every 4 turns
#  - his attacks (Mint Slash) can Paralyze; 3,000 ATK, 1,600 DEF
#  - at 5% or lower (first time): 9,999 fixed damage to 4 random units (4 actions that turn)
# How the game's boss AI works (from its own scripts, e.g. the Frontier Hunter bosses):
#   act:N            = battle turn N (not the action); turn_act:a,b = battle turns a..b
#   flags 11-20      = reset every turn (12 with "once per turn" moves, 11 to end a turn);
#                      flags 1-10 last the whole battle; params "n,v" set flag n to v
#   params "31,1"    = add 1 to this turn's counter 1; flg_tmp_cntup_under:1,k = counter below k
# So the boss may act 6 times a turn; every action adds 1 to counter 1, ordinary actions only run
# while it is below 3, and when nothing else may run the last row ends the turn.
#   flag 13 (this turn only) = the 6-action turn (first time under 50%)
#   flag 14 (this turn only) = the 5% turn: 4 x 9,999 then the turn ends
SHIELD, FIXED, SLASH = '830131253', '6000594', '650460'   # moves re-numbered in data/skill_level_fix.json
C = (31, 1)                                # count this action
BOSSES['vv'] = dict(
    name='Vargas of Mint', unit='990011', look='10011', monster='9900111', ai_id='99001101',
    hp=3000000, atk=3500, df=1600, el=1, act=(6, 6), resist=IMMUNE,
    skills=[(SHIELD, 'Mint Bulwark', '8413008', {'ToZj4zyn': '0'}),          # a shield that doesn't end his turn
            (FIXED, 'Mint Requiem', None),
            # his attack: Fencer Vargas's own swing (frames of unit 10011), normal-attack strength + Paralysis
            (SLASH, 'Mint Slash', None, {'eyUo6a8c': '20:121:1,38:121:1,56:121:1', 'AR7y0sY4': '1',
                                         '6Aou5M9r': '26:50:2:2,44:30:2:2,62:20:2:2@0:100:2:1'})],
    ai=[
        ('skill', ['skill:2', 'hp_pr_under:6', 'limited_act:1'], 100, (14, 1) + C),       # 5%: hit 1
        ('skill', ['skill:2', 'flg_on:14', 'limited_act:1'], 100, C),                      # hit 2
        ('skill', ['skill:2', 'flg_on:14', 'limited_act:1'], 100, C),                      # hit 3
        ('skill', ['skill:2', 'flg_on:14', 'limited_act:1'], 100, C),                      # hit 4
        ('skill', ['skill:3', 'hp_pr_under:50', 'limited_act:1'], 100, (13, 1) + C),                 # 50%: 6-action turn
        ('skill', ['skill:3', 'flg_on:13', 'flg_tmp_cntup_under:1,6'], 100, C),
        ('skill', ['skill:1', 'hp_pr_under:30', 'actbetween:4', 'flg_tmp_cntup_under:1,3'], 100, C),   # shield
        ('skill', ['skill:3', 'flg_tmp_cntup_under:1,3'], 100, C),                         # normal: 3 actions
        ('turn_end', []),
    ])


def build():
    out = {}
    for key, b in BOSSES.items():
        look = U[b['look']]
        label = f"Custom - {b['name']}"
        ai = [ai_row(b['ai_id'], label, i, a[0], a[1], a[2] if len(a) > 2 else 100, 'random', a[3] if len(a) > 3 else ())
              for i, a in enumerate(b['ai'], 1)]
        for r in ai:
            for c in r['q7Nit8JW'].split('#')[1].split('@'):
                if c.startswith('skill:'):
                    assert 1 <= int(c.split(':')[1]) <= len(b['skills'])
        mon = dict(TEMPLATE)
        mon.update({'o49dYfpH': b['monster'], '3evIn0zZ': b['name'], 'e7DK0FQT': str(b['hp']),
                    '67CApcti': str(b['atk']), 'q08xLEsy': str(b['df']), 'iNy0ZU5M': str(b['el']),
                    'eyUo6a8c': look.get('eyUo6a8c', ''), '6Aou5M9r': look.get('6Aou5M9r', ''),
                    'pn16CNah': b['unit'], 'J2hPXGo5': look.get('J2hPXGo5', '3'), 'mv4o39Uz': look.get('mv4o39Uz', '1'),
                    'h2L1YI90': look.get('h2L1YI90', '1'), 'XE7Yi5c3': look.get('XE7Yi5c3', '1'),
                    'D4Y5bWK7': look.get('D4Y5bWK7', '1'), 'i74vGUFa': b['ai_id'],
                    'm4EK7Gt6': str(b['act'][0]), 'oMGC3hW0': str(b['act'][1]), '6fwL59FT': '100',
                    'F4bQ7r8C': '@'.join(f'{s[0]}:1' for s in b['skills']), 'CEeqs63b': b['resist']})
        out[key] = {'battles': 1, 'monsters': [mon],
                    'groups': [{'Qzhp8B40': '1', 'o49dYfpH': b['monster'], 'hZtF1s8B': '0', '3g8PW6x0': '180:302',
                                'hw3L0uVj': '0:0:0:0'}],
                    'ai': ai, 'skills': [skill_row(*s) for s in b['skills']], 'cgs': [],
                    'names': [[b['name']]], 'src': 'custom', 'exact': True}
    out['_how'] = 'built by tools/build_custom_bosses.py; used by the quests in data/custom_dungeons.json'
    path = os.path.join(HERE, 'data', 'custom_bosses.json')
    json.dump(out, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    for k, o in out.items():
        if not k.startswith('_'):
            print(k, o['names'], len(o['ai']), 'AI rows', len(o['skills']), 'skills')
    print('wrote', path)


if __name__ == '__main__':
    build()
