"""
App updates. Put a new build in the "app" folder next to server.py, named
BF-Private-<version>.apk (e.g. BF-Private-1.0.4.apk). The highest version there is the latest.

From 1.0.4 on, the app asks GET /app/check?v=<its version> when it starts. If a newer build is
in the folder, the answer is two lines - the download address and the message - and the app shows
"Download" / "Later". Download opens http://<server>/app/<file>; the phone downloads the APK and the
player installs it over the old one (same signature, so accounts and data stay). Empty answer = up
to date. Older files in the folder are kept (the newest one is offered).
"""
import os
import re

PAT = re.compile(r'^BF-Private-(\d+(?:\.\d+)*)\.apk$', re.I)
MESSAGE = ('Version {new} of the game is ready (you have {old}).\n\n'
           'Tap Download, then open the downloaded file to install it over this one. '
           'Your account and progress stay.')


def vtuple(v):
    try:
        return tuple(int(x) for x in str(v).strip().split('.'))
    except ValueError:
        return (0,)


class AppUpdate:
    def __init__(self, srv):
        self.S = srv
        self.dir = os.path.join(srv.HERE, 'app')
        os.makedirs(self.dir, exist_ok=True)

    def latest(self):
        best = None
        for name in os.listdir(self.dir):
            m = PAT.match(name)
            if m and (best is None or vtuple(m.group(1)) > vtuple(best[0])):
                best = (m.group(1), name)
        return best

    def check(self, have, host):
        best = self.latest()
        if not best or vtuple(best[0]) <= vtuple(have or '0'):
            return ''
        url = f"http://{host}/app/{best[1]}"
        self.S.log.info('app update offered: %s -> %s', have or '?', best[0])
        return url + '\n' + MESSAGE.format(new=best[0], old=have or 'an older version')

    def file(self, name):
        if not PAT.match(name or ''):
            return None
        fp = os.path.join(self.dir, name)
        return fp if os.path.isfile(fp) else None
