#!/usr/bin/env python3
"""
Builds data/story_drops.json: what each story quest's enemies leave in treasure chests.

Source: the curated quest data of joppe2001/bf-private-server-data (archive/mission.json; free
to use, per its author). Their per-enemy chest tables come from the 2014 captures of the original
game (the same captures as our "datamine" quests - identical numbers), carried to every other quest
enemy by enemy (an enemy drops what it dropped in the closest captured quest), plus the wiki's item
list for the quest at the captures' typical rate. See their server/scripts/gen_material_drops.py.

Per quest:
  units  {unit id: [chest %, [[type, target, amount, weight], ...]]}   ordinary enemies
  boss   {unit id: [...]}                                              the last battle's enemies
  chests expected chests per run in their quest (to keep our generated runs at the same rate)
Types: 1 zel, 2 karma, 4 item.

Usage:  python tools/build_story_drops.py <path to bf-private-server-data/archive/mission.json>
"""
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def table(mo):
    ds = [[int(d['target_type']), str(d.get('target_id') or ''), int(d.get('amount') or 1), int(d.get('weight') or 0)]
          for d in mo.get('treasure_drops') or [] if int(d.get('weight') or 0) > 0 and int(d['target_type']) in (1, 2, 4)]
    return [int(mo.get('treasure_chest_chance') or 0), ds] if ds and mo.get('treasure_chest_chance') else None


def main():
    src = sys.argv[1]
    missions = json.load(open(os.path.join(HERE, 'data', 'mission_mst.json'), encoding='utf-8'))
    out = {}
    for rec in json.load(open(src, encoding='utf-8')):
        mid = str(rec['id'])
        if mid not in missions or int(mid) >= 100000 or rec.get('script_monsters'):
            continue
        stages = rec.get('stages') or []
        units, boss = collections.defaultdict(collections.Counter), collections.defaultdict(collections.Counter)
        chests = 0.0
        for si, st in enumerate(stages):
            last = si == len(stages) - 1 or st.get('is_boss')
            for mo in st.get('battle_monsters') or []:
                t = table(mo)
                if not t:
                    continue
                chests += t[0] / 100
                (boss if last else units)[str(mo.get('unit_id'))][json.dumps(t)] += 1
        pick = lambda d: {u: json.loads(c.most_common(1)[0][0]) for u, c in d.items()}
        if units or boss:
            out[mid] = {'units': pick(units), 'boss': pick(boss), 'chests': round(chests, 3)}
    path = os.path.join(HERE, 'data', 'story_drops.json')
    json.dump(out, open(path, 'w', encoding='utf-8'), separators=(',', ':'), sort_keys=True)
    print(f'{len(out)} story quests -> {path} ({os.path.getsize(path) // 1024} KB)')


if __name__ == '__main__':
    main()
