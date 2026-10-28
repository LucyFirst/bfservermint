"""
Boss moves whose numbers follow the wiki rather than the game's own skill-level table.

The game reads a move's numbers (damage %, HP % for proportional hits ...) from its own table
F_SKILL_LEVEL_MST (field Kn51uR4Y of each skill id). The changes are listed in
data/skill_level_fix.json, e.g. Death Gate (Trial No. 003, Demon Abaddon) deals 120% HP like the wiki
says, not the table's 150%. So the server gives the game an edited copy of that table under a newer
version number (the game downloads a table again when its version changes), in the same 19 files.

saves/skill_level.json keeps a revision number that goes up whenever the list of changes changes.
Copies from earlier revisions are moved to saves/backups/old - never deleted.
"""
import hashlib
import json
import os
import re
import shutil
import threading

import masterdata

TABLE = 'F_SKILL_LEVEL_MST'
KEYS = ('zLIvD5o2', 'rF97pVdT')      # file key, encryption key (from the game's code)
F_ID, F_PARAMS = 'nj9Lw7mV', 'Kn51uR4Y'
VERSION_STEP = 5000                  # edited table: original version + 5000 + revision


class SkillLevels:
    def __init__(self, srv):
        self.S = srv
        self.file = os.path.join(srv.SAVE_DIR, 'skill_level.json')
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

    def changes(self):
        try:
            f = json.load(open(os.path.join(self.S.HERE, 'data', 'skill_level_fix.json'), encoding='utf-8'))
        except Exception:
            return {}
        return {k: v['params'] for k, v in f.items() if not k.startswith('_') and isinstance(v, dict)}

    def dirs(self):
        return [os.path.join(self.S.CDN_DIR, 'content', 'mst'), os.path.join(self.S.CDN_DIR, 'mst')]

    def base_version(self):
        return int((self.info or {}).get('d2RFtP8T') or 0)

    def part_count(self):
        return int((self.info or {}).get('H6k1LIxC') or 1)

    def name(self, v, i):
        return f'Ver{v}_{KEYS[0]}_{i}.dat' if self.part_count() > 1 else f'Ver{v}_{KEYS[0]}.dat'

    def base_parts(self):
        if self.parts is None:
            v, n = self.base_version(), self.part_count()
            parts = []
            for d in self.dirs():
                names = [os.path.join(d, self.name(v, i)) for i in range(1, n + 1)]
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
            ch = self.changes()
            if not self.info or not self.base_parts():
                if ch:
                    self.S.log.warning('skill levels: the game\'s %s files are not in the CDN - not edited', TABLE)
                return False
            if not ch and not self.state['rev']:
                return False                    # nothing changed, never edited: original table
            sig = hashlib.md5(json.dumps(sorted(ch.items())).encode()).hexdigest()
            if sig != self.state.get('sig'):
                self.state = {'rev': int(self.state.get('rev', 0)) + 1, 'sig': sig}
                tmp = self.file + '.tmp'
                json.dump(self.state, open(tmp, 'w', encoding='utf-8'))
                os.replace(tmp, self.file)
            v, n = self.version(), self.part_count()
            if not all(os.path.exists(os.path.join(d, self.name(v, i))) for d in self.dirs() for i in range(1, n + 1)):
                for i, rows in enumerate(self.base_parts(), 1):
                    rows = [dict(r, **{F_PARAMS: ch[r.get(F_ID)]}) if r.get(F_ID) in ch else r for r in rows]
                    data = masterdata.encrypt(
                        json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode('utf-8'), KEYS[1])
                    for d in self.dirs():
                        os.makedirs(d, exist_ok=True)
                        open(os.path.join(d, self.name(v, i)), 'wb').write(data)
                self.S.log.info('skill levels: table rebuilt (revision %s): %s', self.state['rev'],
                                ', '.join(f'{k}={p}' for k, p in sorted(ch.items())) or 'all original')
            self.archive_old()
            return True

    def archive_old(self):
        """Edited copies from earlier revisions -> saves/backups/old (moved, never deleted)."""
        keep = {self.base_version(), self.version()}
        pat = re.compile(r'^Ver(\d+)_' + re.escape(KEYS[0]) + r'(_\d+)?\.dat$')
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
                target = os.path.join(dest, f'skill-level-table_{tag}_{name}')
                n = 2
                while os.path.exists(target):
                    target = os.path.join(dest, f'skill-level-table_{tag}_{name[:-4]}-{n}.dat')
                    n += 1
                try:
                    shutil.move(os.path.join(d, name), target)
                except OSError:
                    self.S.log.exception('skill levels: could not move %s to backups/old', name)

    def patch_versions(self, rows):
        """URLMst KeC10fuL: point the game at the edited skill-level table."""
        if not self.build():
            return rows
        return [dict(r, d2RFtP8T=str(self.version())) if r.get('moWQ30GH') == TABLE else r for r in rows]
