"""
Our own units (data/custom_units.json) in the game's unit animation table F_UNIT_CGS_MST.

The game finds a unit's animation files (idle / move / attack .csv) in this table, by unit id - a
unit missing from it can't be drawn (the battle crashed loading Vargas of Mint, unit 990011). Each
custom unit gets a copy of the row of the unit it is made from, with its own file names, in an
edited copy of the table under a newer version (the game downloads a table again when its version
changes), with the right row count.
"""
import hashlib
import json
import os
import re
import shutil
import threading

import masterdata

TABLE = 'F_UNIT_CGS_MST'
KEYS = ('6rjmY7tV', 'Y7ezNyn2')      # file key, encryption key (from the game's code)
F_ID, F_FILES = 'pn16CNah', 'Kn51uR4Y'
VERSION_STEP = 5000                  # edited table: original version + 5000 + revision


class UnitCgs:
    def __init__(self, srv):
        self.S = srv
        self.file = os.path.join(srv.SAVE_DIR, 'unit_cgs.json')
        self.mu = threading.RLock()
        self.info = None
        for a in srv.BY_ID.get(srv.RID['URLMstRequest'], []):
            for r in srv.BLOBS[a['resp']].get('KeC10fuL') or []:
                if r.get('moWQ30GH') == TABLE:
                    self.info = dict(r)
        self.rows_cache = None
        try:
            self.state = json.load(open(self.file, encoding='utf-8'))
        except Exception:
            self.state = {'rev': 0, 'sig': ''}

    def dirs(self):
        return [os.path.join(self.S.CDN_DIR, 'content', 'mst'), os.path.join(self.S.CDN_DIR, 'mst')]

    def base_version(self):
        return int((self.info or {}).get('d2RFtP8T') or 0)

    def name(self, v):
        return f'Ver{v}_{KEYS[0]}.dat'

    def base_rows(self):
        if self.rows_cache is None:
            rows = []
            for d in self.dirs():
                p = os.path.join(d, self.name(self.base_version()))
                if os.path.exists(p):
                    rows = json.loads(masterdata.decrypt(open(p, 'rb').read(), KEYS[1]))
                    break
            self.rows_cache = rows
        return self.rows_cache

    def extra_rows(self):
        try:
            raw = json.load(open(os.path.join(self.S.HERE, 'data', 'custom_units.json'), encoding='utf-8'))
        except Exception:
            return []
        base = {r.get(F_ID): r for r in self.base_rows()}
        out = []
        for uid, c in sorted(raw.items()):
            src = str((c or {}).get('from', '')) if isinstance(c, dict) else ''
            if uid.startswith('_') or src not in base or uid in base:
                continue
            files = re.sub(r'_(\d+)\.csv', lambda m: f'_{uid}.csv' if m.group(1) == src else m.group(0),
                           base[src][F_FILES])
            out.append(dict(base[src], **{F_ID: uid, F_FILES: files}))
        return out

    def version(self):
        return self.base_version() + VERSION_STEP + int(self.state['rev'])

    def build(self):
        with self.mu:
            extra = self.extra_rows()
            if not self.info or not self.base_rows() or not extra:
                return False
            sig = hashlib.md5(json.dumps(extra, sort_keys=True).encode()).hexdigest()
            if sig != self.state.get('sig'):
                self.state = {'rev': int(self.state.get('rev', 0)) + 1, 'sig': sig}
                tmp = self.file + '.tmp'
                json.dump(self.state, open(tmp, 'w', encoding='utf-8'))
                os.replace(tmp, self.file)
            v = self.version()
            if not all(os.path.exists(os.path.join(d, self.name(v))) for d in self.dirs()):
                data = masterdata.encrypt(json.dumps(self.base_rows() + extra, ensure_ascii=False,
                                                     separators=(',', ':')).encode('utf-8'), KEYS[1])
                for d in self.dirs():
                    os.makedirs(d, exist_ok=True)
                    open(os.path.join(d, self.name(v)), 'wb').write(data)
                self.S.log.info('unit animations: table rebuilt (revision %s) with %s', self.state['rev'],
                                ', '.join(r[F_ID] for r in extra))
            self.archive_old()
            return True

    def archive_old(self):
        """Edited copies from earlier revisions -> saves/backups/old (moved, never deleted)."""
        keep = {self.base_version(), self.version()}
        pat = re.compile(r'^Ver(\d+)_' + re.escape(KEYS[0]) + r'\.dat$')
        dest = os.path.join(self.S.SAVE_DIR, 'backups', 'old')
        for d in self.dirs():
            if not os.path.isdir(d):
                continue
            tag = os.path.relpath(d, self.S.CDN_DIR).replace(os.sep, '-')
            for name in os.listdir(d):
                m = pat.match(name)
                if not m or int(m.group(1)) in keep or int(m.group(1)) <= self.base_version():
                    continue
                os.makedirs(dest, exist_ok=True)
                try:
                    shutil.move(os.path.join(d, name), os.path.join(dest, f'unit-cgs-table_{tag}_{name}'))
                except OSError:
                    self.S.log.exception('unit animations: could not move %s to backups/old', name)

    def patch_versions(self, rows):
        """URLMst KeC10fuL: point the game at the edited table (version and row count)."""
        if not self.build():
            return rows
        n = str(len(self.base_rows()) + len(self.extra_rows()))
        return [dict(r, d2RFtP8T=str(self.version()), **({'5kbnkTp0': n} if r.get('5kbnkTp0') else {}))
                if r.get('moWQ30GH') == TABLE else r for r in rows]
