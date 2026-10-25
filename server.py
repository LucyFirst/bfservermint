#!/usr/bin/env python3
"""
Private Brave Frontier server.

Speaks the game's protocol (service discovery, guest login, the encrypted
action.php API) and serves downloaded content from a local folder.

Gameplay responses are replayed from a real capture: for every request the
server picks the recorded response whose request best matches, swaps in the
player's own IDs, and keeps each player's state (account, name, tutorial,
team, items, units) saved on disk.

Run:  python server.py            (settings in config.json)
"""
import base64, collections, datetime, glob, gzip, ipaddress, json, logging, os, random, re, ssl, string, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.x509.oid import NameOID

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, 'config.json')))
HOST = CFG['host']                       # what the game connects to, e.g. "192.168.1.71"
PORT = int(CFG.get('port', 443))
PUBLIC = HOST if PORT == 443 else f'{HOST}:{PORT}'
CDN_DIR = os.path.join(HERE, CFG.get('cdn_dir', 'cdn'))
SAVE_DIR = os.path.join(HERE, CFG.get('save_dir', 'saves'))
os.makedirs(SAVE_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', datefmt='%H:%M:%S',
                    handlers=[logging.StreamHandler(sys.stdout),
                              logging.FileHandler(os.path.join(HERE, 'server.log'), encoding='utf-8')])
log = logging.getLogger('bf')

# --------------------------------------------------------------------------- data
T = json.loads(gzip.open(os.path.join(HERE, 'data', 'templates.json.gz')).read())
BLOBS = T['blobs']
REQS = T['requests']                     # request id -> {cls, key}
BY_ID = {}
for a in T['action']:
    BY_ID.setdefault(a['id'], []).append(a)

# Group / field codes used by the game (see maps/ in bf-readable.zip)
G_USERINFO, G_SIGNAL, G_ERROR = 'IKqx1Cn9', '6FrKacq7', 'o5Tt0NV7'
G_UNITS, G_UNITS_ADD = '4ceMWH6k', 'qC2tJs4E'
G_SELL_UNITS = 'Km35HAXv'                # units chosen for selling
F_USERID, F_HANDLE, F_FRIENDID = 'h7eY3sAK', 'B5JQyV8j', '98WfKiyA'
F_GL_USER, F_GL_TOKEN = 'iN7buP2h', 'iN7buP1i'
F_DEVICE, F_DEVICE2 = 'Ma5GnU0H', '6K7LToCD'
F_SIGNAL, F_ERRMSG, F_UNIT_ID = 'Kn51uR4Y', '9GUsug3C', 'edy7fq3L'
F_TUTO_CH, F_TUTO_END = '9sQM2XcN', 'sv6BEI8X'
G_TEAM = 'fEi17cnx'                       # UserTeamInfo (level, currencies, energy...)
# Per-account balances the server keeps track of itself.
WALLET = {'gems': '03UGMHxF', 'zel': 'Najhr8m6', 'karma': 'HTVh8a65', 'friendpoints': 'J3stQ7jd',
          'summontickets': '9r3aLmaB', 'rainbowcoins': 'KAZmxkgy'}
WALLET_NAMES = {'gems': 'gems', 'zel': 'zel', 'karma': 'karma', 'friendpoints': 'honor points',
                'summontickets': 'summon tickets', 'rainbowcoins': 'rainbow coins'}
# Recorded actions whose currency change is applied to the player's balance.
EARN_SPEND = {'MissionEndRequest', 'PresentReceiptRequest', 'UnitMixRequest', 'UnitSellRequest', 'ItemMixRequest',
              'ItemSellRequest', 'TownFacilityUpdateRequest', 'TownUpdateRequest', 'DailyLoginRequest',
              'DailyTaskClaimRewardRequest', 'AchievementRewardReceiveRequest', 'SlotActionRequest',
              'ArenaBattleEndRequest', 'DungeonKeyReceiptRequest', 'EventTokenExchangePurchaseRequest',
              'ShopUseRequest', 'UnitEvoRequest', 'UnitOmniEvoRequest', 'ColosseumBattleEndRequest',
              'FrontierGateEndRequest', 'VortexArenaBattleEndRequest', 'ChallengeArenaBattleEndRequest'}
# Fixed values set with ADMIN.bat 'set' for non-currency fields; applied to every response.
OVERRIDE_FIELDS = {'gems': '03UGMHxF', 'zel': 'Najhr8m6', 'karma': 'HTVh8a65', 'friendpoints': 'J3stQ7jd',
                   'energy': '0P9X1YHs', 'maxenergy': 'YnM14RIP', 'arenaorbs': 'YS2JG9no',
                   'summontickets': '9r3aLmaB', 'rainbowcoins': 'KAZmxkgy', 'level': 'D9wXQI2V',
                   'unitbox': 'ouXxIY63', 'itembox': '5pjoGBC4', 'cost': 'QYP4kId9'}

# The capture was made with this account; its IDs are swapped for each player's.
CAP_USER = 'C0GqprOf'
CAP_FRIEND = '243529875'
CAP_HANDLE = 'Beater'

# Summon prices: banner id -> (gems, honor points), from every recorded banner list / gacha table
GACHA_PRICE = {}
for _b in BLOBS.values():
    for _g in ('1IR86sAv', '5Y4GJeo3'):
        for _r in (_b.get(_g) or []) if isinstance(_b.get(_g), list) else []:
            if isinstance(_r, dict) and '7Ffmi96v' in _r:
                try:
                    GACHA_PRICE[str(_r['7Ffmi96v'])] = (int(_r.get('03UGMHxF') or 0), int(_r.get('J3stQ7jd') or 0))
                except ValueError:
                    pass

# Request ids
RID = {v['cls']: k for k, v in REQS.items()}
VOLATILE_GROUPS = {G_USERINFO, G_SIGNAL, 'VersionInfo', '9u45RGcV'}

# --------------------------------------------------------------------------- crypto
DLS_KEY, DLS_IV = b'7410958164354871', b'Bfw4encrypedPass'

def _pad(b):
    n = 16 - len(b) % 16
    return b + bytes([n]) * n

def _unpad(b):
    n = b[-1]
    return b[:-n] if 1 <= n <= 16 and b[-n:] == bytes([n]) * n else b.rstrip(b'\0')

def aes_key(rid):
    return REQS[rid]['key'].encode().ljust(16, b'\0')[:16]

def dec_body(rid, b64):
    c = Cipher(algorithms.AES(aes_key(rid)), modes.ECB()).decryptor()
    return json.loads(_unpad(c.update(base64.b64decode(b64)) + c.finalize()))

def enc_body(rid, obj):
    c = Cipher(algorithms.AES(aes_key(rid)), modes.ECB()).encryptor()
    raw = json.dumps(obj, separators=(',', ':'), ensure_ascii=False).encode()
    return base64.b64encode(c.update(_pad(raw)) + c.finalize()).decode()

def enc_dls(obj):
    c = Cipher(algorithms.AES(DLS_KEY), modes.CBC(DLS_IV)).encryptor()
    return base64.b64encode(c.update(_pad(json.dumps(obj, separators=(',', ':')).encode())) + c.finalize()).decode()

# --------------------------------------------------------------------------- players
LOCK = threading.RLock()
LAST_SEEN = {}        # user id -> time of the player's last game request (admin page: online / last active)
ACC_FILE = os.path.join(SAVE_DIR, 'accounts.json')
ACCOUNTS = json.load(open(ACC_FILE)) if os.path.exists(ACC_FILE) else {'by_device': {}, 'by_token': {}}

def rand_id(n=8):
    return ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(n))

def save_accounts():
    tmp = ACC_FILE + '.tmp'
    json.dump(ACCOUNTS, open(tmp, 'w'), indent=1)
    os.replace(tmp, ACC_FILE)

# --------------------------------------------------------------------------- sign-in accounts
# Accounts are a username + password. Signing in gives the app an account key, which the game then
# uses in place of its device ID. Signing in again (on any device) makes a new key and a new game
# session, so only the latest device stays signed in.
import hashlib, hmac
from urllib.parse import parse_qs as _parse_qs
ACCOUNTS.setdefault('users', {})
REQUIRE_ACCOUNTS = bool(CFG.get('require_accounts', True))
LOCK_AFTER, LOCK_SECONDS = 5, 15 * 60
USER_RE = re.compile(r'^[A-Za-z0-9_]{3,16}$')
ACCOUNT_OPS = os.path.join(SAVE_DIR, 'account_ops.json')

def _hash(pw, salt):
    return hashlib.pbkdf2_hmac('sha256', pw.encode('utf-8'), bytes.fromhex(salt), 120000).hex()

def _set_password(u, pw):
    u['salt'] = os.urandom(16).hex()
    u['hash'] = _hash(pw, u['salt'])

def _new_session(u):
    """Fresh account key + game session for this account; the previous device is signed out."""
    old_key, old_tok = u.get('key'), u.get('token')
    ACCOUNTS['by_device'].pop(old_key, None)
    old = ACCOUNTS['by_token'].pop(old_tok, None) or {}
    u['key'] = 'acct-' + rand_id(32)
    u['token'] = rand_id(32).lower()
    ACCOUNTS['by_device'][u['key']] = {'gl_user': u['gl_user'], 'token': u['token']}
    ACCOUNTS['by_token'][u['token']] = {'gl_user': u['gl_user'], **({'user_id': old['user_id']} if old.get('user_id') else
                                         ({'user_id': u['user_id']} if u.get('user_id') else {}))}
    return u['key']

def _check_password(name, pw):
    """-> (user, error message)"""
    u = ACCOUNTS['users'].get(name.lower())
    now = time.time()
    if not u:
        return None, 'No account with that username.'
    if u.get('lock_until', 0) > now:
        mins = int((u['lock_until'] - now) // 60) + 1
        return None, f'Too many wrong passwords. Try again in {mins} minute{"s" if mins != 1 else ""}.'
    if not hmac.compare_digest(_hash(pw, u['salt']), u['hash']):
        u['fails'] = u.get('fails', 0) + 1
        if u['fails'] >= LOCK_AFTER:
            u['fails'], u['lock_until'] = 0, now + LOCK_SECONDS
            save_accounts()
            return None, 'Too many wrong passwords. The account is locked for 15 minutes.'
        save_accounts()
        return None, 'Wrong password.'
    u['fails'] = 0
    return u, None

def account_for_gl(gl_user):
    for name, u in ACCOUNTS['users'].items():
        if u.get('gl_user') == gl_user:
            return name
    return None

def _legacy_gl(*ids):
    """The guest account this device used before sign-in existed, if no account has claimed it yet."""
    for legacy in ids:
        legacy = (legacy or '').strip()
        acc = ACCOUNTS['by_device'].get(legacy) if legacy and not legacy.startswith('acct-') else None
        if acc and acc.get('gl_user') and not account_for_gl(acc['gl_user']):
            return acc['gl_user']
    return None

def _adopt_save(u, gl):
    """Give account u the save that belongs to guest account gl (u has no player of its own yet)."""
    u['gl_user'] = gl
    for t, acc in ACCOUNTS['by_token'].items():
        if acc.get('gl_user') == gl and acc.get('user_id'):
            u['user_id'] = acc['user_id']
    if u.get('user_id'):
        pl = load_player(u['user_id'])
        if pl:
            pl['account'] = u['name'].lower()
            save_player(pl)

def account_api(path, form):
    """The app's sign-in box. Replies with two lines: "ok"/"error", then the key or a message."""
    f = {k: (v[0] if v else '') for k, v in form.items()}
    if path.endswith('/version'):          # app 1.0.4+ on start: is there a newer app? (appupdate.py)
        txt = app_update().check(f.get('v', ''), HOST)
        return ('update\n' + txt) if txt else 'ok\n'
    apply_account_ops()
    with LOCK:
        if path.endswith('/check'):
            key = f.get('key', '')
            if key.startswith('acct-') and key in ACCOUNTS['by_device']:
                return 'ok\n'
            return 'error\nYou were signed out because this account signed in on another device. Please log in again.'
        name, pw = f.get('user', '').strip(), f.get('pass', '')
        if path.endswith('/create'):
            if not USER_RE.match(name):
                return 'error\nUsernames are 3-16 letters, numbers or _.'
            if len(pw) < 6:
                return 'error\nPasswords need at least 6 characters.'
            if name.lower() in ACCOUNTS['users']:
                return 'error\nThat username is taken.'
            gl = _legacy_gl(f.get('legacy', ''), f.get('legacy2', ''))
            u = {'name': name, 'gl_user': gl or rand_id(), 'created': int(time.time())}
            _set_password(u, pw)
            for t, acc in ACCOUNTS['by_token'].items():        # an existing save moves with the device
                if gl and acc.get('gl_user') == gl and acc.get('user_id'):
                    u['user_id'] = acc['user_id']
            ACCOUNTS['users'][name.lower()] = u
            key = _new_session(u)
            save_accounts()
            if u.get('user_id'):
                pl = load_player(u['user_id'])
                if pl:
                    pl['account'] = name.lower()
                    save_player(pl)
            log.info('account created: %s%s', name, f' (keeps the save of player {u["user_id"]})' if u.get('user_id') else '')
            return 'ok\n' + key
        if path.endswith('/login'):
            u, err = _check_password(name, pw)
            if err:
                log.info('sign-in refused for %s: %s', name, err)
                return 'error\n' + err
            if not u.get('user_id'):              # no player yet: take over the save already on this device
                gl = _legacy_gl(f.get('legacy', ''), f.get('legacy2', ''))
                if gl:
                    _adopt_save(u, gl)
                    log.info('account %s takes over the save of player %s', u['name'], u.get('user_id'))
            key = _new_session(u)
            save_accounts()
            log.info('signed in: %s', u['name'])
            return 'ok\n' + key
    return 'error\nUnknown request.'

def apply_account_ops():
    """Changes queued by ADMIN.bat (rename, new password, unlock)."""
    if not os.path.exists(ACCOUNT_OPS):
        return
    with LOCK:
        try:
            ops = json.load(open(ACCOUNT_OPS, encoding='utf-8'))
        except Exception:
            ops = []
        os.remove(ACCOUNT_OPS)
        for op in ops:
            u = ACCOUNTS['users'].get(str(op.get('user', '')).lower())
            if not u:
                continue
            if op.get('op') == 'password':
                _set_password(u, op['value'])
                u['fails'], u['lock_until'] = 0, 0
                log.info('admin: new password for %s', u['name'])
            elif op.get('op') == 'unlock':
                u['fails'], u['lock_until'] = 0, 0
                log.info('admin: unlocked %s', u['name'])
            elif op.get('op') == 'rename':
                new = str(op['value'])
                if USER_RE.match(new) and new.lower() not in ACCOUNTS['users']:
                    old = u['name'].lower()
                    ACCOUNTS['users'].pop(old)
                    u['name'] = new
                    ACCOUNTS['users'][new.lower()] = u
                    if u.get('user_id'):
                        pl = load_player(u['user_id'])
                        if pl:
                            pl['account'] = new.lower()
                            save_player(pl)
                    log.info('admin: renamed %s -> %s', old, new)
            elif op.get('op') == 'claim':
                # attach an existing player's save to this account (keeps the device signed in)
                pl = load_player(str(op.get('value')))
                if pl and not (pl.get('account') and pl['account'] != u['name'].lower()):
                    _adopt_save(u, pl['gl_user'])
                    u['user_id'] = pl['user_id']
                    pl['account'] = u['name'].lower()
                    save_player(pl)
                    if u.get('key') in ACCOUNTS['by_device']:
                        ACCOUNTS['by_device'][u['key']]['gl_user'] = pl['gl_user']
                    ACCOUNTS['by_token'][u.get('token')] = {'gl_user': pl['gl_user'], 'user_id': pl['user_id']}
                    log.info('admin: %s now plays %s (id %s)', u['name'], pl.get('handle'), pl['user_id'])
                else:
                    log.info('admin: could not give player %s to %s', op.get('value'), u['name'])
            elif op.get('op') == 'signout':
                _new_session(u)
                log.info('admin: signed out %s', u['name'])
        save_accounts()

def all_players():
    out = []
    for f in glob.glob(os.path.join(SAVE_DIR, 'player_*.json')):
        if f.endswith('.before-clean.json'):
            continue
        try:
            out.append(json.load(open(f, encoding='utf-8')))
        except Exception:
            pass
    return out

def player_path(uid):
    return os.path.join(SAVE_DIR, f'player_{uid}.json')

def load_player(uid):
    p = player_path(uid)
    if not os.path.exists(p):
        return None
    pl = json.load(open(p, encoding='utf-8'))
    # game data (summon lists, banners, area rows...) never lives in a save - older saves kept a
    # copy from whatever reply last carried it (e.g. a one-banner summon list after a summon)
    for g in MASTER_GROUPS - {G_SIGNAL}:
        (pl.get('state') or {}).pop(g, None)
    return pl

def save_player(pl):
    p = player_path(pl['user_id'])
    tmp = p + '.tmp'
    json.dump(pl, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False)
    os.replace(tmp, p)

def new_player(handle, gl_user, gl_token):
    uid = rand_id()
    pl = {'user_id': uid, 'handle': handle, 'friend_id': str(random.randint(100000000, 999999999)),
          'gl_user': gl_user, 'gl_token': gl_token, 'signal_key': rand_id(),
          'created': int(time.time()), 'state': {}, 'units': {}, 'cursor': {}}
    # Fresh-account state = the first GetUserInfo response of the capture.
    first = next(a for a in BY_ID[RID['GetUserInfoRequest']])
    absorb(pl, personalise(pl, BLOBS[first['resp']]), full=True)
    rekey_units(pl, set(pl['units']))
    pl['clean'] = 2
    ensure_wallet(pl)
    return pl

def rekey_units(pl, ids):
    """Give these units new ids of this player's own (and fix the squads that use them)."""
    remap = {}
    for old in list(ids):
        if old in pl['units']:
            u = pl['units'].pop(old)
            new = fresh_unit_id(pl)
            u[F_UNIT_ID] = u['Bvkx8s6M'] = new
            pl['units'][new] = u
            remap[old] = new
    for r in _rows(pl['state'].get('dX7S2Lc1')):
        if r.get(F_UNIT_ID) in remap:
            r[F_UNIT_ID] = remap[r[F_UNIT_ID]]
    return remap

# --------------------------------------------------------------------------- state handling
STATE_GROUPS = set()
for a in BY_ID.get(RID['GetUserInfoRequest'], []):
    STATE_GROUPS |= set(BLOBS[a['resp']].keys())
STATE_GROUPS -= {G_SIGNAL, G_UNITS}
PROTECTED_USER_FIELDS = {F_USERID, F_HANDLE, F_FRIENDID, F_TUTO_CH, F_TUTO_END, F_GL_USER, F_GL_TOKEN, 'N4XVE1uA', '9yVsu21R'}
CLIENT_PROGRESS_FIELDS = {'N4XVE1uA': '0,0@0@0@0&|0,0', '9yVsu21R': ''}
# Game data (banners, events, notices...) may come from recordings. Everything else that a
# GetUserInfo carries belongs to the player and comes ONLY from their save - or, for a part the
# server doesn't track yet, from a brand-new account's defaults - never from the recorded account.
MASTER_GROUPS = {'1IR86sAv', 'IBs49NiH', 'JukkSeNA', '2375D38i', '3SG2wX0R', '3aDk1xk7', '8UawtzHw', 'Bm1WNDQ0',
                 'Md9gG3c0', 'Pj6zDW3m', 'VkoZ5t3K', 'bpD29eiQ', 'da3qD39b', 'fTV3A0By', 'hE1d083b', 'j129kD6r',
                 'keTrog0p', G_SIGNAL}
PLAYER_GROUPS = (STATE_GROUPS - MASTER_GROUPS) | {G_UNITS, 'sEA41vFK', 'tojMy68W', 'XGmGpmYW'}
_FRESH_ACTION = min(BY_ID.get(RID['GetUserInfoRequest'], []), key=lambda a: a['n'])
FRESH = BLOBS[_FRESH_ACTION['resp']]          # the recorded account on its first day = new-account defaults

EMPTY_FOR_NEW = {'30uygM9m', 'tojMy68W', 'sEA41vFK'}     # gifts, friends, present box: a new account has none

def fresh_value(pl, g):
    v = None if g in EMPTY_FOR_NEW else FRESH.get(g)
    if v is None:
        return [] if g not in ('Bnc4LpM8', '4W6EhXLS', '1ry6BKoe', 'M3dw18eB') else {}
    return personalise(pl, {g: v})[g]

def personalise(pl, obj):
    """Swap the capture account's IDs for this player's."""
    s = json.dumps(obj, separators=(',', ':'), ensure_ascii=False)
    s = s.replace(CAP_USER, pl['user_id'])
    obj = json.loads(s)
    ui = obj.get(G_USERINFO)
    for row in (ui if isinstance(ui, list) else [ui] if isinstance(ui, dict) else []):
        if F_HANDLE in row and row[F_HANDLE]:
            row[F_HANDLE] = pl['handle']
        if F_FRIENDID in row and row[F_FRIENDID] == CAP_FRIEND:
            row[F_FRIENDID] = pl['friend_id']
        if F_GL_USER in row and row[F_GL_USER]:
            row[F_GL_USER] = pl['gl_user']
    if G_SIGNAL in obj:
        obj[G_SIGNAL] = [{F_SIGNAL: pl['signal_key']}]
    if 'Pj6zDW3m' in obj:
        try:
            obj['Pj6zDW3m'] = news_engine().patch_notice(obj['Pj6zDW3m'])
        except Exception:
            log.exception('could not set the news page')
    if 'Md9gG3c0' in obj:
        try:
            obj['Md9gG3c0'] = fusion_label_events(obj['Md9gG3c0'])
        except Exception:
            log.exception('could not set the Fusion button label')
    return obj

def absorb(pl, resp, full=False):
    """Fold the user-state parts of a response into the saved player."""
    for g, v in resp.items():
        if g in STATE_GROUPS and g not in MASTER_GROUPS:
            if g == G_USERINFO and pl['state'].get(g):
                # merge: never blank a field, never touch identity/tutorial fields
                cur = pl['state'][g][0] if isinstance(pl['state'][g], list) else pl['state'][g]
                new = v[0] if isinstance(v, list) and v else v
                if isinstance(new, dict):
                    for k, val in new.items():
                        if k in PROTECTED_USER_FIELDS or val in ('', None):
                            continue
                        cur[k] = val
                    pl['state'][g] = [cur]
                continue
            pl['state'][g] = v
    if G_UNITS in resp and isinstance(resp[G_UNITS], list) and full:
        pl['units'] = {u[F_UNIT_ID]: u for u in resp[G_UNITS] if F_UNIT_ID in u}
    if G_UNITS_ADD in resp and isinstance(resp[G_UNITS_ADD], list):
        for u in resp[G_UNITS_ADD]:
            if F_UNIT_ID in u:
                pl['units'][u[F_UNIT_ID]] = u

# Town upgrades (Spheres, Synthesis... / Mountain, River, Farm, Forest): the game sends the new
# levels of every facility (YRgx49WG) and place (yj46Q2xw) and the karma it spent (EuY6L7AX).
# The karma is worked out from the game's own level tables, so it can't be more or less than
# the upgrades cost.
TOWN_GROUPS = {'YRgx49WG': ('y9ET7Aub', 'd0EkJ4TB'), 'yj46Q2xw': ('un80kW9Y', '9ekQ4tZq')}
_TOWN_COST = {}

def town_cost_table(master):
    if master not in _TOWN_COST:
        rows = next((b[master] for b in BLOBS.values() if isinstance(b, dict) and b.get(master)), [])
        key = next(k for g, (k, m) in TOWN_GROUPS.items() if m == master)
        _TOWN_COST[master] = {(str(r.get(key)), _num(r.get('D9wXQI2V')) or 0): _num(r.get('HTVh8a65')) or 0
                              for r in rows if isinstance(r, dict)}
    return _TOWN_COST[master]

def pay_town_upgrade(pl, req):
    spent, known = 0, True
    for g, (key, master) in TOWN_GROUPS.items():
        table = town_cost_table(master)
        old = {str(r.get(key)): r for r in _rows(pl['state'].get(g) or fresh_value(pl, g)) if isinstance(r, dict)}
        for r in _rows(req.get(g)):
            if not isinstance(r, dict):
                continue
            o = old.get(str(r.get(key))) or {}
            lo, hi = _num(o.get('D9wXQI2V')) or 0, _num(r.get('D9wXQI2V')) or 0
            for lv in range(lo + 1, hi + 1):
                c = table.get((str(r.get(key)), lv))
                if c is None:
                    known = False
                spent += c or 0
            # karma put towards the next level without finishing it
            spent += (_num(r.get('HTVh8a65')) or 0) - ((_num(o.get('HTVh8a65')) or 0) if hi == lo else 0)
    sent = sum(_num(r.get('HTVh8a65')) or 0 for r in _rows(req.get('EuY6L7AX')) if isinstance(r, dict))
    if not known or spent < 0:
        spent = sent
    if spent != sent:
        log.warning('town upgrade: the game says %s karma, the level tables say %s - using the tables', sent, spent)
    if spent > 0:
        have = int(pl['wallet'].get('karma', 0))
        pl['wallet']['karma'] = max(0, have - spent)
        log.info('town upgrade: -%s karma (%s -> %s)', spent, have, pl['wallet']['karma'])

def apply_request(pl, cls, req):
    """Requests that carry the player's own choices."""
    if cls == 'TownFacilityUpdateRequest':
        pay_town_upgrade(pl, req)        # before the new levels are saved over the old ones
    if cls == 'DeckEditRequest' and 'UserPartyDeckInfo' in req:
        pass
    # Generic: groups the client sends that are also user state (deck, equipment...)
    for g, v in req.items():
        if g in STATE_GROUPS and g != G_USERINFO and g not in MASTER_GROUPS:
            if is_selector(pl, g, v):
                continue          # just "which one?" (e.g. a token id) - not a new copy of the list
            if g == G_PARTY:
                v = merge_party(pl, v)
            pl['state'][g] = v
    ui = (req.get(G_USERINFO) or [{}])[0]
    # progress the game keeps in your user info and reports itself: area cutscenes seen (N4XVE1uA)
    # and features opened (9yVsu21R)
    st = pl['state'].setdefault(G_USERINFO, [{}])[0]
    for f in CLIENT_PROGRESS_FIELDS:
        if ui.get(f):
            st[f] = ui[f]
    if cls == 'DeckEditRequest' and req.get('Ti62XfZK'):
        t = team_row(pl['state'])
        if t is not None:      # current squad number and squad settings chosen in the game
            for k, v in (req['Ti62XfZK'][0] or {}).items():
                if k in t:
                    t[k] = v if not isinstance(t.get(k), int) else (_num(v) or 0)
    if cls == 'UnitSellRequest':
        pl['_sold'] = {row.get(F_UNIT_ID): pl['units'].pop(row.get(F_UNIT_ID), None) for row in req.get(G_SELL_UNITS) or []}
    if cls == 'TutorialUpdateRequest':
        st = pl['state'].setdefault(G_USERINFO, [{}])[0]
        for f in (F_TUTO_CH, F_TUTO_END):
            if f in ui:
                st[f] = ui[f]
    if cls in ('CreateUserRequest', 'ChangeHandleNameRequest', 'UserInfoEditRequest') and ui.get(F_HANDLE):
        pl['handle'] = ui[F_HANDLE]
        pl['state'].setdefault(G_USERINFO, [{}])[0][F_HANDLE] = ui[F_HANDLE]

def _row_keys(v):
    rows = [r for r in (v if isinstance(v, list) else [v]) if isinstance(r, dict)]
    return [set(r) for r in rows]

def is_selector(pl, g, v):
    """True when the game sends only an id from a list the account keeps in full (fewer fields
    than the saved rows) - storing that would wipe the saved list."""
    sent = _row_keys(v)
    have = _row_keys(pl['state'].get(g)) or _row_keys(fresh_value(pl, g))
    if not sent or not have:
        return False
    full = max(have, key=len)
    return len(full) >= 3 and all(k < full for k in sent)

def fix_selector_overwrites(pl):
    """Repair saves where an id-only request replaced a saved list (it broke the Event Bazaar)."""
    for g in list(pl['state']):
        cur, fresh = _row_keys(pl['state'].get(g)), _row_keys(fresh_value(pl, g))
        if cur and fresh:
            full = max(fresh, key=len)
            if len(full) >= 3 and all(k < full for k in cur):
                pl['state'][g] = fresh_value(pl, g)
                log.info('repaired %s (an id-only request had replaced the saved list)', g)

def team_row(obj):
    t = obj.get(G_TEAM) if isinstance(obj, dict) else None
    t = t[0] if isinstance(t, list) and t else t
    return t if isinstance(t, dict) else None

def ensure_wallet(pl):
    if 'wallet' not in pl:
        t = team_row(pl['state']) or {}
        pl['wallet'] = {k: int(t.get(c) or 0) if str(t.get(c, '0')).lstrip('-').isdigit() else 0
                        for k, c in WALLET.items()}
    # admin adjustments waiting in saves/adjust_<id>.json (written by ADMIN.bat)
    adj = os.path.join(SAVE_DIR, f"adjust_{pl['user_id']}.json")
    if os.path.exists(adj):
        try:
            ops = json.load(open(adj))
            os.remove(adj)
        except Exception:
            ops = []
        for op in ops:
            if op.get('op') == 'unit':
                give_unit(pl, str(op.get('value')))
                continue
            if op.get('op') == 'clean':
                clean_save(pl)
                continue
            f, v = op.get('field'), int(op.get('value', 0))
            if f in WALLET:
                pl['wallet'][f] = max(0, (pl['wallet'].get(f, 0) + v) if op.get('op') == 'add' else v)
            else:
                ov = pl.setdefault('overrides', {})
                if op.get('op') == 'reset':
                    ov.pop(f, None)
                else:
                    ov[f] = v
            log.info('admin: %s %s %s -> %s', op.get('op'), f, v, pl['wallet'].get(f, pl.get('overrides', {}).get(f)))

def wallet_change(pl, cls, req, cap):
    """How this action changes the player's balances."""
    if cls == 'GachaActionRequest':
        rows = [v for k, v in req.items() if k not in VOLATILE_GROUPS and isinstance(v, list)]
        g = next((r for v in rows for r in v if isinstance(r, dict) and '7Ffmi96v' in r), {})
        gid, count = str(g.get('7Ffmi96v', '')), max(1, int(g.get('a329kbl8') or 1))
        if str(g.get('324b023k', '0')) == '1':
            return {'summontickets': -count}
        try:
            gems, honor = summon_editor().price(gid)
        except Exception:
            gems, honor = GACHA_PRICE.get(gid, (0, 0))
        if gems or honor:
            return {k: -v * count for k, v in (('gems', gems), ('friendpoints', honor)) if v}
        return dict(cap.get('delta') or {}) if cap else {}
    if cls == 'MissionEndRequest' and MISSIONS.get(pl.get('current_mission') or ''):
        # the quest's clear bonus (game data) + what this battle picked up (sent by the game).
        # A failed quest keeps only what was picked up - no clear bonus, no helper honor (as bfrevive)
        m, mine = MISSIONS[pl['current_mission']], mission_result(req)
        won = not mission_failed(req)
        if is_trial(pl['current_mission']) and cleared_before(pl, pl['current_mission']):
            pl.pop('_helper_pts', 0)
            pl['_gain'] = {}
            return {}                 # a trial only rewards its first clear
        mid = pl['current_mission']
        ch = {'zel': (int((_num(m['zel']) or 0) * mult('zel') * boost_mult(mid, 'zel')) if won else 0)
                     + (_num(mine.get('Najhr8m6')) or 0),
              'karma': (int((_num(m['karma']) or 0) * mult('karma') * boost_mult(mid, 'karma')) if won else 0)
                       + (_num(mine.get('HTVh8a65')) or 0)}
        pts = pl.pop('_helper_pts', 0)
        if pts and won:
            ch['friendpoints'] = pts                                   # honor points for the helper used
        pl['_gain'] = ch
        return ch
    if cls == 'MissionEndRequest' and cap:
        # clear bonus from the recording + what this battle actually picked up (sent by the game)
        mine, rec = mission_result(req), mission_result(cap['req'])
        ch = dict(cap.get('delta') or {})
        for k, f in (('zel', 'Najhr8m6'), ('karma', 'HTVh8a65')):
            ch[k] = max(0, ch.get(k, 0) - (_num(rec.get(f)) or 0)) + (_num(mine.get(f)) or 0)
        return ch
    if cls == 'UnitMixRequest':
        z = _num(((req.get('mCE3rUu5') or [{}])[0]).get('Rs7bCE3t'))
        return {'zel': -z} if z else {}
    if cls == 'UnitEvoRequest':
        plan = pl.get('_evo') or {}
        return {k: -int(plan[k]) for k in ('zel', 'karma') if int(plan.get(k) or 0)}
    if cls == 'UnitSellRequest':
        um = unit_mst()
        sold = pl.pop('_sold', {}) or {}
        sp = sell_price()
        zel = sum(sp.price((u or {}).get('pn16CNah')) if (u or {}).get('pn16CNah') in um else 0
                  for u in sold.values())
        return {'zel': zel}
    # other recorded rewards (presents, achievements, slots, daily login...) were the recorded
    # account's - they give nothing here until the server works them out itself
    return {}

# --------------------------------------------------------------------------- summons
POOLS_FILE = os.path.join(HERE, 'data', 'gacha_pools.json')
POOLS = json.load(open(POOLS_FILE)) if os.path.exists(POOLS_FILE) else {}
UNIT_MST = {}
G_OPE_UNIT = 'Km35HAXv'
SUMMON_EFFECT = {1: ['1'], 2: ['2'], 3: ['8'], 4: ['10', '11'], 5: ['13', '15'], 6: ['19', '21'],
                 7: ['228', '229'], 8: ['900', '901']}

def unit_mst():
    """Unit master rows (stats, skills) from the game's own data files."""
    if not UNIT_MST:
        p = os.path.join(HERE, 'data', 'unit_mst.json')
        if os.path.exists(p):
            UNIT_MST.update(json.load(open(p, encoding='utf-8')))
    return UNIT_MST

_HL = None
def highlights():
    """Player highlights for Discord (highlights.py). Never lets a highlight break the game."""
    global _HL
    if _HL is None:
        try:
            import highlights as _h
            _HL = _h.Highlights(sys.modules[__name__])
        except Exception:
            log.exception('highlights: could not start')
            _HL = False
    return _HL

def highlight(event, *args):
    try:
        h = highlights()
        if h:
            getattr(h, event)(*args)
    except Exception:
        log.exception('highlight %s', event)

def roll_unit(gid):
    try:
        pool = summon_editor().pool(str(gid))
    except Exception:
        log.exception('summon pool for %s', gid)
        pool = None
    if not pool or not pool.get('units'):
        pool = POOLS.get(str(gid)) if (POOLS.get(str(gid)) or {}).get('units') else POOLS.get('default')
    off = set(admin_settings()['off']['units'])
    units = {int(r): [u for u in v if str(u) not in off] for r, v in (pool.get('units') or {}).items() if v}
    units = {r: v for r, v in units.items() if v} or \
        {int(r): v for r, v in (pool.get('units') or {}).items() if v}       # everything closed: ignore
    rates = {int(r): float(p) for r, p in (pool.get('rates') or {}).items() if int(r) in units}
    if not rates:
        rates = {r: 1.0 for r in units}
    rateup = {str(u): float(m) for u, m in (pool.get('rateup') or {}).items()}

    def pick(r):          # units of a rarity: equally likely, rate-up units count x times
        ids = units[r]
        if not rateup or not any(str(u) in rateup for u in ids):
            return random.choice(ids)
        return random.choices(ids, weights=[rateup.get(str(u), 1.0) for u in ids])[0]
    x, acc = random.uniform(0, sum(rates.values())), 0.0
    for r, p in sorted(rates.items()):
        acc += p
        if x <= acc:
            return pick(r), r
    r = max(units)
    return pick(r), r

# Unit types (nBTx56W9) and how often a new unit gets each one: the game's own unit type table
# (F_UNIT_TYPE_MST ChD5b0jR): 1 Lord 23%, 2 Anima 23%, 3 Breaker 22%, 4 Guardian 22%, 5 Oracle 10%,
# 6 Rex 0% (never rolled).
UNIT_TYPE_CHANCE = {'1': 23, '2': 23, '3': 22, '4': 22, '5': 10}

def roll_unit_type():
    return random.choices(list(UNIT_TYPE_CHANCE), weights=list(UNIT_TYPE_CHANCE.values()))[0]

LORD = '1'

def is_material(unit_id):
    """Evolution / fusion / sell materials (crystals, ghosts, kings, gods, imps, nymphs, spirits, idols,
    totems, pots, frogs, golems...): the game's unit kind is not 0."""
    return str((unit_mst().get(str(unit_id)) or {}).get('xP9oi5Z2', '0')) != '0'

def materials_to_lord(pl):
    """Materials are always Lord, so copies of the same material are alike and can stack. A save is
    copied to saves/backups/old once before its first change."""
    change = [u for u in pl['units'].values() if is_material(u.get('pn16CNah')) and str(u.get('nBTx56W9')) != LORD]
    if not change:
        return
    if not pl.get('materials_lord'):
        try:
            import shutil
            d = os.path.join(SAVE_DIR, 'backups', 'old')
            os.makedirs(d, exist_ok=True)
            dst = os.path.join(d, f"player_{pl['user_id']}_before_lord_materials.json")
            if os.path.exists(player_path(pl['user_id'])) and not os.path.exists(dst):
                shutil.copy2(player_path(pl['user_id']), dst)
        except Exception:
            log.exception('could not back up %s before turning materials into Lord', pl['user_id'])
            return
        pl['materials_lord'] = 1
    for u in change:
        u['nBTx56W9'] = LORD
    log.info('%d material unit(s) of %s are now Lord', len(change), pl['user_id'])

# What each unit type adds to a unit's stats at every level up (the game's unit type table,
# F_UNIT_TYPE_MST: HP 76IHLVsz, ATK 2M4mQZgk, DEF Sj9zR38K, REC 6D3YN9rc, each "low,high"). The game
# keeps these in the unit's own HP/ATK/DEF/REC (an Oracle at Lv 47 had about -46 DEF, +140 REC), so two
# copies of a unit with different types end up with different stats. Each level's roll is fixed per
# unit (seeded from the unit's own id), so the same unit always gets the same numbers.
UNIT_TYPE_GROWTH = {'1': ((0, 0), (0, 0), (0, 0), (0, 0)),         # Lord
                    '2': ((5, 10), (0, 0), (0, 0), (-3, -1)),      # Anima
                    '3': ((0, 0), (1, 3), (-3, -1), (0, 0)),       # Breaker
                    '4': ((0, 0), (0, 0), (1, 3), (-2, 0)),        # Guardian
                    '5': ((0, 0), (0, 0), (-2, 0), (2, 4)),        # Oracle
                    '6': ((10, 15), (1, 2), (1, 2), (1, 2))}       # Rex
STAT_FIELDS = ('e7DK0FQT', '67CApcti', 'q08xLEsy', 'PWXu25cg')    # HP, ATK, DEF, REC

def type_growth(row, lv):
    """The type's stat change from level 1 up to lv for this unit: [hp, atk, def, rec]."""
    g = UNIT_TYPE_GROWTH.get(str(row.get('nBTx56W9') or '1'), UNIT_TYPE_GROWTH['1'])
    out = [0, 0, 0, 0]
    if not any(lo or hi for lo, hi in g):
        return out
    rng = random.Random(f"{row.get('edy7fq3L')}:{row.get('pn16CNah')}:{row.get('nBTx56W9')}")
    for _ in range(2, int(lv) + 1):
        for i, (lo, hi) in enumerate(g):
            out[i] += rng.randint(lo, hi)
    return out

def unit_row_stats(row, m, lv):
    """HP/ATK/DEF/REC fields for this unit at level lv: the unit's own curve plus its type's growth."""
    st = unit_stats(m, lv)
    for f, add in zip(STAT_FIELDS, type_growth(row, lv)):
        st[f] = str(max(1, int(st[f]) + add))
    return st

def fix_type_stats(pl):
    """Once per save: units levelled here before unit types counted get their type's stats. Only
    units whose stats are still the plain curve are changed. A copy of the save goes to
    saves/backups/old first."""
    if pl.get('type_stats') == 1:
        return
    um = unit_mst()
    change = []
    for u in pl['units'].values():
        m = um.get(str(u.get('pn16CNah')))
        lv = _num(u.get('D9wXQI2V')) or 1
        if not m or lv <= 1 or str(u.get('nBTx56W9') or '1') == LORD or is_material(u.get('pn16CNah')):
            continue
        plain = unit_stats(m, lv)
        if all(str(_num(u.get(f)) or 0) == plain[f] for f in STAT_FIELDS):
            change.append((u, m, lv))
    if change:
        try:
            import shutil
            d = os.path.join(SAVE_DIR, 'backups', 'old')
            os.makedirs(d, exist_ok=True)
            dst = os.path.join(d, f"player_{pl['user_id']}_before_type_stats.json")
            if os.path.exists(player_path(pl['user_id'])) and not os.path.exists(dst):
                shutil.copy2(player_path(pl['user_id']), dst)
        except Exception:
            log.exception('could not back up %s before adding unit type stats', pl['user_id'])
            return
        for u, m, lv in change:
            u.update(unit_row_stats(u, m, lv))
        log.info('%d unit(s) of %s now have their unit type stats', len(change), pl['user_id'])
    pl['type_stats'] = 1

# Crafting recipes the player may use (51yQrDBR: 4HqhTf3a recipe id, H6k1LIxC times made, VDKB0Y5h
# limit). Replies always carry the player's own list, so a recorded reply never added recipes and new
# accounts kept only the 29 starting (item) recipes - the Town's Sphere List stayed empty. Every
# account gets the full list the recordings' longest list has (items and spheres alike).
G_RECIPES = '51yQrDBR'
_ALL_RECIPES = []

def all_recipes():
    if not _ALL_RECIPES:
        _ALL_RECIPES.extend(max(([r for r in _rows(b.get(G_RECIPES)) if isinstance(r, dict) and r.get('4HqhTf3a')]
                                 for b in BLOBS.values() if isinstance(b, dict) and G_RECIPES in b),
                                key=len, default=[]))
    return _ALL_RECIPES

def ensure_recipes(pl):
    mine = [r for r in _rows(pl['state'].get(G_RECIPES)) if isinstance(r, dict)]
    have = {str(r.get('4HqhTf3a')) for r in mine}
    add = [dict(r, H6k1LIxC='0') for r in all_recipes() if str(r['4HqhTf3a']) not in have]
    if add:
        pl['state'][G_RECIPES] = mine + add
        log.info('%d crafting recipe(s) opened for %s', len(add), pl['user_id'])

def new_unit_row(pl, template, unit_id, uuid, unit_type=None):
    m = unit_mst().get(unit_id, {})
    if is_material(unit_id):
        unit_type = LORD
    row = dict(template)
    sk, ex = m.get('nj9Lw7mV', '0'), m.get('iEFZ6H19', '0')
    row.update({'edy7fq3L': uuid, 'h7eY3sAK': pl['user_id'], 'pn16CNah': unit_id,
                'nBTx56W9': str(unit_type) if unit_type else roll_unit_type(), 'D9wXQI2V': '1', 'd96tuT2E': '0', 'gQInj3H6': '0',
                'e7DK0FQT': m.get('UZ1Bj7w2', row.get('e7DK0FQT')), '67CApcti': m.get('i9Tn7kYr', row.get('67CApcti')),
                'q08xLEsy': m.get('q78KoWsg', row.get('q08xLEsy')), 'PWXu25cg': m.get('92ij6UGB', row.get('PWXu25cg')),
                'nj9Lw7mV': sk if sk not in ('0', '') else '', '3NbeC8AB': '1' if sk not in ('0', '') else '0',
                'iEFZ6H19': ex if ex not in ('0', '') else '', 'Bvkx8s6M': uuid, 'dJNpLc81': '1'})
    return row

def do_summon(pl, req, resp):
    """Replace the recorded summon result with a roll from this banner's own pool."""
    rows = [v for k, v in req.items() if k not in VOLATILE_GROUPS and isinstance(v, list)]
    g = next((r for v in rows for r in v if isinstance(r, dict) and '7Ffmi96v' in r), {})
    gid, count = str(g.get('7Ffmi96v', '')), max(1, int(g.get('a329kbl8') or 1))
    tmpl_row = (resp.get(G_UNITS_ADD) or [{}])[0]
    tmpl_ope = (resp.get(G_OPE_UNIT) or [{}])[0]
    ope, add, best = [], [], None
    for _ in range(count):
        unit_id, rare = roll_unit(gid)
        uuid = fresh_unit_id(pl)
        row = new_unit_row(pl, tmpl_row, unit_id, uuid)
        add.append(row)
        pl['units'][uuid] = row
        add_to_dictionary(pl, unit_id)
        o = dict(tmpl_ope); o.update({'edy7fq3L': uuid, 'u0vkt9yH': random.choice(SUMMON_EFFECT.get(rare, ['8']))})
        ope.append(o)
        if best is None or rare > best[0]:
            best = (rare, o)
        highlight('summon', pl, unit_id)
        log.info('summon on %s (%s): %s* %s', gid, (POOLS.get(gid) or {}).get('name', 'default pool'), rare,
                 unit_mst().get(unit_id, {}).get('utP1c0CD', unit_id))
    resp[G_UNITS_ADD] = add
    # Every unit gets a row: the game shows a multi summon unit by unit from this list (UserState's
    # gacha index walks it). The gate itself takes the LAST row's effect, so the best unit goes last.
    if best and len(ope) > 1:
        ope.remove(best[1])
        ope.append(best[1])
    resp[G_OPE_UNIT] = ope
    if count > 1 and best:
        log.info('  multi summon gate: %s* (effect %s)', best[0], best[1]['u0vkt9yH'])
    return resp

def check_and_apply(pl, change):
    for k, v in change.items():
        if v < 0 and pl['wallet'].get(k, 0) + v < 0:
            return f"Not enough {WALLET_NAMES.get(k, k)} (you have {pl['wallet'].get(k, 0)}, need {-v})."
    for k, v in change.items():
        if k in pl['wallet']:
            pl['wallet'][k] = max(0, pl['wallet'][k] + v)
    return None

def apply_wallet(pl, resp):
    for obj in (resp, pl['state']):
        t = team_row(obj)
        if t is not None:
            for k, c in WALLET.items():
                t[c] = pl['wallet'].get(k, 0) if isinstance(t.get(c), int) else str(pl['wallet'].get(k, 0))

def apply_overrides(pl, resp):
    ov = pl.get('overrides') or {}
    if not ov or not isinstance(resp, dict):
        return resp
    for g in (resp.get(G_TEAM), pl['state'].get(G_TEAM)):
        rows = g if isinstance(g, list) else [g] if isinstance(g, dict) else []
        for row in rows:
            for name, val in ov.items():
                code = OVERRIDE_FIELDS.get(name)
                if code and name not in WALLET:
                    row[code] = val if isinstance(row.get(code), int) else str(val)
    return resp

# --------------------------------------------------------------------------- squads
# dX7S2Lc1 rows: U9ABSYEp = kind of squad (1 quests, 2 Arena, 3 raid, 4 Ep3, 5 multi, 100 guild
# raid), zsiAn9P1 = squad number, gr48vsdJ 0 = leader, XuJL4pc5 = place. The game sends only the
# kind it edited (e.g. the Arena squads), so the other kinds must be kept.
G_PARTY, F_PARTY_KIND = 'dX7S2Lc1', 'U9ABSYEp'
RAID_SQUAD = '3'                          # squad types: 1 quest, 2 arena, 3 raid (the game's own code)

def active_squad(pl):
    """The quest squad in use (UserTeamInfo Z0Y4RoD7), as a string squad number."""
    t = team_row(pl['state']) or {}
    return str(_num(t.get('Z0Y4RoD7')) or 0)

G_FRIEND_GIFTS = '30uygM9m'          # friend gifts waiting (yDiG31Fe from, 1px2dbX3 to)

_GIFTS = None
_APPUPD = None

def app_update():
    global _APPUPD
    if _APPUPD is None:
        import appupdate
        _APPUPD = appupdate.AppUpdate(sys.modules[__name__])
    return _APPUPD

def gift_box():
    global _GIFTS
    if _GIFTS is None:
        import giftbox
        _GIFTS = giftbox.GiftBox(sys.modules[__name__])
    return _GIFTS

def merge_party(pl, rows):
    rows = [r for r in _rows(rows) if isinstance(r, dict)]
    kinds = {str(r.get(F_PARTY_KIND)) for r in rows}
    if not rows or not kinds:
        return pl['state'].get(G_PARTY) or rows
    kept = [r for r in _rows(pl['state'].get(G_PARTY))
            if isinstance(r, dict) and str(r.get(F_PARTY_KIND)) not in kinds]
    return [r for r in kept + rows if str(r.get(F_PARTY_KIND)) != RAID_SQUAD]

def repair_party(pl):
    """Saves where an Arena squad edit had replaced the quest squads (EMPTY_PARTY_INFO at login):
    take the quest squads back from the newest backup that has them, else give squad 1 a leader."""
    rows = [r for r in _rows(pl['state'].get(G_PARTY)) if isinstance(r, dict)]
    have = set(pl['units'])
    if any(str(r.get(F_PARTY_KIND)) == '1' and r.get(F_UNIT_ID) in have for r in rows):
        return
    found, src = [], 'a new leader'
    bdir = os.path.join(SAVE_DIR, 'backups')
    for f in sorted(glob.glob(os.path.join(bdir, f"player_{pl['user_id']}_*.json")), reverse=True):
        try:
            old = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        found = [r for r in _rows((old.get('state') or {}).get(G_PARTY))
                 if isinstance(r, dict) and str(r.get(F_PARTY_KIND)) == '1' and r.get(F_UNIT_ID) in have]
        if found:
            src = os.path.basename(f)
            break
    if not found and have:
        leader = next((r.get(F_UNIT_ID) for r in rows if r.get(F_UNIT_ID) in have), None) or sorted(have)[0]
        found = [{F_PARTY_KIND: '1', 'zsiAn9P1': '0', F_UNIT_ID: leader, 'gr48vsdJ': '0', 'XuJL4pc5': '0'}]
    if found:
        # every squad needs a leader
        for sq in {str(r.get('zsiAn9P1')) for r in found}:
            if not any(str(r.get('zsiAn9P1')) == sq and str(r.get('gr48vsdJ')) == '0' for r in found):
                next(r for r in found if str(r.get('zsiAn9P1')) == sq)['gr48vsdJ'] = '0'
        pl['state'][G_PARTY] = [r for r in rows if str(r.get(F_PARTY_KIND)) != '1'] + found
        log.info('repaired the quest squads of %s (%d rows, from %s)', pl['user_id'], len(found), src)

def full_user_info(pl):
    """GetUserInfo built from the saved player."""
    try:
        repair_party(pl)
    except Exception:
        log.exception('could not check the squads')
    pl['state'][G_FRIEND_GIFTS] = []         # friend gifts live in giftbox.py (added to the reply below)
    tmpl = BY_ID[RID['GetUserInfoRequest']][-1]
    out = personalise(pl, BLOBS[tmpl['resp']])
    for g in list(out):
        if g in MASTER_GROUPS:
            continue                       # game data: from the recordings, never from a save
        if g in pl['state']:
            out[g] = pl['state'][g]
        elif g in PLAYER_GROUPS:
            out[g] = fresh_value(pl, g)
    out[G_UNITS] = list(pl['units'].values())
    try:
        out[G_FRIEND_GIFTS] = gift_box().rows(pl)
    except Exception:
        log.exception('could not list the friend gifts')
    out[G_SIGNAL] = [{F_SIGNAL: pl['signal_key']}]
    if UNLOCK:
        pl['state'][G_PERMIT] = out[G_PERMIT] = story_permits(pl)
    if 'Y73tHKS8' in out:
        out['Y73tHKS8'] = sp_permits(pl)      # plus quests opened with keys, while they last
    out['3da6bd0a'] = login_campaign_groups(pl)['3da6bd0a']
    (pl.get('login_campaign') or {}).pop('new', None)     # the claim screen shows once per day
    if G_KEYS in out:
        key_opens(pl)
        out[G_KEYS] = key_rows(pl)
    if '3SG2wX0R' in out:            # the game's Vortex tabs come from here, not only from its table
        try:
            out['3SG2wX0R'] = hide_closed_tabs(vortex_editor().patch_area_rows(out['3SG2wX0R']))
        except Exception:
            log.exception('could not apply the edited Vortex tab pictures')
    return out

# --------------------------------------------------------------------------- state model
# The player's save is the truth. A recorded response is only a template: the parts of it that
# describe the recorded account (unit box, collection, items, levels...) are never copied in.
# Where a recorded action changed something (+1 item, +exp, a new unit), only that change is
# applied to this player's own data.
G_DICT = 'GV81ctzR'
G_WAREHOUSE = '9wjrh74P'
G_MIX_RESULT = '1ZbHB6Im'
G_REWARD = 'F5Vs19mb'          # MissionReward: the quest result screen
REQ_NOW = {}            # request being handled (set under LOCK)
# list groups: key field(s) per row, and fields that are counts (applied as +/- changes)
ROW_KEYS = {G_WAREHOUSE: ('kixHbe54',), 'bd5Rj6pN': ('kixHbe54',), 'UT1SVg59': ('j28VNcUW',),
            's8TCo2MS': ('un80kW9Y',), 'yXNM8kL3': None, '51yQrDBR': ('4HqhTf3a',),
            'Y73tHKS8': None, 'Y73mHKS8': None}
COUNT_FIELDS = {G_WAREHOUSE: ('wgV86x1q',)}
# single-row groups whose numbers move by the recorded change (team: exp, energy, orbs...)
TEAM_DELTA_OK = {'d96tuT2E'}       # player exp - the only team number a recorded reward may change
NO_DELTA_TEAM = set(WALLET.values()) | {'D9wXQI2V', 'YnM14RIP', '0P9X1YHs', '9m5FWR8q', 'QYP4kId9', 'ouXxIY63', '5pjoGBC4'}
NEVER_FROM_REPLAY = {G_UNITS, G_DICT, G_USERINFO}
# only these recorded actions may change player exp/energy etc. (home screen refreshes must not)
REWARD_ACTIONS = {'PresentReceiptRequest', 'AchievementRewardReceiveRequest', 'DailyLoginRequest',
                  'DailyTaskClaimRewardRequest', 'SlotActionRequest', 'ArenaBattleEndRequest', 'DungeonKeyReceiptRequest',
                  'EventTokenExchangePurchaseRequest', 'ColosseumBattleEndRequest', 'FrontierGateEndRequest'}
# groups the real server always sends complete (cleared quests, items, item guide, unlocks)
FULL_LIST_GROUPS = {'UT1SVg59', G_WAREHOUSE, 'bd5Rj6pN', 'Y73tHKS8', 'Y73mHKS8', 'yXNM8kL3', '51yQrDBR'}

def _rows(v):
    return v if isinstance(v, list) else [v] if isinstance(v, dict) else []

def _key(g, row):
    ks = ROW_KEYS.get(g, ())
    if ks is None:      # mixed lists (unlocked areas/dungeons/quests): the whole row is the identity
        return tuple(sorted((k, str(v)) for k, v in row.items() if k != F_USERID))
    if not ks:
        ks = tuple(k for k in row if k != F_USERID)[:1]
    return tuple((k, str(row.get(k))) for k in ks)

PERMIT_GROUPS = [g for g, k in ROW_KEYS.items() if k is None]

def repair_permits(pl):
    """Saves from before the fix had unlock lists with area/dungeon/quest entries mixed up."""
    if pl.get('permits_fixed'):
        return
    tmpl = max(BY_ID.get(RID['GetUserInfoRequest'], []), key=lambda a: a['n'], default=None)
    for g in PERMIT_GROUPS:
        rows = list(_rows(pl['state'].get(g))) + (list(BLOBS[tmpl['resp']].get(g) or []) if tmpl else [])
        seen, out = set(), []
        for r in rows:
            if isinstance(r, dict):
                k = _key(g, r)
                if k not in seen:
                    seen.add(k); out.append(r)
        if out:
            pl['state'][g] = out
    pl['permits_fixed'] = 1
    log.info('repaired unlock lists for %s', pl['user_id'])

def _num(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None

# What each recorded group looked like just before each recorded action (per recording).
BEFORE = {}
def _build_before():
    running = {}
    for a in sorted(T['action'], key=lambda a: a['n']):
        rec = a['n'] // 10000
        st = running.setdefault(rec, {})
        resp = BLOBS[a['resp']]
        snap = {}
        for g, v in resp.items():
            if g in STATE_GROUPS or g == G_TEAM:
                snap[g] = {k: dict(r) for k, r in st.get(g, {}).items()}
        BEFORE[a['n']] = snap
        full = a['cls'] == 'GetUserInfoRequest'
        for g, v in resp.items():
            if g in STATE_GROUPS or g == G_TEAM:
                cur = {} if full else st.setdefault(g, {})
                for r in _rows(v):
                    if isinstance(r, dict):
                        cur[_key(g, r)] = r
                st[g] = cur
_build_before()

USER_LEVELS = []
for _b in BLOBS.values():
    if isinstance(_b, dict) and 'YDv9bJ3s' in _b:
        USER_LEVELS = sorted((int(r['D9wXQI2V']), int(r['d96tuT2E']), r) for r in _b['YDv9bJ3s'])
        break

LEVEL_NEED = {L: need for L, need, _r in USER_LEVELS}
LEVEL_ROW = {L: r for L, _n, r in USER_LEVELS}

def _relevel_team(t):
    """Player exp is progress inside the current level (like the real server): level up while it fills
    the next level's requirement. On level up: refill energy, raise max energy and cost."""
    exp, lv = _num(t.get('d96tuT2E')), _num(t.get('D9wXQI2V')) or 1
    if exp is None or not LEVEL_NEED:
        return lv, lv
    old = lv
    while lv + 1 in LEVEL_NEED and LEVEL_NEED[lv + 1] > 0 and exp >= LEVEL_NEED[lv + 1]:
        exp -= LEVEL_NEED[lv + 1]
        lv += 1
    t['d96tuT2E'] = str(exp)
    if lv > old:
        row = LEVEL_ROW[lv]
        t['D9wXQI2V'] = str(lv)
        t['YnM14RIP'] = row['0P9X1YHs']          # max energy
        t['0P9X1YHs'] = row['0P9X1YHs']          # refilled
        t['QYP4kId9'] = row['ziex06DY']          # max cost
        log.info('player level up: %s -> %s', old, lv)
    return old, lv

def fix_progress(pl):
    st = pl['state'].setdefault(G_USERINFO, [{}])[0]
    if not st.get('N4XVE1uA'):
        st['N4XVE1uA'] = CLIENT_PROGRESS_FIELDS['N4XVE1uA']
        log.info('area-cutscene progress was empty - set to the starting value')

def fix_squad(pl):
    t = team_row(pl['state'])
    if t is not None and not pl.get('team_fixed'):
        lv = _num(t.get('D9wXQI2V')) or 1
        row = LEVEL_ROW.get(lv) or {}
        if row.get('oFQ3mbS6'):
            t['3u41PhR2'] = row['oFQ3mbS6']              # max friends for your level
        for k in ('bya9a67k', '22rqpZTo'):               # brave points (daily tasks)
            if k in t:
                t[k] = '0' if isinstance(t[k], str) else 0
        pl['team_fixed'] = 1
    # Raid squads (squad type 3): Raid isn't on this server, but new accounts got the recorded
    # account's raid squads with their starter units in them - and the game won't sell or fuse
    # a unit that's in a raid squad ("Raid Squad is being edited"). Nobody can edit them, so
    # they're dropped.
    party = pl['state'].get(G_PARTY)
    if isinstance(party, list) and any(isinstance(r, dict) and str(r.get(F_PARTY_KIND)) == RAID_SQUAD for r in party):
        pl['state'][G_PARTY] = [r for r in party if not (isinstance(r, dict) and str(r.get(F_PARTY_KIND)) == RAID_SQUAD)]
        log.info('removed the raid squads (they locked units from being sold)')
    if t is not None and not (0 <= (_num(t.get('Z0Y4RoD7')) or 0) <= 9):
        log.info('squad number %s was invalid - back to squad 1', t.get('Z0Y4RoD7'))
        t['Z0Y4RoD7'] = '0' if isinstance(t.get('Z0Y4RoD7'), str) else 0

def fix_exp_model(pl):
    """Saves from before: exp was kept as a running total. Rebuild level + in-level exp from it."""
    if pl.get('exp_model') == 2:
        return
    t = team_row(pl['state'])
    if t is not None and LEVEL_NEED:
        total = _num(t.get('d96tuT2E')) or 0
        t['D9wXQI2V'], t['d96tuT2E'] = '1', str(total)
        _relevel_team(t)
        log.info('player level recalculated from %s exp: level %s', total, t['D9wXQI2V'])
    pl['exp_model'] = 2

def fresh_unit_id(pl):
    nid = max([int(k) for k in pl['units'] if str(k).isdigit()] + [int(pl.get('next_unit_id', 0)), 200000]) + 1
    pl['next_unit_id'] = nid
    return str(nid)

def add_to_dictionary(pl, unit_id):
    d = pl['state'].setdefault(G_DICT, [])
    if not any(r.get('pn16CNah') == unit_id for r in d):
        d.append({F_USERID: pl['user_id'], 'pn16CNah': unit_id, '2pAyFjmZ': '0'})
        return True
    return False

def give_unit(pl, unit_id):
    """ADMIN.bat 'unit <id>': a new level-1 copy of a unit."""
    if unit_id not in unit_mst():
        log.warning('admin: no unit with id %s', unit_id)
        return
    gacha = next((a for a in T['action'] if a['cls'] == 'GachaActionRequest' and G_UNITS_ADD in BLOBS[a['resp']]), None)
    tmpl = personalise(pl, BLOBS[gacha['resp']])[G_UNITS_ADD][0] if gacha else next(iter(pl['units'].values()), {})
    uuid = fresh_unit_id(pl)
    pl['units'][uuid] = new_unit_row(pl, tmpl, unit_id, uuid)
    add_to_dictionary(pl, unit_id)
    log.info('admin: gave unit %s (%s)', unit_mst()[unit_id].get('utP1c0CD'), unit_id)

# --------------------------------------------------------------------------- present box
# Format and reward codes from bfrevive's present box (recorded): 8 gems, 3 zel, 2 karma,
# 6 unit, 4/5/7 item, 12 raid medals. Unclaimed: DbMVG16I 1 / g85qMNxf 0; claimed: 3 / 1.
G_PRESENTS = 'sEA41vFK'
PRESENT_NAMES = {'8': 'gems', '3': 'zel', '2': 'karma', '6': 'unit', '4': 'item', '5': 'item', '7': 'item',
                 '12': 'raid medals'}
LOGIN_CYCLE, LOGIN_GEMS = 30, 15

PRESENT_DAYS, PRESENT_EXPIRED_DAYS = 30, 2      # the game itself shows a gift as expired after 30 days

def present_expired(r, now=None):
    return (now or time.time()) - (_num(r.get('fAi8Th5s')) or 0) > PRESENT_DAYS * 86400

def present_rows(pl):
    """The gift box. A gift is shown as expired for 2 days after its 30 days are up, then removed."""
    now = time.time()
    rows = [r for r in _rows(pl['state'].get(G_PRESENTS)) if isinstance(r, dict)
            and now - (_num(r.get('fAi8Th5s')) or now) <= (PRESENT_DAYS + PRESENT_EXPIRED_DAYS) * 86400]
    pl['state'][G_PRESENTS] = rows
    return rows

def add_present(pl, ptype, target, count, desc, created=None, tag=None, unit_type=None):
    pid = (pl.get('next_present') or 1000) + 1
    pl['next_present'] = pid
    now = created or time.time()
    row = {F_USERID: pl['user_id'], 'S1B82FHK': str(pid), '30Kw4WBa': str(ptype),
           'TdDHf59J': str(target or ''), 'wJsB35iH': str(count), '37moriMq': '', 'i1WQkh4G': '0',
           'fAi8Th5s': str(int(now)),
           'ya7UHG9v': datetime.datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M:%S'),
           '0wmDb9vi': '0', 'vo1GT3Rs': '', 'ZC0msu2L': desc, 'DbMVG16I': '1', 'g85qMNxf': '0'}
    if tag:
        row['srv_gift'] = tag
    if unit_type:
        row['srv_unit_type'] = str(unit_type)        # units from this gift always get this type
    present_rows(pl).append(row)
    log.info('present added: %s x%s %s "%s"', PRESENT_NAMES.get(str(ptype), ptype), count, target or '', desc)

def unit_template(pl):
    gacha = next((a for a in T['action'] if a['cls'] == 'GachaActionRequest' and G_UNITS_ADD in BLOBS[a['resp']]), None)
    return personalise(pl, BLOBS[gacha['resp']])[G_UNITS_ADD][0] if gacha else next(iter(pl['units'].values()), {})

def add_item(pl, item, n):
    """Put n of an item in the item storage (and the item collection)."""
    wh = {r.get('kixHbe54'): r for r in _rows(pl['state'].get(G_WAREHOUSE)) if isinstance(r, dict)}
    row = wh.get(item) or {'n6E8iMf3': str(random.randint(100000, 999999)), 'kixHbe54': item, 'wgV86x1q': '0',
                           'dJNpLc81': '1', 'DbMVG16I': '0'}
    row['wgV86x1q'] = str((_num(row.get('wgV86x1q')) or 0) + n)
    wh[item] = row
    pl['state'][G_WAREHOUSE] = list(wh.values())
    d = pl['state'].setdefault('bd5Rj6pN', [])
    if not any(r.get('kixHbe54') == item for r in d if isinstance(r, dict)):
        d.append({'kixHbe54': item})

def grant(pl, ptype, target, count, out, unit_type=None):
    """Give one reward; out collects what the reply must show (new units, item change)."""
    ptype, count = str(ptype), _num(count) or 0
    if ptype == '8':
        pl['wallet']['gems'] = pl['wallet'].get('gems', 0) + count
    elif ptype == '3':
        pl['wallet']['zel'] = pl['wallet'].get('zel', 0) + count
    elif ptype == '2':
        pl['wallet']['karma'] = pl['wallet'].get('karma', 0) + count
    elif ptype == '6' and str(target) in unit_mst():
        tmpl = out.get('_tmpl') or unit_template(pl)
        out['_tmpl'] = tmpl
        for _ in range(max(1, count)):
            uuid = fresh_unit_id(pl)
            row = new_unit_row(pl, tmpl, str(target), uuid, unit_type)
            pl['units'][uuid] = row
            add_to_dictionary(pl, str(target))
            out.setdefault('units', []).append(row)
    elif ptype in ('4', '5', '7') and target:
        add_item(pl, str(target), max(1, count))
        out['items'] = True
    elif ptype == '12':
        add_medals(pl, count)
        out['medals'] = True
    else:
        return False
    return True

def receive_presents(pl, req):
    """PresentReceipt: o6uWU0Z7 i1WQkh4G 2 = claim everything, 1 = just present S1B82FHK."""
    sel = (req.get('o6uWU0Z7') or [{}])[0]
    every = str(sel.get('i1WQkh4G', '2')) != '1'
    want = str(sel.get('S1B82FHK', ''))
    out, got = {}, []
    now = time.time()
    for r in present_rows(pl):
        if r.get('g85qMNxf') == '1' or not (every or r.get('S1B82FHK') == want):
            continue
        if present_expired(r, now):
            log.info('present %s not claimed - it expired', r.get('S1B82FHK'))
            continue
        utype = r.get('srv_unit_type') or \
            ('1' if str(r.get('ZC0msu2L') or '').startswith('Trial cleared:') else None)   # trial units: Lord
        grant(pl, r.get('30Kw4WBa'), r.get('TdDHf59J'), r.get('wJsB35iH'), out, utype)
        r.update({'g85qMNxf': '1', 'DbMVG16I': '3', '0wmDb9vi': str(int(now)),
                  'vo1GT3Rs': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
        got.append(f"{PRESENT_NAMES.get(r.get('30Kw4WBa'), r.get('30Kw4WBa'))} x{r.get('wJsB35iH')}")
    # keep the box tidy: unclaimed ones plus the 30 most recently claimed
    rows = present_rows(pl)
    done = sorted((r for r in rows if r.get('g85qMNxf') == '1'), key=lambda r: int(r.get('0wmDb9vi') or 0))
    pl['state'][G_PRESENTS] = [r for r in rows if r.get('g85qMNxf') != '1'] + done[-30:]
    log.info('presents claimed: %s', ', '.join(got) or 'nothing')
    t = dict(team_row(pl['state']) or {})
    resp = {G_PRESENTS: present_rows(pl), G_TEAM: [t]}
    if out.get('units'):
        resp[G_UNITS_ADD] = out['units']
        resp[G_DICT] = pl['state'].get(G_DICT, [])
    if out.get('items'):
        resp[G_WAREHOUSE] = pl['state'][G_WAREHOUSE]
        resp['bd5Rj6pN'] = pl['state']['bd5Rj6pN']
    resp[G_MEDALS] = medal_rows(pl)
    return resp

def deliver_server_gifts(pl):
    """Server-wide gifts from the admin page: each player gets each one once, dated when it was made
    (so it expires for everyone at the same time). Withdrawn gifts leave unclaimed gift boxes."""
    try:
        gifts = admin_page().gifts()
    except Exception:
        return
    if not gifts:
        return
    got = pl.setdefault('srv_gifts', [])
    gone = {g['id'] for g in gifts if g.get('deleted')}
    if gone:
        rows = present_rows(pl)
        keep = [r for r in rows if not (r.get('srv_gift') in gone and r.get('g85qMNxf') != '1')]
        if len(keep) != len(rows):
            pl['state'][G_PRESENTS] = keep
    now = time.time()
    for g in gifts:
        if g['id'] in got or g.get('deleted') or now - g['created'] > PRESENT_DAYS * 86400:
            continue
        if str(g['type']) == '6':
            for _ in range(int(g['count'])):
                add_present(pl, 6, g['target'], 1, g['message'], created=g['created'], tag=g['id'])
        else:
            add_present(pl, g['type'], g['target'], g['count'], g['message'], created=g['created'], tag=g['id'])
        got.append(g['id'])

def first_clear_gift(pl, mid):
    add_present(pl, 8, '', 1, 'First clear reward')

LC_DAYS, LC_GEMS = 15, 15
LC_IMG = '/logincampaign/private15_login_icon_day{}.png'
LC_BANNER = '/logincampaign/private15_login_banner.png'

def login_gifts(pl):
    """Once per day (00:00 UTC, like the keys): the Login Campaign moves on a day - 15 days of
    15 gems, then a new campaign starts - and that day's gems go to the present box. Daily raid
    medals too (Brave Slots)."""
    today = _utc_day()
    lc = pl.setdefault('login_campaign', {})
    if not lc and pl.get('login_gift_day') == today:
        # today's gems already came from the earlier login bonus: that counts as campaign day 1
        lc.update({'cycle': 1, 'day': 1, 'seen': today, 'total': 1, 'new': 1})
    if lc.get('seen') == today:
        return
    if not lc.get('cycle') or lc.get('day', 0) >= LC_DAYS:
        lc['cycle'], lc['day'] = (lc.get('cycle') or 0) + 1, 1
    else:
        lc['day'] += 1
    lc['seen'], lc['total'], lc['new'] = today, (lc.get('total') or 0) + 1, 1
    pl['login_gift_day'] = today
    add_present(pl, 8, '', LC_GEMS, f"Login Campaign Day {lc['day']} of {LC_DAYS}")
    add_present(pl, 12, '1', 120, 'Daily Raid Medals')     # like bfrevive: medals for Brave Slots

def login_campaign_groups(pl):
    """The Login Campaign screen: campaign (Bd29Pqw0, in URLMst), its day tiles (bD18x9Ti, in
    URLMst) and this player's progress (3da6bd0a, in GetUserInfo). Tiles before the current day show
    as claimed; the current one is stamped when the player taps Claim (the game remembers that itself,
    per campaign id - so each new 15-day campaign gets a new id)."""
    lc = pl.get('login_campaign') or {'cycle': 1, 'day': 1, 'seen': _utc_day(), 'total': 1}
    cid = str(1000 + lc['cycle'])
    start = (lc['seen'] - (lc['day'] - 1)) * 86400          # so the timer shows the days left
    return {'Bd29Pqw0': [{'H1Dkq93v': cid, 'qA7M9EjP': start, '1adb38d5': str(LC_DAYS), 'b38adb8i': LC_BANNER}],
            'bD18x9Ti': [{'H1Dkq93v': cid, 'n0He37p1': str(d), 'b38adb8i': LC_IMG.format(d)}
                         for d in range(1, LC_DAYS + 1)],
            '3da6bd0a': [{'H1Dkq93v': cid, 'ad6i23pO': lc['day'], '1adb38d5': str(lc.get('total') or 1),
                          '4tswNoV9': 1 if lc.get('new') and lc.get('seen') == _utc_day() else 0}]}

def welcome_gifts(pl):
    """bfrevive's one-time gifts for a new account (tutorial bonus and welcome gems)."""
    if pl.get('welcome_gifts'):
        return
    pl['welcome_gifts'] = 1
    add_present(pl, 8, '', 5, 'Tutorial Complete Bonus')
    add_present(pl, 3, '', 10000, 'Tutorial Complete Bonus')
    add_present(pl, 4, '20000', 3, 'Tutorial Complete Bonus')
    add_present(pl, 8, '', 30, 'Welcome to the private server!')

SPIN_FILE = os.path.join(HERE, 'data', 'login_spin.json')
SPIN = json.load(open(SPIN_FILE)) if os.path.exists(SPIN_FILE) else []
SPIN_DAYS, SPIN_GEM_GROUP, SPIN_PITY = 31, '32', 7

def _spin_group(st):
    """Today's chest set: the day of the 31-day cycle, or the all-gems set after 7 days without a gem."""
    if st.get('since_gem', 0) >= SPIN_PITY - 1:
        return SPIN_GEM_GROUP
    return str((st.get('days', 0) % SPIN_DAYS) + 1)

def _spin_first(group):
    return next((r['id'] for r in SPIN if r['group'] == group), '1')

def login_bonus_info(pl):
    """Drudr2w5 (in URLMst): the daily login chests. 35JXN4Ay spins used today / 5xStG99s allowed,
    ad6i23pO day, u8iD6ka7 days to the guaranteed gem, XIvaD6Jp last prize, outas79f first prize of
    the chest set the game shows next (F_SG_DAILYLOGIN_REWARDS_MST, which the game has itself)."""
    st = pl.setdefault('spin', {})
    done = st.get('day') == _utc_day()
    return {'XIvaD6Jp': str(st.get('last', '1')), '35JXN4Ay': '1' if done or not SPIN else '0', '5xStG99s': '1',
            'ad6i23pO': str((st.get('days', 0) - (1 if done else 0)) % SPIN_DAYS + 1),
            'u8iD6ka7': str(max(0, SPIN_PITY - 1 - max(0, st.get('since_gem', 0) - (1 if done else 0)))),
            'ZC0msu2L': ' day(s) more to guaranteed Gem!', 'outas79f': _spin_first(_spin_group(st))}

def daily_spin(pl):
    """DailyLoginRequest: open today's chest - one prize from today's set, by the game's own rates."""
    st = pl.setdefault('spin', {})
    resp = {G_TEAM: [team_row(pl['state']) or {}]}
    if st.get('day') == _utc_day() or not SPIN:
        resp['Drudr2w5'] = login_bonus_info(pl)
        return resp
    group = _spin_group(st)
    rows = [r for r in SPIN if r['group'] == group]
    x, acc, win = random.uniform(0, sum(r['rate'] for r in rows)), 0.0, rows[-1]
    for r in rows:
        acc += r['rate']
        if x <= acc:
            win = r
            break
    kind = {'11': '2'}.get(win['type'], win['type'])
    out = {}
    if kind == '8000':
        pl['wallet']['summontickets'] = pl['wallet'].get('summontickets', 0) + int(win['count'])
    elif not grant(pl, kind, win['target'], win['count'], out):
        add_present(pl, kind, win['target'], win['count'], 'Daily Login reward')
    st.update({'day': _utc_day(), 'last': win['id'], 'days': st.get('days', 0) + 1,
               'since_gem': 0 if win['type'] == '8' else st.get('since_gem', 0) + 1})
    if out.get('units'):
        resp[G_UNITS_ADD] = out['units']
        resp[G_DICT] = pl['state'].get(G_DICT, [])
    if out.get('items'):
        resp[G_WAREHOUSE] = list(_rows(pl['state'].get(G_WAREHOUSE)))
        resp['bd5Rj6pN'] = list(_rows(pl['state'].get('bd5Rj6pN')))
    resp['Drudr2w5'] = login_bonus_info(pl)
    log.info('daily login chest (set %s): prize %s - type %s %s x%s', group, win['id'], win['type'],
             unit_mst().get(win['target'], {}).get('utP1c0CD', win['target']), win['count'])
    return resp


# --------------------------------------------------------------------------- Brave Slots
# Spin outcomes and their rates come from bfrevive's recorded spins (every SlotAction reply):
# yT3NBME0 1 unit / 2 item or sphere / 5 one medal back, CW1bko4F "id@count", 3WZ7KTp8 rank,
# D20kuSLy the reel pictures. A spin costs 3 medals (slot info b5yeVr61 "1@3").
G_MEDALS, G_SLOT_RESULT = '6C0kzwM5', 's8r5M6wI'
SLOT_COST = 3
SLOT_TABLE = []

def slot_table():
    if not SLOT_TABLE:
        seen = {}
        for a in T['action']:
            if a['cls'] == 'SlotActionRequest':
                for x in BLOBS[a['resp']].get(G_SLOT_RESULT) or []:
                    k = (x['yT3NBME0'], x['CW1bko4F'], x['3WZ7KTp8'], x['D20kuSLy'])
                    seen[k] = seen.get(k, 0) + 1
        SLOT_TABLE.extend((k, n) for k, n in seen.items())
    return SLOT_TABLE

def slot_prizes_page():
    """Brave Slots "View Prizes": every prize the slots can give and how often (the same table the
    spins are drawn from)."""
    import html as _h
    table = slot_table()
    total = sum(n for _, n in table) or 1
    names = admin_page().items if hasattr(admin_page(), 'items') else {}
    rows = []
    for (kind, prize, rank, _reels), n in sorted(table, key=lambda x: -x[1]):
        pid, cnt = (prize.split('@') + ['1'])[:2]
        if kind == '1':
            name, what = unit_mst().get(pid, {}).get('utP1c0CD', pid), 'Unit'
        elif kind == '2':
            name, what = (names.get(pid) or [pid])[0], 'Item'
        else:
            name, what = 'Brave Medal', 'Medal back'
        rows.append(f'<tr><td>{_h.escape(str(name))}{" x" + cnt if cnt not in ("", "1") else ""}</td>'
                    f'<td class="k">{what}</td><td>{n * 100.0 / total:.2f}%</td></tr>')
    body = ('<p class="note">One spin costs 3 Brave Medals.</p><table><tr><th>Prize</th><th>Kind</th>'
            '<th>Chance</th></tr>' + ''.join(rows) + '</table>') if rows else '<p class="note">No prizes yet.</p>'
    return ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, '
            'initial-scale=1"><title>Brave Slots prizes</title><style>body{margin:0;background:#16110b;color:#f3e6c8;'
            'font:15px/1.4 sans-serif;padding:12px}h1{font-size:20px;margin:4px 0 6px;color:#ffd77a;text-align:center}'
            '.note{text-align:center;color:#c9b48a}table{width:100%;border-collapse:collapse;font-size:14px}'
            'th,td{padding:6px 4px;border-bottom:1px solid #3a2c16;text-align:left}th{color:#c9b48a;font-weight:normal;'
            'font-size:12px}td.k{color:#c9b48a;font-size:12px}</style></head><body><h1>Brave Slots prizes</h1>'
            + body + '</body></html>')

def medal_rows(pl):
    rows = [r for r in _rows(pl['state'].get(G_MEDALS) or fresh_value(pl, G_MEDALS)) if isinstance(r, dict)]
    if not rows:
        rows = [{F_USERID: pl['user_id'], '8ZSQ5F2V': '1', 'wgV86x1q': '0', 'KI42FwRt': '0'}]
    rows[0][F_USERID] = pl['user_id']
    pl['state'][G_MEDALS] = rows
    return rows

def add_medals(pl, n):
    r = medal_rows(pl)[0]
    r['wgV86x1q'] = str(max(0, (_num(r.get('wgV86x1q')) or 0) + n))
    return _num(r['wgV86x1q'])

def slot_spin(pl, req):
    sel = (req.get('kLz5ujP2') or [{}])[0]
    spins = max(1, min(10, _num(sel.get('d04gRmkE')) or 1))
    have = _num(medal_rows(pl)[0].get('wgV86x1q')) or 0
    if have < SLOT_COST * spins:
        return None, f'Not enough medals (you have {have}, need {SLOT_COST * spins}).'
    table = slot_table()
    if not table:
        return None, 'Brave Slots has no prize data yet.'
    total = sum(n for _, n in table)
    out, results, won = {}, [], []
    add_medals(pl, -SLOT_COST * spins)
    for _ in range(spins):
        x, acc = random.uniform(0, total), 0
        for k, n in table:
            acc += n
            if x <= acc:
                break
        kind, prize, rank, reels = k
        pid, cnt = (prize.split('@') + ['1'])[:2]
        if kind == '1':
            grant(pl, 6, pid, cnt, out)
        elif kind == '2':
            grant(pl, 4, pid, cnt, out)
        else:
            add_medals(pl, _num(cnt) or 1)
        results.append({'yT3NBME0': kind, 'CW1bko4F': prize, '3WZ7KTp8': rank, 'D20kuSLy': reels, 'HgI9wEJ0': '',
                        '3Vcj8eUn': '0', 'NLbQm24T': '0', 'h6smq0WE': reels})
        won.append(unit_mst().get(pid, {}).get('utP1c0CD', pid) + (f' x{cnt}' if cnt != '1' else '')
                   if kind != '5' else 'medal')
    left = medal_rows(pl)[0]['wgV86x1q']
    for r in results:
        r['A0P7psui'] = left
    log.info('Brave Slots x%s: %s (medals left %s)', spins, ', '.join(won), left)
    resp = {G_SLOT_RESULT: results, G_MEDALS: medal_rows(pl), G_TEAM: [dict(team_row(pl['state']) or {})],
            G_UNITS_ADD: out.get('units', []), G_DICT: pl['state'].get(G_DICT, [])}
    if out.get('items'):
        resp[G_WAREHOUSE] = pl['state'][G_WAREHOUSE]
        resp['bd5Rj6pN'] = pl['state']['bd5Rj6pN']
        resp['VSRPkdId'] = []
    return resp, None

MISSIONS_FILE = os.path.join(HERE, 'data', 'mission_mst.json')
MISSIONS = json.load(open(MISSIONS_FILE, encoding='utf-8')) if os.path.exists(MISSIONS_FILE) else {}
F_AP, F_MAXAP = '0P9X1YHs', 'YnM14RIP'
AP_SECONDS = 180          # one energy back every 3 minutes

def regen_energy(pl):
    t = team_row(pl['state'])
    if t is None:
        return
    now = time.time()
    last = pl.get('ap_time') or now
    ap, mx = _num(t.get(F_AP)) or 0, _num(t.get(F_MAXAP)) or 0
    gained = int((now - last) // AP_SECONDS)
    if ap >= mx:
        pl['ap_time'] = now
        return
    if gained:
        t[F_AP] = str(min(mx, ap + gained))
        pl['ap_time'] = last + gained * AP_SECONDS
    else:
        pl.setdefault('ap_time', now)

def start_mission(pl, req):
    """MissionStart: remember the quest and pay its energy."""
    info = (req.get('9Q1Lq5FS') or [{}])[0]
    mid = str(info.get('j28VNcUW', ''))
    m = MISSIONS.get(mid)
    pl['current_mission'] = mid
    # honor points for the helper picked: what this server offered for them (50 friend / 10 other)
    helper = str(info.get(F_USERID) or '')
    offered = (pl.get('_helpers') or {}).get(helper)
    sent = _num(info.get('J3stQ7jd')) or 0
    pl['_helper_pts'] = (min(offered, sent) if offered is not None else min(50, sent)) if helper else 0
    pl.pop('_continued', None)
    if not m:
        return None
    t = team_row(pl['state'])
    cost = _num(m.get('energy')) or 0
    if mission_closed(mid):
        return 'This quest is closed right now.'
    if str(mid) in EXTRA_FIGHTS and str(mid) not in admin_settings()['on_extra']:
        return 'This quest is not open yet.'
    if is_trial(mid) and not TRIAL_SPECS.get(str(mid)) and not battle_generator().refs.get(mid):
        # no real battle data for this trial yet - better closed than a made-up boss fight
        return 'This trial is not open yet.'
    if is_trial(mid):
        clears = set(cleared_ids(pl))
        missing = [x for x in trial_needs(mid) if x not in clears]
        if missing:
            return 'Clear ' + ', '.join(f'"{(MISSIONS.get(x) or {}).get("name", x)}"' for x in missing) + ' first.'
    if is_trial(mid) and cleared_before(pl, mid):
        cost = 0                     # a cleared trial can be replayed for free (and gives nothing)
    if t is not None and cost:
        ap = _num(t.get(F_AP)) or 0
        if ap < cost:
            return f'Not enough energy (you have {ap}, need {cost}).'
        t[F_AP] = str(ap - cost)
    log.info('quest start: %s "%s" (-%s energy)', mid, m['name'], cost)
    return None

def story_completion(mid):
    """(dungeon, area) that a first clear of this quest completes - the game plays the story scene for
    them. A dungeon is complete with its last quest; an area when the next area needs this quest."""
    if not mid or int(mid) >= 100000:
        return '', '0'
    dun = (MISSIONS.get(mid) or {}).get('dungeon', '')
    last = max((int(k) for k, v in MISSIONS.items() if v.get('dungeon') == dun and int(k) < 100000), default=None)
    dungeon = dun if last is not None and int(mid) == last else ''
    area = '0'
    areas = sorted(int(a) for a in (UNLOCK.get('area') or {}) if int(a) < 100000)
    for i, a in enumerate(areas):
        need = str((UNLOCK.get('area') or {}).get(str(a), ''))
        if i > 0 and mid in [x.strip() for x in need.split(',')]:
            area = str(areas[i - 1])
            break
    return dungeon, area

TRIAL_DUNGEON = '2000000'
TRIAL_REWARDS_FILE = os.path.join(HERE, 'data', 'trial_rewards.json')
TRIAL_REWARDS = json.load(open(TRIAL_REWARDS_FILE, encoding='utf-8')) if os.path.exists(TRIAL_REWARDS_FILE) else {}

# boss fights for the trials, built by tools/build_trials.py from the game's own trial scripts and moves
TRIAL_SPECS_FILE = os.path.join(HERE, 'data', 'trial_specs.json')
TRIAL_SPECS = json.load(open(TRIAL_SPECS_FILE, encoding='utf-8')) if os.path.exists(TRIAL_SPECS_FILE) else {}

def is_trial(mid):
    return (MISSIONS.get(str(mid or '')) or {}).get('dungeon') == TRIAL_DUNGEON

def cleared_ids(pl):
    return [r.get('j28VNcUW') for r in _rows(pl['state'].get('UT1SVg59')) if isinstance(r, dict) and r.get('j28VNcUW')]

def cleared_before(pl, mid):
    return str(mid) in {r.get('j28VNcUW') for r in _rows(pl['state'].get('UT1SVg59')) if isinstance(r, dict)}

def clear_bonus_text(mid):
    """MissionReward 92ms7J5M (the game's "clear bonus" list, "type:id:count,..." with the gift box
    type codes: 8 gems, 6 unit, 4 item): what the first clear put in the gift box, so the result
    screen shows it."""
    gems, out = 1, []                                 # 1 gem: first_clear_gift
    if is_trial(mid):
        rw = TRIAL_REWARDS.get(str(mid)) or {}
        gems += _num(rw.get('gems')) or 0
        for u in rw.get('units') or []:
            out.append(f"6:{u.get('id')}:{_num(u.get('count')) or 1}")
        for it in rw.get('items') or []:
            out.append(f"4:{it.get('id')}:{_num(it.get('count')) or 1}")
    return ','.join([f'8:0:{gems}'] + out)

def trial_first_clear(pl, mid):
    """A trial's one-time reward (data/trial_rewards.json) goes to the gift box."""
    rw = TRIAL_REWARDS.get(str(mid)) or {}
    name = (MISSIONS.get(str(mid)) or {}).get('name', mid)
    if _num(rw.get('gems')):
        add_present(pl, 8, '', _num(rw['gems']), f'Trial cleared: {name}')
    for u in rw.get('units') or []:
        add_present(pl, 6, str(u.get('id')), _num(u.get('count')) or 1, f'Trial cleared: {name}', unit_type=1)
    for it in rw.get('items') or []:
        add_present(pl, 4, str(it.get('id')), _num(it.get('count')) or 1, f'Trial cleared: {name}')

def mission_failed(req):
    """The game marks a lost / abandoned quest with state 3 (a clear is 2)."""
    return str(((req or {}).get(G_MNUM) or [{}])[0].get('j3g5P4cq')) == '3'

def end_mission(pl, resp):
    """MissionEnd: the quest's own clear exp, plus a clear record for this quest."""
    mid = pl.get('current_mission')
    m = MISSIONS.get(mid or '')
    if not m:
        return False
    if mission_failed(REQ_NOW.get('req')):
        # failed: keep the drops and pickups, but no exp, no clear, no first-clear reward
        t = team_row(pl['state']) or {}
        gain = pl.pop('_gain', {}) or {}
        tmplr = dict((_rows(resp.get(G_REWARD)) or [{}])[0])
        tmplr.update({'92ms7J5M': '', 'wPb5uMR9': '0', '81CsGAR9': str(t.get('D9wXQI2V', '1')), 'd96tuT2E': '0',
                      '4sQ8vBXm': '', 'NgPQbA46': '0', 'mauD5qZ1': '0',
                      '3MAT6quo': ','.join(pl.pop('_drops', []) or []), 'Najhr8m6': str(gain.get('zel', 0)),
                      'HTVh8a65': str(gain.get('karma', 0)), 'S2ChOKo6': ''})
        resp[G_REWARD] = [tmplr]
        resp.pop('UT1SVg59', None)
        pl.pop('_continued', None)
        log.info('quest failed: %s "%s" (kept the drops and %s zel picked up)', mid, m['name'], gain.get('zel', 0))
        return True
    t = team_row(pl['state'])
    before, after = 1, 1
    first = mid not in {r.get('j28VNcUW') for r in _rows(pl['state'].get('UT1SVg59')) if isinstance(r, dict)}
    exp_gain = 0 if (is_trial(mid) and not first) else int((_num(m['exp']) or 0) * mult('exp') * boost_mult(mid, 'exp'))
    if t is not None:
        t['d96tuT2E'] = str((_num(t.get('d96tuT2E')) or 0) + exp_gain)
        before, after = _relevel_team(t)
    # the reward summary screen: this quest, these units, this exp - never the recording's
    gain = pl.pop('_gain', {}) or {}
    # tell the game once per quest (the game itself skips scenes the player has already watched)
    sent = pl.setdefault('story_sent', [])
    done = story_completion(mid) if mid not in sent else ('', '0')
    if done != ('', '0'):
        sent.append(mid)
        log.info('  story: dungeon %s / area %s complete', done[0] or '-', done[1] if done[1] != '0' else '-')
        if done[1] and done[1] != '0':
            highlight('area', pl, done[1])
    if after > before:
        highlight('level', pl, before, after)
    tmplr = dict((_rows(resp.get(G_REWARD)) or [{}])[0])
    tmplr.update({'92ms7J5M': clear_bonus_text(mid) if first else '',
                  'wPb5uMR9': '1' if after > before else '0', '81CsGAR9': str(before),
                  'd96tuT2E': str(exp_gain),
                  '4sQ8vBXm': done[0], 'NgPQbA46': done[1],     # dungeon / area finished -> its story scene
                  'mauD5qZ1': mid if first else '0',     # bfrevive names the quest only on a first clear
                  '3MAT6quo': ','.join(pl.pop('_drops', []) or []), 'Najhr8m6': str(gain.get('zel', 0)),
                  'HTVh8a65': str(gain.get('karma', 0)), 'S2ChOKo6': ''})
    resp[G_REWARD] = [tmplr]
    clears = {r.get('j28VNcUW'): r for r in _rows(pl['state'].get('UT1SVg59')) if isinstance(r, dict)}
    tmpl = dict((_rows(resp.get('UT1SVg59')) or [{}])[0])
    row = clears.get(mid) or dict(tmpl, **{F_USERID: pl['user_id'], 'j28VNcUW': mid, 'HIQoGb10': '0'})
    row['JATWnN57'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    row['HIQoGb10'] = str((_num(row.get('HIQoGb10')) or 0) + 1)
    clears[mid] = row
    pl['state']['UT1SVg59'] = list(clears.values())
    resp['UT1SVg59'] = list(clears.values())          # the game expects the full list
    if is_trial(mid) and not first:
        log.info('quest clear: %s "%s" - trial already cleared, no rewards', mid, m['name'])
    else:
      log.info('quest clear: %s "%s" (+%s exp, +%s zel, +%s karma)%s', mid, m['name'], exp_gain, gain.get('zel', 0),
               gain.get('karma', 0),
             ' - first clear' if first else '')
    if first:
        first_clear_gift(pl, mid)
        if is_trial(mid):
            trial_first_clear(pl, mid)
            highlight('trial', pl, mid)
    return True

def record_quest_stats(pl, req):
    if trial_replay(pl, pl.get('current_mission')):
        return                        # replaying a cleared trial counts for no task or achievement
    try:
        achievement_engine().record_quest(pl, req)
    except Exception:
        log.exception('could not count battle stats')
    try:
        if not mission_failed(req) and pl.get('current_mission'):
            daily_engine().event(pl, 'quest', mid=str(pl['current_mission']))
    except Exception:
        log.exception('could not count daily tasks')

# --------------------------------------------------------------------------- quest battles
BATTLES_FILE = os.path.join(HERE, 'data', 'battles.json')
BATTLES = json.load(open(BATTLES_FILE, encoding='utf-8')) if os.path.exists(BATTLES_FILE) else {}
G_BGROUP, G_BMON, G_MON, G_AI, G_MNUM, G_MSTART = 'pj41dy9g', '75t0sx9z', 'U0v5IeJo', '89ausgc4', 'Kz7qfSs5', '9Q1Lq5FS'
LAYOUT = {1: ['180:302'], 2: ['180:302', '120:248'], 3: ['180:302', '120:248', '96:352'],
          4: ['160:240', '215:300', '160:360', '105:300'], 5: ['215:250', '215:350', '160:300', '105:240', '105:360']}
REC_START = {}          # mission id -> recorded MissionStart (exact battles)
MON_TMPL, AI_ROWS = None, {}
for _a in sorted(T['action'], key=lambda a: a['n']):
    if _a['cls'] == 'MissionStartRequest':
        _r = BLOBS[_a['resp']]
        _mid = str(((_a['req'].get(G_MSTART) or [{}])[0]).get('j28VNcUW', ''))
        REC_START.setdefault(_mid, _a)
        for _row in _r.get(G_AI, []):
            AI_ROWS.setdefault(_row['4eEVw5hL'], [])
            if _row not in AI_ROWS[_row['4eEVw5hL']]:
                AI_ROWS[_row['4eEVw5hL']].append(_row)
        if MON_TMPL is None and _r.get(G_MON):
            MON_TMPL = _r[G_MON][0]

# quests fetched from bfrevive with probe_bfrevive.py (exact, like a recording)
QUEST_REC_DIR = os.path.join(HERE, 'quest_recordings')
QUEST_REC = {}
if os.path.isdir(QUEST_REC_DIR):
    for _f in os.listdir(QUEST_REC_DIR):
        if _f.endswith('.json'):
            try:
                _d = json.load(open(os.path.join(QUEST_REC_DIR, _f), encoding='utf-8'))
                QUEST_REC[str(_d['mission'])] = _d['response']
            except Exception:
                pass

def _monster_row(mon_id, unit_id, name, hp, atk, df, ai='1', act=(1, 1), zel=None, karma=None, el=None):
    m = unit_mst().get(unit_id, {})
    row = dict(MON_TMPL or {})
    zel = int(zel if zel is not None else min(3000, hp * 0.04))
    karma = int(karma if karma is not None else zel * 0.63)
    row.update({'o49dYfpH': mon_id, '3evIn0zZ': name or m.get('utP1c0CD', ''), 'e7DK0FQT': str(int(hp)),
                '67CApcti': str(int(atk)), 'q08xLEsy': str(int(df)), 'iNy0ZU5M': str(el or m.get('iNy0ZU5M', '1')),
                'eyUo6a8c': m.get('eyUo6a8c', row.get('eyUo6a8c')), '6Aou5M9r': m.get('6Aou5M9r', row.get('6Aou5M9r')),
                'n9h7p02P': m.get('n9h7p02P', '2'), 'pn16CNah': unit_id,
                'J2hPXGo5': m.get('J2hPXGo5', '2'), 'mv4o39Uz': m.get('mv4o39Uz', '1'), 'h2L1YI90': m.get('h2L1YI90', '1'),
                'XE7Yi5c3': m.get('XE7Yi5c3', '1'), 'D4Y5bWK7': m.get('D4Y5bWK7', '1'), 'i74vGUFa': ai if ai in AI_ROWS else '1',
                'oMGC3hW0': str(act[1]), 'm4EK7Gt6': str(act[0]), 'F4bQ7r8C': '', 'Najhr8m6': str(zel), 'HTVh8a65': str(karma)})
    return row

import arena as _arena
import achievements as _achievements
import friends as _friends
import dailytasks as _dailytasks
_ARENA = _ACH = _FRIENDS = _DAILY = None

def friend_engine():
    global _FRIENDS
    if _FRIENDS is None:
        _FRIENDS = _friends.Friends(sys.modules[__name__])
    return _FRIENDS

def achievement_engine():
    global _ACH
    if _ACH is None:
        _ACH = _achievements.Achievements(sys.modules[__name__])
    return _ACH

def add_team_counts(req, resp):
    """UserTeamInfo counts the home screen shows as red badges: gifts waiting in the present box
    (EfinBo65) and friend requests waiting for an answer (qVBx7g2c)."""
    rows = resp.get(G_TEAM) if isinstance(resp, dict) else None
    rows = [r for r in _rows(rows) if isinstance(r, dict)]
    if not rows:
        return
    with LOCK:
        pl = find_player(req)
        if not pl:
            return
        now = time.time()
        gifts = sum(1 for r in present_rows(pl) if r.get('g85qMNxf') != '1' and not present_expired(r, now))
        asks = friend_engine().waiting(pl)
    for r in rows:
        if str(r.get(F_USERID) or pl['user_id']) != pl['user_id']:
            continue
        r['EfinBo65'] = str(gifts) if isinstance(r.get('EfinBo65', ''), str) else gifts
        r['qVBx7g2c'] = str(asks) if isinstance(r.get('qVBx7g2c', ''), str) else asks

def fix_badges(req, resp):
    """The Records badges (which sections have rewards waiting) - worked out for this player."""
    try:
        with LOCK:
            pl = find_player(req)
            if pl:
                b = dict(resp['h23iRjGN'])
                b['dUujjBBK'] = achievement_engine().badges(pl)
                resp = dict(resp, h23iRjGN=b)
    except Exception:
        log.exception('could not work out the badges')
    return resp

import admin_web as _admin_web
_ADMIN = None
ADMIN_SETTINGS_FILE = os.path.join(SAVE_DIR, 'admin_settings.json')
_ADMIN_SETTINGS = {'mtime': None, 'data': None}
DEFAULT_SETTINGS = {'mult': {'exp': 1.0, 'zel': 1.0, 'karma': 1.0, 'drop': 1.0, 'item': 1.0},
                    'off': {'areas': [], 'vortex': [], 'banners': [], 'units': [], 'trials': []},
                    'schedule': {},
                    'boost': {},
                    'gold_names': [],
                    'sell': {},
                    'fusion': {'great': {'on': 0, 'chance': 12.0, 'mult': 1.5},
                               'super': {'on': 0, 'chance': 3.0, 'mult': 2.0}, 'label': 'none'},
                    'features': {'frontierhunter': 0, 'guild': 0, 'raid': 0},
                    'on_extra': []}         # off-by-default extras switched on (JP trials, Brave Dances)

# Game areas the admin page can switch off (the game's own featureCheck switches). What the game does
# with each switch off was read from its code.
GAME_FEATURES = [
    ('frontierhunter', 'Akras Summoners\' Hall Survey Office (Frontier Hunter)', 'Randall',
     'Not drawn on the Randall map at all - nothing to tap.'),
    ('guild', 'Guild', 'Home screen',
     'Stays in the home screen list with its locked picture, and cannot be tapped.'),
    ('raid', 'Raid Battle', 'Home screen',
     'Stays in the home screen list with its closed picture, and cannot be tapped.'),
]

def admin_page():
    global _ADMIN
    if _ADMIN is None:
        _ADMIN = _admin_web.Admin(sys.modules[__name__])
    return _ADMIN

def admin_settings():
    """Rates and switches from the admin page (saves/admin_settings.json), re-read when it changes."""
    try:
        mt = os.path.getmtime(ADMIN_SETTINGS_FILE)
    except OSError:
        mt = None
    if _ADMIN_SETTINGS['data'] is None or mt != _ADMIN_SETTINGS['mtime']:
        d = json.loads(json.dumps(DEFAULT_SETTINGS))
        if mt is not None:
            try:
                f = json.load(open(ADMIN_SETTINGS_FILE, encoding='utf-8'))
                d['mult'].update({k: float(v) for k, v in (f.get('mult') or {}).items() if k in d['mult']})
                d['off'].update({k: [str(x) for x in v] for k, v in (f.get('off') or {}).items() if k in d['off']})
                d['schedule'] = {str(k): v for k, v in (f.get('schedule') or {}).items() if isinstance(v, dict)}
                d['boost'] = {str(k): v for k, v in (f.get('boost') or {}).items() if isinstance(v, dict)}
                d['gold_names'] = [str(x) for x in (f.get('gold_names') or [])]
                if (f.get('fusion') or {}).get('label') in ('none', 'great', 'super'):
                    d['fusion']['label'] = f['fusion']['label']
                for kind, v in ((f.get('fusion') or {}).items()):
                    if kind in ('great', 'super') and isinstance(v, dict):
                        d['fusion'][kind].update({'on': 1 if v.get('on') else 0,
                                                  'chance': float(v.get('chance', d['fusion'][kind]['chance'])),
                                                  'mult': float(v.get('mult', d['fusion'][kind]['mult']))})
                d['sell'] = {str(k): int(v) for k, v in (f.get('sell') or {}).items()
                             if str(v).lstrip('-').isdigit()}
                d['features'].update({str(k): 1 if v else 0 for k, v in (f.get('features') or {}).items()
                                      if str(k) in d['features']})
                d['on_extra'] = [str(x) for x in (f.get('on_extra') or [])]
            except Exception:
                log.exception('admin_settings.json could not be read - using normal rates')
        _ADMIN_SETTINGS.update(mtime=mt, data=d)
        battlegen.SETTINGS.update(drop=d['mult']['drop'], item=d['mult']['item'], off_units=set(d['off']['units']),
                                  unit_drop=dict(d.get('unit_drop') or {}))
    return _ADMIN_SETTINGS['data']

def save_admin_settings(d):
    tmp = ADMIN_SETTINGS_FILE + '.tmp'
    json.dump(d, open(tmp, 'w', encoding='utf-8'), indent=1)
    os.replace(tmp, ADMIN_SETTINGS_FILE)
    admin_settings()

MISSION_ALIAS = {}          # custom Vortex quest -> the quest it copies (vortexedit.py)
DUNGEON_OVERRIDE = {}       # dungeon -> [tab, land] for moved / new Vortex banners
_VORTEX = None

_SUMMONS = None

def summon_editor():
    """Summons as the admin page set them up (summons.py)."""
    global _SUMMONS
    if _SUMMONS is None:
        import summons
        _SUMMONS = summons.Summons(sys.modules[__name__])
    return _SUMMONS

def patch_summons(resp):
    try:
        return summon_editor().patch_response(resp)
    except Exception:
        log.exception('could not apply the summon settings')
        return resp

def feature_check(v):
    """featureCheck.php: the Summon screen is one list to swipe through - no tile menu."""
    v = json.loads(json.dumps(v))
    feats = admin_settings().get('features', {})
    for f in v if isinstance(v, list) else [v]:
        if isinstance(f, dict) and 'gacha_category' in f:
            f['gacha_category'] = 0
        if isinstance(f, dict):
            for k, on in feats.items():      # areas switched off on the admin page
                if k in f:
                    f[k] = 1 if on else 0
    return v

_EVOGATE = None
_NEWS = None

def news_engine():
    """The News page (see newsweb.py)."""
    global _NEWS
    if _NEWS is None:
        import newsweb
        _NEWS = newsweb.News(sys.modules[__name__])
    return _NEWS
_SELLPRICE = None

def sell_price():
    """Unit sell prices set on the admin page (see sellprice.py)."""
    global _SELLPRICE
    if _SELLPRICE is None:
        import sellprice
        _SELLPRICE = sellprice.SellPrice(sys.modules[__name__])
    return _SELLPRICE

def evo_gate():
    """Evolutions into units switched off on the admin page (see evogate.py)."""
    global _EVOGATE
    if _EVOGATE is None:
        import evogate
        _EVOGATE = evogate.EvoGate(sys.modules[__name__])
    return _EVOGATE

def vortex_editor():
    global _VORTEX
    if _VORTEX is None:
        import vortexedit
        _VORTEX = vortexedit.VortexEditor(sys.modules[__name__])
        _VORTEX.apply_to_server()
    return _VORTEX

def mission_alias(mid):
    return MISSION_ALIAS.get(str(mid), str(mid))

_GEO = None
def mission_place(mid):
    """(area id, land id) of a quest, from the game's dungeon data."""
    global _GEO
    if _GEO is None:
        p = os.path.join(HERE, 'data', 'dungeon_area.json')
        _GEO = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {'dungeon': {}, 'area': {}}
    m = MISSIONS.get(str(mid)) or {}
    d = str(m.get('dungeon', ''))
    a = DUNGEON_OVERRIDE.get(d) or _GEO['dungeon'].get(d)
    return (a[0], a[1]) if a else ('', '99' if (_num(mid) or 0) >= 100000 else '')

def dungeon_area(did):
    mission_place('0')
    a = DUNGEON_OVERRIDE.get(str(did)) or _GEO['dungeon'].get(str(did))
    return a[0] if a else ''

def dungeon_closed(did):
    if str(did) == TRIAL_DUNGEON:
        return False
    return (str(did) in admin_settings()['off'].get('banners', []) or area_closed(dungeon_area(did))
            or schedule_closed(did))

# --------------------------------------------------------------------------- banner days
# Day-of-the-week banners (the game's weekday dungeons, found by their monday/tuesday/... pictures)
# and any schedule set on the admin page: settings 'schedule' {banner: {mode, days, until}}
#   mode 'days'   open only on those days (0 = Monday ... 6 = Sunday, server PC's clock)
#        'always' open every day        'off' closed
#   until        open every day until then (seconds), whatever the mode - then back to normal
DAY_WORDS = (('monday', [0]), ('tuesday', [1]), ('wednesday', [2]), ('thursday', [3]), ('friday', [4]),
             ('saturday', [5]), ('sunday', [6]), ('weekend', [5, 6]))
_DEFAULT_DAYS = None

def default_banner_days():
    """{banner: [days]} for the game's own weekday banners."""
    global _DEFAULT_DAYS
    if _DEFAULT_DAYS is None:
        out = {}
        try:
            for r in vortex_editor().M.base('F_DUNGEON_MST'):
                img = str(r.get('21VKZo0E') or '').lower()
                if str(r.get('9C64Qwe0')) != '99' or not img.startswith('sp_quest_banner_'):
                    continue
                word = img[len('sp_quest_banner_'):].split('.')[0].rstrip('0123456789_')
                for w, days in DAY_WORDS:
                    if word == w:
                        out[str(r['MHx05sXt'])] = days
        except Exception:
            log.exception('could not work out the weekday banners')
        _DEFAULT_DAYS = out
    return _DEFAULT_DAYS

def banner_schedule(did):
    did = str(did)
    sc = admin_settings().get('schedule', {}).get(did)
    if sc:
        return dict(sc, default=False)
    days = default_banner_days().get(did)
    return {'mode': 'days', 'days': days, 'until': 0, 'default': True} if days else None

def schedule_closed(did, now=None):
    sc = banner_schedule(did)
    if not sc:
        return False
    now = now or time.time()
    if (sc.get('until') or 0) > now:
        return False
    if sc.get('mode') == 'off':
        return True
    if sc.get('mode') == 'days':
        return time.localtime(now).tm_wday not in (sc.get('days') or [])
    return False

_AREA_PARENT = None

def area_parent(area):
    """The Vortex tab a sub-tab sits in (area sHMai2ZG), or ''."""
    global _AREA_PARENT
    if _AREA_PARENT is None:
        try:
            _AREA_PARENT = {str(r['VjCY7rX4']): str(r.get('sHMai2ZG') or '') for r in vortex_editor().M.base('F_AREA_MST')}
        except Exception:
            _AREA_PARENT = {}
    p = _AREA_PARENT.get(str(area), '')
    return '' if p in ('0', 'None') else p

def area_closed(area):
    off = admin_settings()['off']
    if not area or str(area) == TRIAL_DUNGEON:
        return False             # the Research Lab: its trials are switched one by one (Trials section)
    if area in off['areas'] or area in off['vortex']:
        return True
    par = area_parent(area)                 # a sub-tab inside a closed tab is closed with it
    return bool(par) and par != area and par in off['vortex']

def trial_closed(mid):
    """A trial switched off on the admin page (or a Japan-only one) - not offered, cannot be started."""
    mid = str(mid)
    if mid in EXTRA_FIGHTS:
        return mid not in admin_settings()['on_extra']
    return is_trial(mid) and mid in admin_settings()['off'].get('trials', [])

# Off until switched on (admin page, Trials section) - so they can be worked on before release:
# the JP-only trials and the Brave Dance hall (its fights show in the Research Lab when on).
JP_TRIALS = ['2000012', '2000013', '2000014', '2000015']
BRAVE_DANCES = ['2100000', '2100001', '2100002', '2100003', '2100004', '2100005', '2100006', '2100007',
                '82100001', '82100002']
EXTRA_FIGHTS = set(JP_TRIALS) | set(BRAVE_DANCES)

def extra_on(mid):
    return str(mid) in admin_settings()['on_extra']

# the trials in the order Global released them (the order the admin page lists them)
TRIAL_ORDER = ['2000000', '2000001', '2000002', '2000003', '2000005', '8360021', '8300031', '2000004', '2000006',
               '8360041', '2000008', '2000007', '2000009', '2000011', '2000010', '8360051']

def trial_overview():
    """Every trial for the admin page: on/off, who it pits you against, what the first clear gives."""
    off = set(admin_settings()['off'].get('trials', []))
    ids = [m for m in TRIAL_ORDER if m in MISSIONS] + sorted(
        (m for m in MISSIONS if is_trial(m) and m not in TRIAL_ORDER and m not in EXTRA_FIGHTS), key=int) + \
        [m for m in JP_TRIALS + BRAVE_DANCES if m in MISSIONS]
    need = {}
    try:
        need = {r['j28VNcUW']: r.get('HSRhkf70', '') for r in vortex_editor().tables()[2]}
    except Exception:
        pass
    cleared = collections.Counter()
    try:
        for p in all_players():
            for r in _rows((p.get('state') or {}).get('UT1SVg59')):
                if isinstance(r, dict) and is_trial(r.get('j28VNcUW')):
                    cleared[r['j28VNcUW']] += 1
    except Exception:
        pass
    out = []
    for mid in ids:
        spec = TRIAL_SPECS.get(mid) or recorded_spec(mid) or {}
        bosses = []
        for m in spec.get('monsters') or []:
            if m.get('3evIn0zZ') and m['3evIn0zZ'] not in bosses:
                bosses.append(m['3evIn0zZ'])
        rw = TRIAL_REWARDS.get(mid) or {}
        gift = ([f"{rw['gems']} gem{'s' if _num(rw['gems']) != 1 else ''}"] if _num(rw.get('gems')) else []) + \
               [unit_mst().get(str(u.get('id')), {}).get('utP1c0CD', str(u.get('id'))) for u in rw.get('units') or []]
        m = MISSIONS[mid]
        extra = mid in EXTRA_FIGHTS
        out.append({'id': mid, 'name': m.get('name', mid), 'on': extra_on(mid) if extra else mid not in off,
                    'extra': ('JP-only trial' if mid in JP_TRIALS else 'Brave Dance hall') if extra else '',
                    'ready': bool(spec) or bool(battle_generator().refs.get(mid)),
                    'source': {'bfrevive': 'bfrevive recording', 'built': 'built from the game files'}.get(spec.get('src'), '-'),
                    'battles': spec.get('battles') or _num(m.get('battles')) or 1, 'bosses': bosses,
                    'first_clear': gift + [f"{_num(m.get('exp')) or 0:,} exp", f"{_num(m.get('zel')) or 0:,} zel"],
                    'needs': [MISSIONS.get(x.strip(), {}).get('name', x.strip()) for x in str(need.get(mid, '')).split(',')
                              if x.strip() not in ('', '0')],
                    'cleared_by': cleared.get(mid, 0)})
    return out

def trial_needs(mid):
    """The quests that must be cleared before this trial opens - the game's own rule (quest table
    HSRhkf70), as served (a switched-off trial hands its requirement on to the next one)."""
    try:
        _, _, rows = vortex_editor().tables()
    except Exception:
        return []
    r = next((r for r in rows if r.get('j28VNcUW') == str(mid)), None)
    return [x.strip() for x in str((r or {}).get('HSRhkf70') or '').split(',') if x.strip() not in ('', '0')]

def trial_replay(pl, mid):
    """A trial this account has already cleared: it can be fought again, but gives nothing."""
    return is_trial(mid) and cleared_before(pl, mid)

def mission_closed(mid):
    if is_trial(mid):
        return trial_closed(mid)     # trials: only their own switch counts
    d = (MISSIONS.get(str(mid)) or {}).get('dungeon')
    return area_closed(mission_place(mid)[0]) or (d is not None and (str(d) in admin_settings()['off'].get('banners', [])
                                                                    or schedule_closed(d)))

def mult(k):
    return admin_settings()['mult'].get(k, 1.0)

# Admin accounts (admin page > Players > Profile > "Gold name"): their name reaches OTHER players
# in the game's colour markup, so friend lists, helpers and the arena show it in gold. The
# account's own name (home screen, story) stays plain - it is never sent to itself this way.
GOLD = '255:215:0'

def add_display_name(resp):
    """Your own UserInfo row: LZ2PfNm4 (the game's transfer-code account ID, unused here) carries the
    name the app from v13 shows for you in rankings and arena screens - gold for admin accounts."""
    rows = resp.get(G_USERINFO) if isinstance(resp, dict) else None
    for row in (rows if isinstance(rows, list) else [rows] if isinstance(rows, dict) else []):
        if isinstance(row, dict) and row.get(F_HANDLE):
            row['LZ2PfNm4'] = shown_name({'user_id': row.get(F_USERID), 'handle': row[F_HANDLE]})
    return resp

def shown_name(p):
    name = p.get('handle', 'Summoner') or 'Summoner'
    if str(p.get('user_id')) in (admin_settings().get('gold_names') or []):
        return f'<color={GOLD}>{name}</color>'
    return name

# --------------------------------------------------------------------------- Vortex reward boosts
# The game's own quest bonus ("XP Received x2" on a Vortex banner): a dungeon row in the Vortex
# permit list carries the bonus type (nA95Bdj6), its rate (5Z1LNoyH) and the seconds it has left
# (5ry19G4Y). Types from the game's code and its campaign_text.png: 2 XP, 3 Zel ("Gold Received"),
# 4 Karma. The game shows one bonus per banner. In battle the game itself multiplies the zel /
# karma monsters drop (MissionNum row of MissionStart); the server multiplies the clear rewards.
BOOST_TYPES = {'exp': 2, 'zel': 3, 'karma': 4}
BOOST_FIELDS = ('nA95Bdj6', '5Z1LNoyH', '5ry19G4Y')
BOOST_NO_END = 30 * 86400            # a boost with no end date shows 30 days left, renewed each visit

def active_boost(did):
    b = (admin_settings().get('boost') or {}).get(str(did or ''))
    if not b or b.get('type') not in BOOST_TYPES:
        return None
    try:
        rate = float(b.get('rate') or 1)
    except (TypeError, ValueError):
        return None
    until = int(b.get('until') or 0)
    if rate <= 1 or (until and until <= time.time()):
        return None
    return {'type': b['type'], 'rate': rate, 'until': until}

def _rate_text(r):
    return str(int(r)) if float(r).is_integer() else f'{r:g}'

def boost_fields(b):
    left = int(b['until'] - time.time()) if b['until'] else BOOST_NO_END
    return {'nA95Bdj6': str(BOOST_TYPES[b['type']]), '5Z1LNoyH': _rate_text(b['rate']), '5ry19G4Y': str(max(1, left))}

def mission_boost(mid):
    return active_boost((MISSIONS.get(str(mid or '')) or {}).get('dungeon'))

def boost_mult(mid, kind):
    b = mission_boost(mid)
    return b['rate'] if b and b['type'] == kind else 1.0

def add_boosts(rows):
    """Vortex permit rows: the bonus on each boosted dungeon row (and none left on the others)."""
    out = []
    for r in rows:
        if isinstance(r, dict) and any(k in r for k in BOOST_FIELDS):
            r = {k: v for k, v in r.items() if k not in BOOST_FIELDS}
        if isinstance(r, dict) and r.get('MHx05sXt') and not r.get('j28VNcUW'):
            b = active_boost(r['MHx05sXt'])
            if b:
                r = dict(r, **boost_fields(b))
        out.append(r)
    return out

def daily_engine():
    global _DAILY
    if _DAILY is None:
        _DAILY = _dailytasks.DailyTasks(sys.modules[__name__])
    return _DAILY

def arena_engine():
    global _ARENA
    if _ARENA is None:
        _ARENA = _arena.Arena(sys.modules[__name__])
    return _ARENA

# every quest start is generated fresh from references (recordings, datamine, wiki) - see battlegen.py
import battlegen
SKILL_ROWS = {}
_RECORDED = {}
for _a in sorted(T['action'], key=lambda a: a['n']):
    if _a['cls'] == 'MissionStartRequest':
        _mid = str(((_a['req'].get(G_MSTART) or [{}])[0]).get('j28VNcUW', ''))
        _RECORDED.setdefault(_mid, []).append(BLOBS[_a['resp']])
for _mid, _r in QUEST_REC.items():
    _RECORDED.setdefault(_mid, []).append(_r)
for _rs in _RECORDED.values():
    for _r in _rs:
        for _row in _r.get(G_AI, []):
            AI_ROWS.setdefault(_row['4eEVw5hL'], [])
            if _row not in AI_ROWS[_row['4eEVw5hL']]:
                AI_ROWS[_row['4eEVw5hL']].append(_row)
        for _row in _r.get('8aiBoHg5', []) or []:
            SKILL_ROWS.setdefault(_row.get('nj9Lw7mV'), _row)
# Monster-only enemies (no unit behind them, e.g. Creator Maxwell, Juggernaut) need their animation
# list (8hoyIF9Q) sent with the quest, keyed by the monster's id - without it the game crashes as
# the enemy appears. Indexed by the monster's own animation file, from every recording.
MON_CGS = {}
for _rs in _RECORDED.values():
    for _r in _rs:
        _by_id = {}
        for _c in _r.get('8hoyIF9Q', []) or []:
            if isinstance(_c, dict):
                _by_id.setdefault(_c.get('o49dYfpH'), []).append(_c)
        for _m in _r.get(G_MON, []) or []:
            _k = _m.get('QqfI9mM4') or _m.get('2EF0d6ue')
            if _k and _m.get('o49dYfpH') in _by_id and _k not in MON_CGS:
                MON_CGS[_k] = [{'fpc3rbs7': c.get('fpc3rbs7'), '0QxL2is7': c.get('0QxL2is7')} for c in _by_id[_m['o49dYfpH']]]
_GEN = None
MIMIC_ROW = next((m for _rs in _RECORDED.values() for _r in _rs for m in _r.get(G_MON, [])
                  if m.get('pn16CNah') == '60142'), None)

def battle_generator():
    global _GEN
    if _GEN is None:
        _GEN = battlegen.Battlegen(_RECORDED, BATTLES, BATTLES, MISSIONS, unit_mst(), log)
    return _GEN

def trial_mission_start(mid, req, base, spec):
    """A trial fight: the bosses (all forms and summons), their moves and AI, exactly as built."""
    n = spec['battles']
    resp = dict(base)
    resp[G_BGROUP] = [{'ZSf8e1MG': str(bi), 'Qzhp8B40': str(bi), 'j28VNcUW': mid, 'VETu07N6': str(bi),
                       '5aetPz3C': '0', 'etM5TCb9': '1' if bi == n else '0'} for bi in range(1, n + 1)]
    resp[G_BMON] = [dict(r) for r in spec['groups']]
    resp[G_MON] = []
    for r in spec['monsters']:          # this run's numbers: within a few % of the built ones
        r = dict(r)
        for f in ('e7DK0FQT', '67CApcti', 'q08xLEsy'):
            v = _num(r.get(f)) or 0
            if v > 1000:
                r[f] = str(int(v * random.uniform(.97, 1.03)))
        resp[G_MON].append(r)
    if spec.get('cgs'):
        resp['8hoyIF9Q'] = [dict(r) for r in spec['cgs']]       # monster-only sprites (Juggernaut...)
    resp[G_AI] = list(AI_ROWS.get('1', [])) + [dict(r) for r in spec['ai']]
    resp['8aiBoHg5'] = [dict(r) for r in spec['skills']]
    num = dict((base.get(G_MNUM) or [{}])[0])
    num.update({'k9cxD7Ba': str(random.randint(1000000000, 1999999999)),
                'LE6JkUp7': '@'.join(f'{bi}| | ' for bi in range(1, n + 1))})
    resp[G_MNUM] = [num]
    info = dict(((req.get(G_MSTART) or [{}])[0]))
    resp[G_MSTART] = [{k: info.get(k, v) for k, v in ((base.get(G_MSTART) or [{}])[0]).items()} | {'j28VNcUW': mid}]
    log.info('trial battle for %s: %s', mid, ' / '.join(','.join(b) for b in spec['names']))
    return resp

# fights fetched from bfrevive that are played exactly as recorded (bosses with several forms,
# fixed line-ups): trials and the Brave Dance hall, when we have no hand-built spec for them
EXACT_DUNGEONS = {TRIAL_DUNGEON, '2100000'}

def recorded_spec(mid):
    r = QUEST_REC.get(str(mid))
    if not r or (MISSIONS.get(str(mid)) or {}).get('dungeon') not in EXACT_DUNGEONS:
        return None
    order = {g['Qzhp8B40']: str(int(_num(g.get('VETu07N6')) or 0)) for g in r.get(G_BGROUP) or []}
    if not order:
        return None
    groups = [dict(b, Qzhp8B40=order[b['Qzhp8B40']]) for b in r.get(G_BMON) or [] if b.get('Qzhp8B40') in order]
    mons = {m['o49dYfpH']: m for m in r.get(G_MON) or []}
    used = {b['o49dYfpH'] for b in groups}
    names = [[mons[b['o49dYfpH']].get('3evIn0zZ', '?') for b in groups
              if b['Qzhp8B40'] == str(i) and b['o49dYfpH'] in mons] for i in range(1, len(order) + 1)]
    return {'battles': len(order), 'groups': groups, 'monsters': [m for k, m in mons.items() if k in used],
            'ai': [a for a in r.get(G_AI) or [] if a.get('4eEVw5hL') != '1'],
            'skills': r.get('8aiBoHg5') or [], 'cgs': r.get('8hoyIF9Q') or [], 'names': names, 'src': 'bfrevive'}

def generate_mission_start(pl, mid, req, base):
    spec = TRIAL_SPECS.get(str(mid)) or recorded_spec(mid)
    if spec and not str(mid).startswith('_'):
        return trial_mission_start(str(mid), req, base, spec)
    plan = battle_generator().plan(mission_alias(mid))
    groups, bmon, mons = [], [], []
    drops = []
    seq = [0]

    def row_for(m):
        seq[0] += 1
        mon_id = str(8000000 + seq[0])
        if m.get('row'):           # a referenced enemy: its look, AI and skills, with this run's numbers
            row = dict(m['row'])
            row.update({'o49dYfpH': mon_id, 'e7DK0FQT': str(int(m['hp'])), '67CApcti': str(int(m['atk'])),
                        'q08xLEsy': str(int(m['def'])), 'Najhr8m6': str(int(m.get('zel') or 0)),
                        'HTVh8a65': str(int(m.get('karma') or 0))})
        else:
            row = _monster_row(mon_id, m['u'], m.get('name'), m['hp'], m['atk'], m['def'], m.get('ai', '1'),
                               tuple(m.get('act') or (1, 1)), m.get('zel'), m.get('karma'), m.get('el'))
        mons.append(row)
        return mon_id

    n = len(plan['battles'])
    for bi, b in enumerate(plan['battles'], 1):
        groups.append({'ZSf8e1MG': str(bi), 'Qzhp8B40': str(bi), 'j28VNcUW': mid, 'VETu07N6': str(bi),
                       '5aetPz3C': '0', 'etM5TCb9': '1' if bi == n else '0'})
        lay = LAYOUT.get(len(b['mons']), LAYOUT[5])
        for pos, m in enumerate(b['mons']):
            bmon.append({'Qzhp8B40': str(bi), 'o49dYfpH': row_for(m), 'hZtF1s8B': str(pos), '3g8PW6x0': lay[pos],
                         'hw3L0uVj': f"0:{m.get('drop') or m['u']}:0:0"})
        drops.append(f"{bi}|{'-'.join(b['unit_drops']) or ' '}|{'-'.join(b['chests']) or ' '}")
    # the mimic group the game keeps ready (treasure chests that bite)
    mim = plan.get('mimic') or {'u': '60142'}
    if MIMIC_ROW and mim['u'] == '60142':      # the game's own mimic, exactly as bfrevive sends it
        mons.append(dict(MIMIC_ROW))
        mimic_id = MIMIC_ROW['o49dYfpH']
    else:                                       # Bat/Dragon Mimic: as strong as this quest's enemies
        hp = [int(r['e7DK0FQT']) for r in mons] or [1000]
        at = [int(r['67CApcti']) for r in mons] or [290]
        df = [int(r['q08xLEsy']) for r in mons] or [50]
        mult = 1.3 if mim['u'] == '60144' else 1.0
        mimic_id = row_for({'u': mim['u'], 'name': mim.get('name'), 'hp': int(sorted(hp)[len(hp) // 2] * mult),
                            'atk': int(sorted(at)[len(at) // 2] * mult), 'def': int(sorted(df)[len(df) // 2]),
                            'ai': (MIMIC_ROW or {}).get('i74vGUFa', '1')})
    bmon.append({'Qzhp8B40': str(n + 1), 'o49dYfpH': mimic_id,
                 'hZtF1s8B': '0', '3g8PW6x0': '160:240', 'hw3L0uVj': f"0:{mim['u']}:0:0"})
    resp = dict(base)
    resp[G_BGROUP], resp[G_BMON], resp[G_MON] = groups, bmon, mons
    ai_ids = {r.get('i74vGUFa', '1') for r in mons} | {'1'}
    resp[G_AI] = [row for a in sorted(ai_ids) for row in AI_ROWS.get(a, [])]
    skills = {r.get('nj9Lw7mV'): r for r in plan['skills']}
    for r in mons:             # every skill an enemy row names must be sent with it
        for part in filter(None, (r.get('F4bQ7r8C') or '').replace('@', ',').split(',')):
            sid = part.split(':')[0]
            if sid not in skills and sid in SKILL_ROWS:
                skills[sid] = SKILL_ROWS[sid]
    cgs = []
    for r in mons:             # animation lists for monster-only enemies, under this quest's monster ids
        key = r.get('QqfI9mM4') or r.get('2EF0d6ue')
        num = re.fullmatch(r'monster_cgg_(\w+)\.csv', str(r.get('QqfI9mM4') or ''))
        if key not in MON_CGS and num and str(r.get('pn16CNah')) == '0':
            # no recording of this monster: the game's usual set (idle, idle, attack, attack)
            n = num.group(1)
            MON_CGS[key] = [{'fpc3rbs7': str(i), '0QxL2is7': f"monster_{'idle' if i < 3 else 'atk'}_cgs_{n}.csv"}
                            for i in range(1, 5)]
        for c in MON_CGS.get(key) or []:
            cgs.append({'o49dYfpH': r['o49dYfpH'], **c})
    if cgs:
        resp['8hoyIF9Q'] = cgs
    if skills:
        resp['8aiBoHg5'] = list(skills.values())
    else:
        resp.pop('8aiBoHg5', None)
    num = dict((base.get(G_MNUM) or [{}])[0])
    num.update({'k9cxD7Ba': str(random.randint(1000000000, 1999999999)), 'LE6JkUp7': '@'.join(drops)})
    resp[G_MNUM] = [num]
    info = dict(((req.get(G_MSTART) or [{}])[0]))
    resp[G_MSTART] = [{k: info.get(k, v) for k, v in ((base.get(G_MSTART) or [{}])[0]).items()} | {'j28VNcUW': mid}]
    log.info('quest battles for %s (%s): %s', mid, plan['label'],
             ' / '.join(','.join(m.get('name') or unit_mst().get(m['u'], {}).get('utP1c0CD', m['u']) for m in b['mons'])
                        for b in plan['battles']))
    return resp

def mission_result(req):
    return ((req or {}).get('rXvA1E5y') or [{}])[0]

def _pairs(text):
    out = []
    for part in str(text or '').split(','):
        f = part.split(':')
        if f and f[0].isdigit():
            out.append((f[0], int(f[1]) if len(f) > 1 and f[1].isdigit() else 1))
    return out

def mission_drops(pl, req, resp):
    """Units and items the game reports this battle dropped."""
    res = mission_result(req)
    if trial_replay(pl, pl.get('current_mission')):
        res = {}                      # a trial only rewards its first clear
    pl['_drops'] = []
    tmpl = (resp.get(G_UNITS_ADD) or [None])[0]
    if tmpl is None:
        g = next((a for a in T['action'] if a['cls'] == 'GachaActionRequest' and G_UNITS_ADD in BLOBS[a['resp']]), None)
        tmpl = personalise(pl, BLOBS[g['resp']])[G_UNITS_ADD][0] if g else {}
    new = []
    for uid, n in _pairs(res.get('3MAT6quo')):
        for _ in range(max(1, n)):
            if uid in unit_mst():
                u = new_unit_row(pl, tmpl, uid, fresh_unit_id(pl))
                u['dJNpLc81'] = '1'
                pl['units'][u[F_UNIT_ID]] = u
                first = add_to_dictionary(pl, uid)
                new.append(u)
                pl.setdefault('_drops', []).append(f"{uid}:{u[F_UNIT_ID]}:{1 if first else 0}:1")
    resp[G_UNITS_ADD] = new
    wh = {r.get('kixHbe54'): r for r in _rows(pl['state'].get(G_WAREHOUSE)) if isinstance(r, dict)}
    shown = []
    for item, n in _pairs(res.get('47cJBUxz')):
        row = wh.get(item) or {'n6E8iMf3': str(random.randint(100000, 999999)), 'kixHbe54': item, 'wgV86x1q': '0',
                               'dJNpLc81': '1', 'DbMVG16I': '0'}
        row['wgV86x1q'] = str((_num(row.get('wgV86x1q')) or 0) + n)
        wh[item] = row
        shown.append(row)
        d = pl['state'].setdefault('bd5Rj6pN', [])
        if not any(r.get('kixHbe54') == item for r in d):
            d.append({'kixHbe54': item})
    pl['state'][G_WAREHOUSE] = list(wh.values())
    resp[G_WAREHOUSE] = list(wh.values())             # full list, like the real server
    if new and not trial_replay(pl, pl.get('current_mission')):
        highlight('drops', pl, [u['pn16CNah'] for u in new], pl.get('current_mission'))
    if new or shown:
        log.info('battle drops: units %s, items %s', [unit_mst()[u['pn16CNah']].get('utP1c0CD') for u in new],
                 res.get('47cJBUxz') or '-')

# --------------------------------------------------------------------------- story unlocks
UNLOCK = json.load(open(os.path.join(HERE, 'data', 'unlock_mst.json'))) if os.path.exists(
    os.path.join(HERE, 'data', 'unlock_mst.json')) else {}
G_PERMIT = 'yXNM8kL3'
_FIRST_INFO = min(BY_ID.get(RID['GetUserInfoRequest'], []), key=lambda a: a['n'], default=None)
PERMIT_BASE = list((BLOBS[_FIRST_INFO['resp']].get(G_PERMIT) or [])) if _FIRST_INFO else []

def story_permits(pl):
    """What the quest map shows: the starting set, plus everything whose required quest you cleared
    (the same rule bfrevive follows - read from the game's own quest data)."""
    clears = {r.get('j28VNcUW') for r in _rows(pl['state'].get('UT1SVg59')) if isinstance(r, dict)}
    def ok(need):
        need = str(need or '0')
        return need != '0' and all(x.strip() in clears for x in need.split(','))
    rows = [dict(r) for r in PERMIT_BASE]
    have = {tuple(sorted(r.items())) for r in rows}
    def add(field, i):
        r = {field: i}
        k = tuple(sorted(r.items()))
        if k not in have:
            have.add(k); rows.append(r)
    for kind, field in (('land', '9C64Qwe0'), ('area', 'VjCY7rX4')):
        for i, need in sorted((UNLOCK.get(kind) or {}).items(), key=lambda x: int(x[0])):
            if (int(i) < 100000 or kind == 'land') and ok(need):
                add(field, i)
    dun_ok = {d for d, need in (UNLOCK.get('dungeon') or {}).items() if int(d) < 100000 and (ok(need) or need == '0')}
    mis_dun = UNLOCK.get('mission_dungeon') or {}
    for i, need in sorted((UNLOCK.get('mission') or {}).items(), key=lambda x: int(x[0])):
        if int(i) >= 100000 or not ok(need):
            continue
        d = mis_dun.get(i)
        if d and d in dun_ok:
            add('MHx05sXt', d)       # like bfrevive: the dungeon row, then its quests
        add('j28VNcUW', i)
    return [r for r in rows if not closed_row(r)]

def closed_row(r):
    """A permit row for an area the admin page closed (the area itself, its dungeons, its quests)."""
    if r.get('VjCY7rX4'):
        return area_closed(str(r['VjCY7rX4']))
    if r.get('MHx05sXt'):
        return dungeon_closed(r['MHx05sXt'])
    if r.get('j28VNcUW'):
        return mission_closed(r['j28VNcUW'])
    return False

# --------------------------------------------------------------------------- Summoners' Hall keys
G_KEYS, G_KEY_MST, G_BADGE = 'eFU7Qtb0', '4NG79sX1', 'h23iRjGN'
KEY_MST = {}
for _a in BY_ID.get(RID['URLMstRequest'], []):
    for _r in BLOBS[_a['resp']].get(G_KEY_MST) or []:
        KEY_MST[_r['16KMNJLb']] = _r

def _utc_day(ts=None):
    return int((ts or time.time()) // 86400)

def key_rows(pl):
    """Each key: how many you hold, and whether today's 3 are waiting (new batch every day, 00:00 UTC
    like bfrevive)."""
    rows = {r.get('16KMNJLb'): r for r in _rows(pl['state'].get(G_KEYS)) if isinstance(r, dict)}
    today = _utc_day()
    for kid in KEY_MST or {'1': {}, '2': {}, '3': {}}:
        r = rows.get(kid) or {F_USERID: pl['user_id'], '16KMNJLb': kid, 'wgV86x1q': '0', 'KI42FwRt': '0',
                              'BY8fZ7M1': '0', 'g85qMNxf': '1', 'b6QR1CH5': '', 'H6k1LIxC': '0'}
        last = _num(r.get('KI42FwRt')) or 0
        can = last < today
        r['g85qMNxf'] = '1' if can else '0'
        nxt = today if can else today + 1
        r['b6QR1CH5'] = datetime.datetime.utcfromtimestamp(nxt * 86400).strftime('%Y-%m-%d %H:%M:%S')
        rows[kid] = r
    pl['state'][G_KEYS] = [rows[k] for k in sorted(rows, key=lambda x: int(x))]
    return pl['state'][G_KEYS]

def receive_keys(pl, req):
    sel = (req.get('9S5M2yI3') or [{}])[0]
    want = str(sel.get('16KMNJLb', '0'))
    today = _utc_day()
    for r in key_rows(pl):
        if (want in ('0', '') or r['16KMNJLb'] == want) and r['g85qMNxf'] == '1':
            m = KEY_MST.get(r['16KMNJLb'], {})
            n, limit = _num(m.get('khsb74Nq')) or 3, _num(m.get('N7I9vYZb')) or 50
            r['wgV86x1q'] = str(min(limit, (_num(r['wgV86x1q']) or 0) + n))
            r['KI42FwRt'] = str(today)
            log.info('received %s x%s (now %s)', m.get('BM29ZgnK', 'key ' + r['16KMNJLb']), n, r['wgV86x1q'])
    return key_rows(pl)

def key_patterns(kid):
    """The key's usage choices from its master row, e.g. 3 keys -> Super Metal Parade (quests ...)."""
    out = []
    for part in str(KEY_MST.get(kid, {}).get('CR6aKWg8') or '').split('/'):
        f = part.split(':')
        if len(f) >= 4 and f[0].isdigit():
            out.append({'num': int(f[0]), 'event': int(f[1]) if f[1].isdigit() else 0, 'name': f[2],
                        'missions': [x for x in f[3].split(',') if x]})
    return out

def key_opens(pl):
    """Quests opened with keys that are still running: {mission: until}. Expired ones are dropped."""
    now = time.time()
    op = {m: t for m, t in (pl.get('key_open') or {}).items() if t > now}
    pl['key_open'] = op
    # like bfrevive: while a key's choice is open, BY8fZ7M1 = that choice's event number and
    # H6k1LIxC = seconds left; both 0 otherwise
    act = {k: v for k, v in (pl.get('key_active') or {}).items() if v.get('until', 0) > now}
    pl['key_active'] = act
    for r in key_rows_raw(pl):
        a = act.get(r.get('16KMNJLb'))
        r['BY8fZ7M1'] = str(a['event']) if a else '0'
        r['H6k1LIxC'] = str(max(1, int(a['until'] - now))) if a else '0'
    return op

def key_rows_raw(pl):
    return [r for r in _rows(pl['state'].get(G_KEYS)) if isinstance(r, dict)]

_ALL_SP = None

def all_sp_rows():
    """Every Vortex banner the recordings ever listed (on whichever day they were made), with its
    tab and quests, as plain rows. Banners that only ever showed up with a timer (opened by a key
    or a short event) are left out - keys still open those the usual way."""
    global _ALL_SP
    if _ALL_SP is not None:
        return _ALL_SP
    plain, timed, areas, quests = [], set(), [], []
    seen = set()
    for a in sorted(T['action'], key=lambda a: -a['n']):
        b = BLOBS[a['resp']]
        if not isinstance(b, dict):
            continue
        for r in b.get('Y73tHKS8') or []:
            if not isinstance(r, dict):
                continue
            if r.get('MHx05sXt'):
                d = str(r['MHx05sXt'])
                if 'C1vG0iKh' in r or 'qY49LBjw' in r:
                    timed.add(d)
                elif d not in seen:
                    seen.add(d)
                    plain.append(d)
            elif r.get('VjCY7rX4') and str(r['VjCY7rX4']) not in areas:
                areas.append(str(r['VjCY7rX4']))
            elif r.get('j28VNcUW') and str(r['j28VNcUW']) not in quests:
                quests.append(str(r['j28VNcUW']))
    try:
        mis_dun = {m['j28VNcUW']: m.get('MHx05sXt') for m in vortex_editor().tables()[2]}
    except Exception:
        mis_dun = {}
    for m, info in MISSIONS.items():
        mis_dun.setdefault(m, info.get('dungeon'))
    dungeons = set(plain)
    rows = [{'VjCY7rX4': x} for x in areas]
    rows += [{'MHx05sXt': d} for d in plain]
    rows += [{'j28VNcUW': m} for m in quests if str(mis_dun.get(m)) in dungeons]
    _ALL_SP = rows
    log.info('Vortex list: %d banners from the recordings (%d timed ones left to keys)',
             len(plain), len(timed - dungeons))
    return rows

def scheduled_sp_rows():
    """Rows for every banner that has days (the game's weekday banners and any the admin page
    scheduled): its tab, itself and its quests from the game's tables - the schedule then decides
    whether it shows today."""
    ids = set(default_banner_days()) | set(admin_settings().get('schedule', {}))
    if not ids:
        return []
    _, dungeons, missions = vortex_editor().tables()
    area_of = {str(d['MHx05sXt']): str(d.get('VjCY7rX4') or '') for d in dungeons}
    rows, tabs = [], []
    for did in sorted(ids, key=lambda x: int(x) if x.isdigit() else 0):
        qs = [str(m['j28VNcUW']) for m in missions if str(m.get('MHx05sXt')) == did and str(m['j28VNcUW']) in MISSIONS]
        if did not in area_of or not qs:
            continue
        if area_of[did] and area_of[did] not in tabs:
            tabs.append(area_of[did])
        rows.append({'MHx05sXt': did})
        rows += [{'j28VNcUW': q} for q in qs]
    return [{'VjCY7rX4': t} for t in tabs] + rows

def unlocked_sp_quests(pl, rows):
    """Every quest of a listed Vortex banner that this player has unlocked, by the game's own rule
    (quest table HSRhkf70: the quests to clear first). The recordings only caught the quests the
    recorded account had open, so later quests of a banner (Leon's Deception 2 and 3...) were missing."""
    _, _, missions = vortex_editor().tables()
    by_dun = {}
    for m in missions:
        by_dun.setdefault(str(m.get('MHx05sXt')), []).append(m)
    clears = set(cleared_ids(pl))
    have = {str(r['j28VNcUW']) for r in rows if r.get('j28VNcUW')}
    out = []
    for r in rows:
        out.append(r)
        d = str(r.get('MHx05sXt') or '')
        if not d or d not in by_dun or 'qY49LBjw' in r:      # timed (key) banners list their own quests
            continue
        for m in sorted(by_dun[d], key=lambda m: int(m.get('XuJL4pc5') or 0)):
            mid = str(m['j28VNcUW'])
            if mid in have or mid not in MISSIONS:
                continue
            need = [x.strip() for x in str(m.get('HSRhkf70') or '').split(',') if x.strip() not in ('', '0')]
            if all(x in clears for x in need):
                out.append({'j28VNcUW': mid})
                have.add(mid)
    return out

def sp_permits(pl):
    """The special (Vortex) permit list: the account's own list, plus key-opened quests with the
    seconds they have left, each placed right after its dungeon row."""
    op = key_opens(pl)
    base = [dict(r) for r in _rows(pl['state'].get('Y73tHKS8') or fresh_value(pl, 'Y73tHKS8')) if isinstance(r, dict)]
    now = time.time()
    by_dun = {}
    for m, t in op.items():
        by_dun.setdefault(MISSIONS.get(m, {}).get('dungeon'), []).append({'j28VNcUW': m})
    # like bfrevive: the DUNGEON row carries the seconds left (the Vortex screen shows it open and
    # draws the countdown from it); the opened quests follow it as plain rows
    left = {}
    for m, t in op.items():
        d = MISSIONS.get(m, {}).get('dungeon')
        left[d] = max(left.get(d, 0), max(1, int(t - now)))
    out = []
    for r in base:
        if r.get('j28VNcUW') in op:
            continue
        if r.get('MHx05sXt') in by_dun:
            out.append(dict(r, C1vG0iKh='1', qY49LBjw=str(left[r['MHx05sXt']])))
            out += by_dun.pop(r['MHx05sXt'])
            continue
        out.append(r)
    for d, rows in by_dun.items():           # dungeon not listed yet: add it too
        out.append({'MHx05sXt': d, 'C1vG0iKh': '1', 'qY49LBjw': str(left[d])})
        out += rows
    try:
        have = {tuple(sorted(r.items())) for r in out}
        have_ids = {(k, str(v)) for r in out for k, v in r.items() if k in ('VjCY7rX4', 'MHx05sXt', 'j28VNcUW')}
        # every banner the admin page has open shows, not only the ones the recorded day had
        for r in all_sp_rows():
            k, v = next(iter(r.items()))
            if (k, v) not in have_ids:
                out.append(dict(r))
                have_ids.add((k, v))
        for r in scheduled_sp_rows():         # day banners the recordings never caught on their day
            k, v = next(iter(r.items()))
            if (k, v) not in have_ids:
                out.append(dict(r))
                have_ids.add((k, v))
        out += [r for r in vortex_editor().permit_rows() if tuple(sorted(r.items())) not in have]
    except Exception:
        log.exception('could not add the custom Vortex banners')
    try:
        out = unlocked_sp_quests(pl, out)
    except Exception:
        log.exception('could not add the unlocked Vortex quests')
    out = drop_empty_tabs(out, [r for r in out if not closed_row(r)])
    try:
        out = add_boosts(out)
    except Exception:
        log.exception('could not add the Vortex reward boosts')
    return out

def drop_empty_tabs(before, after):
    """A Vortex tab whose banners are all closed (or not on today) is left out too - the game
    shows a tab, and a sub-tab inside a folder like Travellers, from its own row whether or not
    anything is in it. Folder tabs go when every tab inside them has gone."""
    def tabs_with_banners(rows):
        t = {}
        for r in rows:
            if r.get('MHx05sXt'):
                a = dungeon_area(r['MHx05sXt'])
                if a:
                    t[a] = t.get(a, 0) + 1
        return t
    had, has = tabs_with_banners(before), tabs_with_banners(after)
    keep = set(has)
    for a in list(has):                     # the folders a living tab sits in stay
        seen = set()
        while a and a not in seen:
            seen.add(a)
            a = area_parent(a)
            if a:
                keep.add(a)
    kids = {}
    for r in before:
        a = str(r.get('VjCY7rX4') or '')
        if a and area_parent(a):
            kids.setdefault(area_parent(a), []).append(a)
    def emptied(a, seen=()):
        if a in keep:
            return False
        if a in had:                        # had banners, none left
            return True
        k = [x for x in kids.get(a, []) if x not in seen]
        return bool(k) and all(emptied(x, seen + (a,)) for x in k)
    gone = {str(r['VjCY7rX4']) for r in before if r.get('VjCY7rX4') and emptied(str(r['VjCY7rX4']))}
    HIDDEN_TABS.clear()
    HIDDEN_TABS.update(gone)
    return [r for r in after if not (r.get('VjCY7rX4') and str(r['VjCY7rX4']) in gone)]

HIDDEN_TABS = set()          # Vortex tabs left empty by the admin page (last Vortex list built)

def hide_closed_tabs(rows):
    """GetUserInfo 3SG2wX0R: the game draws a folder's tabs (Travellers...) from these rows, not
    from the Vortex list - so closed and emptied Vortex tabs are taken out here as well."""
    return [r for r in rows if not (isinstance(r, dict) and str(r.get('9C64Qwe0')) == '99'
                                    and (area_closed(str(r.get('VjCY7rX4'))) or str(r.get('VjCY7rX4')) in HIDDEN_TABS))]

def use_key(pl, req):
    """DungeonKeyUse: the game sends xki65BPq {16KMNJLb: key, phe9Ta54: how many keys}. That many
    keys picks one of the key's choices (1 = Metal Parade, 3 = Super, 5 = Mega...), whose quests
    open for the key's time limit."""
    sel = (req.get('xki65BPq') or req.get('9S5M2yI3') or [{}])[0]
    kid = str(sel.get('16KMNJLb', ''))
    cnt = _num(sel.get('phe9Ta54')) or 1
    m = KEY_MST.get(kid, {})
    pats = key_patterns(kid)
    pat = next((x for x in pats if x['num'] == cnt), None) or (pats[0] if pats else None)
    for r in key_rows(pl):
        if r['16KMNJLb'] != kid:
            continue
        have = _num(r['wgV86x1q']) or 0
        if have < cnt:
            return f"Not enough keys (you have {have}, need {cnt})."
        r['wgV86x1q'] = str(have - cnt)
        r['H6k1LIxC'] = str(cnt)
        until = time.time() + (_num(m.get('i9sBW8uD')) or 1800)
        missions = (pat or {}).get('missions') or ([m['MHx05sXt']] if m.get('MHx05sXt') else [])
        op = pl.setdefault('key_open', {})
        dun = MISSIONS.get(missions[0], {}).get('dungeon') if missions else None
        for mid in [x for x in op if MISSIONS.get(x, {}).get('dungeon') == dun]:
            op.pop(mid)            # a new choice replaces whatever was open in that dungeon
        for mid in missions:
            op[mid] = until
        pl.setdefault('key_active', {})[kid] = {'event': (pat or {}).get('event', 0), 'until': until}
        log.info('used %s x%s: %s open for %s min (keys left %s)', m.get('BM29ZgnK', 'key ' + kid), cnt,
                 (pat or {}).get('name', missions), int((_num(m.get('i9sBW8uD')) or 1800) / 60), r['wgV86x1q'])
        return None
    return "You don't have that key."

_REC_UNIT_IDS = set()

def recorded_unit_ids():
    """Every unit id that ever appeared in a recording (the recorded account's units)."""
    if not _REC_UNIT_IDS:
        import re as _re
        pat = _re.compile(r'"edy7fq3L":\s*"(\d+)"')
        for b in BLOBS.values():
            _REC_UNIT_IDS.update(pat.findall(json.dumps(b)))
        for a in T['action']:
            _REC_UNIT_IDS.update(pat.findall(json.dumps(a['req'])))
    return _REC_UNIT_IDS

def clean_save(pl):
    """ADMIN.bat 'clean': take everything that came from the recorded account out of this save.
    Keeps: name, level, balances, quest clears, squads, keys, units you got on this server.
    A copy of the save is kept as player_<id>.before-clean.json."""
    import shutil
    try:
        shutil.copy(player_path(pl['user_id']), player_path(pl['user_id']).replace('.json', '.before-clean.json'))
    except Exception:
        pass
    rec = recorded_unit_ids()
    starter = {u.get(F_UNIT_ID) for u in _rows(FRESH.get(G_UNITS))}
    removed = [u for k, u in pl['units'].items() if k in rec and k not in starter]
    for u in removed:
        pl['units'].pop(u[F_UNIT_ID], None)
    remap = rekey_units(pl, {k for k in pl['units'] if k in rec})       # starter units: same units, own ids
    # squads: drop units that are gone; a squad left without a leader gets one
    have = set(pl['units'])
    deck = [r for r in _rows(pl['state'].get('dX7S2Lc1')) if r.get(F_UNIT_ID) in have]
    spare = list(remap.values()) or list(have)
    for sq in range(10):
        rows = [r for r in deck if str(r.get('zsiAn9P1')) == str(sq)]
        if spare and not any(str(r.get('gr48vsdJ')) == '0' for r in rows):
            deck.append({'U9ABSYEp': '1', 'zsiAn9P1': str(sq), F_UNIT_ID: spare[0], 'gr48vsdJ': '0', 'XuJL4pc5': str(len(rows))})
    pl['state']['dX7S2Lc1'] = deck
    # unit guide = units you own now + a new account's guide
    d = {r.get('pn16CNah') for r in _rows(fresh_value(pl, G_DICT))} | {u['pn16CNah'] for u in pl['units'].values()}
    pl['state'][G_DICT] = [{F_USERID: pl['user_id'], 'pn16CNah': x, '2pAyFjmZ': '0'} for x in sorted(d, key=int)]
    # everything else the recording filled in goes back to new-account defaults
    keep = {G_USERINFO, G_TEAM, 'UT1SVg59', 'dX7S2Lc1', G_KEYS, G_PERMIT, G_DICT, 'Y73tHKS8', 'Y73mHKS8'}
    reset = []
    for g in list(pl['state']):
        if g in PLAYER_GROUPS and g not in keep:
            pl['state'][g] = fresh_value(pl, g)
            reset.append(g)
    pl['clean'] = 2
    log.info('clean: removed %d units from the recordings (%s), gave %d starter units their own ids, reset %d '
             'other parts to new-account defaults; backup: player_%s.before-clean.json', len(removed),
             ', '.join(sorted({unit_mst().get(u['pn16CNah'], {}).get('utP1c0CD', u['pn16CNah']) for u in removed}))[:300],
             len(remap), len(reset), pl['user_id'])

def merge_replay(pl, cls, cap, resp):
    """Apply what a recorded action *changed* to this player, then show the player's own data."""
    before = BEFORE.get(cap['n'], {})
    handled = set()
    if cls == 'MissionEndRequest':
        mission_drops(pl, REQ_NOW.get('req'), resp)
        record_quest_stats(pl, REQ_NOW.get('req') or {})
        handled = {G_UNITS_ADD, G_WAREHOUSE, 'bd5Rj6pN', G_REWARD}
        if end_mission(pl, resp):
            handled |= {'UT1SVg59', G_TEAM}
        elif resp.get(G_REWARD):
            t = team_row(pl['state']) or {}
            r = dict(_rows(resp[G_REWARD])[0])
            r.update({'wPb5uMR9': '0', '81CsGAR9': str(t.get('D9wXQI2V', '1')), 'd96tuT2E': '0',
                      '3MAT6quo': ','.join(pl.pop('_drops', []) or []), 'mauD5qZ1': '0'})
            resp[G_REWARD] = [r]
    for g in list(resp):
        if g in handled:
            continue
        if g == G_UNITS_ADD:
            resp[g] = []          # units handed out in a recording were the recorded account's
    return show_player(pl, resp)

def show_player(pl, resp):
    """Put the player's own data in place of the recording's in an outgoing response."""
    if G_PERMIT in resp and UNLOCK:
        pl['state'][G_PERMIT] = story_permits(pl)
        resp[G_PERMIT] = pl['state'][G_PERMIT]
    for g in list(resp):
        if g == G_PERMIT and UNLOCK:
            continue
        if g == G_UNITS:
            resp[g] = list(pl['units'].values())
        elif g == G_DICT:
            resp[g] = pl['state'].get(G_DICT, [])
        elif g == G_TEAM and team_row(pl['state']) is not None:
            resp[g] = [team_row(pl['state'])]
        elif g == G_USERINFO and pl['state'].get(G_USERINFO):
            row = dict(_rows(resp[g])[0]) if _rows(resp[g]) else {}
            mine = _rows(pl['state'][G_USERINFO])[0]
            row.update({k: v for k, v in mine.items() if k in PROTECTED_USER_FIELDS or k in row})
            resp[g] = [row]
        elif g == 'Y73tHKS8':
            resp[g] = sp_permits(pl)
        elif g in FULL_LIST_GROUPS and g in pl['state']:
            resp[g] = list(_rows(pl['state'][g]))
        elif g in PLAYER_GROUPS and g not in (G_UNITS_ADD, G_REWARD, G_OPE_UNIT):
            resp[g] = pl['state'][g] if g in pl['state'] else fresh_value(pl, g)
        elif g in STATE_GROUPS and isinstance(resp[g], list) and g in pl['state']:
            mine = {_key(g, r): r for r in _rows(pl['state'][g]) if isinstance(r, dict)}
            resp[g] = [mine.get(_key(g, r), r) if isinstance(r, dict) else r for r in resp[g]]
    return resp

# --------------------------------------------------------------------------- fusion
EXP_PATTERN = {}
for _b in BLOBS.values():
    if isinstance(_b, dict) and 'JYFGe9y6' in _b:
        for r in _b['JYFGe9y6']:
            EXP_PATTERN.setdefault(r['5UvTp7q1'], {})[int(r['D9wXQI2V'])] = int(r['grKTq15A'])
        break

def _cum_exp(pattern, lv):
    p = EXP_PATTERN.get(str(pattern), {})
    return sum(p.get(l, 0) for l in range(1, lv + 1))

def unit_stats(m, lv):
    maxlv = max(1, int(m.get('EI1DF8Yt') or 1))
    f = (lv - 1) / (maxlv - 1) if maxlv > 1 else 1
    st = lambda lo, hi: str(int(int(m.get(lo) or 0) + (int(m.get(hi) or 0) - int(m.get(lo) or 0)) * f))
    return {'e7DK0FQT': st('UZ1Bj7w2', '3WMz78t6'), '67CApcti': st('i9Tn7kYr', 'omuyP54D'),
            'q08xLEsy': st('q78KoWsg', '32INDST4'), 'PWXu25cg': st('92ij6UGB', 'X9P3AN5d')}

def material_exp(base_m, mat, mat_m):
    lv = int(mat.get('D9wXQI2V') or 1)
    adj = int(mat_m.get('Gd4NS7H6') or 0)
    if adj:
        exp = adj + random.randint(1400, 2300)
    else:   # ordinary units: worth more the rarer and higher level they are
        r = int(mat_m.get('7ofj5xa1') or 1)
        exp = int(mat_m.get('gvT2ds0Q') or 1) * 100 + lv * r * 30 + random.randint(50, 300)
    if mat_m.get('iNy0ZU5M') == base_m.get('iNy0ZU5M'):
        exp = int(exp * 1.5)
    return exp

# Brave Burst levels: unit row 3NbeC8AB = BB level, RQ5GnFE2 = SBB level (skill ids nj9Lw7mV / iEFZ6H19).
# Burst Frog +1, Lawson Frog +2, Burst Emperor +5, Burst Queen +20 (the game's own unit table); levels
# past the BB's highest level go to the SBB. A material with the same BB as the base unit has a chance.
FUSION_SKILL_FILE = os.path.join(HERE, 'data', 'fusion_skill.json')
FUSION_SKILL = json.load(open(FUSION_SKILL_FILE, encoding='utf-8')) if os.path.exists(FUSION_SKILL_FILE) else {}
SAME_BB_CHANCE = 0.2                 # each material with the same BB as the base: chance of +1 level

def skill_max(skill_id):
    return int((FUSION_SKILL.get('skill_levels') or {}).get(str(skill_id)) or 10)

def bb_levels_from(base, mats):
    """How many BB levels the materials add."""
    up = 0
    bb = str(base.get('nj9Lw7mV') or '')
    for m in mats:
        up += int((FUSION_SKILL.get('bb_up') or {}).get(str(m.get('pn16CNah')), 0))
        if bb and str(m.get('nj9Lw7mV') or '') == bb and random.random() < SAME_BB_CHANCE:
            up += 1
    return up

def raise_bb(base, n):
    """Put n BB levels on the unit: BB first, then SBB. Returns (bb0, bb1, sbb0, sbb1)."""
    has_bb = str(base.get('nj9Lw7mV') or '') not in ('', '0')
    has_sbb = str(base.get('iEFZ6H19') or '') not in ('', '0')
    bb0 = _num(base.get('3NbeC8AB')) or (1 if has_bb else 0)
    sbb0 = _num(base.get('RQ5GnFE2')) or 0
    bb1, sbb1 = bb0, sbb0
    if has_bb and n > 0:
        add = min(n, skill_max(base['nj9Lw7mV']) - bb0)
        bb1 = bb0 + max(0, add)
        n -= max(0, add)
        if has_sbb and n > 0:
            sbb1 = min(skill_max(base['iEFZ6H19']), sbb0 + n)
    return bb0, bb1, sbb0, sbb1

# SP for units with SP enhancements (the Omni SP system): Sphere Frog 10 (only once the second sphere
# slot is open), Burst Emperor 5, a unit of the same line (any rarity) 5, Burst Frog 1, anything else a
# small chance of 1. Up to the unit's most usable SP (GIO9DTif, 100).
SP_FROM = {'20302': 10, '10313': 5, '10312': 1}
SP_SAME_LINE, SP_OTHER_CHANCE = 5, 0.1

def sp_from(base, mats):
    if str(base.get('pn16CNah')) not in (FE.get('units') or {}):
        return 0
    series = FUSION_SKILL.get('series') or {}
    line = series.get(str(base.get('pn16CNah')))
    gain = 0
    for m in mats:
        mid = str(m.get('pn16CNah'))
        if mid == '20302':
            gain += SP_FROM[mid] if base.get('5gXxT7LZ') else 0
        elif mid in SP_FROM:
            gain += SP_FROM[mid]
        elif line and series.get(mid) == line:
            gain += SP_SAME_LINE
        elif random.random() < SP_OTHER_CHANCE:
            gain += 1
    return gain

# Fusion Great / Super success: the game's own odds and exp (Great 12% x1.5, Super 3% x2). The admin
# page (Rates > Fusion success) can switch on a boost for either - its own chance and exp multiplier.
# The game's result line ("Great Success! 1.5x XP!") takes its number from DEFINE_MST
# (UnitMixGreatExpRate 2inP0tCg / UnitMixSuperExpRate zn65EXYF), which the server sends at login.
FUSION_NORMAL = {'great': {'chance': 12.0, 'mult': 1.5}, 'super': {'chance': 3.0, 'mult': 2.0}}

def fusion_params():
    """{'great': {'chance': percent, 'mult': exp multiplier}, 'super': {...}} in force now."""
    out = {}
    for kind, normal in FUSION_NORMAL.items():
        b = (admin_settings().get('fusion') or {}).get(kind) or {}
        out[kind] = {'chance': float(b['chance']), 'mult': float(b['mult'])} if b.get('on') else dict(normal)
    return out

# The Unit screen's Fusion button label comes from the general event list (Md9gG3c0, GetUserInfo):
# event type 3 -> "Great Success Rate Up", 4 -> "Increased XP", 5 -> "Super Success Rate Up" (the
# app from v12 on; older apps show "Fusion Cost 1/2 Off" there). The admin page picks which shows.
FUSION_LABEL_TYPES = {'3', '4', '5'}
FUSION_LABEL_ROWS = {
    'great': {'v80VfcXr': '3', 'b4D63uav': '3', '1wHpzcy5': '0', 't5R47iwj': 'Great Success Rate Up!'},
    'super': {'v80VfcXr': '9905', 'b4D63uav': '5', '1wHpzcy5': '0', 't5R47iwj': 'Super Success Rate Up!'}}

# Title screen music: general event type 4000, params = a file in content/sound. On the home screen
# the game downloads it and remembers it; the title screen plays it from the next start (the app's own
# bf001_title.mp3 when there is none). Set with "title_music" in config.json.
TITLE_MUSIC_TYPE = '4000'

def fusion_label_events(rows):
    rows = [r for r in (rows or []) if not (isinstance(r, dict) and str(r.get('b4D63uav')) in FUSION_LABEL_TYPES)]
    label = (admin_settings().get('fusion') or {}).get('label', 'none')
    if label in FUSION_LABEL_ROWS:
        rows.append(dict(FUSION_LABEL_ROWS[label]))
    music = str(CFG.get('title_music', 'bf001_title.mp3') or '')
    rows = [r for r in rows if not (isinstance(r, dict) and str(r.get('b4D63uav')) == TITLE_MUSIC_TYPE)]
    if music:
        rows.append({'v80VfcXr': '8000', 'b4D63uav': TITLE_MUSIC_TYPE, '1wHpzcy5': '0', 't5R47iwj': music})
    return rows

def _num_text(x):
    return str(int(x)) if float(x).is_integer() else f'{x:g}'

def patch_define_mst(resp):
    """URLMst VkoZ5t3K (DEFINE_MST): the Great / Super exp multipliers the game shows after a fusion."""
    d = resp.get('VkoZ5t3K')
    row = d[0] if isinstance(d, list) and d else d
    if isinstance(row, dict):
        fp = fusion_params()
        row['2inP0tCg'] = _num_text(fp['great']['mult'])
        row['zn65EXYF'] = _num_text(fp['super']['mult'])

# Imps: fusing Power/Guard/Healing/Vigor/Almighty Imps raises a unit's HP/ATK/DEF/REC for good, up to
# that unit's imp limit (game data, data/unit_imps.json). Unit row: TokWs1B3 HP, t4m1RH6Y ATK,
# e6mY8Z0k DEF, X6jf8DUw REC on top of the level stats - the game adds them itself.
IMP_FILE = os.path.join(HERE, 'data', 'unit_imps.json')
IMPS = json.load(open(IMP_FILE, encoding='utf-8')) if os.path.exists(IMP_FILE) else {'boost': {}, 'caps': {}}
IMP_FIELDS = ('TokWs1B3', 't4m1RH6Y', 'e6mY8Z0k', 'X6jf8DUw')

def apply_imps(base, mats):
    """Add the imps among mats to base (a unit row, changed in place). Returns what was added."""
    add = [0, 0, 0, 0]
    for m in mats:
        for i, v in enumerate(IMPS['boost'].get(str(m.get('pn16CNah')), ())):
            add[i] += v
    if not any(add):
        return add
    caps = IMPS['caps'].get(str(base.get('pn16CNah')), [0, 0, 0, 0])
    got = [0, 0, 0, 0]
    for i, f in enumerate(IMP_FIELDS):
        now = _num(base.get(f)) or 0
        new = max(now, min(caps[i], now + add[i]))
        got[i] = new - now
        base[f] = str(new)
    return got

def do_fusion(pl, req, resp):
    rows = req.get(G_SELL_UNITS) or []
    base_id = next((r.get(F_UNIT_ID) for r in rows if str(r.get('mnZ5K4Ii')) == '1'), None)
    mat_ids = [r.get(F_UNIT_ID) for r in rows if str(r.get('mnZ5K4Ii')) == '2']
    base = pl['units'].get(base_id)
    if not base:
        return error_response('That unit is not in your unit box.')
    um = unit_mst()
    bm = um.get(base['pn16CNah'], {})
    pattern, maxlv = bm.get('5UvTp7q1', '10'), int(bm.get('EI1DF8Yt') or 1)
    gained = sum(material_exp(bm, pl['units'][m], um.get(pl['units'][m]['pn16CNah'], {}))
                 for m in mat_ids if m in pl['units'])
    fp = fusion_params()
    roll = random.random() * 100
    sup, great = fp['super']['chance'], fp['great']['chance']
    success = 3 if roll < sup else 2 if roll < sup + great else 1
    gained = int(gained * (1, 1, fp['great']['mult'], fp['super']['mult'])[success])
    lv0, total0 = int(base.get('D9wXQI2V') or 1), int(base.get('gQInj3H6') or 0)
    in0 = int(base.get('d96tuT2E') or 0)
    cap = _cum_exp(pattern, maxlv)
    total = min(cap, total0 + gained)
    lv = lv0
    while lv < maxlv and total >= _cum_exp(pattern, lv + 1):
        lv += 1
    in_lv = 0 if lv >= maxlv else total - _cum_exp(pattern, lv)
    frog = any(pl['units'].get(m, {}).get('pn16CNah') in SPHERE_FROGS for m in mat_ids)
    bb0, bb1, sbb0, sbb1 = raise_bb(base, bb_levels_from(base, [pl['units'][m] for m in mat_ids if m in pl['units']]))
    sp0 = _num(base.get('bFQbZh3x')) or 0
    sp_top = _num(base.get('GIO9DTif')) or 100
    sp1 = min(sp_top, sp0 + sp_from(base, [pl['units'][m] for m in mat_ids if m in pl['units']])) if sp0 < sp_top else sp0
    base = dict(base)
    imp = apply_imps(base, [pl['units'][m] for m in mat_ids if m in pl['units']])
    if any(imp):
        log.info('fusion: imps +%s HP +%s ATK +%s DEF +%s REC (now %s)', *imp, '/'.join(base[f] for f in IMP_FIELDS))
    for m in mat_ids:
        pl['units'].pop(m, None)
    if frog and not base.get('5gXxT7LZ'):
        base = dict(base, **{'5gXxT7LZ': 1, 'RXfC31FA': '0' if str(base.get('RXfC31FA')) in ('-1', '') else base['RXfC31FA']})
        log.info('sphere frog: second sphere slot opened')
    base = dict(base, **{'D9wXQI2V': str(lv), 'gQInj3H6': str(total), 'd96tuT2E': str(in_lv)}, **unit_row_stats(base, bm, lv))
    if sp1 != sp0:
        base['bFQbZh3x'] = str(sp1)
        log.info('fusion: SP %s -> %s', sp0, sp1)
    if (bb1, sbb1) != (bb0, sbb0):
        base['3NbeC8AB'], base['RQ5GnFE2'] = str(bb1), str(sbb1)
        log.info('fusion: BB level %s -> %s, SBB level %s -> %s', bb0, bb1, sbb0, sbb1)
    pl['units'][base_id] = base
    # The result screen: 5Hi7MVQe (LvupStatus) "lv:hp:atk:def:rec" for each level passed, and
    # Xoq6z0VI (ParamUpState) the final stats - the game compares the two and shows the gain. Both
    # carry the unit's extra stats (type add + imps); the levels before this fusion with the old imps.
    stat_keys = ('e7DK0FQT', '67CApcti', 'q08xLEsy', 'PWXu25cg')
    add_keys = ('cuIWp89g', 'RT4CtH5d', 'GcMD0hy6', 'C1HZr3pb')
    def shown(L, row):
        st = unit_row_stats(row, bm, L)
        return ':'.join(str(int(st[k]) + (_num(row.get(a)) or 0) + (_num(row.get(e)) or 0))
                        for k, a, e in zip(stat_keys, add_keys, IMP_FIELDS))
    before_row = dict(base, **{f: str((_num(base.get(f)) or 0) - g) for f, g in zip(IMP_FIELDS, imp)})
    steps = ','.join(f"{L}:" + shown(L, before_row if L == lv0 else base) for L in range(lv0, lv + 1))
    final = f"{lv}:" + shown(lv, base)
    zel = ((req.get('mCE3rUu5') or [{}])[0]).get('Rs7bCE3t', '0')
    tmpl = dict((resp.get(G_MIX_RESULT) or [{}])[0])
    tmpl.update({'pn16CNah': base['pn16CNah'], F_UNIT_ID: base_id, 'Najhr8m6': str(zel), 'd96tuT2E': str(gained),
                 '81CsGAR9': str(lv0), 'MD8r7Wa0': str(lv), 'rQ9JVD0w': str(in0), 'K3iqB72Q': str(in_lv),
                 '6hMI5xeF': str(success - 1),           # 0 normal, 1 great, 2 super success
                 'vZH12LoA': str(bb0), '8hVR4Fjr': str(bb1),  # BB level before / after
                 'g9DXe3HZ': str(sbb0), '0xgWq5bD': str(sbb1),  # SBB level before / after
                 '0xhLsnDv': str(sp0), '3YVEeL2a': str(sp1),    # SP before / after
                 'kuHWF7yc': str(sp_top), 'ksED59z9': str(sp_top),
                 '5Hi7MVQe': steps, 'Xoq6z0VI': final})
    resp[G_MIX_RESULT] = [tmpl]
    resp[G_UNITS_ADD] = []
    daily_engine().event(pl, 'fuse', len([m for m in mat_ids if m]))
    log.info('fusion: %s lv %s -> %s (+%s exp%s)', um.get(base['pn16CNah'], {}).get('utP1c0CD', base['pn16CNah']),
             lv0, lv, gained, {1: '', 2: ', GREAT success', 3: ', SUPER success'}[success])
    return show_player(pl, resp)

# --------------------------------------------------------------------------- SP enhancements
# FeSkillGet bx56032l: pn16CNah unit, edy7fq3L unit number, ri6D9yBi the SP skills picked.
# Unit row: bFQbZh3x SP owned, 3RgneFpP SP used, GIO9DTif most SP usable, Fnxab5CN unlocked SP skills
# as "category@skill:skill/category@skill" (how the game's own reader splits it).
FE_FILE = os.path.join(HERE, 'data', 'fe_skills.json')
FE = json.load(open(FE_FILE, encoding='utf-8')) if os.path.exists(FE_FILE) else {}
FE_RESET_GEMS = 3

def fe_unlocked(u):
    out = []
    for part in str(u.get('Fnxab5CN') or '').split('/'):
        if '@' in part:
            out += [x for x in part.split('@', 1)[1].split(':') if x and x != '0']
    return out

def fe_string(unit_id, ids):
    cats = {}
    for sid, cat, _need in FE.get('units', {}).get(str(unit_id), []):
        if sid in ids:
            cats.setdefault(cat, []).append(sid)
    return '/'.join(f"{c}@{':'.join(v)}" for c, v in sorted(cats.items(), key=lambda x: int(x[0])))

def fe_skill_get(pl, req):
    """Unlock SP enhancements. Returns an error text or None."""
    row = (_rows(req.get('bx56032l')) or [{}])[0]
    uid = str(row.get('edy7fq3L') or '')
    u = pl['units'].get(uid)
    if not u:
        return 'That unit is not in your unit box.'
    unit_id = str(u.get('pn16CNah'))
    table = {sid: (cat, need) for sid, cat, need in FE.get('units', {}).get(unit_id, [])}
    if not table:
        return 'This unit has no SP enhancements.'
    raw = str(row.get('ri6D9yBi') or '')
    if raw.strip() in ('', '0'):
        return None
    if '@' in raw:
        picked = [x for part in raw.split('/') if '@' in part for x in re.split(r'[:,]', part.split('@', 1)[1])]
    else:
        picked = re.findall(r'\d+', raw)
    picked = [x for x in picked if x in table]
    have = fe_unlocked(u)
    new = [x for x in dict.fromkeys(picked) if x not in have]
    if not new:
        log.info('SP: nothing new to unlock on %s (request %r)', unit_id, raw)
        return None
    after = set(have) | set(new)
    for x in new:
        need = table[x][1]
        if need.startswith('1@') and need[2:] not in after:
            return 'Unlock the enhancement before it first.'
    cost = sum(int((FE.get('cost') or {}).get(x, 0)) for x in new)
    owned, used = _num(u.get('bFQbZh3x')) or 0, _num(u.get('3RgneFpP')) or 0
    top = _num(u.get('GIO9DTif')) or 100
    if used + cost > min(owned, top):
        return f'Not enough SP ({cost} needed, {min(owned, top) - used} left).'
    u['3RgneFpP'] = str(used + cost)
    u['Fnxab5CN'] = fe_string(unit_id, after)
    log.info('SP: %s unlocked %s for %s SP (%s used of %s)', unit_mst().get(unit_id, {}).get('utP1c0CD', unit_id),
             ','.join(new), cost, used + cost, owned)
    return None

# --------------------------------------------------------------------------- gem shop
# ShopUse 32ibWjFG: 60IsqxDt what (from the game's code: 1 energy, 2 unit box, 3 item box, 4 arena orbs,
# 5 continue, 6 stamps, 7 friend slots, 9 SP reset, 10 repeat-quest energy), 03UGMHxF gems the game
# shows, 5gXxT7LZ how many slots, edy7fq3L the unit (SP reset). Team: Px1X7fcd unit box size,
# 5pjoGBC4 item box size, 3u41PhR2 friend slots.
SHOP_SERVER_TYPES = {'2', '3', '4', '7', '9'}
SHOP_BOX = {'2': ('Px1X7fcd', 'unit box'), '3': ('5pjoGBC4', 'item box'), '7': ('3u41PhR2', 'friend list')}

def shop_use(pl, req):
    row = (_rows(req.get('32ibWjFG')) or [{}])[0]
    kind = str(row.get('60IsqxDt'))
    gems = _num(row.get('03UGMHxF')) or 0
    t = team_row(pl['state'])
    if t is None:
        return None, 'Your account data is missing.'
    if gems < 0:
        return None, 'Nothing to buy.'
    if pl['wallet'].get('gems', 0) < gems:
        return None, f'You need {gems} gems for this.'
    if kind in SHOP_BOX:
        field, what = SHOP_BOX[kind]
        n = _num(row.get('5gXxT7LZ')) or 0
        if n <= 0:
            return None, 'Nothing to buy.'
        before = _num(t.get(field)) or 0
        t[field] = (before + n) if isinstance(t.get(field), int) else str(before + n)
        log.info('gem shop: %s %s -> %s for %s gems', what, before, before + n, gems)
    elif kind == '4':
        top = _num(t.get('9m5FWR8q')) or 3                  # arena orbs back to full
        t['YS2JG9no'] = top if isinstance(t.get('YS2JG9no'), int) else str(top)
        pl['orb_time'] = time.time()
        log.info('gem shop: arena orbs refilled to %s for %s gems', top, gems)
    elif kind == '9':
        u = pl['units'].get(str(row.get('edy7fq3L') or ''))
        if not u:
            return None, 'That unit is not in your unit box.'
        if not fe_unlocked(u) and not _num(u.get('3RgneFpP')):
            return {G_TEAM: [t], G_UNITS: list(pl['units'].values())}, None     # nothing to reset
        u['Fnxab5CN'], u['3RgneFpP'] = '', '0'
        log.info('gem shop: SP reset on %s for %s gems (the game asks %s)', u.get('pn16CNah'), gems, gems)
    pl['wallet']['gems'] -= gems
    t[WALLET['gems']] = pl['wallet']['gems'] if isinstance(t.get(WALLET['gems']), int) else str(pl['wallet']['gems'])
    resp = {G_TEAM: [t]}
    if kind == '9':
        resp[G_UNITS] = list(pl['units'].values())
    return resp, None

# --------------------------------------------------------------------------- spheres
SPHERE_FROGS = {'20302'}

def equip_spheres(pl, req):
    """ItemSphereEqp wx1ZLFj9.a2utCvs8 = "slot1 storage row:slot2 storage row:unit:slot1 item:slot2 item"
    (-1 / 0 = empty). Unit fields: 0R3qTPK9/Ge8Yo32T slot 1, RXfC31FA/mZA7fH2v slot 2 (RXfC31FA -1 =
    the second slot is still locked - a Sphere Frog opens it)."""
    for row in _rows(req.get('wx1ZLFj9')):
        f = (str(row.get('a2utCvs8') or '').split(':') + ['', '', '', '', ''])[:5]
        r1, r2, uid, i1, i2 = f
        u = pl['units'].get(uid)
        if not u:
            return 'That unit is not in your unit box.'
        empty = lambda x: x in ('', '-1', '0')
        locked = str(u.get('RXfC31FA')) == '-1' and not u.get('5gXxT7LZ')
        if locked and not empty(r2):
            return 'The second sphere slot is locked (fuse a Sphere Frog to open it).'
        u['0R3qTPK9'], u['Ge8Yo32T'] = ('0', '0') if empty(r1) else (r1, i1)
        if not locked:
            u['RXfC31FA'], u['mZA7fH2v'] = ('0', '0') if empty(r2) else (r2, i2)
        log.info('spheres on %s: %s / %s', u.get('pn16CNah'), u['Ge8Yo32T'], u.get('mZA7fH2v'))
    return None

# --------------------------------------------------------------------------- evolution
EVO_MST = {}
ELEMENTS = {'1': 'fire', '2': 'water', '3': 'earth', '4': 'thunder', '5': 'light', '6': 'dark'}
EVO_MATS = ('85X6JHQA', 'wh3YRU08', '7MxucW2J', 'j7fTS3ca', 'Hb8yfmv7')

def evo_mst():
    """Evolution recipes from the game's own data (unit -> evolved unit, zel, karma, material units)."""
    if not EVO_MST:
        p = os.path.join(HERE, 'data', 'evo_mst.json')
        if os.path.exists(p):
            EVO_MST.update(json.load(open(p, encoding='utf-8')))
    return EVO_MST

def evo_plan(pl, req):
    """Check an evolution against the recipe. Returns (plan, error)."""
    rows = req.get('8Z2NQrx1') or []
    base_id = next((r.get('inU8Q4gL') for r in rows if str(r.get('mnZ5K4Ii')) == '1'), None)
    mats = [r.get('inU8Q4gL') for r in rows if str(r.get('mnZ5K4Ii')) == '2']
    base = pl['units'].get(base_id)
    if not base:
        return None, 'That unit is not in your unit box.'
    rec = evo_mst().get(str(base['pn16CNah']))
    if not rec:
        return None, 'This unit cannot evolve.'
    if str(rec['to']) in set(admin_settings()['off']['units']):
        return None, 'This evolution is not available yet.'
    want = ((req.get('I82p0wCL') or [{}])[0]).get('pn16CNah')
    if want and str(want) != rec['to']:
        return None, 'That evolution does not match this unit.'
    # 29MgiJIQ 1 = a unit from the unit box, 2 = an item from storage (inU8Q4gL its storage row)
    units = [m for r, m in zip([r for r in rows if str(r.get('mnZ5K4Ii')) == '2'], mats) if str(r.get('29MgiJIQ', '1')) != '2']
    items = [m for r, m in zip([r for r in rows if str(r.get('mnZ5K4Ii')) == '2'], mats) if str(r.get('29MgiJIQ', '1')) == '2']
    if len(set(units)) != len(units) or base_id in units or any(m not in pl['units'] for m in units):
        return None, 'An evolution material is no longer in your unit box.'
    if sorted(pl['units'][m]['pn16CNah'] for m in units) != sorted(rec['mats']):
        return None, 'The evolution materials are not right for this unit.'
    wh = {str(r.get('n6E8iMf3')): r for r in _rows(pl['state'].get(G_WAREHOUSE)) if isinstance(r, dict)}
    need = {}
    for it in rec.get('items', []):
        need[it] = need.get(it, 0) + 1
    have = {}
    for w in items:
        row = wh.get(str(w)) or next((r for r in wh.values() if str(r.get('kixHbe54')) == str(w)), None)
        if row:
            have[row['kixHbe54']] = (_num(row.get('wgV86x1q')) or 0)
    if any(have.get(it, 0) < n for it, n in need.items()):
        return None, 'You do not have the items this evolution needs.'
    return {'base': base_id, 'mats': units, 'items': need, 'to': rec['to'], 'zel': rec['zel'],
            'karma': rec['karma']}, None

def do_evo(pl, plan, resp):
    um = unit_mst()
    base = pl['units'][plan['base']]
    old, new = base['pn16CNah'], plan['to']
    m = um.get(new, {})
    for x in plan['mats']:
        pl['units'].pop(x, None)
    if plan.get('items'):
        wh = [r for r in _rows(pl['state'].get(G_WAREHOUSE)) if isinstance(r, dict)]
        for r in wh:
            n = plan['items'].get(r.get('kixHbe54'), 0)
            if n:
                r['wgV86x1q'] = str(max(0, (_num(r.get('wgV86x1q')) or 0) - n))
        pl['state'][G_WAREHOUSE] = [r for r in wh if (_num(r.get('wgV86x1q')) or 0) > 0]
        resp[G_WAREHOUSE] = pl['state'][G_WAREHOUSE]
    sk, ex = m.get('nj9Lw7mV', '0'), m.get('iEFZ6H19', '0')
    row = dict(base)
    row.update({'pn16CNah': new, 'D9wXQI2V': '1', 'd96tuT2E': '0', 'gQInj3H6': '0',
                'nj9Lw7mV': sk if sk not in ('0', '') else '', '3NbeC8AB': '1' if sk not in ('0', '') else '0',
                'iEFZ6H19': ex if ex not in ('0', '') else '0',
                'iNy0ZU5M': ELEMENTS.get(str(m.get('iNy0ZU5M')), base.get('iNy0ZU5M', ''))}, **unit_stats(m, 1))
    if is_material(new):
        row['nBTx56W9'] = LORD
    pl['units'][plan['base']] = row
    add_to_dictionary(pl, new)
    daily_engine().event(pl, 'evolve')
    highlight('evo', pl, old, new)
    resp['I82p0wCL'] = [{'pn16CNah': new, 'edy7fq3L': plan['base'], 't9FEW2KC': old,
                         'u1ECvfg8': plan['base'], 'dV3qji4I': '0'}]
    resp[G_UNITS_ADD] = []
    if G_MIX_RESULT in resp:
        resp[G_MIX_RESULT] = []
    log.info('evolution: %s -> %s (%s zel, %d materials)', um.get(old, {}).get('utP1c0CD', old),
             m.get('utP1c0CD', new), plan['zel'], len(plan['mats']))
    return show_player(pl, resp)

# --------------------------------------------------------------------------- replay matching
def _flat(obj, prefix=''):
    out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if prefix == '' and k in VOLATILE_GROUPS:
                continue
            out |= _flat(v, f'{prefix}/{k}')
    elif isinstance(obj, list):
        for v in obj:
            out |= _flat(v, prefix)
    else:
        out.add((prefix, str(obj)))
    return out

def pick_capture(pl, rid, req):
    cands = BY_ID.get(rid)
    if not cands:
        return None
    want = _flat(req)
    best = max(len(want & _flat(c['req'])) for c in cands)
    ties = [c for c in cands if len(want & _flat(c['req'])) == best]
    # cycle through equally good matches (e.g. repeated summons on the same banner)
    k = f"{rid}:" + ','.join(str(c['n']) for c in ties)
    i = pl['cursor'].get(k, 0)
    pl['cursor'][k] = i + 1
    return ties[min(i, len(ties) - 1)] if CFG.get('replay_stick_last', True) else ties[i % len(ties)]

def error_response(msg):
    return {G_ERROR: [{F_ERRMSG: msg}]}

# --------------------------------------------------------------------------- action.php
UNSUPPORTED = set()
UNIT_LIST_FROM_SAVE = {'HomeInfoRequest', 'GetUserInfoRequest'}

def find_player(req):
    ui = (req.get(G_USERINFO) or [{}])[0]
    uid = ui.get(F_USERID)
    if uid:
        pl = load_player(uid)
        if pl:
            return pl
    tok = ui.get(F_GL_TOKEN)
    if tok and tok in ACCOUNTS['by_token']:
        acc = ACCOUNTS['by_token'][tok]
        if acc.get('user_id'):
            pl = load_player(acc['user_id'])
            if pl:
                return pl
    # Fallbacks: same guest account or same device -> that account's player.
    glu = ui.get(F_GL_USER)
    dev = ui.get(F_DEVICE) or ui.get(F_DEVICE2)
    for t, acc in ACCOUNTS['by_token'].items():
        if acc.get('user_id') and glu and acc.get('gl_user') == glu:
            pl = load_player(acc['user_id'])
            if pl:
                return pl
    if dev and dev in ACCOUNTS['by_device']:
        glu = ACCOUNTS['by_device'][dev].get('gl_user')
        for t, acc in ACCOUNTS['by_token'].items():
            if acc.get('user_id') and acc.get('gl_user') == glu:
                pl = load_player(acc['user_id'])
                if pl:
                    return pl
    return None

def login_userinfo(pl, resp):
    """URLMst on start-up: the UserInfo row tells the game which account it is."""
    st = (pl['state'].get(G_USERINFO) or [{}])[0]
    rows = resp.get(G_USERINFO) or [{}]
    row = dict(rows[0])
    row.update({F_USERID: pl['user_id'], F_HANDLE: pl['handle'], F_FRIENDID: pl['friend_id'],
                F_TUTO_CH: str(st.get(F_TUTO_CH, row.get(F_TUTO_CH, '12')) or '12'),
                F_TUTO_END: str(st.get(F_TUTO_END, row.get(F_TUTO_END, '1')) or '1'),
                F_GL_USER: pl['gl_user']})
    for f, default in CLIENT_PROGRESS_FIELDS.items():
        row[f] = st.get(f) or default
    resp[G_USERINFO] = [row]
    return resp

def trial_deck(pl):
    """The two Research Lab squads (zsiAn9P1 0 and 1). Until set, both start as the quest squad."""
    if pl.get('trial_deck'):
        return pl['trial_deck']
    rows = [r for r in _rows(pl['state'].get(G_PARTY)) if isinstance(r, dict) and str(r.get(F_PARTY_KIND)) == '1']
    sq = active_squad(pl)
    if not any(str(r.get('zsiAn9P1')) == sq for r in rows):
        sq = min((str(r.get('zsiAn9P1')) for r in rows), key=lambda x: _num(x) or 0, default=sq)
    rows = [r for r in rows if str(r.get('zsiAn9P1')) == sq]
    out = []
    for deck in ('0', '1'):
        for r in rows:
            out.append({'zsiAn9P1': deck, 'edy7fq3L': r.get('edy7fq3L'), 'gr48vsdJ': str(r.get('gr48vsdJ', '1')),
                        'XuJL4pc5': str(r.get('XuJL4pc5', '0'))})
    return out

FRIEND_ACTIONS = {'FriendGetRequest': None, 'FriendGetAgreeRequest': None,
                  'FriendSearchRequest': lambda F: F.search, 'FriendRandomSearchRequest': lambda F: F.random_search,
                  'FriendApplyRequest': lambda F: F.apply, 'FriendAgreeRequest': lambda F: F.agree,
                  'FriendRefuseRequest': lambda F: F.remove, 'FriendDeleteRequest': lambda F: F.remove,
                  'FriendFavoriteRequest': lambda F: F.favorite}

WHO = threading.local()           # which player the request being handled belongs to (for the log)

def handle_action(rid, req):
    info = REQS.get(rid)
    cls = info['cls'] if info else f'unknown:{rid}'
    apply_account_ops()
    with LOCK:
        pl = find_player(req)
        ui = (req.get(G_USERINFO) or [{}])[0]
        if pl:
            LAST_SEEN[pl['user_id']] = time.time()
            WHO.name = pl.get('handle') or pl['user_id']

        if cls == 'CreateUserRequest':
            tok = ui.get(F_GL_TOKEN, '')
            acc = ACCOUNTS['by_token'].get(tok, {})
            pl = new_player(ui.get(F_HANDLE, 'Summoner'), ui.get(F_GL_USER, acc.get('gl_user', '')), tok)
            highlight('joined', len(glob.glob(os.path.join(SAVE_DIR, 'player_*.json'))) + 1)
            acc['user_id'] = pl['user_id']
            ACCOUNTS['by_token'][tok] = acc
            name = account_for_gl(pl['gl_user'])
            if name:
                pl['account'] = name
                ACCOUNTS['users'][name]['user_id'] = pl['user_id']
            save_accounts()
            apply_request(pl, cls, req)
            save_player(pl)
            log.info('new player %s "%s"', pl['user_id'], pl['handle'])
            return {}

        if pl is not None and pl.get('account'):
            u = ACCOUNTS['users'].get(pl['account'])
            tok = ui.get(F_GL_TOKEN)
            if u and tok and tok != u.get('token'):
                log.info('refused %s: %s is signed in on another device', cls, u['name'])
                return error_response('This account was signed in on another device. Close the game and open it again to sign in.')

        if pl is None:
            # Before the account exists: master data and checks only.
            if cls in ('URLMstRequest', 'NgwordCheckRequest'):
                cap = BY_ID.get(rid, [None])[-1]
                resp = json.loads(json.dumps(BLOBS[cap['resp']])) if cap else {}
                if G_USERINFO in resp:
                    resp[G_USERINFO] = [{k: (v if k not in (F_USERID,) else '') for k, v in r.items()} for r in resp[G_USERINFO]]
                return resp
            log.warning('%s from unknown player', cls)
            return error_response('Account not found on this server. Clear the app data and start again.')

        ensure_wallet(pl)
        repair_permits(pl)
        fix_exp_model(pl)
        fix_squad(pl)
        fix_progress(pl)
        materials_to_lord(pl)
        fix_type_stats(pl)
        ensure_recipes(pl)
        if pl.get('selector_fix') != 1:
            fix_selector_overwrites(pl)
            pl['selector_fix'] = 1
        regen_energy(pl)
        try:
            arena_engine().regen_orbs(pl)     # every reply that carries the team row has the right orbs/timer
        except Exception:
            log.exception('arena orbs')
        if pl.get('user_id') == '5tX43IlP' and not pl.get('arena_cutscene_fix'):
            pl['arena_show_from'] = 2         # the game crashed before their Novice -> Amateur cutscene
            pl['arena_cutscene_fix'] = 1
        if pl.get('user_id') == 'eSqQ1Gdd' and not pl.get('frog_fix_81947') and '81947' in pl['units']:
            # a Sphere Frog was fused into Jack-O' before the server knew about Sphere Frogs
            pl['units']['81947'].update({'5gXxT7LZ': 1, 'RXfC31FA': '0'}) if str(pl['units']['81947'].get('RXfC31FA')) == '-1' else None
            pl['frog_fix_81947'] = 1
        deliver_server_gifts(pl)
        try:
            achievement_engine().level_campaign(pl)
        except Exception:
            log.exception('could not check the Level Up Campaign')
        apply_request(pl, cls, req)
        achievement_engine().count(pl, cls)
        if cls == 'ItemMixRequest':
            n = sum(_num(str(r.get('4HqhTf3a', '')).partition(':')[2]) or 1
                    for r in _rows(req.get('JTf2jY5o')) if isinstance(r, dict)) or 1
            daily_engine().event(pl, 'craft', n)
        daily_engine().sync_team(pl)
        # shop use type 1 = refill button, 10 = the automatic refill between repeat-quest runs
        if cls == 'ShopUseRequest' and str(((req.get('32ibWjFG') or [{}])[0]).get('60IsqxDt')) in ('1', '10'):
            t = team_row(pl['state'])
            if t is not None:          # energy refill: back to full
                t[F_AP] = t.get(F_MAXAP, t.get(F_AP))
                pl['ap_time'] = time.time()
                log.info('energy refilled to %s', t[F_AP])
        if cls in ('GetDistributeDungeonKeyInfoRequest', 'DungeonKeyReceiptRequest', 'DungeonKeyUseRequest'):
            if cls == 'DungeonKeyReceiptRequest':
                receive_keys(pl, req)
            elif cls == 'DungeonKeyUseRequest':
                err = use_key(pl, req)
                if err:
                    save_player(pl)
                    return error_response(err)
            rows = key_rows(pl)
            resp = {G_SIGNAL: [{F_SIGNAL: pl['signal_key']}], G_KEYS: rows,
                    G_BADGE: [{'dD40xrsh': str(sum(1 for r in rows if r['g85qMNxf'] == '1'))}]}
            key_opens(pl)
            if cls == 'DungeonKeyUseRequest':       # bfrevive answers with keys + both unlock lists
                resp.pop(G_BADGE, None)
                if UNLOCK:
                    resp[G_PERMIT] = pl['state'][G_PERMIT] = story_permits(pl)
                resp['Y73tHKS8'] = sp_permits(pl)
                resp['Y73mHKS8'] = []
                key_opens(pl)
            save_player(pl)
            return resp
        if cls == 'MissionStartRequest':
            err = start_mission(pl, req)
            if err:
                save_player(pl)
                return error_response(err)
            mid = pl.get('current_mission') or ''
            # always a fresh run built from references - never a recorded battle replayed
            base = personalise(pl, BLOBS[next(iter(REC_START.values()))['resp']])
            resp = generate_mission_start(pl, mid, req, base)
            pl['last_battle'] = resp[G_MNUM][0]['LE6JkUp7']
            bst = mission_boost(mid)
            for row in _rows(resp.get(G_MNUM)):
                if isinstance(row, dict):       # the game multiplies the zel / karma monsters drop
                    row['nA95Bdj6'] = str(BOOST_TYPES[bst['type']]) if bst else '0'
                    row['5Z1LNoyH'] = _rate_text(bst['rate']) if bst else '1'
            if bst:
                log.info('  reward boost: %s x%s', bst['type'], _rate_text(bst['rate']))
            save_player(pl)
            return resp

        if cls == 'MissionContinueRequest':           # carry on after a wipe: 1 gem, like bfrevive
            if is_trial(pl.get('current_mission')):
                save_player(pl)
                return error_response("You can't continue in the Research Lab.")
            err = check_and_apply(pl, {'gems': -1})
            if err:
                save_player(pl)
                return error_response(err)
            pl['_continued'] = pl.get('current_mission')
            log.info('continue: -1 gem (%s left)', pl['wallet'].get('gems', 0))
            resp = {G_TEAM: [dict(team_row(pl['state']) or {})]}
            apply_wallet(pl, resp)
            apply_overrides(pl, resp)
            save_player(pl)
            return resp
        if cls in ('TrialDeckGetRequest', 'TrialDeckEditRequest'):
            if cls == 'TrialDeckEditRequest' and isinstance(req.get('PnjQ49wK'), list):
                pl['trial_deck'] = [r for r in req['PnjQ49wK'] if isinstance(r, dict)]
                resp = {}
            else:
                resp = {'PnjQ49wK': trial_deck(pl)}
            save_player(pl)
            return resp
        if cls in FRIEND_ACTIONS:
            F = friend_engine()
            if cls in ('FriendGetRequest', 'FriendGetAgreeRequest'):
                resp, err = F.get(pl, req), None
            else:
                resp, err = FRIEND_ACTIONS[cls](F)(pl, req)
            if err:
                save_player(pl)
                return error_response(err)
            save_player(pl)
            return resp
        if cls in ('DailyTaskUserInfoRequest', 'DailyTaskClaimRewardRequest'):
            D = daily_engine()
            if cls == 'DailyTaskUserInfoRequest':
                resp, err = D.info(pl), None
            else:
                resp, err = D.claim(pl, req)
            if err:
                save_player(pl)
                log.info('brave points prize refused: %s', err)
                return error_response(err)
            apply_wallet(pl, resp)
            save_player(pl)
            return resp
        if cls == 'ShopUseRequest' and str(((req.get('32ibWjFG') or [{}])[0]).get('60IsqxDt')) in SHOP_SERVER_TYPES:
            resp, err = shop_use(pl, req)
            save_player(pl)
            if err:
                log.info('gem shop refused: %s', err)
                return error_response(err)
            return resp
        if cls == 'FeSkillGetRequest':
            err = fe_skill_get(pl, req)
            save_player(pl)
            if err:
                log.info('SP enhancement refused: %s', err)
                return error_response(err)
            return show_player(pl, {G_UNITS: [], G_TEAM: []})
        if cls == 'ItemSphereEqpRequest':
            err = equip_spheres(pl, req)
            save_player(pl)
            if err:
                log.info('sphere equip refused: %s', err)
                return error_response(err)
            return {}
        if cls in ('AchievementDeliverRequest', 'AchievementTradeRequest'):
            E = achievement_engine()
            resp, err = (E.deliver if cls == 'AchievementDeliverRequest' else E.trade)(pl, req)
            if err:
                save_player(pl)
                log.info('%s refused: %s', cls, err)
                return error_response(err)
            apply_wallet(pl, resp)
            save_player(pl)
            return resp
        if cls in ('GetAchievementInfoRequest', 'AchievementRewardReceiveRequest', 'AchievementAcceptRequest'):
            E = achievement_engine()
            resp = (E.info if cls == 'GetAchievementInfoRequest' else
                    E.claim if cls == 'AchievementRewardReceiveRequest' else E.accept)(pl, req)
            if cls == 'AchievementRewardReceiveRequest':
                resp[G_BADGE] = {'dUujjBBK': achievement_engine().badges(pl)}   # like the real reply
            save_player(pl)
            return resp
        if cls in ('ArenaInfoRequest', 'ArenaMatchingRequest', 'ArenaBattleStartRequest', 'ArenaBattleEndRequest',
                   'ArenaFriendListRequest'):
            A = arena_engine()
            A.regen_orbs(pl)
            if cls == 'ArenaInfoRequest':
                resp = {'8jBJ7uKR': [A.info(pl)]}
            elif cls == 'ArenaMatchingRequest':
                resp = A.matching(pl)
            elif cls == 'ArenaBattleStartRequest':
                resp, err = A.start(pl, req)
                if err:
                    save_player(pl)
                    return error_response(err)
            elif cls == 'ArenaBattleEndRequest':
                resp = A.end(pl, req, pl['state'].get(G_BADGE))
            else:
                resp = A.ranking(pl)
            apply_wallet(pl, resp)
            apply_overrides(pl, resp)
            save_player(pl)
            return resp
        if cls in ('PresentListRequest', 'PresentReceiptRequest', 'DailyLoginRequest', 'SlotActionRequest'):
            if cls == 'SlotActionRequest':
                resp, err = slot_spin(pl, req)
                if err:
                    save_player(pl)
                    return error_response(err)
            elif cls == 'PresentListRequest':
                resp = {G_PRESENTS: present_rows(pl)}
            elif cls == 'PresentReceiptRequest':
                resp = receive_presents(pl, req)
            else:                                   # the daily login chests
                resp = daily_spin(pl)
            apply_wallet(pl, resp)
            apply_overrides(pl, resp)
            save_player(pl)
            return resp
        if cls in ('FixGiftInfoRequest', 'GetGiftInfoRequest'):
            # friend gifts between players here (giftbox.py) - never the recordings' gift lists
            resp = gift_box().fix(pl, req) if cls == 'FixGiftInfoRequest' else {G_FRIEND_GIFTS: gift_box().rows(pl)}
            pl['state'][G_FRIEND_GIFTS] = []
            save_player(pl)
            return resp
        if cls == 'GetUserInfoRequest':
            welcome_gifts(pl)
            login_gifts(pl)
            resp = full_user_info(pl)
        elif cls == 'URLMstRequest':
            # Always answer as an existing account (latest capture), filled with this player.
            cap = BY_ID[rid][-1]
            resp = login_userinfo(pl, personalise(pl, BLOBS[cap['resp']]))
            welcome_gifts(pl)
            login_gifts(pl)
            resp['Drudr2w5'] = login_bonus_info(pl)
            lcg = login_campaign_groups(pl)
            resp['Bd29Pqw0'], resp['bD18x9Ti'] = lcg['Bd29Pqw0'], lcg['bD18x9Ti']
            try:
                resp['KeC10fuL'] = vortex_editor().patch_versions(resp.get('KeC10fuL') or [])
                log.info('tables offered to the game: %s', ', '.join(
                    f"{r.get('moWQ30GH')}={r.get('d2RFtP8T')}" for r in resp['KeC10fuL']
                    if r.get('moWQ30GH') in ('F_AREA_MST', 'F_DUNGEON_MST', 'F_MISSION_MST')))
            except Exception:
                log.exception('could not offer the edited Vortex tables')
            try:
                resp['KeC10fuL'] = evo_gate().patch_versions(resp.get('KeC10fuL') or [])
            except Exception:
                log.exception('could not offer the edited evolution table')
            try:
                resp['KeC10fuL'] = sell_price().patch_versions(resp.get('KeC10fuL') or [])
            except Exception:
                log.exception('could not offer the edited unit table (sell prices)')
            try:
                patch_define_mst(resp)
            except Exception:
                log.exception('could not set the fusion exp multipliers')
            dt = daily_engine().info(pl)
            for g in ('p283g07d', 'a739yK18', 'k23D7d43'):
                if g in resp:
                    resp[g] = dt[g]
            tok = ui.get(F_GL_TOKEN)
            if tok and ACCOUNTS['by_token'].get(tok, {}).get('user_id') != pl['user_id']:
                ACCOUNTS['by_token'][tok] = {'gl_user': pl['gl_user'], 'user_id': pl['user_id']}
                save_accounts()
            log.info('welcome back %s "%s"', pl['user_id'], pl['handle'])
        else:
            cap = pick_capture(pl, rid, req)
            if cap is None:
                if cls not in UNSUPPORTED:
                    UNSUPPORTED.add(cls)
                log.warning('NOT CAPTURED YET: %s (%s)', cls, rid)
                save_player(pl)
                return error_response(f'Not available on this private server yet ({cls}).')
            if cls == 'UnitEvoRequest':
                pl['_evo'], err = evo_plan(pl, req)
                if err:
                    pl.pop('_evo', None)
                    save_player(pl)
                    log.info('evolution refused: %s (request %s %s)', err, req.get('8Z2NQrx1'), req.get('I82p0wCL'))
                    return error_response(err)
            err = check_and_apply(pl, wallet_change(pl, cls, req, cap))
            if err:
                pl.pop('_evo', None)
                save_player(pl)
                log.info('%s refused: %s', cls, err)
                return error_response(err)
            resp = personalise(pl, BLOBS[cap['resp']])
            if cls == 'GachaActionRequest' and POOLS:
                resp = show_player(pl, do_summon(pl, req, resp))
            elif cls == 'UnitMixRequest':
                resp = do_fusion(pl, req, resp)
            elif cls == 'UnitEvoRequest':
                resp = do_evo(pl, pl.pop('_evo'), resp)
            else:
                REQ_NOW['req'] = req
                resp = merge_replay(pl, cls, cap, resp)
        apply_wallet(pl, resp)
        apply_overrides(pl, resp)
        save_player(pl)
        return resp

# --------------------------------------------------------------------------- HTTP
PLAIN = T['plain']

def plain_capture(path):
    v = PLAIN.get(path)
    return v[-1] if v else None

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'bfserver'

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype='application/json; charset=utf-8', extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, separators=(',', ':'), ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode('utf-8')
        hdrs = {'Content-Type': ctype}
        hdrs.update(extra or {})
        if len(body) > 1024 and 'gzip' in self.headers.get('Accept-Encoding', ''):
            body = gzip.compress(body, 6)
            hdrs['Content-Encoding'] = 'gzip'
        self.send_response(code)
        for k, v in hdrs.items():
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.command != 'HEAD':
            try:
                self.wfile.write(body)
            except (TimeoutError, ConnectionError, ssl.SSLError, OSError) as e:
                # the phone stopped listening (lost signal, app closed or in the background) - nothing to fix
                self.close_connection = True
                log.warning('reply not delivered to %s (%s): the connection dropped (%s)',
                            getattr(WHO, 'name', None) or self.client_address[0], self.path, type(e).__name__)

    def send_file(self, fp, ctype, extra=None):
        """Big files (the app): sent in pieces, not read into memory."""
        size = os.path.getsize(fp)
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(size))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command == 'HEAD':
            return
        log.info('app download started by %s: %s', self.client_address[0], os.path.basename(fp))
        sent = 0
        try:
            with open(fp, 'rb') as f:
                while True:
                    chunk = f.read(1 << 20)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    sent += len(chunk)
        except (TimeoutError, ConnectionError, OSError) as e:
            # the phone went away mid-download (lost signal, closed the browser): nothing to fix here
            log.info('app download by %s stopped after %d of %d MB (%s)', self.client_address[0],
                     sent >> 20, size >> 20, e.__class__.__name__)
            self.close_connection = True
            return
        log.info('app download finished by %s (%d MB)', self.client_address[0], size >> 20)

    def do_HEAD(self):
        self.do_GET()

    def summon_web(self, path, query, raw):
        """The game's summon viewer (tap the summon picture): rates and units of that summon."""
        if path.startswith('/bf/web/unit_icon/'):
            uid = os.path.basename(path).split('.')[0]
            b, ct = (admin_page().image(f'unit/{uid}.png') if uid.isdigit() else (None, None))
            return self._send(200, b, ct, {'Cache-Control': 'max-age=86400'}) if b else self._send(404, '', 'text/plain')
        from urllib.parse import unquote_plus
        texts = [unquote_plus(query or ''), unquote_plus(raw.decode('utf-8', 'replace'))]
        texts += [unquote_plus(x) for t in list(texts) for x in t.replace('&', '=').split('=')]
        E = summon_editor()
        gid = E.find_summon(texts)
        log.info('summon viewer: %s (asked with %s)', gid or 'not found', (query or raw.decode('utf-8', 'replace'))[:200])
        return self._send(200, E.rates_page(gid), 'text/html; charset=utf-8')

    def do_GET(self):
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)
        try:
            if p == '/admin' or p.startswith('/admin/'):
                code, ctype, body, hdr = admin_page().handle(self, 'GET', p, b'')
                return self._send(code, body, ctype, hdr)
            if p == '/dls':
                log.info('discovery from %s', self.client_address[0])
                return self._send(200, {'SREE': enc_dls({
                    'game': PUBLIC, 'resource': PUBLIC, 'mstv': CFG.get('mstv', '21900'),
                    'gumilive': PUBLIC + '/', 'bgimage': '/wallpaper/' + CFG.get('title_wallpaper', 'title3_logo.jpg')})})
            if p == '/app/check':
                have = (q.get('v') or [''])[0]
                return self._send(200, app_update().check(have, HOST), 'text/plain; charset=utf-8')
            if p.startswith('/app/'):
                fp = app_update().file(p[len('/app/'):])
                if not fp:
                    return self._send(404, 'No such app file.', 'text/plain; charset=utf-8')
                return self.send_file(fp, 'application/vnd.android.package-archive',
                                      {'Content-Disposition': f'attachment; filename="{os.path.basename(fp)}"'})
            if p.startswith('/accounts/guest/login'):
                dev = (q.get('vid') or [''])[0] or (q.get('altvid') or [''])[0] or self.client_address[0]
                apply_account_ops()
                if REQUIRE_ACCOUNTS and not (dev.startswith('acct-') and dev in ACCOUNTS['by_device']):
                    log.info('login refused: not signed in (device %s) - this app needs the sign-in update', dev[:12])
                    return self._send(200, {'status': 'failed', 'status_no': '1',
                                            'message': 'Please update the app and sign in.'})
                with LOCK:
                    acc = ACCOUNTS['by_device'].get(dev)
                    if not acc:
                        acc = {'gl_user': rand_id(), 'token': rand_id(32).lower()}
                        ACCOUNTS['by_device'][dev] = acc
                        ACCOUNTS['by_token'][acc['token']] = {'gl_user': acc['gl_user']}
                        save_accounts()
                    log.info('guest login device=%s -> %s', dev[:12], acc['gl_user'])
                    return self._send(200, {'status': 'successful', 'token': acc['token'],
                                            'game_user_id': acc['gl_user'], 'status_no': '0'})
            if p.startswith('/accounts/'):
                return self._send(200, plain_capture(p) or {'status': 'successful'})
            if p == '/bf/gme/action/getServerTime.php':
                return self._send(200, str(int(time.time())), 'text/html; charset=utf-8')
            if p == '/bf/gme/maintenanceClient.php':
                return self._send(200, '', 'text/html; charset=utf-8')
            if p.startswith('/bf/gme/'):
                v = plain_capture(p)
                if v is not None:
                    return self._send(200, feature_check(v) if p.endswith('featureCheck.php') else v)
            if p == '/bf/web/summon_rates.php' or p.startswith('/bf/web/unit_icon/'):
                return self.summon_web(p, u.query, b'')
            if p.startswith('/bf/web/slots/'):
                log.info('Brave Slots prize page opened')
                return self._send(200, slot_prizes_page(), 'text/html; charset=utf-8')
            if '/web/notice/' in p:
                return self._send(200, news_engine().page(), 'text/html; charset=utf-8')
            if p.startswith('/bf/web/') or p.startswith('/views/'):
                return self._send(200, '<html><body style="background:#111;color:#ddd;font-family:sans-serif">'
                                       '<h3>Private server</h3><p>No news here.</p></body></html>',
                                  'text/html; charset=utf-8')
            return self.serve_cdn(p)
        except Exception:
            log.exception('GET %s failed', self.path)
            return self._send(500, {'error': 'server error'})

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get('Content-Length', 0))
        raw = self.rfile.read(n) if n else b''
        try:
            if u.path.startswith('/admin/'):
                code, ctype, body, hdr = admin_page().handle(self, 'POST', u.path, raw)
                return self._send(code, body, ctype, hdr)
            if u.path.startswith('/accounts/priv/'):
                text = account_api(u.path.rstrip('/'), _parse_qs(raw.decode('utf-8', 'replace')))
                return self._send(200, text, 'text/plain; charset=utf-8')
            if u.path == '/bf/gme/action.php':
                env = json.loads(raw)
                rid = env['F4q6i9xe']['Hhgi79M1']
                if rid not in REQS:
                    log.warning('unknown request id %s', rid)
                    return self._send(200, {'F4q6i9xe': {'Hhgi79M1': rid, 'aV6cLn3v': '---'}, 'a3vSYuq2': {}})
                req = dec_body(rid, env['a3vSYuq2']['Kn51uR4Y'])
                t0 = time.time()
                WHO.name = ''
                resp = handle_action(rid, req)
                if resp is None:
                    resp = {}             # the game crashes on a null reply; bfrevive sends {}
                if isinstance(resp, dict) and isinstance(resp.get('h23iRjGN'), dict):
                    resp = fix_badges(req, resp)
                resp = patch_summons(resp)
                try:
                    add_display_name(resp)
                except Exception:
                    log.exception('could not add the display name')
                try:
                    add_team_counts(req, resp)
                except Exception:
                    log.exception('could not add the gift / friend request counts')
                log.info('%-34s %5.0f ms  [%s]', REQS[rid]['cls'], (time.time() - t0) * 1000,
                         re.sub(r'<[^>]+>', '', getattr(WHO, 'name', '') or '-'))
                return self._send(200, {'F4q6i9xe': {'Hhgi79M1': rid, 'aV6cLn3v': '---'},
                                        'a3vSYuq2': {F_SIGNAL: enc_body(rid, resp)}},
                                  'text/html; charset=utf-8')
            if u.path == '/bf/web/summon_rates.php' or u.path.startswith('/bf/web/unit_icon/'):
                return self.summon_web(u.path, u.query, raw)
            if u.path.startswith('/bf/web/slots/'):
                log.info('Brave Slots prize page opened')
                return self._send(200, slot_prizes_page(), 'text/html; charset=utf-8')
            if u.path.startswith('/bf/gme/'):
                v = plain_capture(u.path)
                if v is not None:
                    return self._send(200, feature_check(v) if u.path.endswith('featureCheck.php') else v)
            log.warning('unhandled POST %s', self.path)
            return self._send(404, {'error': 'not found'})
        except Exception:
            log.exception('POST %s failed', self.path)
            return self._send(500, {'error': 'server error'})

    # ---- content (art, sound, data files)
    def serve_cdn(self, p):
        from urllib.parse import unquote
        p = unquote(p)        # the game sometimes asks for sgtext%5f...: the same file as sgtext_...
        rel = os.path.normpath(p.lstrip('/')).replace('\\', '/')
        if rel.startswith('..'):
            return self._send(403, '')
        fp = os.path.join(CDN_DIR, rel)
        if os.path.isfile(fp):
            ext = os.path.splitext(fp)[1].lower()
            ctype = {'.png': 'image/png', '.jpg': 'image/jpeg', '.mp3': 'audio/mpeg', '.ogg': 'audio/ogg',
                     '.json': 'application/json', '.zip': 'application/zip', '.html': 'text/html'}.get(ext, 'application/octet-stream')
            data = open(fp, 'rb').read()
            if '/mst/' in p or 'sp_quest_banner_custom_' in p:
                log.info('sent to the game: %s (%d bytes)', p, len(data))
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            if self.command != 'HEAD':
                try:
                    self.wfile.write(data)
                except (TimeoutError, ConnectionError, ssl.SSLError, OSError) as e:
                    # the game closed the connection while a file was on its way (app closed, lost
                    # signal, a download it no longer needed): it asks again when it needs the file
                    log.info('content download cut off by the game: %s (%s)', p, e.__class__.__name__)
                    self.close_connection = True
            return
        if CFG.get('fetch_missing_content') and p.startswith('/content/'):
            data = fetch_upstream(p)
            if data is not None:
                os.makedirs(os.path.dirname(fp), exist_ok=True)
                open(fp, 'wb').write(data)
                log.info('fetched from bfrevive: %s', p)
                return self.serve_cdn(p)
        log.warning('missing content: %s', p)
        return self._send(404, '', 'text/plain')

# --------------------------------------------------------------------------- upstream content
UPSTREAM = CFG.get('content_source', 'https://cdn.bfrevive.net')
_UP_CTX = ssl.create_default_context()
if os.path.exists(os.path.join(HERE, 'certs', 'bfrevive_ca.crt')):
    _UP_CTX.load_verify_locations(os.path.join(HERE, 'certs', 'bfrevive_ca.crt'))

def fetch_upstream(path):
    import urllib.request, urllib.error
    req = urllib.request.Request(f'{UPSTREAM}{path}?v=132', headers={
        'User-Agent': 'Apache-HttpClient/UNAVAILABLE (java 1.5)', 'Accept-Encoding': 'gzip'})
    try:
        with urllib.request.urlopen(req, context=_UP_CTX, timeout=30) as r:
            data = r.read()
            return gzip.decompress(data) if r.headers.get('Content-Encoding') == 'gzip' else data
    except Exception as e:
        log.warning('could not fetch %s from bfrevive: %s', path, e)
        return None

# --------------------------------------------------------------------------- TLS
def make_cert():
    ca_cert = x509.load_pem_x509_certificate(open(os.path.join(HERE, 'certs', 'ca.crt'), 'rb').read())
    ca_key = serialization.load_pem_private_key(open(os.path.join(HERE, 'certs', 'ca.key'), 'rb').read(), None)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    try:
        san = [x509.IPAddress(ipaddress.ip_address(HOST))]
    except ValueError:
        san = [x509.DNSName(HOST)]
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST)]))
            .issuer_name(ca_cert.subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=2))
            .not_valid_after(now + datetime.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=True, content_commitment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    crt, kf = os.path.join(HERE, 'certs', 'server.crt'), os.path.join(HERE, 'certs', 'server.key')
    open(crt, 'wb').write(cert.public_bytes(serialization.Encoding.PEM))
    open(kf, 'wb').write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                           serialization.NoEncryption()))
    return crt, kf

def main():
    crt, kf = make_cert()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(crt, kf)
    class TLSServer(ThreadingHTTPServer):
        daemon_threads = True
        def finish_request(self, request, client_address):
            # TLS handshake per connection, in its own thread, with logging
            try:
                request.settimeout(30)
                conn = ctx.wrap_socket(request, server_side=True)
            except Exception as e:
                log.warning('connection from %s failed during secure handshake: %s', client_address[0], e)
                return
            log.info('connection from %s', client_address[0]) if CFG.get('log_connections', True) else None
            self.RequestHandlerClass(conn, client_address, self)
    srv = TLSServer(('0.0.0.0', PORT), Handler)
    # The game downloads content (art, sound, data files) over plain HTTP on port 80.
    http_port = int(CFG.get('http_port', 80))
    if http_port:
        try:
            plain = ThreadingHTTPServer(('0.0.0.0', http_port), Handler)
            plain.daemon_threads = True
            threading.Thread(target=plain.serve_forever, daemon=True).start()
            log.info('content downloads on http://%s:%d', HOST, http_port)
        except OSError as e:
            log.error('could not open port %d for content downloads (%s) - the game will not be able to '
                      'download files. Close any program using port %d, or run START-HERE as administrator.',
                      http_port, e, http_port)
    n_cdn = sum(len(f) for _, _, f in os.walk(CDN_DIR)) if os.path.isdir(CDN_DIR) else 0
    log.info('Brave Frontier private server on https://%s  (%d captured responses, %d content files)',
             PUBLIC, len(T['action']), n_cdn)
    log.info('Saves: %s   Press Ctrl+C to stop.', SAVE_DIR)
    try:
        import discordlog
        d = discordlog.Discord(sys.modules[__name__])
        if d.url:
            log.addHandler(discordlog.ErrorHandler(d))
            d.started()
            log.info('posting server news to Discord')
    except Exception:
        log.exception('could not set up the Discord posts')
    try:
        vortex_editor().build()
    except Exception:
        log.exception('could not prepare the edited Vortex tables')
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log.info('stopped')

if __name__ == '__main__':
    main()
