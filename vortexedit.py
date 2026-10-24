"""
Vortex banners: what the admin page shows and changes.

In the game each Vortex banner is a dungeon (F_DUNGEON_MST row, banner picture 21VKZo0E) inside a
Vortex tab (F_AREA_MST row with land 99); its quests are the F_MISSION_MST rows of that dungeon.

Changes are kept in saves/vortex.json and given to the game as edited copies of those three tables
under a newer version number (the game downloads a table again when its version changes):
  edits   {dungeon: {"image": file or "", "area": tab}}     new picture / moved to another tab
  custom  {dungeon: {"name", "area", "image", "order", "quests": [{"id", "src"}]}}
          new banners; their quests are copies of existing quests (same battles and rewards,
          new quest numbers) so the originals stay where they are.
Banner pictures uploaded on the admin page go to cdn/content/dungeon/ in the game's own format.
"""
import base64
import json
import os
import threading
import time

import masterdata

CUSTOM_DUNGEON_BASE = 9900000
CUSTOM_MISSION_BASE = 99000000
# Japan-only trials (not part of Global): left out of the game's quest table so the Research Lab
# doesn't list them
HIDDEN_MISSIONS = {'2000012', '2000013', '2000014', '2000015'}
VERSION_STEP = 5000                  # edited tables: original version + 5000 + revision
TABLE_FORMAT = 3                     # raise when the way the tables are built changes (+10000 each),
                                     # so the game fetches them again even if no edit changed
BANNER_W, BANNER_H = 640, 224


class VortexEditor:
    def __init__(self, srv):
        self.S = srv
        self.file = os.path.join(srv.SAVE_DIR, 'vortex.json')
        self.mu = threading.RLock()
        versions = {}
        for a in srv.BY_ID.get(srv.RID['URLMstRequest'], []):
            for r in srv.BLOBS[a['resp']].get('KeC10fuL') or []:
                if r.get('moWQ30GH') in masterdata.TABLES:
                    versions[r['moWQ30GH']] = dict(r)
        self.versions = versions
        self.M = masterdata.Masters(srv.CDN_DIR, versions)
        p = os.path.join(srv.HERE, 'data', 'dungeon_names.json')
        self.dnames = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {}
        self.state = self.load()
        self.built_rev = None
        # banners the game actually lists: the Vortex list of the recorded account
        self.listed = set()
        for a in srv.T['action']:
            b = srv.BLOBS[a['resp']]
            if isinstance(b, dict):
                for r in b.get('Y73tHKS8') or []:
                    if isinstance(r, dict) and r.get('MHx05sXt'):
                        self.listed.add(str(r['MHx05sXt']))

    # ------------------------------------------------------------------ state
    def load(self):
        try:
            s = json.load(open(self.file, encoding='utf-8'))
        except Exception:
            s = {}
        s.setdefault('rev', 0)
        s.setdefault('edits', {})
        s.setdefault('custom', {})
        return s

    def save(self):
        tmp = self.file + '.tmp'
        json.dump(self.state, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        os.replace(tmp, self.file)

    def changed(self):
        self.state['rev'] = self.state.get('rev', 0) + 1
        self.save()
        self.apply_to_server()
        self.build()

    def hidden_missions(self):
        try:
            st = self.S.admin_settings()
            off = set(st['off'].get('trials', []))
            on = set(st.get('on_extra', []))
        except Exception:
            off, on = set(), set()
        return (self.S.EXTRA_FIGHTS - on) | off

    def active(self):
        return True          # always: the off-by-default fights are left out of the game's table

    # ------------------------------------------------------------------ the game's tables
    def tables(self):
        """(areas, dungeons, missions) with this server's changes."""
        areas = [dict(r) for r in self.M.base('F_AREA_MST')]
        dungeons = [dict(r) for r in self.M.base('F_DUNGEON_MST')]
        missions = [dict(r) for r in self.M.base('F_MISSION_MST')]
        hide = self.hidden_missions()
        need = {r['j28VNcUW']: r.get('HSRhkf70', '') for r in missions}

        def bridge(req, seen=()):
            # a trial that is left out hands its own unlock requirement on, so the next one still opens
            out = []
            for x in [x.strip() for x in str(req or '').split(',') if x.strip()]:
                if x in hide and x not in seen:
                    out += [y for y in bridge(need.get(x), seen + (x,)) if y not in out]
                elif x not in hide and x not in out:
                    out.append(x)
            return out
        missions = [r for r in missions if r['j28VNcUW'] not in hide]
        # Brave Dance fights switched on: listed in the Research Lab, after the trials, open to all
        lab = next((r for r in dungeons if r['MHx05sXt'] == self.S.TRIAL_DUNGEON), None)
        if lab is not None:
            last = max([int(r.get('XuJL4pc5') or 0) for r in missions if r.get('MHx05sXt') == lab['MHx05sXt']] or [0])
            for r in missions:
                if r['j28VNcUW'] in self.S.BRAVE_DANCES:
                    last += 1
                    r.update({'MHx05sXt': lab['MHx05sXt'], 'VjCY7rX4': lab.get('VjCY7rX4', r.get('VjCY7rX4')),
                              '9C64Qwe0': lab.get('9C64Qwe0', r.get('9C64Qwe0')), 'XuJL4pc5': str(last),
                              'HSRhkf70': '0'})
        for r in missions:
            if any(x.strip() in hide for x in str(r.get('HSRhkf70') or '').split(',')):
                r['HSRhkf70'] = ','.join(bridge(r['HSRhkf70'])) or '0'
        dun_by = {r['MHx05sXt']: r for r in dungeons}
        mis_by_dun = {}
        for r in missions:
            mis_by_dun.setdefault(r.get('MHx05sXt'), []).append(r)
        area_by = {r['VjCY7rX4']: r for r in areas}
        for did, e in self.state['edits'].items():
            r = dun_by.get(did)
            if not r:
                continue
            if e.get('image') is not None:
                # The Vortex list shows the tab's own picture (area H0UB8pqm); a tab that uses the
                # same picture as this banner (one-banner tabs like Heavenly Paradise) follows it.
                tab = area_by.get(r.get('VjCY7rX4'))
                if tab is not None and tab.get('H0UB8pqm') and tab.get('H0UB8pqm') == r.get('21VKZo0E'):
                    tab['H0UB8pqm'] = e['image'] or tab['H0UB8pqm']
                r['21VKZo0E'] = e['image']
            if e.get('area'):
                r['VjCY7rX4'] = e['area']
                for m in mis_by_dun.get(did, []):
                    m['VjCY7rX4'] = e['area']
        mis_src = {r['j28VNcUW']: r for r in missions}
        for did, c in sorted(self.state['custom'].items()):
            quests = [q for q in c.get('quests', []) if q.get('src') in mis_src]
            src_dun = dun_by.get(mis_src[quests[0]['src']]['MHx05sXt']) if quests else None
            row = dict(src_dun or next(r for r in dungeons if r.get('9C64Qwe0') == '99'))
            row.update({'MHx05sXt': did, '9C64Qwe0': '99', 'VjCY7rX4': c['area'],
                        'XuJL4pc5': str(c.get('order', 1)), '21VKZo0E': c.get('image') or row.get('21VKZo0E', ''),
                        'HSRhkf70': '0', 'np4K1xtq': ''})
            dungeons.append(row)
            for n, q in enumerate(quests, 1):
                m = dict(mis_src[q['src']])
                m.update({'j28VNcUW': q['id'], 'MHx05sXt': did, 'VjCY7rX4': c['area'], '9C64Qwe0': '99',
                          'XuJL4pc5': str(n), 'HSRhkf70': '0', 'np4K1xtq': '', '8f4NYKxb': ''})
                missions.append(m)
        # Names and descriptions in English, not just the dictionary key (MST_DUNGEONS_...): the game
        # shows the key itself when its own text files lack it (seen on the Metal Parade key dungeon).
        texts = self.texts()
        if texts:
            for rows in (areas, dungeons, missions):
                for r in rows:
                    for k, v in r.items():
                        if isinstance(v, str) and v.startswith('MST_DUNGEONS_') and v in texts:
                            r[k] = texts[v]
        return areas, dungeons, missions

    _TEXTS = None

    def texts(self):
        """MST_DUNGEONS_... -> English, from the game's own text files (cdn/content/sgtext)."""
        if VortexEditor._TEXTS is None:
            import base64, glob, re, zipfile
            out, best = {}, {}
            for f in glob.glob(os.path.join(self.S.CDN_DIR, 'content', 'sgtext', 'sgtext_*.zip')):
                m = re.match(r'sgtext_(.+?)_(\d+)\.zip$', os.path.basename(f))
                if not m or not re.search(r'dungeon', m.group(1), re.I):
                    continue
                try:
                    z = zipfile.ZipFile(f)
                    raw = z.read(z.namelist()[0]).decode('utf-8', 'replace')
                    try:
                        txt = base64.b64decode(''.join(raw.split())).decode('utf-8', 'replace')
                    except Exception:
                        txt = raw
                except Exception:
                    continue
                ver = int(m.group(2))
                for line in txt.splitlines():
                    parts = line.split('^')
                    if len(parts) > 1 and parts[0].startswith('MST_DUNGEONS_') and parts[1].strip() \
                            and parts[1] != parts[0] and ver >= best.get(parts[0], -1):
                        out[parts[0]] = parts[1].strip()
                        best[parts[0]] = ver
            VortexEditor._TEXTS = out
            self.S.log.info('vortex: %d quest/dungeon names in English', len(out))
        return VortexEditor._TEXTS

    def build(self):
        """Write the edited tables as a new version the game will download."""
        with self.mu:
            if not self.active():
                self.built_rev = self.state['rev']
                return
            if self.built_rev == self.state['rev'] and self._built_ok():
                return
            areas, dungeons, missions = self.tables()
            for table, rows in (('F_AREA_MST', areas), ('F_DUNGEON_MST', dungeons), ('F_MISSION_MST', missions)):
                info = self.versions.get(table) or {}
                parts = int(info.get('H6k1LIxC') or 1)
                self.M.write(table, self.version(table), rows, parts)
            self.built_rev = self.state['rev']
            self.S.log.info('vortex: game tables rebuilt (revision %s)', self.state['rev'])

    def _built_ok(self):
        return all(self.M.files(t, self.version(t)) for t in masterdata.TABLES)

    def version(self, table):
        base = int((self.versions.get(table) or {}).get('d2RFtP8T') or 0)
        return base + VERSION_STEP + TABLE_FORMAT * 10000 + int(self.state['rev'])

    def patch_versions(self, rows):
        """URLMst KeC10fuL: point the game at the edited tables."""
        if not self.active():
            return rows
        self.build()
        _, dungeons, missions = self.tables()
        counts = {'F_AREA_MST': None, 'F_DUNGEON_MST': len(dungeons), 'F_MISSION_MST': len(missions)}
        out = []
        for r in rows:
            name = r.get('moWQ30GH')
            if name in masterdata.TABLES:
                r = dict(r, d2RFtP8T=str(self.version(name)))
                if counts.get(name) is not None:
                    r['5kbnkTp0'] = str(counts[name])
            out.append(r)
        return out

    def patch_area_rows(self, rows):
        """GetUserInfo 3SG2wX0R: the area rows the game shows its Vortex tabs from (they override the
        table), so edited tab pictures go here too."""
        if not self.active():
            return rows
        areas = {r['VjCY7rX4']: r for r in self.tables()[0]}
        out = []
        for r in rows:
            a = areas.get(str(r.get('VjCY7rX4')))
            if a and a.get('H0UB8pqm') and a.get('H0UB8pqm') != r.get('H0UB8pqm'):
                r = dict(r, H0UB8pqm=a['H0UB8pqm'])
            out.append(r)
        return out

    # ------------------------------------------------------------------ the server's own view
    def apply_to_server(self):
        """Custom quests behave like the quest they copy; moved / new banners sit in their tab."""
        S = self.S
        aliases, geo_d = {}, {}
        for did, c in self.state['custom'].items():
            geo_d[did] = [c['area'], '99']
            for q in c.get('quests', []):
                src = S.MISSIONS.get(q['src'])
                if src:
                    S.MISSIONS[q['id']] = dict(src, dungeon=did, custom_src=q['src'])
                    aliases[q['id']] = q['src']
        for did, e in self.state['edits'].items():
            if e.get('area'):
                geo_d[did] = [e['area'], '99']
        S.MISSION_ALIAS.clear()
        S.MISSION_ALIAS.update(aliases)
        S.DUNGEON_OVERRIDE.clear()
        S.DUNGEON_OVERRIDE.update(geo_d)

    def permit_rows(self):
        """Extra Vortex list rows so new banners show: their tab, dungeon and quests."""
        rows = []
        for did, c in sorted(self.state['custom'].items()):
            if c.get('hidden'):
                continue
            rows.append({'VjCY7rX4': c['area']})
            rows.append({'MHx05sXt': did})
            rows += [{'j28VNcUW': q['id']} for q in c.get('quests', [])]
        return rows

    # ------------------------------------------------------------------ admin page
    def name(self, did):
        c = self.state['custom'].get(did)
        return c['name'] if c else self.dnames.get(did) or f'Banner {did}'

    def overview(self):
        areas, dungeons, missions = self.tables()
        tabs = {r['VjCY7rX4']: r for r in areas if r.get('9C64Qwe0') == '99'}
        by_dun = {}
        for m in missions:
            by_dun.setdefault(m.get('MHx05sXt'), []).append(m)
        out = {}
        for d in dungeons:
            if d.get('9C64Qwe0') != '99':
                continue
            did = d['MHx05sXt']
            tab = d.get('VjCY7rX4')
            if did == self.S.TRIAL_DUNGEON or tab == self.S.TRIAL_DUNGEON:
                continue                 # the Research Lab trials have their own section (Trials)
            qs = sorted(by_dun.get(did, []), key=lambda m: int(m.get('XuJL4pc5') or 0))
            out.setdefault(tab, []).append({
                'id': did, 'name': self.name(did), 'image': d.get('21VKZo0E') or '',
                'custom': did in self.state['custom'],
                'listed': did in self.listed or did in self.state['custom'],
                'edited': did in self.state['edits'],
                'quests': [{'id': m['j28VNcUW'], 'name': (self.S.MISSIONS.get(m['j28VNcUW']) or {}).get('name')
                            or m['j28VNcUW'], 'energy': (self.S.MISSIONS.get(m['j28VNcUW']) or {}).get('energy', '')}
                           for m in qs]})
        result = []
        for tid, ban in out.items():
            t = tabs.get(tid) or {}
            ban.sort(key=lambda b: (not b['custom'], not b['listed'], int(b['id']) if b['id'].isdigit() else 0))
            par = str(t.get('sHMai2ZG') or '')
            par = par if par not in ('', '0', 'None') else ''
            result.append({'id': tid, 'name': t.get('V84mzqoX') or tid, 'order': int(t.get('XuJL4pc5') or 0),
                           'parent': par, 'parent_name': (tabs.get(par) or {}).get('V84mzqoX') or par,
                           'listed': any(b['listed'] for b in ban), 'banners': ban})
        # folder tabs (Travellers, Seasons Past...) hold other tabs; list them even with no banners
        have = {t['id'] for t in result}
        i = 0
        while i < len(result):
            par = result[i]['parent']
            if par and par not in have and par in tabs:
                t = tabs[par]
                gp = str(t.get('sHMai2ZG') or '')
                gp = gp if gp not in ('', '0', 'None') else ''
                result.append({'id': par, 'name': t.get('V84mzqoX') or par, 'order': int(t.get('XuJL4pc5') or 0),
                               'parent': gp, 'parent_name': (tabs.get(gp) or {}).get('V84mzqoX') or gp,
                               'listed': False, 'banners': []})
                have.add(par)
            i += 1
        kids = {}
        for t in result:
            if t['parent']:
                kids.setdefault(t['parent'], []).append(t)
        def listed(t, seen=()):
            return t['listed'] or any(listed(k, seen + (t['id'],)) for k in kids.get(t['id'], []) if k['id'] not in seen)
        for t in result:
            t['listed'] = listed(t)
        result.sort(key=lambda t: (not t['listed'], t['order'], int(t['id']) if t['id'].isdigit() else 0))
        return result

    def save_image(self, did, data_url):
        """A picture from the admin page (already 640x224 PNG): stored as the game's art file."""
        if not data_url.startswith('data:image/png;base64,'):
            raise ValueError('The picture must be a PNG.')
        png = base64.b64decode(data_url.split(',', 1)[1])
        if png[:8] != b'\x89PNG\r\n\x1a\n' or len(png) > 3_000_000:
            raise ValueError('That picture could not be read.')
        name = f'sp_quest_banner_custom_{did}_{int(time.time())}.png'
        d = os.path.join(self.S.CDN_DIR, 'content', 'dungeon')
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, name), 'wb').write(masterdata.file_crypt_encode(png))
        return name

    def set_image(self, did, data_url):
        with self.mu:
            name = self.save_image(did, data_url)
            if did in self.state['custom']:
                self.state['custom'][did]['image'] = name
            else:
                self.state['edits'].setdefault(did, {})['image'] = name
            self.changed()
            return name

    def restore(self, did):
        with self.mu:
            if did in self.state['edits']:
                self.state['edits'].pop(did)
                self.changed()

    def move(self, did, area):
        with self.mu:
            if did in self.state['custom']:
                self.state['custom'][did]['area'] = area
            else:
                e = self.state['edits'].setdefault(did, {})
                e['area'] = area
            self.changed()

    def create(self, name, area, quests, data_url=None):
        with self.mu:
            name = str(name or '').strip()[:40]
            if not name:
                raise ValueError('Give the banner a name.')
            src = [q for q in quests if q in self.S.MISSIONS]
            if not src:
                raise ValueError('Pick at least one quest for the banner.')
            used_d = [int(d) for d in self.state['custom']] or [CUSTOM_DUNGEON_BASE]
            did = str(max(used_d) + 1)
            used_m = [int(q['id']) for c in self.state['custom'].values() for q in c.get('quests', [])] or [CUSTOM_MISSION_BASE]
            nxt = max(used_m) + 1
            qs = [{'id': str(nxt + i), 'src': s} for i, s in enumerate(src)]
            self.state['custom'][did] = {'name': name, 'area': area, 'quests': qs, 'order': 1,
                                         'image': self.save_image(did, data_url) if data_url else '',
                                         'created': int(time.time())}
            self.changed()
            return did

    def delete(self, did):
        with self.mu:
            if self.state['custom'].pop(did, None) is not None:
                self.changed()
