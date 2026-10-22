"""
Evolutions into units that are switched off on the admin page.

The game decides from its own evolution table (F_UNIT_EVO_MST) whether a unit can evolve: a unit
with no row there shows like a unit at its last form (no Evolve button). So the server gives the
game an edited copy of that table with the rows leading to switched-off units left out, under a
newer version number (the game downloads a table again when its version changes). The server's
own evolution check refuses those evolutions too, for a game that still has the old table.

saves/evo_gate.json keeps a revision number that goes up whenever the set of removed rows changes.
"""
import hashlib
import json
import os
import threading

import masterdata

TABLE = 'F_UNIT_EVO_MST'
KEYS = ('Ef6oG0mz', 'R9IryC5v')      # file key, encryption key (from the game's code)
F_FROM, F_TO = 'pn16CNah', '74VFwuTd'
VERSION_STEP = 5000                  # edited table: original version + 5000 + revision


class EvoGate:
    def __init__(self, srv):
        self.S = srv
        self.file = os.path.join(srv.SAVE_DIR, 'evo_gate.json')
        self.mu = threading.RLock()
        self.info = None
        for a in srv.BY_ID.get(srv.RID['URLMstRequest'], []):
            for r in srv.BLOBS[a['resp']].get('KeC10fuL') or []:
                if r.get('moWQ30GH') == TABLE:
                    self.info = dict(r)
        self.rows = None
        try:
            self.state = json.load(open(self.file, encoding='utf-8'))
        except Exception:
            self.state = {'rev': 0, 'sig': ''}

    # ------------------------------------------------------------------ the game's table
    def dirs(self):
        return [os.path.join(self.S.CDN_DIR, 'content', 'mst'), os.path.join(self.S.CDN_DIR, 'mst')]

    def base_version(self):
        return int((self.info or {}).get('d2RFtP8T') or 0)

    def base_rows(self):
        if self.rows is None:
            rows = []
            v = self.base_version()
            for d in self.dirs():
                p = os.path.join(d, f'Ver{v}_{KEYS[0]}.dat')
                if os.path.exists(p):
                    rows = json.loads(masterdata.decrypt(open(p, 'rb').read(), KEYS[1]))
                    break
            self.rows = rows
        return self.rows

    def closed(self):
        try:
            return set(self.S.admin_settings()['off']['units'])
        except Exception:
            return set()

    def blocked(self, to_unit):
        return str(to_unit) in self.closed()

    def kept_rows(self):
        off = self.closed()
        return [r for r in self.base_rows() if str(r.get(F_TO)) not in off]

    def version(self):
        return self.base_version() + VERSION_STEP + int(self.state['rev'])

    def _path(self, version):
        return os.path.join(self.dirs()[0], f'Ver{version}_{KEYS[0]}.dat')

    def build(self):
        """Bring the edited table up to date. Returns the rows the game gets, or None to leave the
        game's own table alone."""
        with self.mu:
            if not self.info or not self.base_rows():
                return None
            rows = self.kept_rows()
            removed = sorted(str(r.get(F_FROM)) for r in self.base_rows() if r not in rows)
            sig = hashlib.md5(','.join(removed).encode()).hexdigest()
            if not removed and not self.state['rev']:
                return None                     # nothing switched off, never edited: original table
            if sig != self.state.get('sig'):
                self.state = {'rev': int(self.state.get('rev', 0)) + 1, 'sig': sig}
                tmp = self.file + '.tmp'
                json.dump(self.state, open(tmp, 'w', encoding='utf-8'))
                os.replace(tmp, self.file)
            if not all(os.path.exists(os.path.join(d, f'Ver{self.version()}_{KEYS[0]}.dat')) for d in self.dirs()):
                data = json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
                for d in self.dirs():
                    os.makedirs(d, exist_ok=True)
                    open(os.path.join(d, f'Ver{self.version()}_{KEYS[0]}.dat'), 'wb').write(
                        masterdata.encrypt(data, KEYS[1]))
                self.S.log.info('evolutions: table rebuilt (revision %s, %d evolutions into switched-off '
                                'units left out)', self.state['rev'], len(removed))
            return rows

    def patch_versions(self, rows):
        """URLMst KeC10fuL: point the game at the edited evolution table."""
        kept = self.build()
        if kept is None:
            return rows
        out = []
        for r in rows:
            if r.get('moWQ30GH') == TABLE:
                r = dict(r, d2RFtP8T=str(self.version()), H6k1LIxC='1', **{'5kbnkTp0': str(len(kept))})
            out.append(r)
        return out
