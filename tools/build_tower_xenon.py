"""
Xenon in the Unholy Tower (data/battles.json "roamer" of quests 8301001-8310001) and the AI of his
Sir Sancus form (data/custom_ai.json), following the wiki's tower pages.

  Xenon Lv1-4, Royal Guard Xenon Lv5-7: the recorded rows (moves and AI of the real game: every 3 turns
    Soul Slash / Ardent Soul Slash or Soul Execution); Lv3 and Lv6 copy the level below
  Sir Sancus Xenon Lv8-9 (wiki, Floors 71-80): Ardent Soul Slash more often under 70% HP, Soul Desecration
    at 50% and 30%, Soul Smite (40-45% HP to all) at 10%
  Sir Sancus Xenon Lv10 (wiki, Floors 91-100): Soul Smite when under 50% (and again under 20%), then the
    next turn 3 Soul Desecrations in a row; Ardent Soul Slash now and then
  Floors 21-30: Xenon Lv3 never in the last wave (wiki)
  every form: immune to critical hits (wiki), with the game's own crit-negating passive

Run:  python3 tools/build_tower_xenon.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, 'tools'))
from build_custom_bosses import ai_row      # noqa: E402

ARDENT, DESECRATION, SMITE = '8550021', '8550023', '8550024'
SANCUS_SKILLS = f'{ARDENT}:1@{DESECRATION}:1@{SMITE}:1'
AI_LV8, AI_LV10 = '845000210', '845000220'
STATS = {9: (2000000, 2500, 1200), 10: (3200000, 3000, 1500)}      # level: HP, ATK, DEF
ROAMING_LV10 = (160000, 1600, 1000)       # Floors 91-100: Xenon Lv10 met before the last wave (original numbers)
# a monster's passive (hjAy9St3 = process id, 2Smu5Mtq = its numbers, read by MonsterMst like a leader
# skill's): process 53 = damage resistances; these numbers are the game's own "negates critical hits"
# leader skill (id 1200: 100,100 crit, 100,100 crit damage)
NO_CRIT = ('53', '100,100,0,0,100,100,0,0,0,0')
USE_NO_CRIT = True


def quest(i):
    return '8310001' if i == 10 else f'830{i}001'


def recorded():
    rec = {}
    for i in range(1, 11):
        r = json.load(open(os.path.join(HERE, 'quest_recordings', f'mission_{quest(i)}.json')))['response']
        for m in r['U0v5IeJo']:
            if 'Xenon' in m['3evIn0zZ']:
                rec[i] = m
    return rec


def ai():
    lv8 = [('skill', ['skill:3', 'hp_pr_under:10', 'limited_act:1'], 100),
           ('skill', ['skill:2', 'hp_pr_under:30', 'limited_act:1'], 100),
           ('skill', ['skill:2', 'hp_pr_under:50', 'limited_act:1'], 100),
           ('skill', ['skill:1', 'hp_pr_under:70'], 45),
           ('skill', ['skill:1'], 15),
           ('attack', [], 50, (), 'weakness'),
           ('attack', [], 100)]
    lv10 = [('skill', ['skill:3', 'hp_pr_under:50', 'limited_act:1'], 100, (1, 1, 16, 1)),
            ('skill', ['skill:3', 'hp_pr_under:20', 'limited_act:1', 'flg_off:16'], 100, (1, 1, 16, 1)),
            ('skill', ['skill:2', 'flg_on:1', 'flg_off:16'], 100, (1, 0, 17, 1)),     # the turn after: 3 in a row
            ('skill', ['skill:2', 'flg_on:17'], 100),
            ('skill', ['skill:1', 'hp_pr_under:50'], 35),
            ('skill', ['skill:1'], 20),
            ('attack', [], 50, (), 'weakness'),
            ('attack', [], 100)]
    out = {'_how': 'built by tools/build_tower_xenon.py (Sir Sancus Xenon in the Unholy Tower)'}
    for aid, name, rows in ((AI_LV8, 'Sir Sancus Xenon Lv8-9 (wiki)', lv8), (AI_LV10, 'Sir Sancus Xenon Lv10 (wiki)', lv10)):
        out[aid] = [ai_row(aid, name, n, r[0], r[1], r[2], r[4] if len(r) > 4 else 'random', r[3] if len(r) > 3 else ())
                    for n, r in enumerate(rows, 1)]
    return out


def main():
    rec = recorded()
    res = lambda a, b: ':'.join(str((int(x) + int(y)) // 2) for x, y in zip(a.split(':'), b.split(':')))
    p = os.path.join(HERE, 'data', 'battles.json')
    B = json.load(open(p, encoding='utf-8'))
    for i in range(1, 11):
        q = B[quest(i)]
        mob = q['roamer']['mobs'][0]
        if i in (8, 9, 10):
            row = dict(rec[9 if i == 8 else i], F4bQ7r8C=SANCUS_SKILLS, i74vGUFa=AI_LV10 if i == 10 else AI_LV8)
            if i == 8:
                row['CEeqs63b'] = res(rec[7]['CEeqs63b'], rec[9]['CEeqs63b'])
            mob.update(u='860015', name=f'Sir Sancus Xenon Lv{i}', drop={'u': '860013', 'pct': mob['drop']['pct'], 'lv': 1})
        elif i in rec:
            row = dict(rec[i])
        else:                                   # Lv3 / Lv6: the level below, resistances in between
            row = dict(rec[i - 1], CEeqs63b=res(rec[i - 1]['CEeqs63b'], rec[i + 1]['CEeqs63b']))
        row['3evIn0zZ'] = mob['name']
        # immune to critical hits (wiki); USE_NO_CRIT = False takes it off again
        if USE_NO_CRIT:
            row.update(hjAy9St3=NO_CRIT[0], **{'2Smu5Mtq': NO_CRIT[1]})
        else:
            row.pop('hjAy9St3', None)
            row.pop('2Smu5Mtq', None)
        mob['row'], mob['ai'] = row, row['i74vGUFa']
        if i in STATS:                          # owner's numbers (Lv9 / Lv10 are much tougher than the original)
            mob.update(hp=STATS[i][0], atk=STATS[i][1], **{'def': STATS[i][2]})
            row.update(e7DK0FQT=str(STATS[i][0]), **{'67CApcti': str(STATS[i][1]), 'q08xLEsy': str(STATS[i][2])})
        q['roamer']['no_last'] = (i == 3)
        q['roamer']['max'] = 2
        if i == 10:          # Floors 91-100: the last wave is the full boss; the one roaming before it is weaker
            boss = json.loads(json.dumps(mob))
            q['roamer']['last_mobs'] = [boss]
            w = ROAMING_LV10
            mob.update(hp=w[0], atk=w[1], **{'def': w[2]})
            mob['row'] = dict(mob['row'], e7DK0FQT=str(w[0]), **{'67CApcti': str(w[1]), 'q08xLEsy': str(w[2])})
        q['roamer']['also'] = ['860013', '860014', '860015']   # no other Xenon from the recorded line-ups
        print(quest(i), mob['name'], row['pn16CNah'], row['F4bQ7r8C'], row['i74vGUFa'])
    json.dump(B, open(p, 'w', encoding='utf-8'), ensure_ascii=False)
    json.dump(ai(), open(os.path.join(HERE, 'data', 'custom_ai.json'), 'w', encoding='utf-8'), indent=1)


if __name__ == '__main__':
    main()
