#!/usr/bin/env python3
"""Unpack downloaded game content from a mitmproxy capture into the cdn/ folder.

Usage:  python extract_cdn.py [file.flow ...]   (default: every .flow / flows* file in the folder above)
Only successful downloads are kept; existing files are left alone.
"""
import glob, gzip, os, sys, zlib
from urllib.parse import urlparse, unquote

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'cdn')

def parse(data, i=0):
    j = data.index(b':', i); n = int(data[i:j]); s = j + 1; e = s + n
    payload, t = data[s:e], data[e:e + 1]
    if t == b',': v = payload
    elif t == b';': v = payload.decode()
    elif t == b'#': v = int(payload)
    elif t == b'^': v = float(payload)
    elif t == b'!': v = payload == b'true'
    elif t == b'~': v = None
    elif t == b']':
        v, k = [], 0
        while k < len(payload):
            x, k = parse(payload, k); v.append(x)
    elif t == b'}':
        v, k = {}, 0
        while k < len(payload):
            key, k = parse(payload, k); val, k = parse(payload, k)
            v[key.decode() if isinstance(key, bytes) else key] = val
    else:
        raise ValueError(t)
    return v, e + 1

def stream(path):
    with open(path, 'rb') as f:
        while True:
            head = b''
            while True:
                c = f.read(1)
                if not c: return
                head += c
                if c == b':': break
            n = int(head[:-1])
            body = f.read(n + 1)
            yield parse(head + body)[0]

def hdr(h, name):
    for k, v in h or []:
        if k.decode().lower() == name: return v.decode()
    return ''

def main(paths):
    saved = skipped = 0
    for path in paths:
        for f in stream(path):
            if f.get('type') != 'http' or not f.get('response'): continue
            q, r = f['request'], f['response']
            if r.get('status_code') != 200 or r.get('content') is None: continue
            rel = unquote(q['path'].decode().split('?')[0]).lstrip('/')
            rel = os.path.normpath(rel)
            if rel.startswith('..') or not rel or rel == '.': continue
            body = r['content']
            enc = hdr(r['headers'], 'content-encoding')
            if enc == 'gzip': body = gzip.decompress(body)
            elif enc == 'deflate': body = zlib.decompress(body)
            out = os.path.join(OUT, rel)
            if os.path.exists(out): skipped += 1; continue
            os.makedirs(os.path.dirname(out), exist_ok=True)
            open(out, 'wb').write(body); saved += 1
            if saved % 500 == 0: print(saved, 'files...')
    print(f'done: {saved} new files, {skipped} already there -> {OUT}')

if __name__ == '__main__':
    args = sys.argv[1:] or [os.path.join(HERE, '..', '*')]
    files = []
    for a in args:
        for f in glob.glob(a):
            if os.path.isfile(f) and (f.endswith('.flow') or os.path.basename(f).startswith('flows')):
                files.append(f)
    if not files: sys.exit('No .flow files found next to the bfserver folder.')
    for f in files: print('reading', os.path.basename(f))
    main(files)
