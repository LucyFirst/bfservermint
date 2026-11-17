"""
Which Missions (Administration Office) rewards a player has waiting - what makes the red badge.
    cd /opt/bfserver && python3 tools/badges.py <name or user id>
"""
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)
import server as S  # noqa: E402

want = ' '.join(sys.argv[1:]).strip().lower()
for f in glob.glob(os.path.join(S.SAVE_DIR, 'player_*.json')):
    pl = json.load(open(f, encoding='utf-8'))
    if want not in (str(pl.get('handle', '')).lower(), str(pl.get('user_id', '')).lower()):
        continue
    A = S.achievement_engine()
    print(pl.get('handle'), pl.get('user_id'), 'badge:', A.badges(pl) or '(none)')
    for r in A.master:
        cat = str(r.get('KT71m8Ae'))
        if cat in ('2', '3', '5'):
            continue
        row = A.row(pl, r)
        if row['rPk8gtY5'] == '1':
            print(f"  category {cat} section {r.get('3rhygS9K')} id {r.get('M7SXoc31')}:",
                  str(r.get('xXdp1q0Y', '')).replace('<br>', ' '), '|', row)
    for aid, m in A.daily(pl)['missions'].items():
        if m.get('done') and not m.get('taken'):
            print('  daily mission', aid, (A.by_id.get(aid) or {}).get('xXdp1q0Y'))
    break
else:
    print('no player named', want)
