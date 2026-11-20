"""
Print the game's skill table rows (F_SKILL_MST: every process of a Brave Burst with its frames) for skill ids.
    cd /opt/bfserver && python3 tools/skill_row.py 40025 60025 160025
The table's file and key are found by trying the game's 8-letter table keys on the downloaded files.
"""
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)
import masterdata  # noqa: E402

KEYS = ['hasMovie', 'a4M4dh1i', 'kIj2n6s5', 'y6bU2dqW', 'Jki71Bm9', 'M3vCu81Q', 'K3b981cX', 'hR47gBvi', 'O3nEmUs2', 'y38bDaMv', 'D3Kiq93b', 'M83aDbK2', 'D3Bda18b', '39s0l4sj', '4sTslk4s', '390l4skd', 'Kdnxl203', 'dk4S04ls', '94Slf02l', 'gyY4TCm8', 'P8t7ZXS8', 'Sf8jpQ0V', 'nGPo2mp5', 'aIG5sQwe', 'VymTrQvy', 'L5t2Wfrq', 'SbZW6TMJ', 'ru80uthU', '1ozLjap7', 'Jba1QEOD', 'khrEOkEv', 'IwODP3o4', 's4tsRd7H', 'n3th3poV', 'ni16xAsw', 'LaD2pF1e', 'jHsf3JDz', 'pRo1Azab', 'J2b3drej', 'VR6mJD3M', '2W4Ja7KU', 'G7CqU3EQ', '5T6cZ7LD', 'qJBsPw38', 'zGcIk58e', 'XkBhe70R', 'KY59Mwhq', 'vj6fEsg7', '73bUW8wn', '6TICDW7R', 'mbK4Qg5w', '7DAkY1rQ', 'pS2HMv8e', 'FhzWn1m9', 'a9B3nDuA', '8GhcfX4Q', '6q9ID0wP', '1nte4gCY', 'gm4oR9D5', 'zBxwtg89', 'rvIa85RA', 'w3Yv7uSF', '13s7RxgT', '31BhsATV', '6Zwi24yp', 'vw0WR4rt', '4b5HGKSs', 'G8pKiV5q', '5Wdw19KR', 'dFgVx91b', 'vAkyg17f', 'LTpPwF82', '93xUK0iZ', 'L76ovBpj', 'J3TBnt9Z', 'P6QeyAS5', '4FEhdoA8', 'n6HkMa2E', '2Im7tLhs', '5fueF6wx', 'HNdG95VT', 'B57MJYoF', 'w5W8RPSG', 'D2FdZK5v', '4keDxjn7', '0VIAZHT9', 'BQJq29Zh', '3FWEGtX9', '9ojr0tPC', 'kP4pTJ7n', 'PDGK5sk3', '2h9r3yEY', 'w3Z9EeUR', 'U1XChi09', '9zJFtHr3', 'V9zLvR7n', 'gCXM17Do', '1qM7vU8G', 'KT9yF4hW', 'rvu7si38', 'yDI6H4iP', 'SDYeM57E', '0X7AZGWY', 'W52ZC4UH', '1PHLaV7x', 'c6Y7pDSg', 'ds9ku86H', 'uU0B82dP', '5YH0Z2vg', 'oytz0pV3', '6vGMbam8', 'fnF63wQP', 'j3Cfv4wq', 'vxH5tn1i', 'cygP1CT2', 'MZGk4Tx5', 'Z9DX1Vr4', '6Sv01F4z', 'SA9y7qu6', '5IRuFdg7', 'Io3rU5Gj', 'QfxUi78N', '48EZ6hoH', 'YG6kUpH9', '7PVuS4c3', '8zheJ5vL', 'pr12tHoL', 'ek2v17VF', 'Mjvnb1m6', 'NrgSKj90', '1kIne6HZ', 'B9QqhU04', '4ecN8PfH', 'DkW45yc7', 'Da8eP0LE', '9s63tcDS', 'gU7Fe3pa', 'AMU5du76', 'LG9A2zrV', 'FA69am2p', 'tLaGZ8h3', 'J78zLqGW', 'Z0wSMY9p', 'i9LwmI78', 'FQe7WJ3N', 'WIf3Ag8C', '6CpMF8kb', 'P2q3wIXz', 't4cxTG8A', 'tqA2Yj4k', 'zLo93XBY', 'Nt8xg5eG', 'W37QmhSb', 'hL86KWoR', '82sgSkVw', 'GJc6VM4k', '9SHj5sZ6', 'Yg4n5c7G', 'n0DUZr86', 'I5jMpA62', 'VAti08bF', 'YdE56NQq', 'L60J1vWU', 'fSsA7Rg5', 'tdk6VX4P', '78ganRXv', 'rLk8V3sj', 'v4rwhV5p', '1fPrRAs3', 'r6dVAv7g', 'q45JQWax', 'gy91IuGm', 'wBumj9e7', 'EwQ0S5cX', '8HG9C1nw', '2GXuZeC4', 'Ap3m7Z8z', '9Du1jefh', 'f2CGjz3B', 'JQfDKp12', 'BAu8d7F3', '83JWTCGy', 'houmK14R', '1M65tIPz', 'drCPNM10', '4dE8UKcw', 'HGYZ1Ag5', '7Hvw8MjR', '7Q0xtYai', 'DX7KZ0Tf', 'UH1ruJ36', 'Fz0wQE7k', '9sNASF7L', 'oQ46p2nF', 'BZhUb52W', '4JE67mXt', 'qjsI9d0x', '5aueM1ix', 'HF7B1hat', 'SBcq4h6J', 'nm1SBZ9g', 'b7PN0hsK', 'M7emSXn1', 'v9W2qg7T', '6i4Av1tW', 'XtSrv61M', 'uHsAU32G', 'Ygh8Id2X', '30gISzhf', 'fnBdH1U4', 'Lj3RUx1n', 'Pe43fToq', 'V7w2fFys', 'c6o5axqu', 'Dg8Fbh2q', 'Gie01w2u', 'G8hbp4sT', 'SMPHEx74', 'Qqp3bo6v', 'W3sQD9Z5', 'L5DNP2H9', '1eJFgSw7', 'E0Cvyn7s', '4gA3WCQX', 'y1hZzE74', 'zVo1bRp9', 's7iC3PT0', 'F2Dz3QHU', 'U91CxXGi', '6hrbyGk1', '4toup8DY', 'b1eBIjZ0', '4qzZk5yo', '8G2oi6Q9', '07C3NBe8', 'w01Rv8oC', 'L2ImUj6y', 'j8ahk0XI', '2UigwF0A', 'oZv16I5C', 'r3Az5CEn', '9SyMp4kF', 'd09qZTQB', 'K7whs0vk', '0MrW7RQF', 'WDuRFG12', 'Hd0o42FW', 'oz8eEY3W', '8AeT7pVj', 'sR2A38UZ', 'TS7kxf8V', 'iDZs3U0L', 'zC72U9MZ', 'Td0YwbL8', '7u56cXTC', 'Jwm2rG7z', 'z60fkyIR', 'WY35sbVL', 'FUI6rv7h', '8Nn0mde6', 'm9wv4dsi', 'Y70NXZUz', 'zafH9rL1', '1uSXYt5o', '3A2d9nBL', 'twao5YK9', '1PJ59Mhf', 'Zxb8TPG7', 'YUG3V8vD', 'o67FTH9L', '52o9IuwY', '2bNh4JTB', 'CmZ98McF', 'Q38dBveW', 'isX0tI2D', 'we5dRx18', 'j9Cb3mQP', 'rmINZ0V2', '9BWHPDj6', 'f2zR6vU8', 'N40Qek5z', 'wS3u0ryP', 'Ivy49YHq', 'z7Ywj9rx', 'nmA2D6Ey', 'Kx8z9RyS', 'G8fF1UNE', 'ZzP7MS4g', 'PoGc18Mk', 'q25fApus', 'gN1u7ZeG', 'D7hadU1p', 'L6gfBpn3', 'fYJjG4B6', 'c0Ejwh7S', '4KAXRjg0', 'a4jIgR2T', 'w8Phatd4', '6de7rGsP', 'z7UiGPQ1', 'L4pBaT0q', 'I9TkKu6N', '1PzmrHL9', '39mGIANe', 'HMib7V50', 'W3Q62S1F', 'yu28TMIo', 'Pgnu7d8k', 'E81Hkv3g', 'f9eHCSc8', '4WbIjfB3', 'gVmh3Lw2', 'qga56iME', 'xMFHp36r', '7z5RKwhU', '79MTkNus', '76rVpcGL', 'i7xd5oMb', 'gui7x0cr', 'B30v8k1P', 'x48SzgpH', '2sjB0RvV', 'Ny6rB7HV', '35fQnyPs', '8ozA4wZv', '3TM68IaF', 'na7Wb8u1', 'kpN3M6tK', '1U9ouXzP', 'hdt2N6mr', 'z5dp9GX8', '8Uk4nHfC', 'V6Yf4g1X', 'V5N34aUf', 'm2iqC6Ka', 'Sb3MW61V', 'M4eAVZs1', 'wkCyV73D', 'JA03wvHG', 'zLIvD5o2', 'rF97pVdT', '59CzkeG2', 'UF51Te63', 'W2j4E3te', 'NFJ74Wmg', 'KuCe7t3p', 'em68GxrB', 'Mkum4tB6', 'He71kZYp', 'W3NMn25Z', '7vtcn58P', 'Xep8qk4D', '9p3b8NCg', 'VbFp8XZ6', '2TuAmfX4', 'iTMk51hB', 'e5t01snA', 'qP6nD0IL', 'T8wGcKd1', 'XjL3fKG0', '89CxLmhs', 'k6H83Uix', 'WqvJ64XG', 'kT3vA9j2', 'LaM0o9P7', 'Ydra41vX', '7h5fuTma', 'fNVP2i89', 'aZ97rNyw', 'b7fn2L05', 'Np2gx5qI', '0bxTF82d', 'y7Qvrd3J', '18CDzh7v', 'e5SEqU4m', 'n5cWAf7H', 'v8Ln7NBE', 'F9fGSe42', 'Hxidv1y0', '72jYgkoa', '7f4JNbwr', '7Q2iopWx', 'X74u8mIS', 'oeUQ8q7D', 'fRFY13ie', '6rjmY7tV', 'Y7ezNyn2', '2npmC9Nw', 'Apc5eIw6', '9ie4KnDt', 'GIo0uK8b', 'C0pU8ev4', 'Eqx4BrW2', 'Ef6oG0mz', 'R9IryC5v', 'Wwn4Epj6', 'Iz7wM0j9', 'kI94EypD', '2yCoEm0q', 'nd18wpsy', 'XZvyRf62', '8gu2U4Mh', 'dc0jxn8u', '2r9cNSdt', '7nL1WTUb', 'J4heGZ2U', '4a6GF3Pt', '7AdvRect', 'cTZ3W2JG']
want = set(sys.argv[1:])
files = sorted(glob.glob(os.path.join(HERE, 'cdn', '**', 'Ver*_*.dat'), recursive=True))
tables = {}
for f in files:
    m = re.match(r'Ver(\d+)_([A-Za-z0-9]+?)(?:_(\d+))?\.dat$', os.path.basename(f))
    if m:
        tables.setdefault((m.group(1), m.group(2)), []).append((int(m.group(3) or 1), f))
found = 0
for (ver, fk), parts in tables.items():
    if fk not in KEYS:
        continue
    i = KEYS.index(fk)
    for key in KEYS[max(0, i - 1):i + 2]:
        try:
            rows = []
            for _, f in sorted(parts):
                d = json.loads(masterdata.decrypt(open(f, 'rb').read(), key))
                rows += d if isinstance(d, list) else next((v for v in d.values() if isinstance(v, list)), [])
        except Exception:
            continue
        hits = [r for r in rows if isinstance(r, dict) and str(r.get('nj9Lw7mV')) in want]
        if hits:
            found += 1
            print('TABLE', ver, fk, key, len(rows), 'rows')
            for r in hits:
                print(json.dumps(r, ensure_ascii=False))
        break
if not found:
    print('no table with those skill ids;', len(tables), 'tables found')
