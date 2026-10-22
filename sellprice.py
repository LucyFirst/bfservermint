"""
Sell prices of units, set on the admin page (Rates > Unit sell prices).

The game shows a unit's sell price from its own unit table (F_UNIT_MST, field eKtE6k0n) and the
server pays from the same number. So the server gives the game an edited copy of that table with
the new prices, under a newer version number (the game downloads a table again when its version
changes), and pays the new price when a unit is sold.

F_UNIT_MST comes in 8 files (Ver{version}_2r9cNSdt_{1..8}.dat); the edited copy keeps the same
split. saves/sell_price.json keeps a revision number that goes up whenever the prices change.
Copies from earlier revisions are moved to saves/backups/old (like old account backups) - never
deleted; clear that folder by hand when you want. The game's original table stays where it is.
"""
import hashlib
import json
import os
import re
import shutil
import threading

import masterdata

TABLE = 'F_UNIT_MST'
KEYS = ('2r9cNSdt', '7nL1WTUb')      # file key, encryption key (from the game's code)
F_ID, F_SELL = 'pn16CNah', 'eKtE6k0n'
VERSION_STEP = 5000                  # edited table: original version + 5000 + revision

# the units the admin page lists (Rates > Unit sell prices)
SELL_UNITS = [('50131', 'Jewel Ghost'), ('50132', 'Jewel King'), ('50133', 'Jewel God'),
              ('50553', 'Lawson Jewel God'), ('50134', 'Golden God')]


class SellPrice:
    def __init__(self, srv):
        self.S = srv
        self.file = os.path.join(srv.SAVE_DIR, 'sell_price.json')
        self.mu = threading.RLock()
        self.info = None
        for a in srv.BY_ID.get(srv.RID['URLMstRequest'], []):
            for r in srv.BLOBS[a['resp']].get('KeC10fuL') or []:
                if r.get('moWQ30GH') == TABLE:
                    self.info = dict(r)
        self.parts = None
        try:
            self.state = json.load(open(self.file, encoding='utf-8'))
        except Exception:
            self.state = {'rev': 0, 'sig': ''}

    # ------------------------------------------------------------------ prices
    def overrides(self):
        """{unit id: zel} from the admin page."""
        try:
            raw = self.S.admin_settings().get('sell') or {}
        except Exception:
            raw = {}
        out = {}
        for k, v in raw.items():
            try:
                out[str(k)] = max(0, int(v))
            except (TypeError, ValueError):
                pass
        return out

    def normal(self, unit_id):
        try:
            return int(self.S.unit_mst().get(str(unit_id), {}).get(F_SELL) or 0)
        except (TypeError, ValueError):
            return 0

    def price(self, unit_id):
        o = self.overrides()
        return o[str(unit_id)] if str(unit_id) in o else self.normal(unit_id)

    # ------------------------------------------------------------------ the game's table
    def dirs(self):
        return [os.path.join(self.S.CDN_DIR, 'content', 'mst'), os.path.join(self.S.CDN_DIR, 'mst')]

    def base_version(self):
        return int((self.info or {}).get('d2RFtP8T') or 0)

    def part_count(self):
        return int((self.info or {}).get('H6k1LIxC') or 1)

    def base_parts(self):
        if self.parts is None:
            v, n = self.base_version(), self.part_count()
            parts = []
            for d in self.dirs():
                names = [os.path.join(d, f'Ver{v}_{KEYS[0]}_{i}.dat') for i in range(1, n + 1)]
                if all(os.path.exists(p) for p in names):
                    parts = [json.loads(masterdata.decrypt(open(p, 'rb').read(), KEYS[1])) for p in names]
                    break
            self.parts = parts
        return self.parts

    def version(self):
        return self.base_version() + VERSION_STEP + int(self.state['rev'])

    def build(self):
        """Bring the edited table up to date. True when the game should get the edited copy."""
        with self.mu:
            o = self.overrides()
            changes = {u: z for u, z in o.items() if z != self.normal(u)}
            if not self.info or not self.base_parts():
                return False
            if not changes and not self.state['rev']:
                return False                    # nothing changed, never edited: original table
            sig = hashlib.md5(json.dumps(sorted(changes.items())).encode()).hexdigest()
            if sig != self.state.get('sig'):
                self.state = {'rev': int(self.state.get('rev', 0)) + 1, 'sig': sig}
                tmp = self.file + '.tmp'
                json.dump(self.state, open(tmp, 'w', encoding='utf-8'))
                os.replace(tmp, self.file)
            v, n = self.version(), self.part_count()
            if not all(os.path.exists(os.path.join(d, f'Ver{v}_{KEYS[0]}_{i}.dat'))
                       for d in self.dirs() for i in range(1, n + 1)):
                for i, rows in enumerate(self.base_parts(), 1):
                    rows = [dict(r, **{F_SELL: str(changes[r.get(F_ID)])}) if r.get(F_ID) in changes else r
                            for r in rows]
                    data = masterdata.encrypt(
                        json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode('utf-8'), KEYS[1])
                    for d in self.dirs():
                        os.makedirs(d, exist_ok=True)
                        open(os.path.join(d, f'Ver{v}_{KEYS[0]}_{i}.dat'), 'wb').write(data)
                self.S.log.info('sell prices: unit table rebuilt (revision %s): %s', self.state['rev'],
                                ', '.join(f'{u}={z}' for u, z in sorted(changes.items())) or 'all normal')
            self.archive_old()
            return True

    def archive_old(self):
        """Edited copies from earlier revisions -> saves/backups/old (moved, never deleted)."""
        keep = {self.base_version(), self.version()}
        pat = re.compile(r'^Ver(\d+)_' + re.escape(KEYS[0]) + r'_\d+\.dat$')
        dest = os.path.join(self.S.SAVE_DIR, 'backups', 'old')
        moved = 0
        for d in self.dirs():
            if not os.path.isdir(d):
                continue
            tag = os.path.relpath(d, self.S.CDN_DIR).replace(os.sep, '-')      # content-mst / mst
            for name in os.listdir(d):
                m = pat.match(name)
                if not m or int(m.group(1)) in keep or int(m.group(1)) <= self.base_version():
                    continue
                os.makedirs(dest, exist_ok=True)
                target = os.path.join(dest, f'unit-table_{tag}_{name}')
                n = 2
                while os.path.exists(target):
                    target = os.path.join(dest, f'unit-table_{tag}_{name[:-4]}-{n}.dat')
                    n += 1
                try:
                    shutil.move(os.path.join(d, name), target)
                    moved += 1
                except OSError:
                    self.S.log.exception('sell prices: could not move %s to backups/old', name)
        if moved:
            self.S.log.info('sell prices: moved %d old unit table file(s) to backups/old', moved)

    def patch_versions(self, rows):
        """URLMst KeC10fuL: point the game at the edited unit table."""
        if not self.build():
            return rows
        out = []
        for r in rows:
            if r.get('moWQ30GH') == TABLE:
                r = dict(r, d2RFtP8T=str(self.version()))
            out.append(r)
        return out

    # ------------------------------------------------------------------ admin page
    def listing(self):
        o = self.overrides()
        return [{'id': u, 'name': self.S.unit_mst().get(u, {}).get('utP1c0CD') or n,
                 'normal': self.normal(u), 'price': o.get(u, self.normal(u))} for u, n in SELL_UNITS]
