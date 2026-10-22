#!/usr/bin/env python3
"""
Edit a player on the private server. Changes apply on the game's next
screen change (e.g. go to the home screen) - no restart needed.

Balances (each account keeps its own; summons/quests spend and earn from them):
  gems 50              give 50 gems          (gems -10 takes 10 away)
  zel 100000           give zel
  karma 100000         give karma
  friendpoints 500     give honor points
  summontickets 5      give summon tickets
  rainbowcoins 5       give rainbow coins
  set gems 100         set an exact balance

Fixed values (stay at what you set until you 'reset' them):
  level 500   energy 999   maxenergy 999   arenaorbs 3
  unitbox 4000   itembox 3200   cost 999
  reset level          stop fixing a value

Units:
  unit 850377          give yourself a unit by id (find ids with: find <name>)
  find jack            search units by name

Clean-up:
  clean                take everything that came from the recordings out of your save
                       (keeps your name, level, gems/zel, quest clears, squads and the units
                       you got on this server; a backup copy of the save is kept)

Sign-in accounts (changes apply right away, even while the server runs):
  accounts                     list accounts (username, player, locked?)
  password <username> <new>    set a new password (also unlocks the account)
  rename <username> <new>      change a username
  unlock <username>            clear a lockout after too many wrong passwords
  signout <username>           sign that account out of its device
  claim <username> <player>    attach an existing save to an account (player name or id;
                               close and reopen the game afterwards)

Other:
  show                 balances for the current player
  players              list players
  use <name>           switch to another player (name or id)
"""
import glob, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SAVES = os.path.join(HERE, 'saves')
WALLET = ['gems', 'zel', 'karma', 'friendpoints', 'summontickets', 'rainbowcoins']
FIXED = ['level', 'energy', 'maxenergy', 'arenaorbs', 'unitbox', 'itembox', 'cost']
ALIASES = {'gem': 'gems', 'honor': 'friendpoints', 'tickets': 'summontickets', 'ticket': 'summontickets',
           'orbs': 'arenaorbs', 'stamina': 'energy', 'lv': 'level', 'rainbow': 'rainbowcoins'}

ACC_FILE = os.path.join(SAVES, 'accounts.json')
ACC_OPS = os.path.join(SAVES, 'account_ops.json')

def account_queue(op, user, value=None):
    ops = []
    if os.path.exists(ACC_OPS):
        try:
            ops = json.load(open(ACC_OPS, encoding='utf-8'))
        except Exception:
            ops = []
    ops.append({'op': op, 'user': user, 'value': value})
    json.dump(ops, open(ACC_OPS, 'w', encoding='utf-8'))

def account_cmd(w, parts):
    import time
    users = {}
    try:
        users = json.load(open(ACC_FILE, encoding='utf-8')).get('users', {})
    except Exception:
        pass
    if w == 'accounts':
        if not users:
            print('  no accounts yet')
        for k, u in sorted(users.items()):
            pl = None
            if u.get('user_id'):
                try:
                    pl = json.load(open(os.path.join(SAVES, f"player_{u['user_id']}.json"), encoding='utf-8'))
                except Exception:
                    pass
            locked = ' LOCKED' if u.get('lock_until', 0) > time.time() else ''
            who = f"player {pl.get('handle')} (id {u['user_id']})" if pl else 'no player yet'
            print(f"  {u['name']:18} {who}{locked}")
        return
    if len(parts) < 2:
        print(__doc__); return
    name = parts[1].lower()
    if name not in users:
        print(f'  no account called {parts[1]}'); return
    if w == 'password' and len(parts) == 3:
        if len(parts[2]) < 6:
            print('  passwords need at least 6 characters'); return
        account_queue('password', name, parts[2]); print(f"  new password set for {users[name]['name']}")
    elif w == 'rename' and len(parts) == 3:
        import re
        if not re.match(r'^[A-Za-z0-9_]{3,16}$', parts[2]):
            print('  usernames are 3-16 letters, numbers or _'); return
        if parts[2].lower() in users:
            print('  that username is taken'); return
        account_queue('rename', name, parts[2]); print(f"  {users[name]['name']} renamed to {parts[2]}")
    elif w == 'unlock':
        account_queue('unlock', name); print(f"  {users[name]['name']} unlocked")
    elif w == 'claim' and len(parts) == 3:
        d = find(parts[2])
        if not d:
            return
        account_queue('claim', name, d['user_id'])
        print(f"  {users[name]['name']} will play {d.get('handle')} (id {d['user_id']}) - close and reopen the game")
    elif w == 'signout':
        account_queue('signout', name); print(f"  {users[name]['name']} will have to sign in again")
    else:
        print(__doc__)
    print('  (if the server is running it picks this up on the next sign-in or game start)')

def players():
    out = []
    for p in sorted(glob.glob(os.path.join(SAVES, 'player_*.json')), key=os.path.getmtime, reverse=True):
        try:
            out.append(json.load(open(p, encoding='utf-8')))
        except Exception:
            pass
    return out

def find(sel):
    pl = players()
    if not pl:
        sys.exit('No players yet - create an account in the game first.')
    if sel is None:
        return pl[0]
    for d in pl:
        if sel.lower() in (str(d.get('user_id')).lower(), str(d.get('handle')).lower()):
            return d
    print(f'  no player called {sel}')
    return None

def queue(d, op, field, value=0):
    path = os.path.join(SAVES, f"adjust_{d['user_id']}.json")
    ops = []
    if os.path.exists(path):
        try:
            ops = json.load(open(path))
        except Exception:
            ops = []
    ops.append({'op': op, 'field': field, 'value': int(value)})
    json.dump(ops, open(path, 'w'))

def pending(d):
    path = os.path.join(SAVES, f"adjust_{d['user_id']}.json")
    try:
        return json.load(open(path)) if os.path.exists(path) else []
    except Exception:
        return []

def show(d):
    w = dict(d.get('wallet') or {})
    ov = dict(d.get('overrides') or {})
    for op in pending(d):
        f, v = op['field'], op['value']
        if op['op'] in ('unit', 'clean'):
            continue
        if f in WALLET:
            w[f] = max(0, w.get(f, 0) + v) if op['op'] == 'add' else v
        elif op['op'] == 'reset':
            ov.pop(f, None)
        else:
            ov[f] = v
    print(f"  player: {d.get('handle')} (id {d.get('user_id')})")
    for f in WALLET:
        print(f'    {f:14} {w.get(f, 0)}')
    if ov:
        print('  fixed values:', ', '.join(f'{k}={v}' for k, v in ov.items()))
    if pending(d):
        print('  (changes reach the game on its next screen change)')

def run(cmd, cur):
    parts = cmd.split()
    if not parts:
        return cur
    w = ALIASES.get(parts[0].lower(), parts[0].lower())
    if w in ('accounts', 'password', 'rename', 'unlock', 'signout', 'claim'):
        account_cmd(w, parts)
        return cur
    if w == 'players':
        for d in players():
            print(f"  {str(d.get('handle')):20} id {d.get('user_id')}")
        return cur
    if w == 'use' and len(parts) > 1:
        d = find(parts[1])
        if d:
            print('  now editing', d.get('handle'))
            return d.get('user_id')
        return cur
    if w == 'find' and len(parts) > 1:
        q = ' '.join(parts[1:]).lower()
        um = json.load(open(os.path.join(HERE, 'data', 'unit_mst.json'), encoding='utf-8'))
        hits = [(i, u) for i, u in um.items() if q in str(u.get('utP1c0CD', '')).lower()]
        for i, u in sorted(hits, key=lambda x: (-int(x[1].get('7ofj5xa1') or 0), x[1]['utP1c0CD']))[:30]:
            print(f"  {i:>8}  {u.get('7ofj5xa1')}*  {u.get('utP1c0CD')}")
        if not hits:
            print('  no unit matches', q)
        return cur
    d = find(cur)
    if d is None:
        return cur
    if w == 'unit' and len(parts) == 2 and parts[1].isdigit():
        um = json.load(open(os.path.join(HERE, 'data', 'unit_mst.json'), encoding='utf-8'))
        if parts[1] not in um:
            print('  no unit with id', parts[1]); return cur
        queue(d, 'unit', 'unit', parts[1])
        print(f"  giving {um[parts[1]].get('utP1c0CD')} ({um[parts[1]].get('7ofj5xa1')}*) - appears on the next screen change")
        return cur
    if w == 'clean':
        queue(d, 'clean', 'clean', 0)
        print('  clean-up queued - it happens on the next screen change in the game')
        print('  (a backup is saved as saves\\player_%s.before-clean.json)' % d.get('user_id'))
        return cur
    if w == 'show':
        show(d); return cur
    if w == 'set' and len(parts) == 3:
        f = ALIASES.get(parts[1].lower(), parts[1].lower())
        if f in WALLET + FIXED and parts[2].lstrip('-').isdigit():
            queue(d, 'set', f, parts[2]); print(f'  {f} set to {parts[2]}'); show(d); return cur
    if w == 'reset' and len(parts) == 2:
        f = ALIASES.get(parts[1].lower(), parts[1].lower())
        queue(d, 'reset', f); print(f'  {f} back to normal'); return cur
    if len(parts) == 2 and parts[1].lstrip('-').isdigit():
        if w in WALLET:
            queue(d, 'add', w, parts[1])
            print(f"  {'+' if not parts[1].startswith('-') else ''}{parts[1]} {w}")
            show(d); return cur
        if w in FIXED:
            queue(d, 'set', w, parts[1]); print(f'  {w} fixed at {parts[1]}'); return cur
    print(__doc__)
    return cur

def main():
    if len(sys.argv) > 1:
        run(' '.join(sys.argv[1:]), None)
        return
    print(__doc__)
    d = find(None)
    print(f"Editing: {d.get('handle')}   (Enter on an empty line to quit)")
    show(d)
    cur = d.get('user_id')
    while True:
        try:
            cmd = input('> ').strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not cmd:
            break
        cur = run(cmd, cur)

if __name__ == '__main__':
    main()
