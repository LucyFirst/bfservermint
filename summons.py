"""
Summons as the admin page sets them up: which summons the game shows, what each costs, its units,
its rates and its picture.

The game's own data:
  1IR86sAv  (GachaList, GetUserInfo)  the summons the Summon screen pages through - 7Ffmi96v id,
            4N27mkt1 name, 03UGMHxF gem cost, J3stQ7jd honor cost, 1Dg0vUX3 page picture,
            W2c9g0Je short text (rates / price), 3sdHQb69 long text
  5Y4GJeo3  (URLMst)  the summon table (same ids and costs)
  JukkSeNA  selector summons: r7MSYD2c = the units to choose from
  IBs49NiH  the old tile menu - switched off (featureCheck gacha_category = 0) so the Summon
            screen is one list you swipe through
Pools (units and rates) start from data/gacha_pools.json; changes live in saves/summons.json:
  {summon id: {hidden, gems, honor, image, units: [ids], rates: {rarity: percent}, text}}
"""
import base64
import json
import os
import threading
import time

import masterdata

HONOR_ID = '1000'
PAGE_W, PAGE_H = 640, 632


def _i(x, d=0):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return d


class Summons:
    def __init__(self, srv):
        self.S = srv
        self.file = os.path.join(srv.SAVE_DIR, 'summons.json')
        self.mu = threading.RLock()
        self.rows, self.order, shown = {}, [], []
        # every summon row the recordings have, newest first; the newest GachaList decides the
        # game's normal order and which summons it showed
        acts = sorted(srv.T['action'], key=lambda a: -a['n'])
        for a in acts:
            b = srv.BLOBS[a['resp']]
            if not isinstance(b, dict):
                continue
            for r in b.get('1IR86sAv') or []:
                if isinstance(r, dict) and r.get('7Ffmi96v') and len(r) > 4:
                    gid = str(r['7Ffmi96v'])
                    if gid not in self.rows:
                        self.rows[gid] = dict(r)
            if a['cls'] == 'GachaListRequest' and not shown:
                shown = [str(r['7Ffmi96v']) for r in b.get('1IR86sAv') or [] if isinstance(r, dict)]
        self.selector = {}
        for a in acts:
            b = srv.BLOBS[a['resp']]
            if isinstance(b, dict):
                for r in b.get('JukkSeNA') or []:
                    if isinstance(r, dict) and r.get('7Ffmi96v'):
                        self.selector.setdefault(str(r['7Ffmi96v']), dict(r))
        self.order = shown + [g for g in self.rows if g not in shown]
        self.shown = set(shown)
        self.state = self.load()
        for gid in sorted((g for g, e in self.state.items() if (e or {}).get('custom')), key=int, reverse=True):
            self._add_custom_row(gid)

    # ------------------------------------------------------------------ your own summons
    # A custom summon is a new summon (its own id from 900001 up) made from a copy of an existing one:
    # same look and kind, then its own name, cost, units, rates and picture. The summon it was copied
    # from is never changed. Custom summons sit at the front of the Summon screen.
    CUSTOM_FIRST = 900001

    def is_custom(self, gid):
        return bool((self.state.get(gid) or {}).get('custom'))

    def _add_custom_row(self, gid):
        c = self.state[gid]['custom']
        src = self.rows.get(str(c.get('from'))) or next(iter(self.rows.values()))
        row = dict(src)
        row.update({'7Ffmi96v': gid, '4N27mkt1': c.get('name') or f'Summon {gid}'})
        for k in ('8HM3v3gg', 'SzV0Nps7', 'qA7M9EjP', 'a632bkyQ', '4tswNoV9'):
            row.pop(k, None)          # no campaign, dates, resummon or once-a-day rules from the copy
        self.rows[gid] = row
        if gid not in self.order:
            self.order.insert(0, gid)

    def create(self, src, name):
        src = str(src)
        name = str(name or '').strip()[:60]
        with self.mu:
            if src not in self.rows or self.kind(src) != 'summon':
                raise ValueError('Pick a normal summon to copy (not a selector or the Honor Summon).')
            if not name:
                raise ValueError('Give the new summon a name.')
            gid = str(max([self.CUSTOM_FIRST - 1] + [int(g) for g in self.state if self.is_custom(g)]) + 1)
            p = self.pool(src)
            gems, honor = self.price(src)
            e = {'custom': {'from': src, 'name': name, 'made': int(time.time())}, 'hidden': True,
                 'gems': gems, 'honor': honor,
                 'units': [u for v in p['units'].values() for u in v], 'rates': dict(p['rates'])}
            if p.get('rateup'):
                e['rateup'] = dict(p['rateup'])
            src_e = self.state.get(src) or {}
            if src_e.get('image'):
                e['image'] = src_e['image']
            self.state[gid] = e
            self._add_custom_row(gid)
            self.save()
        return gid

    def delete(self, gid):
        with self.mu:
            if not self.is_custom(gid):
                raise ValueError('Only summons you made yourself can be deleted.')
            old = self.state.pop(gid)
            self.rows.pop(gid, None)
            if gid in self.order:
                self.order.remove(gid)
            # kept (never thrown away): saves/summons_deleted.json
            f = os.path.join(self.S.SAVE_DIR, 'summons_deleted.json')
            try:
                gone = json.load(open(f, encoding='utf-8'))
            except Exception:
                gone = {}
            gone[f'{gid}_{int(time.time())}'] = old
            json.dump(gone, open(f, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            self.save()

    # ------------------------------------------------------------------ saved changes
    def load(self):
        try:
            return json.load(open(self.file, encoding='utf-8'))
        except Exception:
            return {}

    def save(self):
        tmp = self.file + '.tmp'
        json.dump(self.state, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        os.replace(tmp, self.file)

    # ------------------------------------------------------------------ what a summon is now
    def kind(self, gid):
        if gid == HONOR_ID:
            return 'honor'
        pool = self.S.POOLS.get(gid) or {}
        if gid in self.selector or pool.get('selector') or _i(self.rows.get(gid, {}).get('03UGMHxF')) == 999:
            return 'selector'
        return 'summon'

    def hidden(self, gid):
        e = self.state.get(gid) or {}
        if 'hidden' in e:
            return bool(e['hidden'])
        return gid not in self.shown or self.kind(gid) == 'selector'

    def price(self, gid):
        """(gems, honor) per summon."""
        r, e = self.rows.get(gid) or {}, self.state.get(gid) or {}
        gems = e['gems'] if 'gems' in e else _i(r.get('03UGMHxF'))
        honor = e['honor'] if 'honor' in e else _i(r.get('J3stQ7jd'))
        if gid not in self.rows and gid in self.S.GACHA_PRICE and 'gems' not in e and 'honor' not in e:
            return self.S.GACHA_PRICE[gid]
        return gems, honor

    def unit_rarity(self, uid):
        return _i((self.S.unit_mst().get(str(uid)) or {}).get('7ofj5xa1'))

    def pool(self, gid):
        """{'units': {rarity: [ids]}, 'rates': {rarity: percent}} - the admin page's, else the
        recorded pool, else the default one. Selectors: {'selector': [ids]}."""
        e = self.state.get(gid) or {}
        base = self.S.POOLS.get(gid)
        if self.kind(gid) == 'selector':
            ids = e.get('units')
            if ids is None:
                ids = (base or {}).get('selector') or \
                    [x for x in str((self.selector.get(gid) or {}).get('r7MSYD2c') or '').split(',') if x]
            return {'selector': [str(x) for x in ids]}
        if e.get('units') is not None:
            units = {}
            for u in e['units']:
                units.setdefault(str(self.unit_rarity(u)), []).append(str(u))
        else:
            units = {str(r): [str(x) for x in v] for r, v in ((base or self.S.POOLS.get('default') or {}).get('units') or {}).items()}
        rates = e.get('rates') or (base or self.S.POOLS.get('default') or {}).get('rates') or {}
        rates = {str(r): float(p) for r, p in rates.items() if str(r) in units and float(p) > 0}
        if not rates:
            rates = {r: round(100.0 / len(units), 2) for r in units} if units else {}
        have = {u for v in units.values() for u in v}
        rateup = {str(u): float(m) for u, m in (e.get('rateup') or {}).items() if str(u) in have and float(m) > 1}
        out = {'units': units, 'rates': rates, 'rateup': rateup}
        pity = e.get('pity') or {}
        pu = [str(u) for u in pity.get('units') or [] if str(u) in have]
        if pu and _i(pity.get('every')) >= 2:
            out['pity'] = {'units': pu, 'every': _i(pity['every'])}
        return out

    def chances(self, gid, off=()):
        """{unit id: percent per summon} - rarity chance shared by weight (rate-up units count more)."""
        p = self.pool(gid)
        if 'selector' in p:
            return {}
        total = sum(p['rates'].values()) or 1
        out = {}
        for r, ids in p['units'].items():
            ids = [u for u in ids if u not in off] or ids
            if r not in p['rates'] or not ids:
                continue
            w = {u: p['rateup'].get(u, 1.0) for u in ids}
            tw = sum(w.values())
            for u in ids:
                out[u] = p['rates'][r] * 100.0 / total * w[u] / tw
        return out

    def uses_default(self, gid):
        e = self.state.get(gid) or {}
        return self.kind(gid) != 'selector' and e.get('units') is None and gid not in self.S.POOLS

    def page_image(self, gid):
        e = self.state.get(gid) or {}
        return e.get('image') or (self.rows.get(gid) or {}).get('1Dg0vUX3') or ''

    def rate_text(self, gid):
        p = self.pool(gid)
        gems, honor = self.price(gid)
        parts = [f'<color=255:0:0>{r}★</color> {p["rates"][r]:g}%' for r in sorted(p['rates'], key=int)]
        cost = f'{honor} Honor Pts' if honor and not gems else f'{gems} gem{"s" if gems != 1 else ""}'
        return f'<center>{", ".join(parts)}<br>Enjoy <color=255:255:0>SUMMON</color> for <color=255:255:0>{cost}</color>!<center>'

    def auto_text(self, gid):
        """The text under the summon picture: name, where to see rates, and its best units."""
        r = self.rows.get(gid) or {}
        name = r.get('4N27mkt1') or 'Summon'
        if self.kind(gid) == 'selector':
            return r.get('3sdHQb69') or f'<center>[<color=255:255:0>{name}</color>]<center>'
        p = self.pool(gid)
        off = set(self.S.admin_settings()['off']['units'])
        um = self.S.unit_mst()
        top, names = None, []
        for rr in sorted(p['units'], key=int, reverse=True):
            ids = [u for u in p['units'][rr] if u not in off]
            if ids:
                top = rr
                names = sorted((um.get(u) or {}).get('utP1c0CD') or u for u in ids)
                break
        line = ''
        if top:
            shown = ', '.join(names[:8])
            more = f', and {len(names) - 8} more' if len(names) > 8 else ''
            line = f'<br><color=255:0:0>{top}\u2605</color> {shown}{more}'
        pity = p.get('pity')
        if pity:
            pn = sorted({(um.get(u) or {}).get('utP1c0CD') or u for u in pity['units']})
            who = ', '.join(pn[:4]) + (f' and {len(pn) - 4} more' if len(pn) > 4 else '')
            line += (f'<br><color=255:255:0>Guaranteed:</color> {"one of " if len(pn) > 1 else ""}{who} '
                     f'within every {pity["every"]} summons')
        return (f'<center>[<color=255:255:0>{name}</color>]<br><color=0:255:0>Tap the picture for rates and all units</color>'
                f'{line}<center>')

    def text(self, gid):
        custom = (self.state.get(gid) or {}).get('text')
        if custom:
            # the game's text box reads <...> as tags and shows &lt; as-is: swap angle brackets out,
            # then turn the admin page's [color=#rrggbb]...[/color] into the game's colour tags
            import re

            def flat(line):
                # colours may sit inside each other on the page; the game gets each stretch of
                # words with the colour closest to it
                out, stack = [], []
                for tok in re.split(r'(\[color=#[0-9a-fA-F]{6}\]|\[/color\])', line):
                    if tok.startswith('[color=#') and tok.endswith(']') and len(tok) == 15:
                        stack.append(tok[8:14])
                    elif tok == '[/color]':
                        if stack:
                            stack.pop()
                    elif tok:
                        if stack:
                            h = stack[-1]
                            out.append(f'<color={int(h[0:2], 16)}:{int(h[2:4], 16)}:{int(h[4:6], 16)}>{tok}</color>')
                        else:
                            out.append(tok)
                return ''.join(out)
            body = '<br>'.join(flat(x.replace('<', '\u2039').replace('>', '\u203a')) for x in str(custom).splitlines())
            return f'<center>{body}<center>'
        return self.auto_text(gid)

    def row(self, gid):
        """The 1IR86sAv row the game gets."""
        r = dict(self.rows[gid])
        e = self.state.get(gid) or {}
        gems, honor = self.price(gid)
        r['03UGMHxF'], r['J3stQ7jd'] = str(gems), str(honor)
        if e.get('image'):
            r['1Dg0vUX3'] = e['image']
        if self.kind(gid) != 'selector' and any(k in e for k in ('gems', 'honor', 'rates', 'units')):
            r['W2c9g0Je'] = self.rate_text(gid)
        r['3sdHQb69'] = self.text(gid)
        return r

    # ------------------------------------------------------------------ the game's replies
    def patch_response(self, resp):
        if not isinstance(resp, dict):
            return resp
        if isinstance(resp.get('At7Gny2V'), list):      # web page addresses: the summon viewer
            resp['At7Gny2V'] = [dict(r, qlO1kVZZ=RATES_PATH) if isinstance(r, dict) else r for r in resp['At7Gny2V']]
        if isinstance(resp.get('1IR86sAv'), list) and resp['1IR86sAv'] and isinstance(resp['1IR86sAv'][0], dict) \
                and len(resp['1IR86sAv'][0]) > 4:
            resp['1IR86sAv'] = [self.row(g) for g in self.order if g in self.rows and not self.hidden(g)]
        if isinstance(resp.get('5Y4GJeo3'), list):
            out = []
            for r in resp['5Y4GJeo3']:
                gid = str(r.get('7Ffmi96v', '')) if isinstance(r, dict) else ''
                if gid in self.rows and isinstance(r, dict) and r.get('3sdHQb69'):
                    r = dict(r, **{'3sdHQb69': self.text(gid)})
                if gid in self.state:
                    gems, honor = self.price(gid)
                    r = dict(r, **{'03UGMHxF': str(gems), 'J3stQ7jd': str(honor)})
                    if self.state[gid].get('image'):
                        r['1Dg0vUX3'] = self.state[gid]['image']
                out.append(r)
            have = {str(r.get('7Ffmi96v', '')) for r in out if isinstance(r, dict)}
            keys = next((list(r) for r in out if isinstance(r, dict)), None)
            by_id = {str(r.get('7Ffmi96v', '')): r for r in out if isinstance(r, dict)}
            for gid in self.order:
                if self.is_custom(gid) and gid not in have and not self.hidden(gid):
                    row = self.row(gid)
                    src = by_id.get(str(self.state[gid]['custom'].get('from')))
                    m = dict(src) if src else {k: row.get(k, '') for k in (keys or row)}
                    m.update({k: v for k, v in row.items() if k in m})
                    m['7Ffmi96v'] = gid
                    out.append(m)
            resp['5Y4GJeo3'] = out
        if isinstance(resp.get('JukkSeNA'), list):
            out = []
            for r in resp['JukkSeNA']:
                gid = str(r.get('7Ffmi96v', '')) if isinstance(r, dict) else ''
                if gid in self.state and (self.state[gid].get('units') is not None):
                    r = dict(r, r7MSYD2c=','.join(self.pool(gid)['selector']))
                out.append(r)
            resp['JukkSeNA'] = out
        return resp

    # ------------------------------------------------------------------ admin page
    def overview(self):
        um = self.S.unit_mst()
        out = []
        for gid in self.order:
            r = self.rows[gid]
            p = self.pool(gid)
            gems, honor = self.price(gid)
            if 'selector' in p:
                ids = p['selector']
                counts = {}
            else:
                ids = [u for v in p['units'].values() for u in v]
                counts = {r_: len(v) for r_, v in p['units'].items()}
            out.append({'id': gid, 'name': r.get('4N27mkt1') or gid, 'kind': self.kind(gid),
                        'hidden': self.hidden(gid), 'gems': gems, 'honor': honor,
                        'image': self.page_image(gid), 'edited': gid in self.state and not self.is_custom(gid),
                        'custom': self.is_custom(gid),
                        'copied_from': ((self.state.get(gid) or {}).get('custom') or {}).get('from'),
                        'default_pool': self.uses_default(gid), 'rates': p.get('rates', {}),
                        'counts': counts, 'units': ids, 'rateup': p.get('rateup', {}),
                        'pity': p.get('pity') or {},
                        'text': (self.state.get(gid) or {}).get('text') or '',
                        'auto_text': self.plain(self.auto_text(gid)),
                        'auto_markup': self.markup(self.auto_text(gid)),
                        'unit_names': {u: (um.get(u) or {}).get('utP1c0CD') or u for u in ids}})
        return out

    def update(self, gid, data):
        with self.mu:
            if gid not in self.rows:
                raise ValueError('That summon was not found.')
            e = dict(self.state.get(gid) or {})
            if 'hidden' in data:
                e['hidden'] = bool(data['hidden'])
            for k in ('gems', 'honor'):
                if k in data and data[k] not in (None, ''):
                    v = _i(data[k], -1)
                    if not 0 <= v <= 100000:
                        raise ValueError(f'The {k} cost must be between 0 and 100000.')
                    e[k] = v
            if 'name' in data and data['name'] is not None and e.get('custom'):
                nm = str(data['name']).strip()[:60]
                if not nm:
                    raise ValueError('Give the summon a name.')
                e['custom'] = dict(e['custom'], name=nm)
                self.rows[gid]['4N27mkt1'] = nm
            if 'text' in data and data['text'] is not None:
                t = str(data['text']).strip()[:1200]
                if t:
                    e['text'] = t
                else:
                    e.pop('text', None)
            if 'units' in data and data['units'] is not None:
                um = self.S.unit_mst()
                ids = list(dict.fromkeys(str(u) for u in data['units'] if str(u) in um))
                if not ids:
                    raise ValueError('A summon needs at least one unit.')
                e['units'] = ids
            if 'rateup' in data and data['rateup'] is not None:
                ru = {}
                for u, m in (data['rateup'] or {}).items():
                    try:
                        m = float(m)
                    except (TypeError, ValueError):
                        continue
                    if not 0 < m <= 1000:
                        raise ValueError('A rate-up must be between x1 and x1000.')
                    if m > 1:
                        ru[str(u)] = round(m, 2)
                e['rateup'] = ru
                if not ru:
                    e.pop('rateup')
            if 'pity' in data and data['pity'] is not None:
                pity = data['pity'] or {}
                every = _i(pity.get('every'))
                pu = list(dict.fromkeys(str(u) for u in pity.get('units') or []))
                if pu and every:
                    if not 2 <= every <= 1000:
                        raise ValueError('The guarantee must be every 2 to 1000 summons.')
                    have = set(e.get('units') or [u for v in self.pool(gid).get('units', {}).values() for u in v])
                    if any(u not in have for u in pu):
                        raise ValueError('Guaranteed units must be in this summon.')
                    e['pity'] = {'units': pu, 'every': every}
                else:
                    e.pop('pity', None)
            if 'rates' in data and data['rates'] is not None:
                rates = {str(r): float(p) for r, p in data['rates'].items() if str(p).strip() not in ('', 'None')}
                if any(p < 0 for p in rates.values()) or not any(p > 0 for p in rates.values()):
                    raise ValueError('Give at least one rarity a chance above 0%.')
                e['rates'] = {r: round(p, 3) for r, p in rates.items() if p > 0}
            self.state[gid] = e
            self.save()

    @staticmethod
    def markup(t):
        """The game's text as the admin page writes it: [color=#rrggbb]...[/color], one line per line."""
        import re
        t = re.sub(r'<br\s*/?>', '\n', str(t or ''))
        t = re.sub(r'<color=(\d+):(\d+):(\d+)>', lambda m: '[color=#%02x%02x%02x]' % tuple(min(255, int(x)) for x in m.groups()), t)
        t = t.replace('</color>', '[/color]')
        return re.sub(r'<[^>]+>', '', t).strip()

    @staticmethod
    def plain(t):
        import re
        t = re.sub(r'<br\s*/?>', '\n', str(t or ''))
        return re.sub(r'<[^>]+>', '', t).strip()

    def restore(self, gid):
        with self.mu:
            if self.is_custom(gid):
                raise ValueError('This is your own summon - there is no original to go back to.')
            self.state.pop(gid, None)
            self.save()

    def set_image(self, gid, data_url):
        if gid not in self.rows:
            raise ValueError('That summon was not found.')
        if not data_url.startswith('data:image/png;base64,'):
            raise ValueError('The picture must be a PNG.')
        png = base64.b64decode(data_url.split(',', 1)[1])
        if png[:8] != b'\x89PNG\r\n\x1a\n' or len(png) > 4_000_000:
            raise ValueError('That picture could not be read.')
        name = f'gacha_custom_{gid}_{int(time.time())}.png'
        d = os.path.join(self.S.CDN_DIR, 'content', 'gacha')
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, name), 'wb').write(masterdata.file_crypt_encode(png))
        with self.mu:
            self.state.setdefault(gid, {})['image'] = name
            self.save()
        return name


# --------------------------------------------------------------------------- the game's rates page
RATES_PATH = '/bf/web/summon_rates.php'
ELEMENTS = {'1': ('Fire', '#ff6a3d'), '2': ('Water', '#3db4ff'), '3': ('Earth', '#6fd04f'), '4': ('Thunder', '#ffd23d'),
            '5': ('Light', '#fff3b0'), '6': ('Dark', '#b07cff')}


def _esc(s):
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def find_summon(self, texts):
    """Which summon the game's viewer asked about. It sends 'summon id@detail id' - plain, and
    encrypted with the game's web key - so look for a known summon id in either."""
    import re
    cands = []
    for t in texts:
        t = str(t or '')
        cands.append(t)
        for chunk in re.findall(r'[A-Za-z0-9+/=_-]{16,}', t):
            for key in ('Zy3MDURw',):
                try:
                    cands.append(masterdata.decrypt(chunk.replace('-', '+').replace('_', '/'), key).decode('utf-8', 'replace'))
                except Exception:
                    pass
    by_detail = {str(r.get('TCnm1F4v')): g for g, r in self.rows.items() if r.get('TCnm1F4v')}
    for c in cands:
        m = re.search(r'(\d+)@(\d+)', c)
        if m:
            if m.group(1) in self.rows:
                return m.group(1)
            if m.group(2) in by_detail:
                return by_detail[m.group(2)]
    for c in cands:
        for n in re.findall(r'\d{3,8}', c):
            if n in self.rows:
                return n
    return None


def rates_page(self, gid):
    if not gid or gid not in self.rows:
        body = '<p class="none">This summon\'s details could not be found.</p>'
        title = 'Summon details'
    else:
        r = self.rows[gid]
        title = r.get('4N27mkt1') or 'Summon'
        um = self.S.unit_mst()
        p = self.pool(gid)
        gems, honor = self.price(gid)
        cost = f'{honor:,} Honor Points' if honor and not gems else f'{gems:,} Gem{"s" if gems != 1 else ""}'
        out = [f'<div class="cost">{_esc(cost)} per summon</div>']
        if 'selector' in p:
            groups = [('Choose one', None, p['selector'])]
            out.append('<p class="note">Pick the unit you want from this list.</p>')
        else:
            off = set(self.S.admin_settings()['off']['units'])
            units = {rr: [u for u in v if u not in off] or v for rr, v in p['units'].items()}
            total = sum(p['rates'].values()) or 1
            ch0 = self.chances(gid, off)
            rows = []
            for rr in sorted(p['rates'], key=int, reverse=True):
                pct = p['rates'][rr] * 100.0 / total
                n = len(units.get(rr, []))
                normal = [ch0[u] for u in units.get(rr, []) if u in ch0 and u not in p.get('rateup', {})]
                each = f'{(pct / n if n else 0):.3f}%'
                if len(normal) < n:
                    each = (f'{normal[0]:.3f}%' if normal else '-') + '<br><small>rate up more</small>'
                rows.append(f'<tr><td class="star">{rr}★</td><td>{pct:.2f}%</td><td>{n}</td>'
                            f'<td>{each}</td></tr>')
            out.append('<h2>Rates</h2><table><tr><th>Rarity</th><th>Chance</th><th>Units</th><th>Each unit</th></tr>'
                       + ''.join(rows) + '</table>')
            groups = [(f'{rr}★ units', p['rates'][rr] * 100.0 / total, units.get(rr, []))
                      for rr in sorted(p['rates'], key=int, reverse=True)]
            ch = self.chances(gid, off)
            up = sorted((u for u in p.get('rateup', {}) if u in ch), key=lambda u: -ch[u])
            if up:
                cells = []
                for u in up:
                    m = um.get(u) or {}
                    el, col = ELEMENTS.get(str(m.get('iNy0ZU5M')), ('', '#aaa'))
                    cells.append(f'<div class="u up"><img src="/bf/web/unit_icon/{_esc(u)}.png" alt="" onerror="this.style.visibility=\'hidden\'">'
                                 f'<span class="n">{_esc(m.get("utP1c0CD") or u)}</span>'
                                 f'<span class="e" style="color:{col}">{_esc(el)}</span>'
                                 f'<span class="pc">{ch[u]:.3f}%</span></div>')
                out.insert(1, '<h2 class="ruh">Rate up!</h2><div class="grid">' + ''.join(cells) + '</div>')
        for name, pct, ids in groups:
            ids = sorted(ids, key=lambda u: (str((um.get(u) or {}).get('iNy0ZU5M') or ''), (um.get(u) or {}).get('utP1c0CD') or u))
            cells = []
            for u in ids:
                m = um.get(u) or {}
                el, col = ELEMENTS.get(str(m.get('iNy0ZU5M')), ('', '#aaa'))
                cells.append(f'<div class="u"><img src="/bf/web/unit_icon/{_esc(u)}.png" alt="" onerror="this.style.visibility=\'hidden\'">'
                             f'<span class="n">{_esc(m.get("utP1c0CD") or u)}</span>'
                             f'<span class="e" style="color:{col}">{_esc(el)}</span></div>')
            each = f' &middot; {pct / len(ids):.3f}% each' if pct is not None and ids else ''
            if pct is not None and any(u in p.get('rateup', {}) for u in ids):
                normal = [ch[u] for u in ids if u not in p['rateup'] and u in ch]
                each = f' &middot; {normal[0]:.3f}% each, rate-up units more' if normal else ''
            out.append(f'<h2>{_esc(name)} <small>({len(ids)}{each})</small></h2><div class="grid">{"".join(cells)}</div>')
        body = ''.join(out)
    return f'''<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title><style>
body{{margin:0;background:#16110b;color:#f3e6c8;font:15px/1.4 sans-serif;padding:12px}}
h1{{font-size:20px;margin:4px 0 6px;color:#ffd77a;text-align:center}}
h2{{font-size:16px;margin:18px 0 8px;color:#ffd77a;border-bottom:1px solid #5a4422;padding-bottom:4px}}
h2 small{{color:#c9b48a;font-weight:normal;font-size:13px}}
.cost{{text-align:center;color:#c9b48a;margin-bottom:6px}}
.note,.none{{text-align:center;color:#c9b48a}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
th,td{{padding:6px 4px;border-bottom:1px solid #3a2c16;text-align:center}}
th{{color:#c9b48a;font-weight:normal;font-size:12px}}
td.star{{color:#ffd77a;font-weight:bold}}
.grid{{display:flex;flex-wrap:wrap;gap:6px}}
.ruh{{color:#ff7ad9;border-color:#7a2a63}}
.u.up{{border-color:#ff7ad9;box-shadow:0 0 8px rgba(255,122,217,.35)}}
.pc{{display:block;color:#ff9be4;font-weight:bold;font-size:12px}}
.u{{width:23%;min-width:70px;flex-grow:1;max-width:25%;background:#241a0e;border:1px solid #4a3818;border-radius:8px;padding:4px 2px;text-align:center;box-sizing:border-box}}
.u img{{width:56px;height:56px;display:block;margin:0 auto 2px}}
.u .n{{display:block;font-size:11px;line-height:1.2;min-height:2.4em}}
.u .e{{display:block;font-size:10px}}
</style></head><body><h1>{_esc(title)}</h1>{body}</body></html>'''


Summons.find_summon = find_summon
Summons.rates_page = rates_page
