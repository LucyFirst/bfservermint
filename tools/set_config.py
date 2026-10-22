"""Set one setting in config.json:   python3 /opt/bfserver/tools/set_config.py <name> <value>
Then restart the server (systemctl restart bfserver)."""
import json
import os
import sys

p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.json')
if len(sys.argv) != 3:
    sys.exit(__doc__)
c = json.load(open(p))
v = sys.argv[2]
try:
    v = json.loads(v) if v in ('true', 'false') or v.lstrip('-').isdigit() else v
except ValueError:
    pass
c[sys.argv[1]] = v
json.dump(c, open(p, 'w'), indent=2)
print('set', sys.argv[1])
