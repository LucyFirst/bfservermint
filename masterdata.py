"""
The game's own master tables (areas, dungeons, quests), read from the files the game downloads
(cdn/content/mst/Ver<version>_<file key>[_<part>].dat: base64 of AES-128-ECB, key = the table's
8-letter key zero-padded, PKCS#7). The server can also hand out edited copies under a newer
version - see overrides in vortexedit.py.

Field names in the game's tables:
  area     VjCY7rX4 id, V84mzqoX name, 9C64Qwe0 land (99 = Vortex), XuJL4pc5 order,
           b5VveUd8 background, H0UB8pqm plate / tab banner
  dungeon  MHx05sXt id, bWsLFP96 name key, 9C64Qwe0 land, VjCY7rX4 area (Vortex tab),
           XuJL4pc5 order, 21VKZo0E banner image, L8iA9M6c battle background
  mission  j28VNcUW id, 0iAIR2LP name, 9C64Qwe0 land, VjCY7rX4 area, MHx05sXt dungeon
"""
import base64
import glob
import json
import os
import re

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# table -> (file key, encryption key), from the game's own code
TABLES = {'F_AREA_MST': ('vj6fEsg7', '73bUW8wn'),
          'F_DUNGEON_MST': ('B57MJYoF', 'w5W8RPSG'),
          'F_MISSION_MST': ('4gA3WCQX', 'y1hZzE74')}


def _aes(key):
    return Cipher(algorithms.AES(key.encode().ljust(16, b'\0')[:16]), modes.ECB())


def decrypt(blob, key):
    raw = base64.b64decode(blob)
    p = _aes(key).decryptor().update(raw)
    n = p[-1] if p else 0
    if 1 <= n <= 16 and p[-n:] == bytes([n]) * n:
        return p[:-n]
    return p.rstrip(b'\0')


def encrypt(data, key):
    pad = padding.PKCS7(128).padder()
    p = pad.update(data) + pad.finalize()
    return base64.b64encode(_aes(key).encryptor().update(p))


def file_crypt_decode(b):
    """The game's own scramble for art files (FileCrypt::decode): byte i minus i*i."""
    return bytes((x - i * i) & 255 for i, x in enumerate(b))


def file_crypt_encode(b):
    return bytes((x + i * i) & 255 for i, x in enumerate(b))


class Masters:
    def __init__(self, cdn_dir, versions):
        """versions: table -> {'d2RFtP8T': version, 'H6k1LIxC': parts, '5kbnkTp0': rows} (from URLMst)."""
        self.cdn = cdn_dir
        self.versions = versions
        self.cache = {}

    def dirs(self):
        return [os.path.join(self.cdn, 'content', 'mst'), os.path.join(self.cdn, 'mst')]

    def files(self, table, version):
        fkey = TABLES[table][0]
        for d in self.dirs():
            one = os.path.join(d, f'Ver{version}_{fkey}.dat')
            if os.path.exists(one):
                return [one]
            parts = glob.glob(os.path.join(d, f'Ver{version}_{fkey}_*.dat'))
            if parts:
                return sorted(parts, key=lambda p: int(re.search(r'_(\d+)\.dat$', p).group(1)))
        return []

    def base(self, table):
        """The game's original rows for a table (the version the recordings shipped)."""
        if table not in self.cache:
            v = (self.versions.get(table) or {}).get('d2RFtP8T')
            rows = []
            for f in self.files(table, v):
                rows += json.loads(decrypt(open(f, 'rb').read(), TABLES[table][1]))
            self.cache[table] = rows
        return self.cache[table]

    def write(self, table, version, rows, parts):
        """Write rows as the game's files for a new version. Returns the number of parts."""
        fkey, ekey = TABLES[table]
        parts = max(1, parts)
        size = (len(rows) + parts - 1) // parts
        chunks = [rows[i * size:(i + 1) * size] for i in range(parts)]
        chunks = [c for c in chunks if c] or [[]]
        for d in self.dirs():
            os.makedirs(d, exist_ok=True)
            for n, c in enumerate(chunks, 1):
                name = f'Ver{version}_{fkey}.dat' if len(chunks) == 1 else f'Ver{version}_{fkey}_{n}.dat'
                data = json.dumps(c, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
                open(os.path.join(d, name), 'wb').write(encrypt(data, ekey))
        return len(chunks)
