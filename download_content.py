#!/usr/bin/env python3
"""
Download Brave Frontier game content from bfrevive's content server straight
into this server's cdn\\ folder (no capture file needed).

Where the file names come from:
  0. the game's own resource table (~49,000 files with exact folders)
  1. every content URL found in your .flow recordings (exact)
  2. every unit / enemy ID known (archive + wiki) -> unit art, animations
  3. an archived list of ~11,000 game file names
  4. files discovered inside downloaded files (effects list their textures,
     event scripts list backgrounds and music)
  5. "missing content" lines in server.log (what the game actually asked for)

Safe to stop (Ctrl+C) and run again: finished files and confirmed-missing
files are remembered in dl_state.json.

Usage:  python download_content.py            (everything)
        python download_content.py --log-only (only what server.log says is missing)
"""
import glob, gzip, json, os, re, ssl, sys, threading, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'cdn')
BASE = os.environ.get('BF_CDN_BASE', 'https://cdn.bfrevive.net')
VERSION = '132'
WORKERS = 6
STATE_FILE = os.path.join(HERE, 'dl_state.json')
SEED = os.path.join(HERE, 'data', 'dl_seed.json')
UA = 'Apache-HttpClient/UNAVAILABLE (java 1.5)'

# ----------------------------------------------------------------------------- state
state = {'done': [], 'missing': []}
if os.path.exists(STATE_FILE):
    try:
        state = json.load(open(STATE_FILE))
    except Exception:
        pass
DONE, MISSING = set(state['done']), set(state['missing'])
LOCK = threading.Lock()
SEEN_GROUPS = set()

def save_state():
    with LOCK:
        tmp = STATE_FILE + '.tmp'
        json.dump({'done': sorted(DONE), 'missing': sorted(MISSING)}, open(tmp, 'w'))
        os.replace(tmp, STATE_FILE)

# ----------------------------------------------------------------------------- http
CTX = ssl.create_default_context()
BFREVIVE_CA = os.path.join(HERE, 'certs', 'bfrevive_ca.crt')
if os.path.exists(BFREVIVE_CA):
    CTX.load_verify_locations(BFREVIVE_CA)

def fetch(path):
    """Returns bytes, None for 404, raises on other errors."""
    url = f'{BASE}{path}?v={VERSION}'
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Encoding': 'gzip'})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
                data = r.read()
                if r.headers.get('Content-Encoding') == 'gzip':
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as e:
            if e.code in (403, 404, 410):
                return None
            if e.code == 429:
                time.sleep(10 + attempt * 10)
                continue
            time.sleep(2 + attempt * 3)
        except Exception:
            time.sleep(2 + attempt * 3)
    raise RuntimeError(f'gave up on {path}')

def local_path(path):
    rel = os.path.normpath(path.lstrip('/'))
    return os.path.join(OUT, rel)

# ----------------------------------------------------------------------------- name rules
SIZE_SUFFIX = re.compile(r'_\d+x\d+(?:_\d+)?$')
DIR_HINTS = {}   # first word of file name -> folders seen for it

def learn_dir(path):
    d, f = path.rsplit('/', 1)
    key = f.split('_')[0].split('.')[0].lower()
    if '/sam/' not in d:
        DIR_HINTS.setdefault(key, [])
        if d not in DIR_HINTS[key]:
            DIR_HINTS[key].append(d)

def candidates(name):
    """Possible full paths for a bare file name, most likely first."""
    n, low = name, name.lower()
    stem, ext = os.path.splitext(n)
    out = []
    if ext == '.sam':
        out.append(f'/content/sam/{stem}/{n}')
    elif ext in ('.png', '.jpg') and SIZE_SUFFIX.search(stem):
        s = SIZE_SUFFIX.sub('', stem)
        out.append(f'/content/sam/{s}/{n}')
    if low.startswith('unit_'):
        sub = 'img' if ext == '.png' else ('cgg' if 'cgg' in low else 'cgs')
        out.append(f'/content/unit/{sub}/{n}')
    if low.startswith('monster_'):
        sub = 'img' if ext == '.png' else ('cgg' if 'cgg' in low else 'cgs')
        out += [f'/content/monster/{sub}/{n}', f'/content/unit/{sub}/{n}']
    if low.startswith('dungeon_'):
        out.append(f'/content/dungeon/{n}')
    if ext == '.mp3':
        out.append(f'/content/sound/{n}')
    if re.match(r'map\d+-dungeon', low) or low.startswith('navi_chara') or low.startswith('thum_navi_chara'):
        out.append(f'/content/event/{n}')
    if ext == '.plist':
        out += [f'/content/effect/plist/{n}', f'/content/effect/{n}']
    if re.match(r'(item|sphere|weapon|armor|accessory|material)_?thum', low) or low.startswith('item_'):
        out.append(f'/content/item/{n}')
    if low.startswith('layout_') and ext == '.csv':
        out.append(f'/content/_dlcbundle/{n}')
    key = low.split('_')[0].split('.')[0]
    for d in DIR_HINTS.get(key, [])[:4]:
        out.append(f'{d}/{n}')
    seen, res = set(), []
    for p in out:
        if p not in seen:
            seen.add(p); res.append(p)
    return res

def unit_files(uid):
    return [f'/content/unit/img/unit_ills_thum_{uid}.png', f'/content/unit/img/unit_ills_full_{uid}.png',
            f'/content/unit/img/unit_ills_battle_{uid}.png', f'/content/unit/img/unit_anime_{uid}.png',
            f'/content/unit/cgg/unit_cgg_{uid}.csv', f'/content/unit/cgs/unit_idle_cgs_{uid}.csv',
            f'/content/unit/cgs/unit_atk_cgs_{uid}.csv', f'/content/unit/cgs/unit_move_cgs_{uid}.csv']

# ----------------------------------------------------------------------------- discovery inside files
REF_PATH = re.compile(rb'(/[A-Za-z0-9_\-/]+\.(?:png|jpg|mp3|sam|csv|txt|plist))')
REF_NAME = re.compile(rb'([A-Za-z0-9_\-]+\.(?:png|jpg|mp3|sam|plist))')

def discover(path, data):
    found = []
    d = path.rsplit('/', 1)[0]
    if path.endswith('.sam') or path.endswith('.plist'):
        for m in set(REF_NAME.findall(data)):
            found.append([f'{d}/{m.decode()}'])
    elif path.endswith('.txt') or path.endswith('.csv'):
        for m in set(REF_PATH.findall(data)):
            p = m.decode()
            found.append([p if p.startswith('/content/') else '/content' + p])
        for m in set(REF_NAME.findall(data)):
            c = candidates(m.decode())
            if c:
                found.append(c)
    return found

# ----------------------------------------------------------------------------- work
stats = {'new': 0, 'have': 0, 'missing': 0, 'bytes': 0}

def try_group(group):
    """Try each candidate path until one exists. Returns follow-up groups."""
    for path in group:
        lp = local_path(path)
        if path in DONE or os.path.exists(lp):
            with LOCK:
                DONE.add(path); stats['have'] += 1
            return []
        if path in MISSING:
            continue
        data = fetch(path)
        if data is None:
            with LOCK:
                MISSING.add(path)
            continue
        os.makedirs(os.path.dirname(lp), exist_ok=True)
        open(lp, 'wb').write(data)
        with LOCK:
            DONE.add(path); stats['new'] += 1; stats['bytes'] += len(data)
        learn_dir(path)
        return discover(path, data)
    with LOCK:
        stats['missing'] += 1
    return []

def run(groups):
    queue = []
    def add(g):
        k = tuple(g)
        if g and k not in SEEN_GROUPS:
            SEEN_GROUPS.add(k); queue.append(g)
    for g in groups:
        add(g)
    t0, last = time.time(), 0
    with ThreadPoolExecutor(WORKERS) as ex:
        while queue:
            batch, queue[:] = queue[:], []
            futs = [ex.submit(try_group, g) for g in batch]
            for i, f in enumerate(as_completed(futs), 1):
                try:
                    for g in f.result():
                        add(g)
                except Exception as e:
                    print('  error:', e)
                if time.time() - last > 5:
                    last = time.time()
                    print(f'  new {stats["new"]}  already had {stats["have"]}  not on server {stats["missing"]}'
                          f'  ({stats["bytes"] / 1e6:.0f} MB, {len(batch) - i + len(queue)} queued)', flush=True)
                    save_state()
    save_state()
    print(f'done in {time.time() - t0:.0f}s: {stats["new"]} new files ({stats["bytes"] / 1e6:.0f} MB), '
          f'{stats["have"]} already present, {stats["missing"]} not found on the server')

# ----------------------------------------------------------------------------- seeds
def flow_paths():
    """Content URLs inside any .flow recording next to the bfserver folder (plain text scan)."""
    paths = set()
    for f in glob.glob(os.path.join(HERE, '..', '*')):
        if os.path.isfile(f) and (f.endswith('.flow') or os.path.basename(f).startswith('flows')):
            print('scanning', os.path.basename(f))
            with open(f, 'rb') as fh:
                while True:
                    chunk = fh.read(64 << 20)
                    if not chunk:
                        break
                    for m in re.findall(rb'(/content/[A-Za-z0-9_\-./]+?)(?:\?v=\d+)?(?=[,;:"\s])', chunk):
                        p = m.decode()
                        if '.' in p.rsplit('/', 1)[-1] and not p.endswith('.DS_Store'):
                            paths.add(p)
    return paths

def log_paths():
    paths = set()
    log = os.path.join(HERE, 'server.log')
    if os.path.exists(log):
        for line in open(log, encoding='utf-8', errors='replace'):
            m = re.search(r'missing content: (/content/\S+)', line)
            if m:
                paths.add(m.group(1))
    return paths

def main():
    os.makedirs(OUT, exist_ok=True)
    groups = []
    lp = log_paths()
    print(len(lp), 'files the game asked for (server.log)')
    groups += [[p] for p in sorted(lp)]
    if '--log-only' not in sys.argv:
        fp = flow_paths()
        for p in fp:
            learn_dir(p)
        print(len(fp), 'files named in your recordings')
        groups += [[p] for p in sorted(fp)]
        rp = os.path.join(HERE, 'data', 'resource_paths.json')
        if os.path.exists(rp):
            res = json.load(open(rp))
            for p in res:
                learn_dir('/content' + p)
            groups += [['/content' + p] for p in res]
            print(len(res), 'files from the game\'s own resource table')
        seed = json.load(open(SEED))
        print(len(seed['unit_ids']), 'unit/enemy IDs')
        for uid in seed['unit_ids']:
            groups += [[p] for p in unit_files(uid)]
        n = 0
        for name in seed['files']:
            c = candidates(name)
            if c:
                groups.append(c); n += 1
        print(n, 'names from the archived file list')
    print(f'checking {len(groups)} files against {BASE} (Ctrl+C to pause; run again to resume)')
    try:
        run(groups)
    except KeyboardInterrupt:
        save_state()
        print('\npaused - run again to continue')

if __name__ == '__main__':
    main()
