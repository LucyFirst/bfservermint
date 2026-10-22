"""
News page: what is new or upcoming, written on the admin page (News tab).

Three buttons in the game open it:
  - Home > Info and Menu > News: the game opens the notice address the server sends
    (Pj6zDW3m NoticeInfo, field jsRoN50z). That is a data: address holding the whole page, so it
    needs nothing else from the server (the game adds text after the address - the page ends with
    an open HTML comment so that text is never shown, like bfrevive's page).
  - Title screen > News: the game opens <server>/bf/web/notice/indexCtrl.php - served here.
The banner picture travels inside the page (made small by the admin page before upload).

saves/news.json: {"banner": "data:image/jpeg;base64,..." or "", "posts": [{"id", "title", "date",
"kind": "new" | "upcoming", "body"}], "rev": n}
"""
import hashlib
import html
import json
import os
import re
import threading
import time
from urllib.parse import quote

MAX_BANNER = 600_000            # bytes of the banner's data address
KINDS = {'new': "What's new", 'upcoming': 'Coming soon'}

CSS = ('html{background:#0b1114}body{margin:0;padding:0 0 28px;color:#e8e2d0;'
       'font:15px/1.55 -apple-system,Roboto,"Segoe UI",sans-serif;-webkit-text-size-adjust:100%}'
       '.banner{display:block;width:100%;height:auto;margin:0 0 6px}'
       '.wrap{padding:12px 16px 0}'
       'article{margin:0 0 24px}article+article{border-top:1px solid #2e3b40;padding-top:18px}'
       'h2{margin:0 0 2px;font-size:19px;line-height:1.3;color:#f5cf6b;font-weight:700}'
       '.meta{margin:0 0 12px;font-size:12px;color:#9aa7ab}'
       '.tag{display:inline-block;font-size:11px;font-weight:700;padding:1px 7px;border-radius:9px;'
       'margin-right:6px;vertical-align:1px}.tag.new{background:#1d3a2a;color:#7ee2a0}'
       '.tag.upcoming{background:#3a2f12;color:#ffc966}'
       'p{margin:0 0 12px}ul{margin:0 0 12px;padding-left:20px}li{margin:0 0 6px}'
       'strong{color:#fff}.empty{color:#9aa7ab;text-align:center;margin-top:40px}')


class News:
    def __init__(self, srv):
        self.S = srv
        self.file = os.path.join(srv.SAVE_DIR, 'news.json')
        self.mu = threading.RLock()
        self._cache = None

    # ------------------------------------------------------------------ state
    def load(self):
        try:
            d = json.load(open(self.file, encoding='utf-8'))
        except Exception:
            d = {}
        d.setdefault('banner', '')
        d.setdefault('posts', [])
        d.setdefault('rev', 0)
        return d

    def save(self, d):
        with self.mu:
            d['rev'] = int(d.get('rev') or 0) + 1
            tmp = self.file + '.tmp'
            json.dump(d, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            os.replace(tmp, self.file)
            self._cache = None

    # ------------------------------------------------------------------ page
    @staticmethod
    def _inline(t):
        t = html.escape(t)
        return re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', t)

    def body_html(self, text):
        """Plain text from the admin page: blank line = new paragraph, lines starting with "- " or
        "* " = a bullet list, **word** = bold."""
        out, para, items = [], [], []

        def flush():
            if para:
                out.append('<p>' + '<br>'.join(self._inline(x) for x in para) + '</p>')
                para.clear()
            if items:
                out.append('<ul>' + ''.join(f'<li>{self._inline(x)}</li>' for x in items) + '</ul>')
                items.clear()
        for line in str(text or '').replace('\r', '').split('\n'):
            s = line.strip()
            if not s:
                flush()
            elif s[:2] in ('- ', '* ', '• '):
                if para:
                    flush()
                items.append(s[2:].strip())
            else:
                if items:
                    flush()
                para.append(s)
        flush()
        return ''.join(out)

    def page(self):
        d = self.load()
        parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8">'
                 '<meta name="viewport" content="width=device-width,initial-scale=1">'
                 f'<title>News</title><style>{CSS}</style></head><body>']
        if d['banner']:
            parts.append(f'<img class="banner" src="{d["banner"]}" alt="">')
        parts.append('<div class="wrap">')
        posts = [p for p in d['posts'] if (p.get('title') or p.get('body'))]
        if not posts:
            parts.append('<p class="empty">No news yet.</p>')
        for p in posts:
            kind = p.get('kind') if p.get('kind') in KINDS else 'new'
            meta = f'<span class="tag {kind}">{KINDS[kind]}</span>' + html.escape(p.get('date') or '')
            parts.append(f'<article><h2>{html.escape(p.get("title") or "")}</h2>'
                         f'<p class="meta">{meta}</p>{self.body_html(p.get("body"))}</article>')
        parts.append('</div></body></html>')
        return ''.join(parts)

    def data_url(self):
        """The page as a data: address (what Home > Info and Menu > News open)."""
        with self.mu:
            d = self.load()
            if self._cache and self._cache[0] == d['rev']:
                return self._cache[1]
            url = 'data:text/html;charset=utf-8,' + quote(self.page() + '<!--', safe="!$&'()*+,;=:@/?~-._")
            self._cache = (d['rev'], url)
            return url

    def notice_id(self):
        d = self.load()
        return str(500000 + int(d.get('rev') or 0))

    def patch_notice(self, value):
        """Pj6zDW3m: the notice the game opens from Home > Info and Menu > News."""
        row = dict(value[0]) if isinstance(value, list) and value and isinstance(value[0], dict) else \
            dict(value) if isinstance(value, dict) else {}
        row.update({'xJNom6i0': self.notice_id(), 'jsRoN50z': self.data_url()})
        return [row] if isinstance(value, list) else row

    # ------------------------------------------------------------------ admin page
    def admin_view(self):
        d = self.load()
        return {'banner': d['banner'], 'posts': d['posts'], 'kinds': KINDS}

    def admin_save(self, data):
        d = self.load()
        if 'banner' in data:
            b = str(data.get('banner') or '')
            if b and not re.match(r'^data:image/(jpeg|png|webp);base64,[A-Za-z0-9+/=]+$', b):
                raise ValueError('The banner picture could not be read.')
            if len(b) > MAX_BANNER:
                raise ValueError('The banner picture is too big - try a smaller one.')
            d['banner'] = b
        if 'posts' in data:
            posts = []
            for p in data.get('posts') or []:
                title, body = str(p.get('title') or '').strip(), str(p.get('body') or '').strip()
                if not title and not body:
                    continue
                if len(title) > 120 or len(body) > 8000:
                    raise ValueError('A post is too long (title 120, text 8000 characters).')
                kind = p.get('kind') if p.get('kind') in KINDS else 'new'
                pid = str(p.get('id') or '') or hashlib.md5(f'{time.time()}{title}'.encode()).hexdigest()[:10]
                posts.append({'id': pid, 'title': title, 'date': str(p.get('date') or '').strip()[:40],
                              'kind': kind, 'body': body})
            d['posts'] = posts
        self.save(d)
        self.S.log.info('admin page: news updated (%d post%s%s)', len(d['posts']),
                        '' if len(d['posts']) == 1 else 's', ', with banner' if d['banner'] else '')
        return self.admin_view()
