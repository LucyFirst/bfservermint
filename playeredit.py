"""
The admin page's account editor: see and change one player's progress.

Everything goes through the player's save (saves/player_<id>.json) under the server's lock, so it
can't clash with the game's own requests. Before a change the save is copied to
saves/backups/player_<id>_<date-time>.json (at most one copy every 10 minutes per player, plus one
before every raw edit or restore). The newest 20 per player stay in saves/backups; older ones are
moved to saves/backups/old. The server never deletes a backup.

The game only reads its data again when it logs in, so a player sees changes after fully
restarting the game.
"""
import datetime
import glob
import json
import os
import random
import shutil
import time

BACKUP_EVERY = 10 * 60
KEEP_BACKUPS = 20                    # newest backups kept per player; older ones go to backups/old
IDENTITY = {'user_id', 'gl_user', 'gl_token', 'signal_key', 'friend_id', 'created', 'state', 'units',
            'wallet', 'handle'}

# Team row (fEi17cnx) fields shown on the profile tab
TEAM_FIELDS = [('level', 'D9wXQI2V', 'Level'), ('exp', 'd96tuT2E', 'Exp (inside this level)'),
               ('energy', '0P9X1YHs', 'Energy'), ('maxenergy', 'YnM14RIP', 'Max energy'),
               ('orbs', 'YS2JG9no', 'Arena orbs'), ('maxorbs', '9m5FWR8q', 'Max arena orbs'),
               ('cost', 'QYP4kId9', 'Max squad cost'), ('friends', '3u41PhR2', 'Max friends'),
               ('unitbox', 'ouXxIY63', 'Unit box (extra space)'), ('itembox', '5pjoGBC4', 'Item box space')]
WALLET_FIELDS = [('gems', 'Gems'), ('zel', 'Zel'), ('karma', 'Karma'), ('friendpoints', 'Honor points'),
                 ('summontickets', 'Summon tickets'), ('rainbowcoins', 'Rainbow coins')]

# What the server's own save keys mean
FLAG_INFO = {
    'account': 'Sign-in account name',
    'created': 'When the account was made',
    'clean': 'Save clean-up version already applied',
    'exp_model': 'Exp format of this save (2 = per level)',
    'team_fixed': 'One-time squad / brave points repair done',
    'permits_fixed': 'One-time quest map repair done',
    'selector_fix': 'One-time unit selector repair done',
    'welcome_gifts': 'Welcome gifts already sent',
    'login_gift_day': 'Last day the daily login gift was given',
    'login_gift_count': 'Daily login gifts received in all',
    'login_campaign': 'Login bonus calendar (cycle, day reached, last day seen, days in all)',
    'story_sent': 'Story gifts already sent (by story step)',
    'srv_gifts': 'Server gifts already put in the gift box',
    'next_present': 'Number for the next gift box entry',
    'next_unit_id': 'Number for the next new unit',
    'ap_time': 'When energy was last refilled / counted',
    'orb_time': 'When arena orbs were last counted',
    'current_mission': 'Quest being played right now',
    'last_battle': 'Battle being played right now (checked when it ends)',
    'key_open': 'Vortex keys in use (dungeon -> time opened)',
    'key_active': 'Vortex key dungeons open right now',
    'spin': 'Brave Slots (day, last prize, days in a row, days since gems)',
    'daily': 'Daily missions for today and their progress',
    'dtask': 'Daily tasks for today and their progress',
    'bp': 'Brave points (daily tasks)',
    'bp_taken': 'Brave point rewards already taken',
    'ach': 'Achievements: done / reward taken (by achievement)',
    'ach_points': 'Merit points to spend',
    'ach_points_total': 'Merit points earned in all',
    'ach_trades': 'Merit shop purchases',
    'merit_day': 'Merit gained today',
    'counts': 'Lifetime counters (fusions, evolutions...)',
    'bstats': 'Battle statistics (for achievements)',
    'best_turns': 'Fewest turns per quest',
    'clear_nocont': 'Quests cleared without continuing',
    'arena_seq': 'Arena battle counter',
    'arena_battle': 'Arena battles fought',
    'arena_streak': 'Arena wins in a row',
    'arena_best_streak': 'Best arena win streak',
    'overrides': 'Fixed values set with ADMIN.bat "set" (win over everything else)',
    'trial_deck': 'Squad picked for the trial in progress',
    '_helpers': 'Helpers offered for the current quest',
    'cursor': 'Recording position per screen (server internal)',
}
DAY_KEYS = {'login_gift_day'}

# Game-side account flags (IKqx1Cn9 row)
USERINFO_INFO = {
    'B5JQyV8j': 'Player name',
    'N4XVE1uA': 'Story cutscenes seen (area progress)',
    '9yVsu21R': 'Features unlocked in the menus (comma list)',
    '9sQM2XcN': 'Tutorial step',
    'sv6BEI8X': 'Tutorial finished',
    'iN7buP0j': 'Bundle / sign-up data',
    'h7eY3sAK': 'User id',
    '98WfKiyA': 'Friend id',
}


def _now():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


class PlayerEditor:
    def __init__(self, admin):
        self.A = admin
        self.S = admin.S
        self.bdir = os.path.join(self.S.SAVE_DIR, 'backups')
        p = os.path.join(self.S.HERE, 'data', 'dungeon_names.json')
        self.dnames = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {}

    # ------------------------------------------------------------------ save handling
    def load(self, uid):
        pl = self.S.load_player(str(uid or ''))
        if not pl:
            raise ValueError('That player was not found.')
        self.S.ensure_wallet(pl)
        return pl

    def backups(self, uid):
        out = []
        for f in sorted(glob.glob(os.path.join(self.bdir, f'player_{uid}_*.json')), key=os.path.getmtime, reverse=True):
            out.append({'name': os.path.basename(f), 'size': os.path.getsize(f), 'time': int(os.path.getmtime(f))})
        return out

    def backup(self, uid, force=False):
        src = self.S.player_path(uid)
        if not os.path.exists(src):
            return
        have = self.backups(uid)
        if have and not force and time.time() - have[0]['time'] < BACKUP_EVERY:
            return
        os.makedirs(self.bdir, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        name, n = f'player_{uid}_{stamp}.json', 1
        while os.path.exists(os.path.join(self.bdir, name)):
            n += 1
            name = f'player_{uid}_{stamp}-{n}.json'
        shutil.copy2(src, os.path.join(self.bdir, name))
        os.utime(os.path.join(self.bdir, name))
        self.archive(uid)

    def archive(self, uid):
        """Keep the newest KEEP_BACKUPS per player in saves/backups; older ones move to
        saves/backups/old (never deleted - clear that folder by hand when you want)."""
        old = self.backups(uid)[KEEP_BACKUPS:]
        if not old:
            return
        dest = os.path.join(self.bdir, 'old')
        os.makedirs(dest, exist_ok=True)
        for b in old:
            target = os.path.join(dest, b['name'])
            if os.path.exists(target):
                root, ext = os.path.splitext(b['name'])
                target = os.path.join(dest, f'{root}-{int(time.time())}{ext}')
            shutil.move(os.path.join(self.bdir, b['name']), target)
        self.S.log.info('admin page: moved %d old backup(s) of %s to backups/old', len(old), uid)

    def commit(self, pl, what, force_backup=False):
        self.backup(pl['user_id'], force_backup)
        self.S.save_player(pl)
        self.S.log.info('admin page: %s - %s', pl.get('handle'), what)

    # ------------------------------------------------------------------ views
    def team(self, pl):
        return self.S.team_row(pl['state'])

    def userinfo(self, pl):
        rows = self.S._rows(pl['state'].get(self.S.G_USERINFO))
        return rows[0] if rows and isinstance(rows[0], dict) else None

    def view(self, pl):
        S = self.S
        t = self.team(pl) or {}
        um = S.unit_mst()
        squad = self.squad_units(pl)
        units = []
        for uuid, u in pl['units'].items():
            m = um.get(str(u.get('pn16CNah')), {})
            units.append({'uuid': uuid, 'id': str(u.get('pn16CNah')), 'name': m.get('utP1c0CD') or u.get('pn16CNah'),
                          'rarity': int(m.get('7ofj5xa1') or 0), 'level': S._num(u.get('D9wXQI2V')) or 1,
                          'max': int(m.get('EI1DF8Yt') or 1), 'squad': uuid in squad,
                          'bb': S._num(u.get('3NbeC8AB')) or 0, 'sbb': S._num(u.get('RQ5GnFE2')) or 0,
                          'has_bb': str(u.get('nj9Lw7mV') or '') not in ('', '0'),
                          'has_sbb': str(u.get('iEFZ6H19') or '') not in ('', '0'),
                          'has_sp': str(u.get('pn16CNah')) in (S.FE.get('units') or {}),
                          'sp': S._num(u.get('bFQbZh3x')) or 0, 'sp_used': S._num(u.get('3RgneFpP')) or 0,
                          'sp_max': S._num(u.get('GIO9DTif')) or 100})
        units.sort(key=lambda x: (-x['rarity'], x['name'], x['uuid']))
        items = []
        for r in S._rows(pl['state'].get(S.G_WAREHOUSE)):
            if isinstance(r, dict):
                iid = str(r.get('kixHbe54'))
                items.append({'id': iid, 'name': self.A.item_name(iid), 'count': S._num(r.get('wgV86x1q')) or 0})
        items.sort(key=lambda x: x['name'])
        flags = []
        for k in sorted(pl):
            if k in IDENTITY:
                continue
            v = pl[k]
            note = ''
            if k in DAY_KEYS and isinstance(v, int):
                note = (datetime.date(1970, 1, 1) + datetime.timedelta(days=v)).isoformat()
            elif k in ('ap_time', 'orb_time', 'created') and isinstance(v, (int, float)):
                note = datetime.datetime.fromtimestamp(v).strftime('%Y-%m-%d %H:%M')
            flags.append({'key': k, 'info': FLAG_INFO.get(k, ''), 'value': json.dumps(v, ensure_ascii=False),
                          'note': note, 'big': len(json.dumps(v)) > 400})
        ui = self.userinfo(pl) or {}
        uinfo = [{'key': k, 'info': USERINFO_INFO.get(k, ''), 'value': v if isinstance(v, str) else json.dumps(v)}
                 for k, v in ui.items()]
        uinfo.sort(key=lambda x: (x['info'] == '', x['key']))
        cleared = len(S.cleared_ids(pl))
        path = S.player_path(pl['user_id'])
        return {
            'summary': self.A.player_summary(pl),
            'id': pl['user_id'], 'name': pl.get('handle'), 'account': pl.get('account', ''),
            'friend_id': pl.get('friend_id'),
            'gold': pl['user_id'] in (S.admin_settings().get('gold_names') or []),
            'created': pl.get('created'), 'saved': int(os.path.getmtime(path)) if os.path.exists(path) else None,
            'team': [{'key': k, 'label': lab, 'value': S._num(t.get(code)) if S._num(t.get(code)) is not None else t.get(code)}
                     for k, code, lab in TEAM_FIELDS],
            'wallet': [{'key': k, 'label': lab, 'value': pl['wallet'].get(k, 0)} for k, lab in WALLET_FIELDS],
            'max_level': max(S.LEVEL_NEED) if S.LEVEL_NEED else None,
            'overrides': pl.get('overrides') or {},
            'units': units, 'items': items, 'flags': flags, 'userinfo': uinfo,
            'cleared': cleared, 'backups': self.backups(pl['user_id'])[:30],
        }

    def squad_units(self, pl):
        out = set()
        for r in self.S._rows(pl['state'].get('dX7S2Lc1')):
            if isinstance(r, dict) and r.get('edy7fq3L'):
                out.add(str(r['edy7fq3L']))
        return out

    def quests(self, pl):
        S = self.S
        clears = {r.get('j28VNcUW'): r for r in S._rows(pl['state'].get('UT1SVg59')) if isinstance(r, dict)}
        geo = self.A.geo
        groups = {}
        seen = set()
        for mid, m in S.MISSIONS.items():
            if m.get('custom_src'):
                continue
            did = str(m.get('dungeon') or '')
            area, land = (geo.get('dungeon', {}).get(did) or ['', ''])[:2]
            a = geo.get('area', {}).get(str(area)) or {}
            if did == S.TRIAL_DUNGEON:
                cat, aname = 'Trials', 'Research Lab'
            elif land == '99' or a.get('land') == '99':
                cat, aname = 'Vortex', a.get('name') or f'Tab {area}'
            elif a.get('type') == '0' and a.get('land') not in ('99', '999', '0', None):
                cat, aname = 'Story', a.get('name') or f'Area {area}'
            else:
                cat, aname = 'Other', a.get('name') or (f'Area {area}' if area else 'Unknown')
            try:
                dname = S.vortex_editor().name(did) if cat == 'Vortex' else ''
            except Exception:
                dname = ''
            dname = dname or self.dnames.get(did) or f'Dungeon {did}'
            c = clears.get(mid)
            row = [mid, m.get('name') or mid, int(S._num(c.get('HIQoGb10')) or 1) if c else 0]
            groups.setdefault(cat, {}).setdefault((int(area) if str(area).isdigit() else 0, aname), {}) \
                .setdefault((int(did) if did.isdigit() else 0, dname), []).append(row)
            seen.add(mid)
        extra = [[mid, f'Quest {mid}', int(S._num(c.get('HIQoGb10')) or 1)] for mid, c in clears.items() if mid not in seen]
        if extra:
            groups.setdefault('Other', {}).setdefault((0, 'Not in the quest list'), {})[(0, 'Unknown quests')] = extra
        out = []
        for cat in ('Story', 'Vortex', 'Trials', 'Other'):
            if cat not in groups:
                continue
            areas = []
            for (aid, aname), dmap in sorted(groups[cat].items()):
                dungeons = [{'id': str(did), 'name': dname,
                             'quests': sorted(qs, key=lambda q: int(q[0]) if str(q[0]).isdigit() else 0)}
                            for (did, dname), qs in sorted(dmap.items())]
                areas.append({'id': str(aid), 'name': aname, 'dungeons': dungeons})
            out.append({'name': cat, 'areas': areas})
        return out

    # ------------------------------------------------------------------ changes
    def set_profile(self, pl, data):
        S = self.S
        t = self.team(pl)
        changed = []
        name = str(data.get('name') or '').strip()
        if name and name != pl.get('handle'):
            if len(name) > 12:
                raise ValueError('Names can be at most 12 characters.')
            pl['handle'] = name
            ui = self.userinfo(pl)
            if ui is not None:
                ui[S.F_HANDLE] = name
            changed.append(f'name {name}')
        if 'gold' in data:
            st = S.admin_settings()
            gold = set(st.get('gold_names') or [])
            was = pl['user_id'] in gold
            (gold.add if data.get('gold') else gold.discard)(pl['user_id'])
            if (pl['user_id'] in gold) != was:
                st['gold_names'] = sorted(gold)
                S.save_admin_settings(st)
                changed.append('gold name on' if data.get('gold') else 'gold name off')
        vals = data.get('team') or {}
        if t is not None:
            before = {code: S._num(t.get(code)) for _k, code, _l in TEAM_FIELDS}
            want = {}
            for k, code, lab in TEAM_FIELDS:
                if k not in vals or vals[k] in ('', None):
                    continue
                v = int(vals[k])
                if v < 0:
                    raise ValueError(f'{lab} cannot be below 0.')
                if k == 'level' and S.LEVEL_NEED and not (1 <= v <= max(S.LEVEL_NEED)):
                    raise ValueError(f'Level must be 1 to {max(S.LEVEL_NEED)}.')
                if before[code] != v:
                    want[code] = (k, v)
            setv = lambda code, v: t.__setitem__(code, v if isinstance(t.get(code), int) else str(v))
            new_lv = want.get('D9wXQI2V', (None, None))[1]
            if new_lv and data.get('level_stats') and new_lv in S.LEVEL_ROW:
                # a new level brings that level's max energy, squad cost and friend slots,
                # unless those were typed in too
                row = S.LEVEL_ROW[new_lv]
                for code, src in (('YnM14RIP', '0P9X1YHs'), ('QYP4kId9', 'ziex06DY'), ('3u41PhR2', 'oFQ3mbS6')):
                    if code not in want and str(row.get(src, '')).isdigit():
                        setv(code, int(row[src]))
                changed.append(f'level {new_lv} limits applied')
            for code, (k, v) in want.items():
                setv(code, v)
                changed.append(f'{k} {before[code]} -> {v}')
                if k == 'energy':
                    pl['ap_time'] = time.time()
                if k == 'orbs':
                    pl['orb_time'] = time.time()
        for k, lab in WALLET_FIELDS:
            w = (data.get('wallet') or {}).get(k)
            if w in ('', None):
                continue
            w = int(w)
            if w < 0:
                raise ValueError(f'{lab} cannot be below 0.')
            if pl['wallet'].get(k, 0) != w:
                changed.append(f'{k} {pl["wallet"].get(k, 0)} -> {w}')
                pl['wallet'][k] = w
                if t is not None:
                    code = S.WALLET[k]
                    t[code] = w if isinstance(t.get(code), int) else str(w)
        return changed

    def set_quests(self, pl, ids, clear):
        S = self.S
        ids = [str(i) for i in ids if str(i)]
        rows = [r for r in S._rows(pl['state'].get('UT1SVg59')) if isinstance(r, dict)]
        have = {r.get('j28VNcUW') for r in rows}
        n = 0
        if clear:
            tmpl = dict(rows[0]) if rows else {}
            for mid in ids:
                if mid in have:
                    continue
                r = dict(tmpl, **{S.F_USERID: pl['user_id'], 'j28VNcUW': mid, 'JATWnN57': _now(), 'HIQoGb10': '1'})
                for k in ('Wi4EFe6f', 'RWX38boG', 'bGx5ku0F'):
                    r.setdefault(k, '0')
                    r[k] = '0'
                rows.append(r)
                have.add(mid)
                n += 1
        else:
            drop = set(ids)
            n = sum(1 for r in rows if r.get('j28VNcUW') in drop)
            rows = [r for r in rows if r.get('j28VNcUW') not in drop]
            if str(pl.get('current_mission') or '') in drop:
                pl.pop('current_mission', None)
        pl['state']['UT1SVg59'] = rows
        return n

    def set_unit(self, pl, uuid, level=None, remove=False, bb=None, sbb=None, sp=None):
        S = self.S
        u = pl['units'].get(str(uuid))
        if not u:
            raise ValueError('That unit is not in the unit box any more.')
        m = S.unit_mst().get(str(u.get('pn16CNah')), {})
        name = m.get('utP1c0CD') or u.get('pn16CNah')
        if remove:
            if str(uuid) in self.squad_units(pl):
                raise ValueError(f'{name} is in a squad. Take it out of the squad in the game first.')
            pl['units'].pop(str(uuid))
            return f'removed unit {name} ({uuid})'
        out = []
        if level not in (None, ''):
            maxlv = int(m.get('EI1DF8Yt') or 1)
            lv = maxlv if level == 'max' else int(level)
            if not 1 <= lv <= maxlv:
                raise ValueError(f'{name} can be level 1 to {maxlv}.')
            if lv != (S._num(u.get('D9wXQI2V')) or 1) or level == 'max':
                pattern = m.get('5UvTp7q1', '10')
                u.update({'D9wXQI2V': str(lv), 'gQInj3H6': str(S._cum_exp(pattern, lv)), 'd96tuT2E': '0'},
                         **S.unit_stats(m, lv))
                out.append(f'level {lv}')
        for val, sid, field, lab, low in ((bb, u.get('nj9Lw7mV'), '3NbeC8AB', 'BB', 1),
                                          (sbb, u.get('iEFZ6H19'), 'RQ5GnFE2', 'SBB', 0)):
            if val in (None, ''):
                continue
            if str(sid or '') in ('', '0'):
                raise ValueError(f'{name} has no {lab}.')
            top = S.skill_max(sid)
            v = top if val == 'max' else int(val)
            if not low <= v <= top:
                raise ValueError(f'{lab} level must be {low} to {top}.')
            if str(u.get(field)) != str(v):
                u[field] = str(v)
                out.append(f'{lab} level {v}')
        if sp not in (None, ''):
            top = S._num(u.get('GIO9DTif')) or 100
            v = top if sp == 'max' else int(sp)
            used = S._num(u.get('3RgneFpP')) or 0
            if not used <= v <= top:
                raise ValueError(f'SP must be {used} (already used) to {top}.')
            if str(u.get('bFQbZh3x')) != str(v):
                u['bFQbZh3x'] = str(v)
                out.append(f'SP {v}')
        if not out:
            raise ValueError('Nothing changed.')
        return f'unit {name} ({uuid}): ' + ', '.join(out)

    def set_item(self, pl, iid, count):
        S = self.S
        iid, count = str(iid), int(count)
        if iid not in self.A.items:
            raise ValueError('Pick an item from the list.')
        if count < 0:
            raise ValueError('The amount cannot be below 0.')
        wh = [r for r in S._rows(pl['state'].get(S.G_WAREHOUSE)) if isinstance(r, dict)]
        row = next((r for r in wh if str(r.get('kixHbe54')) == iid), None)
        if row is None and count > 0:
            used = {str(r.get('n6E8iMf3')) for r in wh}
            rid = str(random.randint(100000, 999999))
            while rid in used:
                rid = str(random.randint(100000, 999999))
            row = {'n6E8iMf3': rid, 'kixHbe54': iid, 'wgV86x1q': '0', 'dJNpLc81': '1', 'DbMVG16I': '0'}
            wh.append(row)
            seen = [r for r in S._rows(pl['state'].get('bd5Rj6pN')) if isinstance(r, dict)]
            if not any(str(r.get('kixHbe54')) == iid for r in seen):
                seen.append({'kixHbe54': iid})
                pl['state']['bd5Rj6pN'] = seen
        if row is not None:
            row['wgV86x1q'] = str(count)
        pl['state'][S.G_WAREHOUSE] = [r for r in wh if (S._num(r.get('wgV86x1q')) or 0) > 0]
        return f'item {self.A.item_name(iid)} set to {count}'

    def set_flag(self, pl, key, value, delete=False):
        key = str(key or '').strip()
        if not key or key in IDENTITY:
            raise ValueError('That part of the save cannot be changed here (use Raw save).')
        if delete:
            if key not in pl:
                raise ValueError('That flag is not set.')
            pl.pop(key)
            return f'flag {key} removed'
        try:
            v = json.loads(value)
        except ValueError:
            raise ValueError('The value must be JSON: a number, "text" in quotes, true/false, [..] or {..}.')
        pl[key] = v
        return f'flag {key} = {json.dumps(v)[:80]}'

    def set_userinfo(self, pl, key, value):
        ui = self.userinfo(pl)
        if ui is None:
            raise ValueError('This save has no account row.')
        key = str(key)
        if key in (self.S.F_USERID, self.S.F_FRIENDID):
            raise ValueError('The ids cannot be changed.')
        if key not in ui:
            raise ValueError('Unknown field.')
        old = ui[key]
        if isinstance(old, str):
            ui[key] = str(value)
        else:
            try:
                ui[key] = json.loads(value)
            except ValueError:
                raise ValueError('This field needs a number.')
        if key == self.S.F_HANDLE:
            pl['handle'] = str(value)
        return f'account field {key} = {str(value)[:80]}'

    # ------------------------------------------------------------------ API
    def api(self, action, data):
        S = self.S
        uid = str(data.get('player', ''))
        with S.LOCK:
            pl = self.load(uid)
            if action == 'pe_get':
                return self.view(pl)
            if action == 'pe_quests':
                return {'groups': self.quests(pl), 'cleared': len(S.cleared_ids(pl))}
            if action == 'pe_raw_get':
                return {'raw': json.dumps(pl, ensure_ascii=False, indent=1)}
            if action == 'pe_raw_set':
                try:
                    new = json.loads(data.get('raw') or '')
                except ValueError as e:
                    raise ValueError(f'That is not valid JSON: {e}')
                if not isinstance(new, dict) or new.get('user_id') != pl['user_id'] or \
                        not isinstance(new.get('state'), dict) or not isinstance(new.get('units'), dict):
                    raise ValueError('The save must keep the same user_id and have "state" and "units".')
                self.backup(uid, force=True)
                S.save_player(new)
                S.log.info('admin page: %s - raw save edited', new.get('handle'))
                return {'ok': 'Raw save written (backup made first).', 'view': self.view(self.load(uid))}
            if action == 'pe_restore':
                name = os.path.basename(str(data.get('name') or ''))
                src = os.path.join(self.bdir, name)
                if not name.startswith(f'player_{uid}_') or not os.path.exists(src):
                    raise ValueError('That backup was not found.')
                json.load(open(src, encoding='utf-8'))
                self.backup(uid, force=True)
                shutil.copy2(src, S.player_path(uid))
                S.log.info('admin page: %s - restored backup %s', pl.get('handle'), name)
                return {'ok': f'Restored {name} (the save before it was backed up too).', 'view': self.view(self.load(uid))}
            if action == 'pe_backup':
                self.backup(uid, force=True)
                return {'ok': 'Backup made.', 'view': self.view(pl)}
            if action == 'pe_profile':
                ch = self.set_profile(pl, data)
                if not ch:
                    return {'ok': 'Nothing changed.', 'view': self.view(pl)}
                self.commit(pl, ', '.join(ch))
                return {'ok': 'Saved: ' + ', '.join(ch), 'view': self.view(pl)}
            if action == 'pe_quest_set':
                n = self.set_quests(pl, data.get('ids') or [], bool(data.get('clear')))
                self.commit(pl, f"{'cleared' if data.get('clear') else 'un-cleared'} {n} quest(s)")
                return {'ok': f"{'Marked' if data.get('clear') else 'Unmarked'} {n} quest(s) as cleared.",
                        'groups': self.quests(pl), 'cleared': len(S.cleared_ids(pl))}
            if action == 'pe_unit':
                what = self.set_unit(pl, data.get('uuid'), data.get('level'), bool(data.get('remove')),
                                     data.get('bb'), data.get('sbb'), data.get('sp'))
            elif action == 'pe_item':
                what = self.set_item(pl, data.get('item'), data.get('count', 0))
            elif action == 'pe_flag':
                what = self.set_flag(pl, data.get('key'), data.get('value'), bool(data.get('delete')))
            elif action == 'pe_userinfo':
                what = self.set_userinfo(pl, data.get('key'), data.get('value'))
            else:
                raise ValueError('Unknown action.')
            self.commit(pl, what)
            return {'ok': what[0].upper() + what[1:] + '.', 'view': self.view(pl)}
