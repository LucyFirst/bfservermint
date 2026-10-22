"""
Compare quests fetched from bfrevive (quest_recordings/mission_*.json) with what our server
would build for the same quest WITHOUT that recording - so you can see whether bfrevive's
version is better before relying on it.

    python3 tools/compare_bfrevive.py [folder] [report.html]

folder:  where the fetched mission_*.json files are (default: quest_recordings)
report:  an HTML table is written here (default: bfrevive_compare.html)

For every quest it shows, side by side:
  ours     - where our version comes from (its own data / borrowed from another quest /
             wiki only / not playable) and the boss we would put in
  bfrevive - the boss and enemies they sent, their stats and how many boss skills
  wiki     - the boss the wiki lists for that quest (an independent check on bfrevive)
and a verdict:
  NEW          we had no playable fight for it at all (e.g. trials without data)
  BETTER       ours was a stand-in (borrowed, wiki-only or guessed) and bfrevive has the real one
  SAME BOSS    both have the same boss; bfrevive adds its real stats/skills
  CHECK        bfrevive and the wiki disagree on the boss - look before trusting it
  STAND-IN?    bfrevive uses the same boss in 3+ unrelated dungeons and the wiki doesn't confirm
               it - probably their own placeholder boss (real stats, wrong boss)
  PLACEHOLDER  bfrevive itself only has filler enemies (keep ours)
"""
import html
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)
folder = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(HERE, 'quest_recordings')
out_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, 'bfrevive_compare.html')

fetched = {}
for f in sorted(os.listdir(folder)):
    if f.startswith('mission_') and f.endswith('.json'):
        try:
            d = json.load(open(os.path.join(folder, f), encoding='utf-8'))
            fetched[str(d['mission'])] = d
        except Exception as e:
            print('skip', f, e)
print(len(fetched), 'fetched quests in', folder)

import server as s                                   # loads the game data (takes a little while)
import battlegen

# our generator as it would be WITHOUT these fetched quests
recorded = {k: v for k, v in s._RECORDED.items() if k not in fetched}
gen = battlegen.Battlegen(recorded, s.BATTLES, s.BATTLES, s.MISSIONS, s.unit_mst(), None)
U = s.unit_mst()

def uname(u):
    return (U.get(str(u)) or {}).get('utP1c0CD') or str(u)

def fmt(n):
    n = int(n or 0)
    return f'{n / 1e6:.1f}M' if n >= 1e6 else f'{n / 1e3:.0f}k' if n >= 1e4 else str(n)

import re
def family(name):
    """'[Hard] Foo Lv. 3' / 'Foo - Battle 2' / 'Floor 11-20' -> one family name"""
    name = re.sub(r'\[.*?\]|Lv\.?\s*\d+|Battle\s*\d+|Sector\s*\d+|Floor\s*[\d-]+|最終層|[\d\s.:\-]+', ' ', str(name))
    return ' '.join(name.split()).lower()

# bosses bfrevive puts in many unrelated quests (their stand-ins)
boss_dungeons = {}
for mid, rec in fetched.items():
    r = gen._from_recording(rec['response'])
    if r and r['battles'] and r['battles'][-1]:
        key = r['battles'][-1][0]['u']
        boss_dungeons.setdefault(key, set()).add(family(rec.get('name') or (s.MISSIONS.get(mid) or {}).get('name', mid)))

rows, counts = [], {}
for mid in sorted(fetched, key=int):
    rec = fetched[mid]
    name = rec.get('name') or (s.MISSIONS.get(mid) or {}).get('name', mid)
    theirs = gen._from_recording(rec['response'])
    wiki = s.BATTLES.get(mid) or {}
    wiki_boss = [uname(b['u']) for b in wiki.get('bosses') or []]
    # bfrevive
    t_boss = theirs['battles'][-1] if theirs and theirs['battles'] else []
    t_all = [m for b in (theirs or {}).get('battles', []) for m in b]
    t_filler = sum(1 for m in t_all if m['filler'])
    t_names = [m['name'] or uname(m['u']) for m in t_boss]
    t_stats = ', '.join(f"{m['name']} {fmt(m['hp'])} HP / {fmt(m['atk'])} ATK / {fmt(m['def'])} DEF" for m in t_boss[:3])
    t_skills = len(theirs['skills']) if theirs else 0
    # ours
    if s.is_trial(mid) and not s.TRIAL_SPECS.get(mid) and not gen.refs.get(mid):
        ours_src, o_names, o_skills = 'not playable (trial closed - no data)', [], 0
    else:
        try:
            plan = gen.plan(mid, random.Random(1))
            label = plan.get('label', '')
            o_boss = plan['battles'][-1]['mons'] if plan['battles'] else []
            o_names = [m.get('name') or uname(m['u']) for m in o_boss]
            o_skills = len(plan.get('skills') or [])
            if gen.refs.get(mid):
                ours_src = 'its own data (' + ', '.join(sorted({r['src'] for r in gen.refs[mid]})) + ')'
            elif ' refs ' in label:
                ours_src = 'borrowed from quest ' + label.split(' refs ')[1]
            else:
                ours_src = 'made up from the wiki (' + (wiki.get('src') or 'no wiki') + ')'
        except Exception as e:
            ours_src, o_names, o_skills = f'error building it ({e})', [], 0
    # verdict
    real = t_filler <= len(t_all) // 2 and t_all
    if not real:
        verdict = 'PLACEHOLDER'
    elif wiki_boss and set(wiki_boss) & set(t_names):
        verdict = 'NEW' if ours_src.startswith(('not playable', 'error')) else \
                  'SAME BOSS' if ours_src.startswith('its own data') and set(o_names) & set(t_names) else 'BETTER'
    elif t_boss and len(boss_dungeons.get(t_boss[0]['u'], ())) >= 3:
        verdict = 'STAND-IN?'
    elif wiki_boss:
        verdict = 'CHECK'
    elif ours_src.startswith(('not playable', 'error')):
        verdict = 'NEW'
    elif not ours_src.startswith('its own data') or not set(o_names) & set(t_names):
        verdict = 'BETTER'
    else:
        verdict = 'SAME BOSS'
    counts[verdict] = counts.get(verdict, 0) + 1
    rows.append({'mid': mid, 'name': name, 'verdict': verdict, 'ours_src': ours_src,
                 'ours_boss': ', '.join(o_names) or '-', 'ours_skills': o_skills,
                 'their_boss': ', '.join(t_names) or '-', 'their_stats': t_stats, 'their_skills': t_skills,
                 'their_waves': len((theirs or {}).get('battles', [])), 'wiki_boss': ', '.join(wiki_boss) or '-'})

COL = {'NEW': '#2e7d32', 'BETTER': '#1565c0', 'SAME BOSS': '#6d6d6d', 'CHECK': '#c62828',
       'STAND-IN?': '#8e24aa', 'PLACEHOLDER': '#ef6c00'}
print()
for k in COL:
    print(f'{k:12} {counts.get(k, 0)}')
print()
for r in rows:
    if r['verdict'] in ('CHECK', 'STAND-IN?'):
        print(f"{r['verdict']:12} {r['mid']} {r['name']}: bfrevive boss {r['their_boss']} | wiki {r['wiki_boss']}")

e = html.escape
body = ''.join(
    f"<tr><td>{r['mid']}</td><td>{e(r['name'])}</td>"
    f"<td><b style='color:{COL[r['verdict']]}'>{r['verdict']}</b></td>"
    f"<td>{e(r['ours_src'])}<br><small>boss: {e(r['ours_boss'])} · {r['ours_skills']} skills</small></td>"
    f"<td>{e(r['their_boss'])}<br><small>{e(r['their_stats'])} · {r['their_skills']} skills · {r['their_waves']} waves</small></td>"
    f"<td>{e(r['wiki_boss'])}</td></tr>" for r in rows)
summary = ' · '.join(f"<b style='color:{COL[k]}'>{k}</b> {counts.get(k, 0)}" for k in COL)
open(out_path, 'w', encoding='utf-8').write(f"""<!doctype html><meta charset=utf-8><title>bfrevive vs ours</title>
<style>body{{font:14px system-ui;margin:16px;background:#fff;color:#111}}table{{border-collapse:collapse;width:100%}}
td,th{{border-bottom:1px solid #ddd;padding:6px;vertical-align:top;text-align:left}}small{{color:#666}}</style>
<h2>bfrevive quests vs ours ({len(rows)})</h2><p>{summary}</p>
<table><tr><th>id</th><th>quest</th><th>verdict</th><th>ours (without bfrevive)</th><th>bfrevive</th><th>wiki boss</th></tr>{body}</table>""")
print('report:', out_path)
