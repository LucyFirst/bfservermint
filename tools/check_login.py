"""
Check what the server sends a player at login - without changing anything.
On the Droplet:   python3 /opt/bfserver/tools/check_login.py eSqQ1Gdd
"""
import json
import os
import platform
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)
uid = sys.argv[1] if len(sys.argv) > 1 else 'eSqQ1Gdd'

print('python', platform.python_version(), '| time zone', time.strftime('%Z %z'))
p = os.path.join(HERE, 'saves', f'player_{uid}.json')
if not os.path.exists(p):
    sys.exit(f'NO SAVE FILE: {p}')
raw = json.load(open(p, encoding='utf-8'))
print('save file', os.path.getsize(p), 'bytes, changed', time.ctime(os.path.getmtime(p)))
print('units in save:', len(raw.get('units') or {}))
deck = (raw.get('state') or {}).get('dX7S2Lc1')
print('squad rows in save:', len(deck) if isinstance(deck, list) else repr(deck)[:80])

import server as s                                   # loads the game data (takes a little while)
s.save_player = lambda pl: None                      # look only - never write
s.save_accounts = lambda *a, **k: None
pl = s.load_player(uid)
out = s.full_user_info(pl)
out = s.patch_summons(out)
s.add_display_name(out)
d = out.get('dX7S2Lc1')
have = set(pl['units'])
print('login reply: squad rows', len(d) if isinstance(d, list) else repr(d)[:80],
      '| units', len(out.get(s.G_UNITS) or []),
      '| squad units missing from the box', sum(1 for r in (d or []) if r.get(s.F_UNIT_ID) not in have))
body = s.enc_body(s.RID['GetUserInfoRequest'], out)
print('reply size', len(body), 'bytes; decodes again:', bool(s.dec_body(s.RID['GetUserInfoRequest'], body)))
print('OK' if d else 'PROBLEM: no squad in the login reply')
