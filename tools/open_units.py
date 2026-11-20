"""
Units switched off on the admin page (everything else is open) - for the tier list.
    cd /opt/bfserver && python3 tools/open_units.py
Prints JSON: {"off": [unit ids], "open_count": n}
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)
import server as S  # noqa: E402

off = sorted(set(map(str, S.admin_settings()['off']['units'])))
um = S.unit_mst()
opn = [u for u in um if u not in off and str(um[u].get('xP9oi5Z2', '0')) == '0']
print(json.dumps({'off': off, 'open_count': len(opn)}, separators=(',', ':')))
