"""
The admin page: http://<server>/admin  (any device on the home network; sign in with the admin password).

Players      give gems / zel / karma / honor points / summon tickets straight to a balance, or send a
             unit, item, gems, zel or karma to one player's gift box
             and edit one account: profile, quests cleared, units, items, flags, raw save, backups
             (see playeredit.py)
Gifts        server-wide gifts: every player gets them in the gift box; the game shows them for 30
             days, then as expired for 2 more days, then they are gone
Switches     close story areas, Vortex areas or single units (units leave summons and drops)
Rates        exp / zel / karma for clearing quests, unit drop chance, item chest chance

The password is set the first time the page is opened - only from the PC the server runs on. It is
stored hashed in saves/admin_auth.json (delete that file to set a new one from the server PC).
"""
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import threading
import time

SESSION_HOURS = 12
FAIL_LIMIT, FAIL_LOCK = 5, 15 * 60
GIFT_DAYS, EXPIRED_DAYS = 30, 2
ONLINE_SECONDS = 5 * 60              # a game request in the last 5 minutes = shown as online


class Admin:
    def __init__(self, srv):
        self.S = srv
        self.dir = srv.SAVE_DIR
        self.auth_file = os.path.join(self.dir, 'admin_auth.json')
        self.gifts_file = os.path.join(self.dir, 'server_gifts.json')
        self.sessions = {}
        self.fails = {}
        self.mu = threading.Lock()
        here = srv.HERE
        p = os.path.join(here, 'data', 'item_names.json')
        self.items = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {}
        p = os.path.join(here, 'data', 'dungeon_area.json')
        self.geo = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {'dungeon': {}, 'area': {}}
        self.page = os.path.join(here, 'admin.html')

    # ------------------------------------------------------------------ access
    def allowed_ip(self, ip):
        if self.S.CFG.get('admin_anywhere'):
            return True              # hosted server: the page is open from anywhere, behind the password
        try:
            a = ipaddress.ip_address(ip)
            return a.is_private or a.is_loopback
        except ValueError:
            return False

    def is_server_pc(self, ip):
        return ip in ('127.0.0.1', '::1', self.S.HOST)

    def has_password(self):
        return os.path.exists(self.auth_file)

    @staticmethod
    def _hash(pw, salt):
        return hashlib.pbkdf2_hmac('sha256', pw.encode('utf-8'), bytes.fromhex(salt), 200000).hex()

    def set_password(self, pw):
        salt = secrets.token_hex(16)
        json.dump({'salt': salt, 'hash': self._hash(pw, salt)}, open(self.auth_file, 'w'))

    def check_password(self, pw):
        try:
            a = json.load(open(self.auth_file))
        except Exception:
            return False
        return hmac.compare_digest(self._hash(pw, a['salt']), a['hash'])

    def session_ok(self, token):
        with self.mu:
            t = self.sessions.get(token or '')
            if t and t > time.time():
                return True
            self.sessions.pop(token or '', None)
            return False

    def new_session(self):
        tok = secrets.token_urlsafe(32)
        with self.mu:
            self.sessions[tok] = time.time() + SESSION_HOURS * 3600
        return tok

    # ------------------------------------------------------------------ settings
    def settings(self):
        return self.S.admin_settings()

    def save_settings(self, s):
        self.S.save_admin_settings(s)

    # ------------------------------------------------------------------ server-wide gifts
    def gifts(self):
        try:
            return json.load(open(self.gifts_file, encoding='utf-8'))
        except Exception:
            return []

    def save_gifts(self, g):
        tmp = self.gifts_file + '.tmp'
        json.dump(g, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        os.replace(tmp, self.gifts_file)

    # ------------------------------------------------------------------ helpers
    def unit_name(self, uid):
        return (self.S.unit_mst().get(str(uid)) or {}).get('utP1c0CD', str(uid))

    def item_name(self, iid):
        return (self.items.get(str(iid)) or [str(iid)])[0]

    def reward(self, kind, target, count):
        """(present type, target, count, label) for a reward picked on the page."""
        kind, count = str(kind), int(count)
        if count <= 0:
            raise ValueError('The amount must be at least 1.')
        if kind in ('gems', 'zel', 'karma'):
            return {'gems': 8, 'zel': 3, 'karma': 2}[kind], '', count, f'{count:,} {kind}'
        if kind == 'unit':
            if str(target) not in self.S.unit_mst():
                raise ValueError('Pick a unit from the list.')
            return 6, str(target), count, f'{count} x {self.unit_name(target)}'
        if kind == 'item':
            it = self.items.get(str(target))
            if not it:
                raise ValueError('Pick an item from the list.')
            return int(it[2]), str(target), count, f'{count} x {it[0]}'
        raise ValueError('Unknown reward.')

    def player_summary(self, pl):
        t = self.S.team_row(pl.get('state') or {}) or {}
        w = pl.get('wallet') or {}
        unclaimed = sum(1 for r in self.S._rows((pl.get('state') or {}).get(self.S.G_PRESENTS))
                        if isinstance(r, dict) and r.get('g85qMNxf') != '1')
        seen = self.S.LAST_SEEN.get(pl.get('user_id'))
        if seen is None:                     # not seen since the server started: when the save last changed
            try:
                seen = os.path.getmtime(self.S.player_path(pl.get('user_id')))
            except OSError:
                seen = None
        return {'seen': int(seen) if seen else None, 'online': bool(seen and time.time() - seen < ONLINE_SECONDS),
                'id': pl.get('user_id'), 'name': pl.get('handle'), 'account': pl.get('account', ''),
                'level': self.S._num(t.get('D9wXQI2V')) or 1, 'gems': w.get('gems', 0), 'zel': w.get('zel', 0),
                'karma': w.get('karma', 0), 'honor': w.get('friendpoints', 0),
                'tickets': w.get('summontickets', 0), 'units': len(pl.get('units') or {}),
                'presents': unclaimed, 'merit': pl.get('ach_points', 0)}

    def areas(self):
        st, vx = [], []
        for aid, a in self.geo.get('area', {}).items():
            n = int(aid)
            if a.get('land') == '99':
                vx.append({'id': aid, 'name': a.get('name') or aid})
            elif a.get('type') == '0' and 0 < n < 10000:
                st.append({'id': aid, 'name': a.get('name') or aid})
        key = lambda x: int(x['id'])
        return sorted(st, key=key), sorted(vx, key=key)

    # ------------------------------------------------------------------ pictures
    def art(self, rel_paths):
        """An art file from the content folder (fetched from bfrevive when missing), unscrambled."""
        import masterdata
        S = self.S
        for rel in rel_paths:
            fp = os.path.join(S.CDN_DIR, rel.lstrip('/'))
            if os.path.isfile(fp):
                b = open(fp, 'rb').read()
                break
        else:
            b = None
            if S.CFG.get('fetch_missing_content'):
                for rel in rel_paths:
                    b = S.fetch_upstream('/' + rel.lstrip('/'))
                    if b:
                        fp = os.path.join(S.CDN_DIR, rel.lstrip('/'))
                        os.makedirs(os.path.dirname(fp), exist_ok=True)
                        open(fp, 'wb').write(b)
                        break
        if not b:
            return None, None
        if b[:8] == b'\x89PNG\r\n\x1a\n':
            return b, 'image/png'
        if b[:2] == b'\xff\xd8':
            return b, 'image/jpeg'
        d = masterdata.file_crypt_decode(b)
        if d[:8] == b'\x89PNG\r\n\x1a\n':
            return d, 'image/png'
        if d[:2] == b'\xff\xd8':
            return d, 'image/jpeg'
        return None, None

    def image(self, sub):
        kind, _, name = sub.partition('/')
        name = os.path.basename(name)
        if kind == 'unit' and name.split('.')[0].isdigit():
            return self.art([f'content/unit/img/unit_ills_thum_{name.split(".")[0]}.png'])
        if kind == 'gacha' and name and not name.startswith('.'):
            return self.art([f'content/gacha/{name}', f'content/_dlcbundle/{name}'])
        if kind == 'banner' and name and not name.startswith('.'):
            return self.art([f'content/dungeon/{name}', f'content/area/{name}', f'content/mission_select/{name}'])
        return None, None

    # ------------------------------------------------------------------ API
    def api(self, action, data, ip):
        S = self.S
        if action.startswith('pe_'):
            if getattr(self, '_peditor', None) is None:
                import playeredit
                self._peditor = playeredit.PlayerEditor(self)
            return self._peditor.api(action, data)
        if action == 'units':
            um = S.unit_mst()
            off = set(self.settings()['off']['units'])
            return {'units': [[i, u.get('utP1c0CD') or i, int(u.get('7ofj5xa1') or 0), int(u.get('iNy0ZU5M') or 0),
                               1 if i in off else 0] for i, u in sorted(um.items(), key=lambda x: int(x[0]))]}
        if action == 'units_set':
            ids = [str(x) for x in data.get('ids') or [] if str(x) in S.unit_mst()]
            s = self.settings()
            cur = set(s['off']['units'])
            cur = cur | set(ids) if data.get('close') else cur - set(ids)
            s['off']['units'] = sorted(cur, key=int)
            self.save_settings(s)
            S.log.info('admin page: %s %d units (%d closed in all)', 'closed' if data.get('close') else 'opened',
                       len(ids), len(cur))
            return {'ok': f"{'Closed' if data.get('close') else 'Opened'} {len(ids)} unit(s). {len(cur)} closed in all.",
                    'closed': s['off']['units']}
        if action == 'features':
            cur = self.settings().get('features', {})
            return {'features': [{'id': k, 'name': n, 'where': w, 'off_effect': e, 'on': bool(cur.get(k, 1))}
                                 for k, n, w, e in S.GAME_FEATURES],
                    'raid_max_class': self.settings().get('raid_max_class', 3)}
        if action == 'raid_class_set':
            n = int(data.get('n') or 0)
            if not 1 <= n <= 7:
                raise ValueError('Raid Class must be 1 to 7.')
            s = self.settings()
            s['raid_max_class'] = n
            self.save_settings(s)
            S.log.info('admin page: Raid Classes open up to %d', n)
            return {'ok': f'Raid Classes 1 to {n} are open. Takes effect straight away (players see the quest list change next time they open it).'}
        if action == 'feature_set':
            k = str(data.get('id', ''))
            if k not in {f[0] for f in S.GAME_FEATURES}:
                raise ValueError('Unknown game area.')
            s = self.settings()
            s.setdefault('features', {})[k] = 1 if data.get('on') else 0
            self.save_settings(s)
            S.log.info('admin page: game area %s switched %s', k, 'on' if data.get('on') else 'off')
            return {'ok': 'Saved - players see it after fully closing and reopening the game.'}
        if action == 'trials':
            return {'trials': S.trial_overview()}
        if action == 'trial_set':
            mid = str(data.get('id', ''))
            if mid not in {t['id'] for t in S.trial_overview()}:
                raise ValueError('Unknown trial.')
            s = self.settings()
            if mid in S.EXTRA_FIGHTS:            # off by default: switched on by being listed
                cur = set(s.get('on_extra', []))
                cur = cur | {mid} if data.get('on') else cur - {mid}
                s['on_extra'] = sorted(cur, key=int)
            else:
                cur = set(s['off'].get('trials', []))
                cur = cur - {mid} if data.get('on') else cur | {mid}
                s['off']['trials'] = sorted(cur, key=int)
            self.save_settings(s)
            S.vortex_editor().changed()          # new quest table version, without (or with) this trial
            S.log.info('admin page: trial %s switched %s', mid, 'on' if data.get('on') else 'off')
            return {'ok': 'Saved - players see it after their next login, once the game has restarted.'}
        if action == 'summons':
            return {'summons': S.summon_editor().overview()}
        if action == 'summon_save':
            gid = str(data.get('id', ''))
            S.summon_editor().update(gid, data)
            S.log.info('admin page: summon %s changed (%s)', gid, ', '.join(k for k in data if k != 'id'))
            return {'ok': 'Saved - players see it the next time they open the Summon screen.',
                    'summon': next(x for x in S.summon_editor().overview() if x['id'] == gid)}
        if action == 'summon_create':
            gid = S.summon_editor().create(str(data.get('from', '')), str(data.get('name', '')))
            S.log.info('admin page: new summon %s "%s" (copy of %s)', gid, data.get('name'), data.get('from'))
            return {'ok': f'New summon #{gid} made. It is hidden until you press "Show in game" - set its units, '
                          'rates, cost and picture first.', 'id': gid}
        if action == 'summon_delete':
            S.summon_editor().delete(str(data.get('id', '')))
            S.log.info('admin page: deleted own summon %s', data.get('id'))
            return {'ok': 'Summon deleted (a copy is kept in saves/summons_deleted.json).'}
        if action == 'summon_restore':
            S.summon_editor().restore(str(data.get('id', '')))
            return {'ok': 'Back to the original summon.'}
        if action == 'summon_image':
            name = S.summon_editor().set_image(str(data.get('id', '')), str(data.get('png') or ''))
            S.log.info('admin page: new picture for summon %s (%s)', data.get('id'), name)
            return {'ok': 'New picture saved - players get it the next time they open the Summon screen.'}
        if action == 'vortex':
            V = S.vortex_editor()
            tabs = V.overview()
            sched = {}
            for t in tabs:
                for b in t['banners']:
                    sc = S.banner_schedule(b['id'])
                    if sc:
                        b['listed'] = True          # day banners are in the game's list on their days
                        t['listed'] = True
                        sched[b['id']] = dict(sc, open_now=not S.schedule_closed(b['id']),
                                              game_days=S.default_banner_days().get(b['id']))
            boosts = {}
            for did in (self.settings().get('boost') or {}):
                b = S.active_boost(did)
                if b:
                    boosts[did] = b
            return {'tabs': tabs, 'closed': self.settings()['off'].get('banners', []),
                    'closed_tabs': self.settings()['off'].get('vortex', []), 'schedules': sched,
                    'boosts': boosts,
                    'today': time.localtime().tm_wday}
        if action == 'banner_schedule':
            did = str(data.get('id', ''))
            mode = str(data.get('mode', ''))
            s = self.settings()
            sch = s.setdefault('schedule', {})
            if mode == 'reset':
                sch.pop(did, None)
                msg = 'Back to the game\'s own days.' if did in S.default_banner_days() else 'Schedule removed.'
            else:
                if mode not in ('days', 'always', 'off'):
                    raise ValueError('Pick when the banner is open.')
                days = sorted({int(x) for x in data.get('days') or [] if str(x).isdigit() and 0 <= int(x) <= 6})
                if mode == 'days' and not days:
                    raise ValueError('Pick at least one day, or choose Always on / Off.')
                until = 0
                if data.get('until'):
                    try:
                        until = int(time.mktime(time.strptime(str(data['until']), '%Y-%m-%d'))) + 86400 - 1
                    except ValueError:
                        raise ValueError('That date could not be read.')
                    if until < time.time():
                        raise ValueError('Pick a date from today on for "open every day until".')
                sch[did] = {'mode': mode, 'days': days or (S.banner_schedule(did) or {}).get('days') or [], 'until': until}
                names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
                msg = {'days': 'Open on ' + ', '.join(names[d] for d in days), 'always': 'Always open',
                       'off': 'Off'}[mode]
                if until:
                    msg += ' - and open every day until ' + time.strftime('%a %d %b', time.localtime(until))
                msg += '. Players see it after their next login.'
            self.save_settings(s)
            S.log.info('admin page: banner %s schedule %s', did, sch.get(did, 'reset'))
            return {'ok': msg}
        if action == 'news_get':
            return S.news_engine().admin_view()
        if action == 'news_save':
            v = S.news_engine().admin_save(data)
            return dict(v, ok='Saved. Players see it the next time they open the News (Home > Info, '
                              'Menu > News, or News on the title screen).')
        if action == 'sphere_recipes':
            on = set(self.settings().get('sphere_on') or [])
            out = [dict(v, id=rid, on=(v.get('cat') == S.SPHERE_ALWAYS or rid in on),
                        always=v.get('cat') == S.SPHERE_ALWAYS)
                   for rid, v in S.SPHERE_SOURCES.items()]
            return {'recipes': out}
        if action == 'sphere_recipes_save':
            ids = {str(x) for x in (data.get('on') or [])}
            bad = ids - set(S.SPHERE_SOURCES)
            if bad:
                raise ValueError('Unknown recipe: ' + ', '.join(sorted(bad)[:5]))
            s = self.settings()
            s['sphere_on'] = sorted(i for i in ids if S.SPHERE_SOURCES[i].get('cat') != S.SPHERE_ALWAYS)
            self.save_settings(s)
            S.log.info('admin page: %d event spheres switched on in the Sphere House', len(s['sphere_on']))
            return {'ok': f"Saved. {len(s['sphere_on'])} event sphere recipe(s) are on. Players see the change the "
                          'next time the game talks to the server (reopen the Town if it is open).'}
        if action == 'unitbox':
            return {'base': self.settings().get('unitbox', S.UNITBOX_START), 'start': S.UNITBOX_START,
                    'max': S.UNITBOX_MAX}
        if action == 'unitbox_save':
            try:
                n = int(str(data.get('base')).strip())
            except (TypeError, ValueError):
                raise ValueError('The unit box size must be a whole number.')
            if not S.UNITBOX_START <= n <= S.UNITBOX_MAX:
                raise ValueError(f'The unit box size must be {S.UNITBOX_START}-{S.UNITBOX_MAX}.')
            s = self.settings()
            s['unitbox'] = n
            self.save_settings(s)
            S.log.info('admin page: base unit box %s', n)
            return {'ok': f'Saved. Every player\'s base unit box is now {n} (plus any space they bought '
                          'with gems). Players see it the next time they open the game.'}
        if action == 'fusion_rates':
            return {'fusion': self.settings().get('fusion'), 'normal': S.FUSION_NORMAL}
        if action == 'fusion_rates_save':
            s = self.settings()
            fu = s.setdefault('fusion', {})
            names = {'great': 'Great', 'super': 'Super'}
            for kind in ('great', 'super'):
                v = (data.get('fusion') or {}).get(kind) or {}
                try:
                    chance, mult = float(v.get('chance')), float(v.get('mult'))
                except (TypeError, ValueError):
                    raise ValueError(f'{names[kind]} Success: chance and multiplier must be numbers.')
                if not 0 <= chance <= 100:
                    raise ValueError(f'{names[kind]} Success: the chance must be 0-100%.')
                if not 1 <= mult <= 100:
                    raise ValueError(f'{names[kind]} Success: the exp multiplier must be 1-100.')
                fu[kind] = {'on': 1 if v.get('on') else 0, 'chance': round(chance, 2), 'mult': round(mult, 2)}
            lab = str(data.get('label') or 'none')
            if lab not in ('none', 'great', 'super'):
                raise ValueError('Pick which label the Fusion button shows.')
            fu['label'] = lab
            if fu['great']['on'] * fu['great']['chance'] + fu['super']['on'] * fu['super']['chance'] + \
               (0 if fu['great']['on'] else S.FUSION_NORMAL['great']['chance']) + \
               (0 if fu['super']['on'] else S.FUSION_NORMAL['super']['chance']) > 100:
                raise ValueError('Great and Super chances together can be at most 100%.')
            self.save_settings(s)
            S.log.info('admin page: fusion success %s', fu)
            return {'ok': 'Saved. New chances count from the next fusion; the multiplier shown in the game '
                          'updates after players fully close and reopen the game.'}
        if action == 'sell_prices':
            return {'units': S.sell_price().listing()}
        if action == 'sell_prices_save':
            s = self.settings()
            sell = {}
            for u, v in (data.get('prices') or {}).items():
                u = str(u)
                if u not in {x for x, _ in __import__('sellprice').SELL_UNITS}:
                    continue
                try:
                    z = int(str(v).strip())
                except ValueError:
                    raise ValueError('Sell prices must be whole numbers of zel.')
                if not 0 <= z <= 99999999:
                    raise ValueError('A sell price must be between 0 and 99,999,999 zel.')
                if z != S.sell_price().normal(u):
                    sell[u] = z
            s['sell'] = sell
            self.save_settings(s)
            S.log.info('admin page: sell prices %s', sell or 'all normal')
            return {'ok': 'Saved. Players see the new prices after fully closing and reopening the game '
                          '(the game downloads its unit list again).'}
        if action == 'banner_boost':
            # reward boost on Vortex banners: {ids, type: exp/zel/karma or '' to remove, rate, until}
            ids = [str(x) for x in data.get('ids') or [] if str(x)]
            if not ids:
                raise ValueError('Pick at least one banner.')
            s = self.settings()
            bo = s.setdefault('boost', {})
            kind = str(data.get('type') or '')
            if not kind:
                for did in ids:
                    bo.pop(did, None)
                self.save_settings(s)
                S.log.info('admin page: reward boost removed from %s', ', '.join(ids))
                return {'ok': f'Boost removed from {len(ids)} banner{"s" if len(ids) > 1 else ""}. '
                              'Players see it after their next login.'}
            if kind not in S.BOOST_TYPES:
                raise ValueError('Pick EXP, Zel or Karma.')
            # whole numbers only: the game's number font for the banner label has digits but no
            # decimal point (campaign_text_number.fnt), so 1.5 would show as "15"
            try:
                rate = float(data.get('rate'))
            except (TypeError, ValueError):
                raise ValueError('The multiplier must be a whole number, like 2 or 3.')
            if not rate.is_integer() or not 2 <= rate <= 99:
                raise ValueError('The multiplier must be a whole number from 2 to 99 - the game can\'t show decimals on the banner.')
            until = 0
            if data.get('until'):
                try:
                    until = int(time.mktime(time.strptime(str(data['until']), '%Y-%m-%d'))) + 86400 - 1
                except ValueError:
                    raise ValueError('That date could not be read.')
                if until < time.time():
                    raise ValueError('Pick an end date from today on.')
            for did in ids:
                bo[did] = {'type': kind, 'rate': rate, 'until': until}
            self.save_settings(s)
            label = {'exp': 'EXP', 'zel': 'Zel', 'karma': 'Karma'}[kind]
            S.log.info('admin page: reward boost %s x%s on %s', kind, rate, ', '.join(ids))
            msg = f'{label} x{S._rate_text(rate)} on {len(ids)} banner{"s" if len(ids) > 1 else ""}'
            if until:
                msg += ' until ' + time.strftime('%a %d %b', time.localtime(until))
            return {'ok': msg + '. Players see it after their next login.'}
        if action == 'banner_close':
            s = self.settings()
            key = 'vortex' if data.get('tab') else 'banners'
            cur = set(s['off'].get(key, []))
            ids = {str(x) for x in data.get('ids') or []}
            cur = cur | ids if data.get('close') else cur - ids
            s['off'][key] = sorted(cur, key=lambda x: int(x) if x.isdigit() else 0)
            self.save_settings(s)
            return {'ok': 'Saved.'}
        if action == 'banner_image':
            name = S.vortex_editor().set_image(str(data.get('id')), str(data.get('png') or ''))
            S.log.info('admin page: new picture for Vortex banner %s (%s)', data.get('id'), name)
            return {'ok': 'New picture saved - players get it the next time they open the game.'}
        if action == 'banner_restore':
            S.vortex_editor().restore(str(data.get('id')))
            return {'ok': 'Banner back to the original picture and tab.'}
        if action == 'banner_move':
            S.vortex_editor().move(str(data.get('id')), str(data.get('tab')))
            return {'ok': 'Banner moved - players see it there the next time they open the game.'}
        if action == 'banner_create':
            did = S.vortex_editor().create(data.get('name'), str(data.get('tab')), [str(q) for q in data.get('quests') or []],
                                           data.get('png') or None)
            S.log.info('admin page: new Vortex banner %s "%s"', did, data.get('name'))
            return {'ok': f'Banner "{data.get("name")}" created - players see it the next time they open the game.'}
        if action == 'banner_delete':
            S.vortex_editor().delete(str(data.get('id')))
            return {'ok': 'Banner deleted.'}
        if action == 'quest_search':
            q = str(data.get('q', '')).strip().lower()
            if len(q) < 2:
                return {'results': []}
            hits = [(i, m) for i, m in S.MISSIONS.items() if (q in str(m.get('name', '')).lower() or q == i)
                    and not m.get('custom_src')]
            hits.sort(key=lambda x: int(x[0]))
            return {'results': [{'id': i, 'name': m.get('name'), 'energy': m.get('energy'),
                                 'banner': S.vortex_editor().name(str(m.get('dungeon')))} for i, m in hits[:40]]}
        if action == 'players':
            return {'players': sorted((self.player_summary(p) for p in S.all_players()),
                                      key=lambda x: str(x['name']).lower())}
        if action == 'search':
            q, kind = str(data.get('q', '')).strip().lower(), data.get('kind')
            if len(q) < 2:
                return {'results': []}
            if kind == 'unit':
                um = S.unit_mst()
                hits = [(i, u) for i, u in um.items() if q in str(u.get('utP1c0CD', '')).lower() or q == i]
                hits.sort(key=lambda x: (-int(x[1].get('7ofj5xa1') or 0), str(x[1].get('utP1c0CD'))))
                off = set(self.settings()['off']['units'])
                return {'results': [{'id': i, 'name': u.get('utP1c0CD'), 'rarity': int(u.get('7ofj5xa1') or 0),
                                     'off': i in off} for i, u in hits[:40]]}
            hits = [(i, v) for i, v in self.items.items() if q in v[0].lower() or q == i]
            hits.sort(key=lambda x: x[1][0])
            return {'results': [{'id': i, 'name': v[0], 'type': v[1]} for i, v in hits[:40]]}
        if action == 'give':
            uid = str(data.get('player', ''))
            with S.LOCK:
                pl = S.load_player(uid)
                if not pl:
                    raise ValueError('That player was not found.')
                S.ensure_wallet(pl)
                if data.get('to') == 'balance':
                    f = {'gems': 'gems', 'zel': 'zel', 'karma': 'karma', 'honor': 'friendpoints',
                         'tickets': 'summontickets'}.get(data.get('kind'))
                    n = int(data.get('count') or 0)
                    if not f or n == 0:
                        raise ValueError('Pick what to change and an amount.')
                    pl['wallet'][f] = max(0, pl['wallet'].get(f, 0) + n)
                    t = S.team_row(pl['state'])
                    if t is not None:
                        code = S.WALLET[f]
                        t[code] = pl['wallet'][f] if isinstance(t.get(code), int) else str(pl['wallet'][f])
                    S.save_player(pl)
                    S.log.info('admin page: %s %+d %s -> %d', pl.get('handle'), n, f, pl['wallet'][f])
                    msg = f"{'Added' if n > 0 else 'Took'} {abs(n):,} {data.get('kind')} - {pl.get('handle')} now has {pl['wallet'][f]:,}."
                else:
                    ptype, target, count, label = self.reward(data.get('kind'), data.get('target'), data.get('count', 1))
                    note = str(data.get('message') or '').strip()[:80] or 'A gift from the server'
                    if ptype == 6:
                        for _ in range(count):
                            S.add_present(pl, 6, target, 1, note)
                    else:
                        S.add_present(pl, ptype, target, count, note)
                    S.save_player(pl)
                    S.log.info('admin page: sent %s to %s', label, pl.get('handle'))
                    msg = f'Sent {label} to {pl.get("handle")}\'s gift box.'
                return {'ok': msg, 'player': self.player_summary(pl)}
        if action == 'gifts':
            now = time.time()
            out = []
            for g in self.gifts():
                if g.get('deleted'):
                    continue
                left = g['created'] + GIFT_DAYS * 86400 - now
                gone = g['created'] + (GIFT_DAYS + EXPIRED_DAYS) * 86400 - now
                if gone <= 0:
                    continue
                out.append(dict(g, status='active' if left > 0 else 'expired',
                                days_left=round((left if left > 0 else gone) / 86400, 1)))
            return {'gifts': sorted(out, key=lambda g: -g['created'])}
        if action == 'gift_create':
            ptype, target, count, label = self.reward(data.get('kind'), data.get('target'), data.get('count', 1))
            note = str(data.get('message') or '').strip()[:80]
            if not note:
                raise ValueError('Write the message players will see in the gift box.')
            g = self.gifts()
            gid = f"g{int(time.time())}{secrets.token_hex(2)}"
            g.append({'id': gid, 'type': ptype, 'target': target, 'count': count, 'label': label,
                      'message': note, 'created': int(time.time())})
            self.save_gifts(g)
            S.log.info('admin page: server-wide gift %s "%s" (%s)', gid, note, label)
            return {'ok': f'Gift "{note}" ({label}) is now waiting for every player.'}
        if action == 'gift_delete':
            g = self.gifts()
            for x in g:
                if x['id'] == data.get('id'):
                    x['deleted'] = int(time.time())
            self.save_gifts(g)
            S.log.info('admin page: server-wide gift %s withdrawn', data.get('id'))
            return {'ok': 'Gift withdrawn - unclaimed copies disappear from gift boxes.'}
        if action == 'settings':
            st, vx = self.areas()
            s = self.settings()
            um = S.unit_mst()
            units = [{'id': u, 'name': self.unit_name(u), 'rarity': int((um.get(u) or {}).get('7ofj5xa1') or 0)}
                     for u in s['off']['units']]
            return {'settings': s, 'story_areas': st, 'vortex_areas': vx, 'off_units': units}
        if action == 'settings_save':
            s = self.settings()
            m = data.get('mult') or {}
            for k in s['mult']:
                if k in m:
                    v = float(m[k])
                    if not 0 <= v <= 100:
                        raise ValueError(f'{k} must be between 0 and 100.')
                    s['mult'][k] = round(v, 2)
            off = data.get('off') or {}
            for k in ('areas', 'vortex', 'banners', 'units'):
                if k in off:
                    s['off'][k] = sorted({str(x) for x in off[k]}, key=lambda x: int(x) if x.isdigit() else 0)
            self.save_settings(s)
            S.log.info('admin page: settings saved - rates %s, closed areas %d, closed Vortex %d, closed units %d',
                       s['mult'], len(s['off']['areas']), len(s['off']['vortex']), len(s['off']['units']))
            return {'ok': 'Saved - the game picks this up on its next screen change.', 'settings': s}
        raise ValueError('Unknown action.')

    # ------------------------------------------------------------------ HTTP
    def handle(self, h, method, path, raw):
        """Returns (code, content type, body, extra headers)."""
        ip = h.client_address[0]
        if not self.allowed_ip(ip):
            return 403, 'text/plain; charset=utf-8', 'The admin page is only open on the home network.', {}
        cookie = {}
        for part in (h.headers.get('Cookie') or '').split(';'):
            k, _, v = part.strip().partition('=')
            cookie[k] = v
        token = cookie.get('bfadmin')
        if method == 'GET':
            if path.startswith('/admin/img/'):
                if not self.session_ok(token):
                    return 401, 'text/plain; charset=utf-8', 'sign in', {}
                data, ctype = self.image(path[len('/admin/img/'):])
                if data is None:
                    return 404, 'text/plain; charset=utf-8', 'no picture', {}
                return 200, ctype, data, {'Cache-Control': 'private, max-age=86400'}
            if path.rstrip('/') == '/admin':
                try:
                    return 200, 'text/html; charset=utf-8', open(self.page, encoding='utf-8').read(), \
                        {'Cache-Control': 'no-store'}
                except OSError:
                    return 500, 'text/plain; charset=utf-8', 'admin.html is missing.', {}
            return 404, 'text/plain; charset=utf-8', 'not found', {}
        action = path[len('/admin/api/'):] if path.startswith('/admin/api/') else ''
        try:
            data = json.loads(raw or b'{}')
        except ValueError:
            data = {}
        j = lambda code, obj, hdr=None: (code, 'application/json; charset=utf-8', obj, hdr or {})
        if action == 'status':
            return j(200, {'has_password': self.has_password(), 'signed_in': self.session_ok(token),
                           'can_setup': self.is_server_pc(ip)})
        if action == 'setup':
            if self.has_password():
                return j(400, {'error': 'A password is already set.'})
            if not self.is_server_pc(ip):
                return j(403, {'error': 'The first password can only be set on the PC the server runs on.'})
            pw = str(data.get('password') or '')
            if len(pw) < 8:
                return j(400, {'error': 'Use at least 8 characters.'})
            self.set_password(pw)
            self.S.log.info('admin page: password set')
            return j(200, {'ok': True}, self.cookie(self.new_session()))
        if action == 'login':
            with self.mu:
                n, until = self.fails.get(ip, (0, 0))
            if until > time.time():
                return j(429, {'error': f'Too many wrong passwords - try again in {int((until - time.time()) // 60) + 1} min.'})
            if not self.has_password():
                return j(400, {'error': 'No password set yet - open this page on the server PC first.'})
            if self.check_password(str(data.get('password') or '')):
                with self.mu:
                    self.fails.pop(ip, None)
                self.S.log.info('admin page: signed in from %s', ip)
                return j(200, {'ok': True}, self.cookie(self.new_session()))
            with self.mu:
                n += 1
                self.fails[ip] = (0, time.time() + FAIL_LOCK) if n >= FAIL_LIMIT else (n, 0)
            self.S.log.info('admin page: wrong password from %s', ip)
            return j(401, {'error': 'Wrong password.'})
        if action == 'logout':
            with self.mu:
                self.sessions.pop(token or '', None)
            return j(200, {'ok': True}, {'Set-Cookie': 'bfadmin=; Path=/admin; Max-Age=0; HttpOnly; SameSite=Strict'})
        if not self.session_ok(token):
            return j(401, {'error': 'Please sign in.', 'signed_out': True})
        try:
            return j(200, self.api(action, data, ip))
        except ValueError as e:
            return j(400, {'error': str(e)})
        except Exception as e:
            self.S.log.exception('admin page: %s failed', action)
            return j(500, {'error': f'Something went wrong ({e}).'})

    @staticmethod
    def cookie(tok):
        return {'Set-Cookie': f'bfadmin={tok}; Path=/admin; Max-Age={SESSION_HOURS * 3600}; HttpOnly; SameSite=Strict'}
