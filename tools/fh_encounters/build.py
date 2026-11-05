import sys, json, re
from collections import Counter, defaultdict
sys.path.insert(0, '/tmp/claude-0/fhwiki')
sys.path.insert(0, '/tmp/claude-0/fhsim/srv')
import parse
import server as S
U = S.unit_mst()
bg = S.battle_generator()
byname = defaultdict(list)
for uid, r in U.items():
    byname[str(r.get('utP1c0CD', '')).strip().lower()].append(uid)
def norm(s): return re.sub(r'[^a-z0-9]', '', s.lower())
bynorm = defaultdict(list)
for k, v in byname.items(): bynorm[norm(k)] += v
recnames = {norm(k): k for k in bg.by_name}
fh = S.fh_engine()
unres = Counter(); out = {}; problems = []
ALIAS = {'Winged Mimic': 'Bat Mimic', 'GS-200': 'GS-220', 'Judgement Demon Zalvard': 'Judgment Demon Zalvard'}
KEYS = ['entrance', 'middle', 'end', 'terminus']
for s in fh.order:
    si = int(s)
    if si < 2: continue
    page = parse.W.get(f'FH:Season{s}')
    ms = fh.missions.get(s, [])
    if not page:
        problems.append(f'season {s}: no wiki page'); continue
    secs = parse.parse(page)
    used = set()
    for m in ms:
        mid = m['j28VNcUW']
        name = S.MISSIONS.get(mid, {}).get('name', '')
        idx = ms.index(m)
        # match: same keyword; else same position
        kw = next((k for k in KEYS if k in name.lower()), None)
        jp = {'序地': 'entrance', '中部': 'middle', '奥地': 'end', '極地': 'terminus'}
        kw = kw or next((v for k, v in jp.items() if k in name), None)
        cand = [i for i, (t, _) in enumerate(secs) if i not in used and kw and kw in t.lower().replace('ending', '')]
        if kw == 'end':
            cand = [i for i in cand if 'end' in secs[i][0].lower().split()[-2:] or re.search(r'\bend\b', secs[i][0].lower())]
        if not cand:
            cand = [i for i, (t, _) in enumerate(secs) if i not in used and not any(k in t.lower() for k in KEYS)]
        if not cand:
            problems.append(f'season {s}: no wiki table for {name} ({mid})'); continue
        i = cand[0]; used.add(i)
        title, rounds = secs[i]
        rr = []
        for r, groups in enumerate(rounds):
            gg = []
            for g in groups:
                ids = []
                for kind, v in g:
                    if kind == 'anime':
                        hit = next((nm for nm, r in bg.by_name.items() if (r.get('row') or {}).get('2EF0d6ue') == f'monster_anime_{v}.png'), None)
                        if hit: ids.append({'name': hit}); continue
                        nm = {'0051': 'calamity demon amu yunos', '0052': 'heresy demon kalon', '0053': 'frontier demon'}.get(v)
                        if nm in bg.by_name: ids.append({'name': nm}); continue
                        unres[('anime', v)] += 1; continue
                    if kind == 'id':
                        v = {'20272': '20172', '30272': '30172', '40272': '40172'}.get(v, v)   # wiki typo (Season 6 End, round 2: Ogre group)
                        if v in U: ids.append(v); continue
                        unres[('id', v)] += 1; continue
                    if v.lower() in ('element', 'curse', ''): continue
                    v = ALIAS.get(v, v)
                    rm = re.match(r'(.*?)\s*\((\d)\s*★\)\s*$', v)
                    if rm and norm(rm.group(1)) in bynorm:
                        c = [x for x in bynorm[norm(rm.group(1))] if U[x].get('7ofj5xa1') == rm.group(2)]
                        if c:
                            ids.append(sorted(c, key=int)[0]); continue
                    n = norm(v)
                    if n in bynorm:
                        ids.append(sorted(bynorm[n], key=int)[0])
                    elif n in recnames:
                        ids.append({'name': recnames[n]})
                    else:
                        unres[v] += 1
                if ids: gg.append(ids)
            if gg: rr.append(gg)
            else: problems.append(f'season {s}: {title} round {r+1} has no enemies')
        out[mid] = {'season': s, 'title': title, 'rounds': rr}
    for i, (t, _) in enumerate(secs):
        if i not in used: problems.append(f'season {s}: wiki table {t!r} not matched to a survey')
# surveys the wiki has no table for (seasons up to 78): an in-between of the same survey the
# season before and after - each round's groups from both
KINDS = ['entrance', 'middle', 'end', 'terminus']
def kind_of(mid):
    nm = S.MISSIONS.get(mid, {}).get('name', '')
    jp = {'序地': 'entrance', '中部': 'middle', '奥地': 'end', '極地': 'terminus'}
    return next((k for k in KINDS if re.search(r'\b%s\b' % k, nm.lower())), None) or next((v for k, v in jp.items() if k in nm), None)
have = {mid: v for mid, v in out.items() if v['rounds'] and all(v['rounds'])}
by_kind = defaultdict(dict)
for mid, v in have.items():
    by_kind[kind_of(mid)][int(v['season'])] = mid
for s in fh.order:
    si = int(s)
    if si < 2 or si > 78: continue
    for m in fh.missions.get(s, []):
        mid = m['j28VNcUW']
        if mid in have: continue
        k = kind_of(mid); seasons = sorted(by_kind[k])
        prev = max([x for x in seasons if x < si], default=None); nxt = min([x for x in seasons if x > si], default=None)
        a = have[by_kind[k][prev]]['rounds'] if prev else None
        b2 = have[by_kind[k][nxt]]['rounds'] if nxt else None
        base = a or b2
        rounds = []
        for r in range(len(base)):
            g = []
            for src in (a, b2):
                if src and r < len(src):
                    for grp in src[r]:
                        if grp not in g: g.append(grp)
            rounds.append(g)
        out[mid] = {'season': s, 'title': f'{S.MISSIONS.get(mid, {}).get("name", mid)} (made from seasons {prev} and {nxt})', 'rounds': rounds, 'made': True}
        print('made', s, k, mid, 'from', prev, nxt, [len(x) for x in rounds])
json.dump(out, open('/tmp/claude-0/fhwiki/fh_encounters.json', 'w'), ensure_ascii=False, indent=0)
print(len(out), 'surveys')
print('\n'.join(problems))
print('UNRESOLVED', len(unres)); print(unres.most_common(200))
