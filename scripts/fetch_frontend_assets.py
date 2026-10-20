"""Fetch front-end CDN assets into static/vendor so the site works offline.
Run once, then delete. Safe to re-run.
"""
import os
import re
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(BASE, 'static', 'vendor')

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')


def ensure(path):
    os.makedirs(path, exist_ok=True)


def get_bytes(url, ua=False):
    req = urllib.request.Request(url)
    if ua:
        req.add_header('User-Agent', UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def save(path, data):
    ensure(os.path.dirname(path))
    with open(path, 'wb') as f:
        f.write(data)
    print('saved', os.path.relpath(path, BASE), len(data), 'bytes')


# 1. Bootstrap RTL css + bundle js
save(os.path.join(VENDOR, 'bootstrap', 'bootstrap.rtl.min.css'),
     get_bytes('https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.rtl.min.css'))
save(os.path.join(VENDOR, 'bootstrap', 'bootstrap.bundle.min.js'),
     get_bytes('https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js'))

# 2. Bootstrap Icons css + fonts (css references ./fonts/*)
save(os.path.join(VENDOR, 'bootstrap-icons', 'bootstrap-icons.min.css'),
     get_bytes('https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css'))
for name in ('bootstrap-icons.woff2', 'bootstrap-icons.woff'):
    save(os.path.join(VENDOR, 'bootstrap-icons', 'fonts', name),
         get_bytes(f'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/fonts/{name}'))

# 3. Chart.js UMD
save(os.path.join(VENDOR, 'chartjs', 'chart.umd.min.js'),
     get_bytes('https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js'))

# 4. Cairo font: fetch Google css (woff2), download each font file locally, rewrite urls
css = get_bytes(
    'https://fonts.googleapis.com/css2?family=Cairo:wght@400;500;600;700;800;900&display=swap',
    ua=True,
).decode('utf-8')

files_dir = os.path.join(VENDOR, 'cairo', 'files')
ensure(files_dir)
urls = sorted(set(re.findall(r'url\((https://fonts\.gstatic\.com/[^)]+)\)', css)))
for i, u in enumerate(urls, 1):
    fname = f'cairo-{i:02d}.woff2'
    save(os.path.join(files_dir, fname), get_bytes(u, ua=True))
    css = css.replace(u, f'files/{fname}')

save(os.path.join(VENDOR, 'cairo', 'cairo.css'), css.encode('utf-8'))
print('DONE asset fetch')
