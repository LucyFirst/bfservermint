"""
Builds data/wiki_item_drops.json - materials a quest should drop (the wiki's item pages, "How to Obtain")
that the quest's generated chests never hold.

  1. reads every material's page from the Brave Frontier wiki (bravefrontierglobal.fandom.com)
  2. finds the quests each page names (quest names, or a dungeon with no quest under it = all its quests);
     Raid, Colosseum, bazaar, guild, harvesting, login and other non-quest sources are skipped
  3. plays each of those quests RUNS times through the battle generator (without this file's additions)
     and keeps the (quest, material) pairs that never turned up in a chest
  4. writes them as [item, chance per run, 1]: a quest named on the page gets NAMED, a whole dungeon WIDE,
     at most MAX_PER_RUN extra materials per run on average for any one quest

Run on a copy with config.json (it imports server.py):  python3 tools/build_wiki_item_drops.py
"""
import collections
import json
import os
import random
import re
import subprocess
import sys
import urllib.parse

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
API = 'https://bravefrontierglobal.fandom.com/api.php'
RUNS = 300
NAMED, WIDE, MAX_PER_RUN = 0.25, 0.10, 1.0
SKIP = re.compile(r'raid battle|colosseum|bazaar|guild|harvest|exchange|arena|login|reward|summon|frontier gate|'
                  r'grand quest|trial|tournament|shop|chest|stage \d|giveaway|slots', re.I)


def data(name):
    return json.load(open(os.path.join(HERE, 'data', name), encoding='utf-8'))


def fetch(names):
    out = {}
    for i in range(0, len(names), 50):
        url = API + '?' + urllib.parse.urlencode({'action': 'query', 'prop': 'revisions', 'rvprop': 'content',
                                                  'rvslots': 'main', 'titles': '|'.join(names[i:i + 50]),
                                                  'redirects': 1, 'format': 'json'})
        q = json.loads(subprocess.check_output(['curl', '-s', '-m', '60', url]))['query']
        back = {r['to']: r['from'] for r in q.get('redirects', [])}
        norm = {x['to']: x['from'] for x in q.get('normalized', [])}
        for p in q['pages'].values():
            t = back.get(p['title'], p['title'])
            t = norm.get(t, t)
            if 'revisions' in p:
                txt = p['revisions'][0]['slots']['main']['*']
                m = re.search(r'\|howToObtain\s*=(.*?)\n\|[a-zA-Z]+\s*=', txt, re.S)
                out[t] = m.group(1).strip() if m else ''
    return out


def norm(s):
    s = s.lower().replace('’', "'").replace('‘', "'").replace('`', "'")
    s = re.sub(r'\[\[(?:[^\]|]*\|)?([^\]]*)\]\]', r'\1', s)
    s = re.sub(r'\[http\S*\s([^\]]*)\]', r'\1', s)
    s = re.sub(r'\blv\.?\s*(\d+)', r'lv. \1', s)
    s = re.sub(r"[^a-z0-9.' ]+", ' ', s)
    return ' ' + re.sub(r'\s+', ' ', s).strip() + ' '


def match(pages, M, DN):
    byname = collections.defaultdict(list)
    for mid, m in M.items():
        n = norm(m.get('name') or '')
        if len(re.sub(r'lv\. \d+|[^a-z]', '', n)) >= 4:
            byname[n].append(mid)
    order = sorted(byname, key=len, reverse=True)
    dnorm = {k: norm(v) for k, v in DN.items() if v != k}
    by_dun = collections.defaultdict(list)
    for mid, m in M.items():
        by_dun[str(m.get('dungeon'))].append(mid)
    named, wide_lines = collections.defaultdict(set), []
    for item, txt in pages.items():
        stack = []
        for line in (txt or '').split('\n'):
            m = re.match(r'^(\*+)(.*)', line.strip())
            if not m:
                continue
            stack = stack[:len(m.group(1)) - 1] + [m.group(2)]
            ctx = ' '.join(stack)
            if SKIP.search(ctx):
                continue
            nl, nc, rest, found = norm(m.group(2)), norm(ctx), norm(m.group(2)), []
            for n in order:
                if n in rest:
                    found.append(n)
                    rest = rest.replace(n, ' ')
            for n in found:
                mids = byname[n]
                if len(mids) > 1:      # same name twice: the one in the dungeon named here, else a named dungeon
                    f = [x for x in mids if dnorm.get(str(M[x].get('dungeon')), '#') in nc] or \
                        [x for x in mids if str(M[x].get('dungeon')) in dnorm and str(M[x].get('dungeon')) != '1000000']
                    mids = f or mids
                named[item].update(mids)
            if not found:
                wide_lines.append((item, [d for d, dn in dnorm.items() if len(dn) > 6 and dn in nl]))
    wide = collections.defaultdict(set)
    for item, duns in wide_lines:
        for d in duns:
            if not any(str(M[x].get('dungeon')) == d for x in named[item]):
                wide[item].update(by_dun[d])
    return named, wide


def main():
    names = data('item_names.json')
    item_id = {v[0]: k for k, v in names.items() if v[1] == 'material'}
    pages = fetch(sorted(item_id))
    print(len(pages), 'material pages read')
    M, DN = data('mission_mst.json'), data('dungeon_names.json')
    named, wide = match({k: v for k, v in pages.items() if k in item_id}, M, DN)
    os.chdir(HERE)
    import battlegen
    battlegen.WIKI_ITEM_DROPS.clear()          # what the quests drop without this file
    import server
    gen = server.battle_generator()
    quests = sorted({q for d in (named, wide) for v in d.values() for q in v})
    seen = {}
    for q in quests:
        s = set()
        try:
            for i in range(RUNS):
                for b in gen.plan(q, random.Random(i))['battles']:
                    s.update(re.findall(r'^\d+/1/4:\d+:(\d+):', c)[0] for c in b['chests']
                             if re.match(r'^\d+/1/4:\d+:\d+:', c))
        except Exception as e:
            print('  could not play', q, e)
            s = None
        seen[q] = s
    out = collections.defaultdict(dict)
    for d, pct in ((wide, WIDE), (named, NAMED)):      # named after wide: a named quest gets the higher chance
        for item, qs in d.items():
            for q in qs:
                if seen.get(q) is not None and item_id[item] not in seen[q]:
                    out[q][item_id[item]] = pct
    res = {'_how': 'built by tools/build_wiki_item_drops.py from the wiki item pages; used by battlegen.py'}
    for q in sorted(out, key=int):
        total = sum(out[q].values())
        k = min(1.0, MAX_PER_RUN / total) if total else 1.0
        res[q] = [[i, round(p * k, 3), 1] for i, p in sorted(out[q].items())]
    path = os.path.join(HERE, 'data', 'wiki_item_drops.json')
    json.dump(res, open(path, 'w', encoding='utf-8'), indent=0)
    print(len(res) - 1, 'quests,', sum(len(v) for k, v in res.items() if k != '_how'), 'materials added ->', path)


if __name__ == '__main__':
    main()
