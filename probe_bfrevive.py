#!/usr/bin/env python3
"""
Ask bfrevive's real server for the battle setup of a few quests, using YOUR
bfrevive account (the emulator ID you recorded with), and save each answer in
quest_recordings\\ - the private server then plays those quests exactly.

Starting a quest on bfrevive costs that quest's energy on your bfrevive
account. The quest is left unfinished there (the game may offer to resume it
next time you open the real bfrevive app - just give up/retreat).

Usage:  python probe_bfrevive.py              (next 60 quests not fetched yet)
        python probe_bfrevive.py 22 23 504    (these quest ids)
        python probe_bfrevive.py vortex       (Vortex quests bfrevive has open right now)
        python probe_bfrevive.py vortex 150   (the same, up to 150 quests this run)
        add "again" to fetch quests already fetched once more (bfrevive improves its battles)

When energy runs out it uses the free energy refill; if a refill ever costs
gems it stops instead.
"""
import base64, gzip, json, os, random, ssl, sys, time, urllib.parse, urllib.request
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

HERE = os.path.dirname(os.path.abspath(__file__))
API = 'https://api.bfrevive.net'
OUT = os.path.join(HERE, 'quest_recordings')
LIMIT = 60          # quests per run (run again to continue - already fetched ones are skipped)
WAIT = 5            # seconds between requests - keep it gentle
# the bfrevive account you recorded with (from your recordings)
LOGIN_QUERY = ('dn=PGEM10&dp=Android&vid=0a813b2f-0219-463d-bd88-404cd5b222b5&v=14&ak=0839899613932562'
               '&device_id=&altvid=ecc67371-3012-430d-b7b7-b978aacfa727'
               '&identifiers=%7B%22android_id%22%3A%22324fe03d3d3b4ff0%22%7D&daid=0a813b2f-0219-463d-bd88-404cd5b222b5')

T = json.loads(gzip.open(os.path.join(HERE, 'data', 'templates.json.gz')).read())
REQS = T['requests']
RID = {v['cls']: k for k, v in REQS.items()}
MISSIONS = json.load(open(os.path.join(HERE, 'data', 'mission_mst.json'), encoding='utf-8'))
CTX = ssl.create_default_context()
CTX.load_verify_locations(os.path.join(HERE, 'certs', 'bfrevive_ca.crt'))

def key(rid):
    return REQS[rid]['key'].encode().ljust(16, b'\0')[:16]

def enc(rid, obj):
    raw = json.dumps(obj, separators=(',', ':'), ensure_ascii=False).encode()
    n = 16 - len(raw) % 16
    c = Cipher(algorithms.AES(key(rid)), modes.ECB()).encryptor()
    return base64.b64encode(c.update(raw + bytes([n]) * n) + c.finalize()).decode()

def dec(rid, b64):
    c = Cipher(algorithms.AES(key(rid)), modes.ECB()).decryptor()
    p = c.update(base64.b64decode(b64)) + c.finalize()
    return json.loads(p[:-p[-1]])

def http(path, body=None, headers=None):
    req = urllib.request.Request(API + path, data=body, headers=headers or {}, method='POST' if body is not None else 'GET')
    with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
        data = r.read()
        if r.headers.get('Content-Encoding') == 'gzip':
            data = gzip.decompress(data)
        return data

def action(cls, obj):
    rid = RID[cls]
    body = json.dumps({'F4q6i9xe': {'aV6cLn3v': str(random.randint(10000, 99999)), 'Hhgi79M1': rid},
                       'a3vSYuq2': {'Kn51uR4Y': enc(rid, obj)}}, separators=(',', ':')).encode()
    data = http('/bf/gme/action.php', body, {'Accept': '*/*', 'Accept-Encoding': 'gzip', 'Accept-Language': 'en',
                                            'User-Agent': 'android', 'Content-Type': 'application/x-www-form-urlencoded'})
    env = json.loads(data)
    payload = (env.get('a3vSYuq2') or {}).get('Kn51uR4Y')
    return dec(rid, payload) if payload else {}

def template(cls):
    return json.loads(json.dumps([a for a in T['action'] if a['cls'] == cls][-1]['req']))

def vortex_dungeons():
    """{dungeon id} of the Vortex (land 99) - from data/dungeon_area.json."""
    try:
        da = json.load(open(os.path.join(HERE, 'data', 'dungeon_area.json'), encoding='utf-8'))['dungeon']
    except Exception:
        return set()
    return {d for d, (area, land) in da.items() if str(land) == '99'}


def main():
    global LIMIT
    words = [a.lower() for a in sys.argv[1:]]
    vortex, again = 'vortex' in words, 'again' in words
    args = [a for a in sys.argv[1:] if a.isdigit()]
    if vortex and args:
        LIMIT, args = int(args[0]), []
    explicit = bool(args)
    ids = args
    os.makedirs(OUT, exist_ok=True)
    print('logging in to bfrevive as your recorded account...')
    lg = json.loads(http('/accounts/guest/login/?' + LOGIN_QUERY, None,
                         {'Accept-Encoding': 'gzip', 'Content-Type': 'application/json; charset=utf-8',
                          'Accept': 'application/json; charset=utf-8', 'GCLIENTID': 'Android_sg.gumi.bravefrontier'}))
    if lg.get('status') != 'successful':
        sys.exit(f'login refused: {lg}')
    time.sleep(WAIT)
    req = template('GetUserInfoRequest')
    ui = req['IKqx1Cn9'][0]
    ui['iN7buP1i'], ui['iN7buP2h'] = lg['token'], lg['game_user_id']
    info = action('GetUserInfoRequest', req)
    if 'o5Tt0NV7' in info:
        sys.exit(f"bfrevive said: {info['o5Tt0NV7']}")
    me = info['IKqx1Cn9'][0]
    team = info['fEi17cnx'][0]
    signal = info.get('6FrKacq7') or req['6FrKacq7']
    ap = int(team.get('0P9X1YHs') or 0)
    print(f"logged in as {me.get('B5JQyV8j')} ({me.get('h7eY3sAK')}), energy {ap}/{team.get('YnM14RIP')}")
    ui['h7eY3sAK'] = me.get('h7eY3sAK', ui['h7eY3sAK'])
    cleared = {r.get('j28VNcUW') for r in info.get('UT1SVg59') or [] if isinstance(r, dict)}
    json.dump({'cleared': sorted(cleared, key=lambda x: int(x) if str(x).isdigit() else 0),
               'energy': ap, 'got': time.strftime('%Y-%m-%d %H:%M:%S')},
              open(os.path.join(OUT, '_account.json'), 'w'))
    print(f'your bfrevive account has cleared {len(cleared)} quests')
    battles = {}
    try:
        battles = json.load(open(os.path.join(HERE, 'data', 'battles.json'), encoding='utf-8'))
    except Exception:
        pass
    gems = int(team.get('03UGMHxF') or 0)
    maxap = int(team.get('YnM14RIP') or 0)
    have = {f[8:-5] for f in os.listdir(OUT) if f.startswith('mission_') and f.endswith('.json')}
    for f in os.listdir(OUT):
        if f.startswith('_refused_'):
            try:   # locked quests: try again once you've cleared more quests on bfrevive
                if json.load(open(os.path.join(OUT, f))).get('cleared_then', -1) >= len(cleared):
                    have.add(f[9:-5])
            except Exception:
                pass
    have |= {str(((a['req'].get('9Q1Lq5FS') or [{}])[0]).get('j28VNcUW')) for a in T['action'] if a['cls'] == 'MissionStartRequest'}
    if again:
        have = set()
    if vortex and not explicit:
        vd = vortex_dungeons()
        open_now = {str(r.get('MHx05sXt')) for r in info.get('Y73tHKS8') or [] if isinstance(r, dict) and r.get('MHx05sXt')}
        pool = [k for k in sorted(MISSIONS, key=int) if str(MISSIONS[k].get('dungeon')) in vd and k not in have]
        ids = [k for k in pool if str(MISSIONS[k].get('dungeon')) in open_now]
        print(f'Vortex: {len(pool)} quests not fetched yet, {len(ids)} of them in dungeons bfrevive has open now; '
              f'this run does up to {LIMIT}')
        ids = ids[:LIMIT]
    elif not explicit:
        # default batch: quests not datamined (those are already exact) and not fetched yet
        ids = [k for k in sorted(MISSIONS, key=int)
               if k not in have and battles.get(k, {}).get('src') != 'datamine'
               and MISSIONS[k]['name'].isascii() and not MISSIONS[k]['name'].startswith(('Frontier Spire', 'Uncharted Frontier'))]
        print(f'{len(ids)} quests still to fetch; this run does up to {LIMIT}')
        ids = ids[:LIMIT]
    got = refused = fails = 0

    def refill():
        nonlocal ap, gems, signal
        time.sleep(WAIT)
        sr = template('ShopUseRequest')
        sr['IKqx1Cn9'], sr['6FrKacq7'] = [dict(ui)], signal
        sr['32ibWjFG'] = [{'60IsqxDt': '1', '03UGMHxF': '0', '5gXxT7LZ': '0', 'rA9jDCP5': '0'}]
        rr = action('ShopUseRequest', sr)
        t = (rr.get('fEi17cnx') or [{}])[0]
        if 'o5Tt0NV7' in rr or not t:
            print(f'  energy refill refused: {rr.get("o5Tt0NV7") or list(rr)}')
            return False
        new_gems = int(t.get('03UGMHxF') or gems)
        if new_gems < gems:
            print(f'  STOP: the energy refill cost gems ({gems} -> {new_gems}). Not refilling again.')
            gems = new_gems
            return None
        ap, gems = int(t.get('0P9X1YHs') or 0), new_gems
        print(f'  energy refilled for free -> {ap}')
        return True

    for mid in ids:
        m = MISSIONS.get(mid, {})
        cost = int(m.get('energy') or 0)
        name = m.get('name', '?')
        if cost > maxap and maxap:
            print(f'  {mid} "{name}": costs {cost} energy, more than your max {maxap} - skipped')
            continue
        if cost > ap:
            ok = refill()
            if ok is None:
                break
            if not ok or ap < cost:
                print('  out of energy - stopping here (run again later)')
                break
        time.sleep(WAIT)
        ms = template('MissionStartRequest')
        ms['IKqx1Cn9'] = [dict(ui)]
        ms['6FrKacq7'] = signal
        ms['9Q1Lq5FS'] = [{'h7eY3sAK': '0', 'J3stQ7jd': '0', 'j28VNcUW': mid, 'jkldTrhL': '0', 'Z0Y4RoD7': '0',
                           'nA95Bdj6': '0', '5Z1LNoyH': '0', 'u1iPEVUq': '-1'}]
        try:
            r = action('MissionStartRequest', ms)
            fails = 0
        except Exception as e:
            fails += 1
            print(f'  {mid} "{name}": request failed ({e})')
            if fails >= 3:
                print('  3 failures in a row - bfrevive may be down or blocking; stopping.')
                break
            time.sleep(30)
            continue
        if 'o5Tt0NV7' in r or 'U0v5IeJo' not in r:
            refused += 1
            print(f'  {mid} "{name}": bfrevive refused - {r.get("o5Tt0NV7") or list(r)}')
            json.dump({'mission': mid, 'refused': r, 'cleared_then': len(cleared)}, open(os.path.join(OUT, f'_refused_{mid}.json'), 'w'))
            continue
        signal = r.get('6FrKacq7') or signal
        ap = max(0, ap - cost)
        mons = r['U0v5IeJo']
        filler = sum(1 for x in mons if (x.get('e7DK0FQT'), x.get('67CApcti'), x.get('q08xLEsy')) == ('1000', '290', '50'))
        last = max(r['pj41dy9g'], key=lambda b: int(b['VETu07N6']))['Qzhp8B40']
        boss_ids = {x['o49dYfpH'] for x in r['75t0sx9z'] if x['Qzhp8B40'] == last}
        bosses = [x for x in mons if x['o49dYfpH'] in boss_ids]
        real_boss = [x for x in bosses if (x.get('67CApcti'), x.get('q08xLEsy')) != ('290', '50') or x.get('F4bQ7r8C')]
        kind = 'real enemies' if filler <= len(mons) // 2 else ('placeholder enemies, REAL BOSS' if real_boss else 'placeholder enemies and boss')
        json.dump({'mission': mid, 'name': name, 'response': r, 'got': time.strftime('%Y-%m-%d %H:%M:%S')},
                  open(os.path.join(OUT, f'mission_{mid}.json'), 'w', encoding='utf-8'), ensure_ascii=False)
        got += 1
        print(f'  {mid} "{name}": {kind} - boss: {", ".join(x.get("3evIn0zZ", "?") for x in bosses)}  (energy {ap})')
    print(f'fetched {got}, refused {refused}.')
    print('done. Results are in quest_recordings\\ (restart the private server to use them).')

if __name__ == '__main__':
    main()
