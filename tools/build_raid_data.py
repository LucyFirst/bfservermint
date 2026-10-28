#!/usr/bin/env python3
"""
Builds the two data files Raid needs (raid.py):

  data/raid_data.json       from the game's own Raid tables (cdn/content/mst, F_RAID_*):
                            missions, mission bosses, bosses, boss parts, map points, harvest
                            drops per point, clear conditions, Raid Class rewards.
  data/raid_templates.json  from the bfrevive Raid recordings (recordings_raw/bfrevive_raid_*):
                            one recorded row per reply group (field names and defaults), the
                            small enemies bfrevive uses on the map, their AI and skills, the
                            recorded bosses' look (for checking) and the helper row layout.
                            Rows are references only: raid.py fills in this server's values.

Usage:  python tools/build_raid_data.py [mst folder]      (default: cdn/content/mst)
"""
import glob
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import masterdata  # noqa: E402

# Raid table -> (file key, encryption key), from the game's code (libgame string table)
RAID_TABLES = {
    'mission': ('f9eHCSc8', '4WbIjfB3'),        # F_RAID_MISSION_MST
    'mission_boss': ('W3Q62S1F', 'yu28TMIo'),   # F_RAID_MISSION_BOSS_MST
    'boss': ('4KAXRjg0', 'a4jIgR2T'),           # F_RAID_BOSS_MST
    'parts': ('w8Phatd4', '6de7rGsP'),          # F_RAID_BOSS_PARTS_MST
    'point': ('79MTkNus', '76rVpcGL'),          # F_RAID_POINT_MST
    'mission_point': ('gVmh3Lw2', 'qga56iME'),  # F_RAID_MISSION_POINT_MST
    'clear': ('Pgnu7d8k', 'E81Hkv3g'),          # F_RAID_MISSION_CLEAR_CND_MST
    'rc': ('i7xd5oMb', 'gui7x0cr'),             # F_RAID_RC_MST (Raid Class rewards)
    'map': ('39mGIANe', 'HMib7V50'),            # F_RAID_MAP_MST
}


def newest(mst_dir, fkey):
    best = None
    for f in glob.glob(os.path.join(mst_dir, f'Ver*_{fkey}*.dat')):
        b = os.path.basename(f)
        v = int(b[3:b.index('_')])
        if best is None or v > best[0]:
            best = (v, [])
        if v == best[0]:
            best[1].append(f)
    if not best:
        raise SystemExit(f'missing table file {fkey} in {mst_dir}')
    return sorted(best[1])


def load(mst_dir, name):
    fkey, ekey = RAID_TABLES[name]
    rows = []
    for f in newest(mst_dir, fkey):
        rows += json.loads(masterdata.decrypt(open(f, 'rb').read(), ekey))
    return rows


def build_data(mst_dir):
    T = {k: load(mst_dir, k) for k in RAID_TABLES}
    missions = {}
    for r in T['mission']:
        missions[r['j28VNcUW']] = {
            'name': r.get('0iAIR2LP', ''), 'cls': int(r.get('7x3pPB2C') or 1), 'map': r.get('H9sx5D08'),
            'time': int(r.get('qY49LBjw') or 1800), 'players': int(r.get('KR6JacN8') or 4),
            'class_up': r.get('UJTR1h5r') == '1', 'zel': int(r.get('Najhr8m6') or 0),
            'karma': int(r.get('HTVh8a65') or 0), 'medal': r.get('b5yeVr61') or '',
            'target': r.get('WDUyI85C', '')}
    for r in T['mission_boss']:
        m = missions.get(r['j28VNcUW'])
        if m is not None:
            m.setdefault('bosses', []).append({'id': r['SP29fLtH'], 'boss': r['tA4T0GXB'], 'point': r.get('i5r7Vjkq'),
                                               'shown': r.get('g9EcMPR6', '1')})
    for r in T['clear']:
        m = missions.get(r['j28VNcUW'])
        if m is not None and r.get('JRqU2bS6') == '1':
            m.setdefault('clear', []).append(r.get('3l52Vq2h', ''))
    bosses = {r['tA4T0GXB']: {'name': r.get('K1sM9FtZ', ''), 'music': r.get('nDf28LtU', '')} for r in T['boss']}
    for r in T['parts']:
        b = bosses.setdefault(r['tA4T0GXB'], {'name': ''})
        b.setdefault('parts', []).append({'id': r['o49dYfpH'], 'main': r.get('7Hf3DnVj') == '1',
                                          'order': int(r.get('2gZIRQ7D') or 0), 'anime': r.get('2EF0d6ue', ''),
                                          'cgg': r.get('QqfI9mM4', ''), 'idle': r.get('0QxL2is7', '')})
    for b in bosses.values():
        b.get('parts', []).sort(key=lambda p: p['order'])
    camps = {}
    for r in T['point']:
        if r.get('AZFM14In') == '1':
            camps.setdefault(r['H9sx5D08'], []).append(r['PV3gH1Yf'])
    drops = {}
    for r in T['mission_point']:
        ds = []
        for part in (r.get('8fjZN6Mx') or '').split(','):
            bits = part.split(':')
            if len(bits) == 3 and bits[1]:
                ds.append([int(bits[0]), bits[1], int(bits[2])])
        drops.setdefault(r['j28VNcUW'], {})[r['PV3gH1Yf']] = {'times': int(r.get('H6k1LIxC') or 0), 'drops': ds}
    rc = {}
    for r in T['rc']:
        rew = []
        for part in (r.get('JQ23rIvk') or '').split(','):
            bits = part.split(':')
            if len(bits) >= 3:
                rew.append([bits[0], bits[1], int(bits[2])])
        rc[r['7x3pPB2C']] = rew
    maps = {r['H9sx5D08']: r.get('0QyCU1dR', '') for r in T['map']}
    return {'missions': missions, 'bosses': bosses, 'camps': camps, 'drops': drops, 'class_rewards': rc,
            'maps': maps}


def build_templates():
    reqs = []
    for f in sorted(glob.glob(os.path.join(HERE, 'recordings_raw', 'bfrevive_raid_*.json.gz'))):
        reqs += json.load(gzip.open(f))['requests']
    one = {}
    mons, ai, skills, boss_rows, helpers, offered = {}, {}, {}, {}, [], None
    for r in reqs:
        p = r.get('resp')
        if not str(r.get('cls', '')).startswith('Raid') or not isinstance(p, dict):
            continue
        for g, v in p.items():
            rows = v if isinstance(v, list) else [v]
            if rows and isinstance(rows[0], dict) and g not in one:
                one[g] = rows[0]
        if r['cls'] == 'RaidMissionBattleStartRequest':
            boss = bool((r['req'].get('2cBk5WH8') or [{}])[0].get('SP29fLtH'))
            for m in p.get('U0v5IeJo') or []:
                (boss_rows if boss else mons).setdefault(m['o49dYfpH'], []).append(m)
            for a in p.get('89ausgc4') or []:
                ai.setdefault(a['4eEVw5hL'], {})[a.get('yu18xScw')] = a
            for s in p.get('8aiBoHg5') or []:
                skills[s['nj9Lw7mV']] = s
        if r['cls'] == 'RaidRoomFriendGetRequest':
            helpers = p.get('ft4zY6Uv') or helpers
        if r['cls'] in ('RaidUserInfoRequest', 'RaidWorloInfoRequest', 'RaidExitRequest') and \
                len(p.get('2WqoMm4d') or []) > len(offered or []):
            offered = [x['j28VNcUW'] for x in p['2WqoMm4d']]
    # the small enemies: every recorded copy (bfrevive gives each battle slightly different numbers)
    return {'rows': one, 'monsters': mons, 'ai': {k: [v[x] for x in sorted(v)] for k, v in ai.items()},
            'skills': skills, 'bosses_seen': {k: v[0] for k, v in boss_rows.items()},
            'helper_row': helpers[0] if helpers else None, 'offered': offered or []}


def main():
    mst_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, 'cdn', 'content', 'mst')
    data = build_data(mst_dir)
    tm = build_templates()
    data['offered'] = [m for m in tm.pop('offered') if m in data['missions']]
    out = os.path.join(HERE, 'data', 'raid_data.json')
    json.dump(data, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, sort_keys=True)
    out2 = os.path.join(HERE, 'data', 'raid_templates.json')
    json.dump(tm, open(out2, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, sort_keys=True)
    print(f"{len(data['missions'])} raid quests ({len(data['offered'])} offered), {len(data['bosses'])} bosses"
          f" -> {out}")
    print(f"{len(tm['rows'])} reply rows, {len(tm['monsters'])} map enemies, {len(tm['ai'])} AI, "
          f"{len(tm['skills'])} skills -> {out2}")


if __name__ == '__main__':
    main()
