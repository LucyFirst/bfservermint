"""
Frontier Hunter results of one season run (for posting): overall Survey points, each survey's best
scores and the MVP rankings. Testers are left out, like in the game.
    cd /opt/bfserver && python3 tools/fh_results.py          the last season that ended
    cd /opt/bfserver && python3 tools/fh_results.py 2        season run 2 (the 2nd season this server ran)
"""
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)
import server as S  # noqa: E402

F = S.fh_engine()
now_n = F.run_at()[0]
n = int(sys.argv[1]) - 1 if len(sys.argv) > 1 else now_n - 1
run = F.run(n)
st = F.start() + n * F.period()
plain = lambda s: re.sub(r'<[^>]+>', '', str(s or ''))
users = run['users']
out = {'run': n + 1, 'season_id': run['season'], 'from': time.strftime('%Y-%m-%d', time.gmtime(st)),
       'to': time.strftime('%Y-%m-%d', time.gmtime(st + F.period())), 'ended': n < now_n}
row = lambda uid, v: {'name': plain(users[uid].get('name')), 'value': v, 'lv': users[uid].get('lv'),
                      'hr': users[uid].get('hr'), 'lead': users[uid].get('unit'),
                      'lead_name': (S.unit_mst().get(str(users[uid].get('unit'))) or {}).get('utP1c0CD')}
out['overall'] = [row(u, v) for u, v in F.ranked(n)[:10]]
out['rankings'] = {}
for r in sorted(F.mvp.get(run['season'], []), key=lambda r: int(r['9w5e3ZJB'])):
    kind, mid = r['9w5e3ZJB'], r.get('j28VNcUW')
    rk = F.ranked(n, kind, mid if mid and mid != '0' else None)[:5]
    out['rankings'][plain(r.get('h6V4weL2') or kind)] = [row(u, v) for u, v in rk]
out['players'] = len([u for u in users if not S.is_tester({'user_id': u})])
print(json.dumps(out, ensure_ascii=False, indent=1))
