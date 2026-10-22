#!/usr/bin/env python3
"""
Copy game files you already have from the emulator (the flat 'files' folder
inside sg.gumi.bravefrontier) into this server's cdn\\ folder, each in the
folder the game expects. Anything not placed here is left for
download_content.py / on-demand fetching.

Usage:  python import_device_files.py [path\\to\\sg.gumi.bravefrontier\\files]
        (default: ..\\sg.gumi.bravefrontier\\files)
"""
import json, os, re, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'cdn')
SIZE_SUFFIX = re.compile(r'_\d+x\d+(?:_\d+)?$')

def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, '..', 'sg.gumi.bravefrontier', 'files')
    if not os.path.isdir(src):
        sys.exit(f'Folder not found: {src}')
    res = json.load(open(os.path.join(HERE, 'data', 'resource_paths.json')))
    by_name = {}
    for p in res:
        by_name.setdefault(p.rsplit('/', 1)[-1], []).append('/content' + p)

    def targets(name):
        if name in by_name:
            return by_name[name]
        low, (stem, ext) = name.lower(), os.path.splitext(name)
        if low.startswith('unit_'):
            sub = 'img' if ext == '.png' else ('cgg' if 'cgg' in low else 'cgs')
            return [f'/content/unit/{sub}/{name}']
        if ext == '.sam':
            return [f'/content/sam/{stem}/{name}']
        if ext in ('.png', '.jpg') and SIZE_SUFFIX.search(stem):
            return [f'/content/sam/{SIZE_SUFFIX.sub("", stem)}/{name}']
        if ext == '.mp3':
            return [f'/content/sound/{name}']
        return []

    copied = skipped = unknown = 0
    unplaced = []
    for name in sorted(os.listdir(src)):
        fp = os.path.join(src, name)
        if not os.path.isfile(fp):
            continue
        if name.endswith('.dat'):
            tl = [f'/content/mst/{name}']
        else:
            tl = targets(name)
        if not tl:
            unknown += 1; unplaced.append(name); continue
        for t in tl:
            dst = os.path.join(OUT, os.path.normpath(t.lstrip('/')))
            if os.path.exists(dst):
                skipped += 1; continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(fp, dst); copied += 1
        if (copied + skipped) % 1000 == 0 and copied:
            print(copied, 'copied...', flush=True)
    open(os.path.join(HERE, 'import_unplaced.txt'), 'w').write('\n'.join(unplaced))
    print(f'done: {copied} files copied, {skipped} already there, {unknown} not placed '
          f'(listed in import_unplaced.txt; the server will fetch those if needed)')

if __name__ == '__main__':
    main()
