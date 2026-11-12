"""Grand Gaia Chronicles: put the enemy line-ups read from Brave Frontier: Reborn (rebornbf.com) into
data/battles.json for the quests we only had placeholders for. Input: one JSON object per line,
{m: mission id, n: name, bw: boss wave (0-based), w: [[[unit, name, element, hp, atk, def, rec, [act min, max],
zel, karma, chest], ...], ...]}. These play exactly as listed (src "datamine"), like the Vargas/Lance/Eze quests."""
import json, sys, os
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EL = {'fire': 1, 'water': 2, 'earth': 3, 'thunder': 4, 'light': 5, 'dark': 6}
B = json.load(open(os.path.join(HERE, 'data', 'battles.json'), encoding='utf-8'))
M = json.load(open(os.path.join(HERE, 'data', 'mission_mst.json'), encoding='utf-8'))
known_ai = {}
for v in B.values():
    for w in v.get('battles') or []:
        for e in w:
            if e.get('ai'): known_ai.setdefault(e['u'], e['ai'])
for line in open(sys.argv[1], encoding='utf-8'):
    r = json.loads(line); mid = str(r['m'])
    waves = [[{'u': str(e[0]), 'name': e[1], 'hp': e[3], 'atk': e[4], 'def': e[5], 'el': EL[e[2]],
               'ai': known_ai.get(str(e[0]), '1'), 'act': e[7] or [1, 1], 'zel': e[8], 'karma': e[9],
               'drop': None, 'items': []} for e in w] for w in r['w']]
    spec = B.setdefault(mid, {'name': r['n']})
    spec.update({'n': len(waves), 'src': 'datamine', 'from': 'rebornbf', 'battles': waves,
                 'bosses': [{'u': e['u'], 'hp': e['hp']} for e in waves[-1]]})
    if mid in M and M[mid].get('battles') != str(len(waves)):
        print(mid, 'battles', M[mid].get('battles'), '->', len(waves)); M[mid]['battles'] = str(len(waves))
    print(mid, r['n'], [len(w) for w in waves])
json.dump(B, open(os.path.join(HERE, 'data', 'battles.json'), 'w', encoding='utf-8'), ensure_ascii=False)
json.dump(M, open(os.path.join(HERE, 'data', 'mission_mst.json'), 'w', encoding='utf-8'), ensure_ascii=False)
