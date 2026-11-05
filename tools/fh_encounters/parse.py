import json, re, sys
W = json.load(open('/tmp/claude-0/fhwiki/seasons.json'))
TPL = re.compile(r'\{\{\s*(?:Template:)?(UnitImage|UnitInfo|Unit)\s*\|([^}]*)\}\}', re.I)
NOTE = re.compile(r"^\s*(''')?\s*Notes?\b", re.I)
HDR = re.compile(r'^\|?!.*$')


def cell_units(cell):
    out = []
    for m in TPL.finditer(cell):
        kind, args = m.group(1), m.group(2)
        a = [x.strip() for x in args.split('|')]
        tail = cell[m.end():m.end() + 6]
        k = re.match(r'\s*[x×]\s*(\d)', tail)
        k = int(k.group(1)) if k else 1
        if kind.lower() == 'unitimage':
            v = next((x for x in a if re.fullmatch(r'\d{4,6}', x)), None)
            item = ('id', v) if v else ('name', next((x for x in a if x and x.lower() != 'thumbnail'), ''))
        else:
            nm = next((x for x in a if x and x.lower() != 'thumbnail'), '')
            item = ('id', nm) if re.fullmatch(r'\d{4,6}', nm) else ('name', nm)
        out += [item] * k
    rest = TPL.sub('', cell)
    for m in re.finditer(r'(?i)unit[ _]ills[ _]anime[ _](\d{4,6})', rest):
        out.append(('id', m.group(1)))
    for m in re.finditer(r'(?i)monster[ _]ills[ _]anime[ _](\d{4})', rest):
        out.append(('anime', m.group(1)))
    rest = re.sub(r'\[\[(?:File|Image):[^\]]*\]\]', '', rest)
    for m in re.finditer(r'\[\[(?!File:|Image:)([^\]|]+)(?:\|[^\]]*)?\]\](?:\s*[x×]\s*(\d))?', rest):
        out += [('name', m.group(1).strip())] * int(m.group(2) or 1)
    return out


def parse(text):
    secs = re.split(r'^==\s*([^=].*?)\s*==\s*$', text, flags=re.M)
    res = []
    for i in range(1, len(secs), 2):
        title, body = secs[i], secs[i + 1]
        if 'Round' not in body:
            continue
        rounds, cur = [], None
        for row in re.split(r'\n\|-[^\n]*', body):
            if re.search(r'^!\s*scope', row, re.M):
                continue
            if re.search(r'^\|?!.*Round\s*\d+', row, re.M):
                cur = []
                rounds.append(cur)
            keep = []
            for l in re.split(r'\n|<br\s*/?>', row):
                if NOTE.match(l) or l.startswith('*') or l.startswith('|}'):
                    break
                if HDR.match(l):
                    continue
                keep.append(l)
            g = cell_units('\n'.join(keep))
            if g and cur is not None:
                cur.append(g)
        res.append((title, rounds))
    return res


if __name__ == '__main__':
    t = parse(W[sys.argv[1] if len(sys.argv) > 1 else 'FH:Season14'])
    for title, rounds in t:
        print(title, [len(r) for r in rounds])
