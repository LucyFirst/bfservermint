"""
Builds data/event_bosses.json - the real moves of event bosses whose recordings show the wrong ones.

What comes from the game itself (client masters, decrypted into /home/claude/mst): every move
(skill rows + their level params: the shields' HP / DEF / element / turns, hits, ailments...).
What is ours: the AI (when each move is used), written from the wiki's move descriptions, and
act counts / ailment resists.

The battle generator (battlegen.py WIKI_LINEUPS) fights these bosses with their recorded look and
this file's moves; the server sends the AI and skill rows with the quest.

Run:  python3 tools/build_event_bosses.py   (writes data/event_bosses.json)
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_trials import S, L, NAMES, ai_from_rules, at, first_turn, rand, ATTACK  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def every_below(hp, n, s):
    """skill s when HP is under hp%, then every n turns"""
    return ('s', s, [f'hp_pr_under:{hp}', f'actbetween:{n}', 'turn_limited_act:1'])


# resist order (CEeqs63b): poison, weak, sick, injury, curse, paralysis - 100 = immune
BOSSES = {
    # Demonic Vindication. Wiki: shields on the first turn and at <80/60/40/20%; susceptible to Paralysis.
    'hellborn dilias': dict(
        skills=[8413004, 8413005, 8413006, 8413007, 8413008, 8413009],
        # 1 Doorway to Hell, 2 Soul Eater, 3 Hellfire Shield, 4 Hellmist, 5 Hellgrove, 6 Hellbolt
        ai=[first_turn(3), at(80, 4), at(60, 5), at(40, 6), at(20, 3),
            rand(1, 40), rand(2, 25), ATTACK],
        act=(2, 2), resist='100:100:100:100:100:0'),
    # Hellish Possession
    'sanguine hood ciara': dict(
        skills=[8413010, 8413011, 8413012, 8413013],
        # 1 Scythe Reave, 2 Savage Reap, 3 Hooded Defilement, 4 Feral Guard (50,000 HP Water shield)
        ai=[at(50, 4), rand(3, 30), rand(2, 35), rand(1, 30), ATTACK],
        act=(1, 2), resist='100:100:100:100:100:100'),
    'pumleficent semira': dict(
        skills=[8413014, 8413015, 8413016, 8413017],
        # 1 Clown Pound, 2 Callous Revilement, 3 Pumpkin Defilement, 4 Jester Guard (60,000 HP Water shield)
        ai=[at(50, 4), rand(3, 30), rand(2, 35), rand(1, 30), ATTACK],
        act=(1, 2), resist='100:100:100:100:100:100'),
    # An Unwalked Crossroads
    'abyssal witch ciara': dict(
        skills=[8401021, 8401022, 8401023, 8401024, 8401025, 8401026, 8401027],
        # 1 Scythe Reaver, 2 Savage Reap, 3 Hooded Defilement, 4 Feral Guard (reflect),
        # 5 Vicious Cleaver (<60%, then every 3 turns), 6 Esoteric Outburst (<50%, then every 2 turns),
        # 7 Phantasmagoric Reaping
        ai=[at(30, 4), at(50, 6), every_below(50, 2, 6), at(60, 5), every_below(60, 3, 5),
            rand(7, 25), rand(3, 25), rand(2, 30), rand(1, 30), ATTACK],
        act=(1, 2), resist='100:100:100:100:100:100'),
    'ruinous despoiler galtier': dict(
        skills=[8401028, 8401029, 8401030, 8401031, 8401032, 8401033, 8401034],
        # 1 Savage Slash, 2 Herald of Death, 3 Knightmare, 4 Dark Shroud (500,000 HP Water shield),
        # 5 Restless Soul, 6 Fright Knight, 7 Knight Terror. Wiki: Dark Shroud on the first turn and
        # at <80/50/30/10%; can be inflicted with Sickness.
        ai=[first_turn(4), at(80, 4), at(50, 4), at(30, 4), at(10, 4),
            ('s', 5, ['limited_act:1'], 15), rand(7, 20), rand(6, 25), rand(3, 25), rand(2, 30), rand(1, 30), ATTACK],
        act=(1, 2), resist='100:100:0:100:100:100'),
}


def skill_row(sid):
    r = dict(S[sid])
    name = (NAMES.get(sid) or '').strip()
    if not name or name.startswith('MST_'):
        name = str(r.get('0nxpBDz2') or '').strip()
    r['0nxpBDz2'] = name if name and not name.startswith('MST_') else 'Attack'
    r['qp37xTDh'] = 'None'
    return r


def build():
    out, aid = {}, 22000000
    for name, b in BOSSES.items():
        aid += 1
        skills = [str(s) for s in b['skills']]
        for s in skills:
            assert s in S, (name, s, 'skill not in client master')
            assert s in L, (name, s, 'no level row')
        rows = ai_from_rules(aid, f'Hellborn Nightmare - {name}', b['ai'])
        for r in rows:
            for c in r['q7Nit8JW'].split('#')[1].split('@'):
                if c.startswith('skill:'):
                    assert 1 <= int(c.split(':')[1]) <= len(skills), (name, c)
        out[name] = {'ai': str(aid), 'act': list(b['act']), 'resist': b['resist'],
                     'skills': '@'.join(f'{s}:1' for s in skills),
                     'skill_rows': [skill_row(s) for s in skills], 'ai_rows': rows}
    p = os.path.join(HERE, 'data', 'event_bosses.json')
    json.dump(out, open(p, 'w'), indent=1, ensure_ascii=False)
    print('wrote', p, ', '.join(out))


if __name__ == '__main__':
    build()
