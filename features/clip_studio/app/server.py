#!/usr/bin/env python3
"""Clip Studio — local web UI for the Auto Poster AI clip feature.

Runs the feature's bundled clip engine (../engine/clip_engine.py) from
a small web UI. Python standard library ONLY for the server
(http.server); the engine subprocess uses whatever Python runs this
file, so run it with the environment that has requirements-clip.txt
installed. This UI ships with the feature because Auto Poster's hosted
app shell does not exist yet — it runs standalone from the repo folder:

    python features/clip_studio/app/server.py   # http://localhost:8765

Jobs persist on disk (data/jobs.json + data/outputs/) so a restart
loses nothing: queued jobs stay queued, a job that was mid-render is
re-queued, finished clips stay in the library.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(APP_DIR)
ENGINE = os.path.join(ROOT, 'engine')
ENGINE_PY = os.path.join(ENGINE, 'clip_engine.py')
BRANDS_DIR = os.path.join(ENGINE, 'brands')
LOGO = os.path.join(ENGINE, 'assets', 'logo-icon.png')
EXAMPLE_CLIP = os.path.join(APP_DIR, 'assets', 'example-clip.mp4')
SAMPLE_FILE = os.path.join(APP_DIR, 'sample', 'sample-podcast.mp4')
# The bundled sample is a 60-second podcast section; this window is a
# stretch of clean speech in it (proven through the face check).
DEMO_START = 10.0
DEMO_END = 19.5
DATA = os.path.join(ROOT, 'data')
OUTPUTS = os.path.join(DATA, 'outputs')
UPLOADS = os.path.join(DATA, 'uploads')
JOBFILES = os.path.join(DATA, 'jobfiles')
JOBS_FILE = os.path.join(DATA, 'jobs.json')
PORT = int(os.environ.get('CLIP_STUDIO_PORT', '8765'))

for d in (DATA, OUTPUTS, UPLOADS, JOBFILES):
    os.makedirs(d, exist_ok=True)

# ---------------------------------------------------------------- store
_lock = threading.Lock()
_jobs = {}


def save_jobs():
    tmp = JOBS_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(list(_jobs.values()), f, indent=1)
    os.replace(tmp, JOBS_FILE)


def load_jobs():
    if os.path.isfile(JOBS_FILE):
        try:
            with open(JOBS_FILE) as f:
                for job in json.load(f):
                    if job.get('status') == 'running':
                        job['status'] = 'queued'  # resume after restart
                        job['note'] = 're-queued after a restart'
                    _jobs[job['id']] = job
        except (OSError, json.JSONDecodeError):
            pass


def new_job(params, rerender_of=None):
    jid = uuid.uuid4().hex[:12]
    job = {
        'id': jid,
        'created': time.strftime('%Y-%m-%d %H:%M:%S'),
        'status': 'queued',
        'params': params,
        'rerender_of': rerender_of,
        'result': None,
        'error': None,
        'note': None,
    }
    with _lock:
        _jobs[jid] = job
        save_jobs()
    return job


# ------------------------------------------------------- progress/stage
# The engine narrates its run in its log; translate the latest milestone
# into a plain-English stage label for the jobs list.
_STAGE_MARKERS = [
    ('transcribed', 'Picking the best moment'),
    ('moment', 'Picking the best moment'),
    ('source passages', 'Framing the speaker'),
    ('layout:', 'Framing the speaker'),
    ('rendered', 'Rendering your clip'),
    ('text layer', 'Rendering your clip'),
    ('QA gate', 'Checking the speaker\u2019s face is visible'),
    ('loudness OUT', 'Setting the sound level'),
    ('DONE', 'Finishing up'),
]


def job_stage(job):
    if job.get('status') == 'queued':
        return 'Waiting in line'
    if job.get('status') != 'running':
        return None
    log_path = os.path.join(JOBFILES, job['id'], 'engine.log')
    try:
        with open(log_path) as f:
            log = f.read()
    except OSError:
        return 'Getting started'
    stage = None
    for marker, label in _STAGE_MARKERS:
        if marker in log:
            stage = label
    return stage or 'Listening to the episode & writing the captions'


def _parse_ts(s):
    try:
        return time.mktime(time.strptime(s, '%Y-%m-%d %H:%M:%S'))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- engine
def brand_kits():
    kits = []
    if os.path.isdir(BRANDS_DIR):
        for fn in sorted(os.listdir(BRANDS_DIR)):
            if fn.endswith('.json'):
                name = fn[:-5]
                try:
                    with open(os.path.join(BRANDS_DIR, fn)) as f:
                        name = json.load(f).get('name', name)
                except (OSError, json.JSONDecodeError):
                    pass
                kits.append({'file': fn, 'name': name})
    return kits


def build_command(job):
    p = job['params']
    jid = job['id']
    jf = os.path.join(JOBFILES, jid)
    os.makedirs(jf, exist_ok=True)
    out = os.path.join(OUTPUTS, jid, 'clip.mp4')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cmd = [sys.executable, ENGINE_PY, p['input'], '--out', out]
    if p.get('start') is not None:
        cmd += ['--start', str(p['start'])]
        if p.get('end') is not None:
            cmd += ['--end', str(p['end'])]
        else:
            cmd += ['--duration', str(p.get('duration') or 45)]
    else:
        cmd += ['--auto', '--duration', str(p.get('duration') or 45)]
    cmd += ['--layout', p.get('layout') or 'cutaway']
    cmd += ['--style', p.get('style') or 'standard']
    if p.get('seed') is not None:
        cmd += ['--seed', str(p['seed'])]
    if p.get('brand'):
        cmd += ['--brand', os.path.join(BRANDS_DIR, p['brand'])]
    if p.get('hook'):
        cmd += ['--hook', p['hook']]
    if p.get('title'):
        cmd += ['--title', p['title']]
    if p.get('corrections'):
        cpath = os.path.join(jf, 'corrections.json')
        with open(cpath, 'w') as f:
            json.dump(p['corrections'], f)
        cmd += ['--corrections', cpath]
    if p.get('captions_file'):
        cmd += ['--captions-file', p['captions_file']]
    return cmd, out


def summarize(build):
    if not build:
        return None
    ln = build.get('loudnorm_output') or {}
    qa = build.get('qa_gate') or {}
    return {
        'hook': build.get('hook'),
        'peak_line': build.get('peak_line'),
        'loudness_lufs': ln.get('input_i'),
        'true_peak_dbtp': ln.get('input_tp'),
        'qa_status': qa.get('status'),
        'qa_checked': qa.get('checked'),
        'qa_failures': qa.get('failures'),
        'style': (build.get('style') or {}).get('name'),
        'brand': (build.get('brand') or {}).get('name'),
        'moment': build.get('moment'),
    }


def run_job(job):
    jid = job['id']
    cmd, out = build_command(job)
    log_path = os.path.join(JOBFILES, jid, 'engine.log')
    env = dict(os.environ)
    bindir = os.path.dirname(sys.executable)
    env['PATH'] = bindir + os.pathsep + env.get('PATH', '')
    with _lock:
        job['status'] = 'running'
        job['started'] = time.strftime('%Y-%m-%d %H:%M:%S')
        save_jobs()
    rc = None
    try:
        with open(log_path, 'w') as logf:
            logf.write('$ ' + ' '.join(cmd) + '\n\n')
            logf.flush()
            proc = subprocess.run(cmd, stdout=logf,
                                  stderr=subprocess.STDOUT, env=env,
                                  cwd=os.path.join(JOBFILES, jid))
            rc = proc.returncode
    except OSError as e:
        rc = -1
        with open(log_path, 'a') as logf:
            logf.write(f'\nfailed to start engine: {e}\n')
    build = None
    bpath = os.path.splitext(out)[0] + '.build.json'
    if os.path.isfile(bpath):
        try:
            with open(bpath) as f:
                build = json.load(f)
        except (OSError, json.JSONDecodeError):
            build = None
    tail = ''
    try:
        with open(log_path) as f:
            tail = f.read()[-3000:]
    except OSError:
        pass
    with _lock:
        job['result'] = summarize(build)
        job['finished'] = time.strftime('%Y-%m-%d %H:%M:%S')
        if rc == 0 and build and (build.get('qa_gate') or {}).get(
                'status') != 'fail':
            job['status'] = 'done'
            job['error'] = None
        else:
            job['status'] = 'failed'
            job['error'] = (
                'Face check failed \u2014 in part of this clip the '
                'speaker\u2019s face wasn\u2019t fully visible, so it '
                'wasn\u2019t shipped. Try a different moment, or the '
                '\u201cBoth people, stacked\u201d layout for interviews. '
                '(The raw details are under Technical details.)'
                if build and (build.get('qa_gate') or {}).get(
                    'status') == 'fail'
                else f'The clip maker hit a problem (code {rc}). '
                     'The raw details are under Technical details on '
                     'this page.')
            job['log_tail'] = tail
        save_jobs()


def worker_loop():
    while True:
        job = None
        with _lock:
            for j in _jobs.values():
                if j['status'] == 'queued':
                    job = j
                    break
        if job is None:
            time.sleep(1.5)
            continue
        run_job(job)


# ---------------------------------------------------------------- srt
def parse_srt(text):
    cues = []
    blocks = re.split(r'\n\s*\n', text.strip())
    for b in blocks:
        lines = [ln for ln in b.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        tline = lines[1] if lines[0].strip().isdigit() else lines[0]
        m = re.match(
            r'(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*'
            r'(\d+):(\d+):(\d+)[,.](\d+)', tline)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000.0
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000.0
        text_lines = lines[2:] if lines[0].strip().isdigit() else lines[1:]
        cues.append({'start': round(start, 3), 'end': round(end, 3),
                     'text': ' '.join(text_lines).strip()})
    return cues


def fmt_ts(sec):
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'


def write_srt(cues, path):
    with open(path, 'w') as f:
        for i, c in enumerate(cues, 1):
            f.write(f"{i}\n{fmt_ts(float(c['start']))} --> "
                    f"{fmt_ts(float(c['end']))}\n{c['text'].strip()}\n\n")


# ---------------------------------------------------------------- http
def parse_multipart(body, content_type):
    m = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', content_type)
    boundary = (m.group(1) or m.group(2)).encode()
    fields, files = {}, {}
    for part in body.split(b'--' + boundary):
        part = part.strip(b'\r\n')
        if not part or part == b'--':
            continue
        if part.endswith(b'--'):
            part = part[:-2].rstrip(b'\r\n')
        head, sep, data = part.partition(b'\r\n\r\n')
        if not sep:
            continue
        head = head.decode('utf-8', 'replace')
        nm = re.search(r'name="([^"]+)"', head)
        if not nm:
            continue
        fm = re.search(r'filename="([^"]*)"', head)
        if fm:
            if fm.group(1):
                files[nm.group(1)] = (fm.group(1), data)
        else:
            fields[nm.group(1)] = data.decode('utf-8', 'replace')
    return fields, files


def job_public(job):
    out = dict(job)
    p = dict(job['params'])
    if p.get('input') and not str(p['input']).startswith('http'):
        p['input'] = os.path.basename(str(p['input']))
    out['params'] = p
    out['stage'] = job_stage(job)
    started = _parse_ts(job.get('started'))
    if started:
        end = _parse_ts(job.get('finished')) or time.time()
        out['elapsed_secs'] = max(0, int(end - started))
    else:
        out['elapsed_secs'] = None
    return out


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        pass

    # -- helpers
    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, text):
        body = text.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, ctype, download_name=None, inline=False):
        if not os.path.isfile(path):
            self.send_json({'error': 'file not found'}, 404)
            return
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(size))
        disp = 'inline' if inline else 'attachment'
        if download_name:
            self.send_header('Content-Disposition',
                             f'{disp}; filename="{download_name}"')
        self.end_headers()
        with open(path, 'rb') as f:
            while True:
                chunk = f.read(262144)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def read_body(self):
        n = int(self.headers.get('Content-Length') or 0)
        return self.rfile.read(n) if n else b''

    # -- GET
    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        if path == '/':
            self.send_html(PAGE)
            return
        if path == '/money':
            self.send_html(MONEY_PAGE)
            return
        if path in ('/logo.png', '/favicon.ico'):
            self.send_file(LOGO, 'image/png', inline=True)
            return
        if path == '/example.mp4':
            self.send_file(EXAMPLE_CLIP, 'video/mp4', inline=True)
            return
        if path == '/api/brands':
            self.send_json({'kits': brand_kits()})
            return
        if path == '/api/jobs':
            with _lock:
                jobs = [job_public(j) for j in
                        sorted(_jobs.values(),
                               key=lambda j: j['created'], reverse=True)]
            self.send_json({'jobs': jobs})
            return
        m = re.match(r'^/api/jobs/([a-f0-9]+)$', path)
        if m:
            job = _jobs.get(m.group(1))
            if not job:
                self.send_json({'error': 'no such job'}, 404)
                return
            self.send_json({'job': job_public(job)})
            return
        m = re.match(r'^/api/jobs/([a-f0-9]+)/cues$', path)
        if m:
            job = _jobs.get(m.group(1))
            srt = os.path.join(OUTPUTS, m.group(1), 'clip.srt')
            if not job or not os.path.isfile(srt):
                self.send_json({'error': 'no captions for this job'}, 404)
                return
            with open(srt) as f:
                self.send_json({'cues': parse_srt(f.read())})
            return
        m = re.match(r'^/download/([a-f0-9]+)/(mp4|srt|json)$', path)
        if m:
            jid, kind = m.groups()
            fn, ct = {'mp4': ('clip.mp4', 'video/mp4'),
                      'srt': ('clip.srt', 'text/plain; charset=utf-8'),
                      'json': ('clip.build.json', 'application/json')}[kind]
            inline = parse_qs(u.query).get('inline') == ['1']
            self.send_file(os.path.join(OUTPUTS, jid, fn), ct,
                           download_name=f'clip-{jid}.{kind}',
                           inline=inline)
            return
        self.send_json({'error': 'not found'}, 404)

    # -- POST
    def do_POST(self):
        u = urlparse(self.path)
        path = u.path
        if path == '/api/jobs':
            self.create_job()
            return
        if path == '/api/demo':
            self.create_demo()
            return
        m = re.match(r'^/api/jobs/([a-f0-9]+)/rerender-captions$', path)
        if m:
            self.rerender_captions(m.group(1))
            return
        self.send_json({'error': 'not found'}, 404)

    def create_demo(self):
        if not os.path.isfile(SAMPLE_FILE):
            self.send_json(
                {'error': 'the sample episode bundled with the app is '
                          'missing from this copy'}, 500)
            return
        params = {
            'input': SAMPLE_FILE,
            'input_label': 'Sample podcast (bundled with the app)',
            'start': DEMO_START,
            'end': DEMO_END,
            'duration': 45,
            'layout': 'cutaway',
            'style': 'standard',
            'seed': None,
            'brand': '',
            'hook': '',
            'title': 'Sample podcast',
            'corrections': None,
            'captions_file': None,
        }
        job = new_job(params)
        self.send_json({'job': job_public(job)}, 201)

    def create_job(self):
        ctype = self.headers.get('Content-Type') or ''
        body = self.read_body()
        fields, files = {}, {}
        if ctype.startswith('multipart/form-data'):
            fields, files = parse_multipart(body, ctype)
        elif ctype.startswith('application/json'):
            try:
                fields = {k: ('' if v is None else str(v))
                          for k, v in json.loads(body or b'{}').items()}
            except json.JSONDecodeError:
                self.send_json({'error': 'bad JSON'}, 400)
                return
        else:
            fields = {k: v[0] for k, v in
                      parse_qs(body.decode('utf-8', 'replace')).items()}

        inp = (fields.get('input_url') or '').strip()
        label = inp
        if 'file' in files and files['file'][0]:
            fname, data = files['file']
            safe = re.sub(r'[^A-Za-z0-9._-]+', '_', os.path.basename(fname))
            saved = os.path.join(UPLOADS,
                                 f'{uuid.uuid4().hex[:8]}_{safe}')
            with open(saved, 'wb') as f:
                f.write(data)
            inp, label = saved, fname
        if not inp:
            self.send_json({'error': 'give a video link or upload a file'},
                           400)
            return

        def num(name):
            v = (fields.get(name) or '').strip()
            if v == '':
                return None
            try:
                return float(v)
            except ValueError:
                return None

        corrections = None
        raw_corr = (fields.get('corrections') or '').strip()
        if ctype.startswith('application/json') and isinstance(
                json.loads(body or b'{}').get('corrections'), dict):
            corrections = json.loads(body)['corrections']
        elif raw_corr:
            try:
                corrections = json.loads(raw_corr)
                if not isinstance(corrections, dict):
                    raise ValueError
            except ValueError:
                self.send_json(
                    {'error': 'corrections must be a JSON object like '
                              '{"Monocero": "Manassero"}'}, 400)
                return

        brand = (fields.get('brand') or '').strip()
        if brand and brand not in {k['file'] for k in brand_kits()}:
            self.send_json({'error': 'unknown brand kit'}, 400)
            return
        seed = num('seed')
        params = {
            'input': inp,
            'input_label': label,
            'start': num('start'),
            'end': num('end'),
            'duration': num('duration') or 45,
            'layout': fields.get('layout') or 'cutaway',
            'style': fields.get('style') or 'standard',
            'seed': int(seed) if seed is not None else None,
            'brand': brand,
            'hook': (fields.get('hook') or '').strip(),
            'title': (fields.get('title') or '').strip(),
            'corrections': corrections,
            'captions_file': None,
        }
        if params['layout'] not in ('cutaway', 'split'):
            self.send_json({'error': 'layout must be cutaway or split'}, 400)
            return
        if params['style'] not in ('standard', 'bar', 'minimal', 'random'):
            self.send_json({'error': 'unknown style'}, 400)
            return
        job = new_job(params)
        self.send_json({'job': job_public(job)}, 201)

    def rerender_captions(self, jid):
        orig = _jobs.get(jid)
        if not orig:
            self.send_json({'error': 'no such job'}, 404)
            return
        try:
            payload = json.loads(self.read_body() or b'{}')
            cues = payload['cues']
            assert isinstance(cues, list) and cues
            for c in cues:
                float(c['start'])
                float(c['end'])
                str(c['text'])
        except (ValueError, KeyError, TypeError, AssertionError):
            self.send_json({'error': 'send {cues: [{start, end, text}]}'},
                           400)
            return
        params = dict(orig['params'])
        job = new_job(params, rerender_of=jid)
        cpath = os.path.join(JOBFILES, job['id'], 'edited_captions.srt')
        os.makedirs(os.path.dirname(cpath), exist_ok=True)
        write_srt(cues, cpath)
        with _lock:
            job['params']['captions_file'] = cpath
            job['note'] = f'caption edit of job {jid}'
            save_jobs()
        self.send_json({'job': job_public(job)}, 201)


_CSS = """
:root{--navy:#0C1425;--amber:#FFB020;--paper:#F8FAFC;--slate:#475569}
*{box-sizing:border-box}body{margin:0;font-family:system-ui,Segoe UI,Arial,sans-serif;background:var(--paper);color:var(--navy)}
header{background:var(--navy);color:#fff;padding:14px 22px;display:flex;gap:14px;align-items:center}
header img{width:44px;height:44px;border-radius:10px;background:#fff}
header h1{font-size:22px;margin:0}header p{margin:2px 0 0;color:#CBD5E1;font-size:13px}
header a.nav{color:var(--amber);margin-left:auto;font-weight:700;text-decoration:none;font-size:15px;white-space:nowrap}
main{max-width:980px;margin:0 auto;padding:20px}
.card{background:#fff;border:1px solid #E2E8F0;border-radius:12px;padding:18px;margin-bottom:18px}
label{display:block;font-size:13px;font-weight:600;margin:10px 0 4px}
input,select,textarea{width:100%;padding:9px;border:1px solid #CBD5E1;border-radius:8px;font-size:14px}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
button{background:var(--amber);border:0;color:var(--navy);font-weight:700;padding:10px 16px;border-radius:8px;cursor:pointer;font-size:14px}
button.sec{background:#E2E8F0}
button.big{font-size:16px;padding:14px 22px}
.badge{display:inline-block;padding:2px 9px;border-radius:99px;font-size:12px;font-weight:700}
.q{background:#FEF3C7}.r{background:#DBEAFE}.d{background:#DCFCE7}.f{background:#FEE2E2}
.job{border-top:1px solid #E2E8F0;padding:12px 0}
.job:first-child{border-top:0}
.small{font-size:12px;color:var(--slate)}
.help{font-size:12px;color:var(--slate);margin:3px 0 10px;line-height:1.45}
video{width:270px;max-width:100%;border-radius:10px;background:#000}
.cue{display:grid;grid-template-columns:150px 1fr;gap:8px;margin:6px 0}
.cue input[type=text]{font-family:inherit}
.err{color:#B91C1C;font-size:13px;white-space:pre-wrap}
a.dl{margin-right:12px;font-size:14px}
a.dl.main{font-weight:700}
details{margin-top:16px;border-top:1px dashed #CBD5E1;padding-top:12px}
summary{cursor:pointer;font-weight:700;font-size:14px}
table.ladder{border-collapse:collapse;width:100%;margin:10px 0}
table.ladder td,table.ladder th{border:1px solid #E2E8F0;padding:9px 11px;text-align:left;font-size:14px;vertical-align:top}
table.ladder th{background:#F1F5F9}
pre.script,textarea.script{white-space:pre-wrap;font-family:inherit;font-size:14px;line-height:1.55;background:#F8FAFC;border:1px solid #E2E8F0;border-radius:8px;padding:14px;width:100%}
ul.check{list-style:none;padding-left:0;line-height:1.9;font-size:15px}
.note{background:#FFFBEB;border:1px solid #FDE68A;border-radius:8px;padding:12px 14px;font-size:14px;line-height:1.55}
"""

PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Clip Studio</title><link rel="icon" href="/favicon.ico">
<style>""" + _CSS + """</style></head><body>
<header><img src="/logo.png" alt="logo"><div>
<h1>Clip Studio</h1><p>Podcast episodes in. Client-getting clips out. — an Auto Poster AI feature, running on this computer</p>
</div><a class="nav" href="/money">Make money with this →</a></header>
<main>

<div class="card" id="welcome">
<h2 style="margin-top:0">New here? Welcome 👋</h2>
<p><b>You don’t need any editing experience.</b> Paste a link to a podcast
episode, press one button, and Clip Studio does the editing for you: it picks
the best moment, frames the speaker’s face, writes the opening line, and adds
captions — a finished clip, ready to post on YouTube Shorts, Instagram Reels
or TikTok.</p>
<p><button class="big" id="demoBtn" type="button">▶ Try it with a sample podcast</button></p>
<p class="help">One click, nothing to type: it makes a real clip from a short
sample episode bundled with the app, so you can see exactly what you get
before using your own video. The sample lives inside the app, so it needs no
download — but your very first clip (this one included) also downloads a
small speech model, about 150&nbsp;MB, so the first run takes a little
longer.</p>
<p class="small" id="demoMsg"></p>
<p>Want the money side first? <a href="/money">See how people earn with clips →</a></p>
</div>

<div class="card"><h2 style="margin-top:0">An example of what you’ll get</h2>
<video controls preload="metadata" src="/example.mp4"></video>
<p class="help">This clip was made by this app from a real podcast episode
(Modern CTO). Every clip you make comes out like this: tall video made for
phones, captions burned in so it works with the sound off, and an opening
line up top that makes people stop scrolling.</p></div>

<div class="card"><h2 style="margin-top:0">Make your own clip</h2>
<form id="f">
<label>Video link (YouTube etc.)</label>
<input name="input_url" placeholder="https://www.youtube.com/watch?v=...">
<p class="help">Paste a link to a podcast episode — YouTube works best. The app downloads the video for you.</p>
<label>…or upload a video file</label>
<input type="file" name="file" accept="video/*,audio/*">
<p class="help">Or pick a video file from your computer instead. Use one or the other — link or file, not both.</p>
<div class="row"><div>
<label>Which part of the episode?</label><select id="mode">
<option value="auto">Auto-pick the best moment (easiest)</option>
<option value="explicit">Use my start / end times</option></select>
<p class="help">Auto-pick listens to the whole episode and chooses the strongest moment for you. Pick start/end times only if you already know the exact moment you want.</p></div>
<div><label>Clip length, seconds (auto-pick)</label>
<input name="duration" type="number" value="45" min="10" max="120">
<p class="help">How long the finished clip is. 45 seconds is a good length for Shorts, Reels and TikTok.</p></div></div>
<div class="row" id="times" style="display:none"><div>
<label>Start (seconds into the video)</label><input name="start" type="number" step="0.1"></div>
<div><label>End (seconds)</label><input name="end" type="number" step="0.1"></div></div>
<div class="row"><div><label>Layout — who is on screen</label><select name="layout">
<option value="cutaway">Follow the speaker</option>
<option value="split">Both people, stacked</option></select>
<p class="help">“Follow the speaker” fills the frame with whoever is talking — right for most podcasts. “Both people, stacked” is for two-person interviews filmed side by side: it shows both faces, one above the other.</p></div>
<div><label>Style — how the clip looks</label><select name="style">
<option value="standard">Standard — clean and proven (recommended)</option>
<option value="bar">Bar — opening line on bold bars</option>
<option value="minimal">Minimal — plain and quiet</option>
<option value="random">Random — surprise me, pick one for me</option></select>
<p class="help">The look of the captions and opening line. Not sure? Leave it on Standard — it’s the recipe we use for paying clients.</p></div></div>
<div class="row"><div><label>Your logo &amp; colours</label><select name="brand" id="brand">
<option value="">Studio default — Podcast Clips That Convert</option></select>
<p class="help">The logo and colours stamped onto your clip at the end. The studio default looks professional straight away. If a client later gives you their own logo, their colours can be added to the app.</p></div>
<div><label>Your own opening line (optional)</label>
<input name="hook" placeholder="Leave blank to auto-write the opening line">
<p class="help">The big line across the top at the start — it’s what makes someone stop scrolling. Leave it blank and the app writes one from the best moment it finds.</p></div></div>
<label>Show name (optional)</label>
<input name="title" placeholder="e.g. Modern CTO">
<p class="help">The podcast’s name, printed at the top of the clip. Leave it blank if you’re just trying things out.</p>
<details><summary>Advanced (you can ignore this)</summary>
<label>Random seed (only for the Random style)</label>
<input name="seed" type="number" placeholder="any number">
<p class="help">If you choose the Random style, the same seed number gives you the same “random” pick again — handy for recreating a clip you liked.</p>
<label>Fix words the captions keep getting wrong</label>
<p class="help">Podcast names and people’s names are the usual troublemakers. If the captions keep hearing a word wrong, type the word the app hears and the word it should be. No code needed — just fill in a line:</p>
<div class="corrpair row"><div><label>The app hears…</label>
<input class="cw" placeholder="e.g. Monocero"></div>
<div><label>…but it should be</label>
<input class="cr" placeholder="e.g. Manassero"></div></div>
<div class="corrpair row"><div><label>The app hears…</label>
<input class="cw" placeholder=""></div>
<div><label>…but it should be</label>
<input class="cr" placeholder=""></div></div>
<div class="corrpair row"><div><label>The app hears…</label>
<input class="cw" placeholder=""></div>
<div><label>…but it should be</label>
<input class="cr" placeholder=""></div></div>
<label>…or paste a word list (JSON format — only if you know it)</label>
<textarea name="corrections" rows="2" placeholder='{"Monocero": "Manassero"}'></textarea>
</details>
<p><button type="submit">Make my clip</button>
<span class="small">One clip renders at a time — others wait in line. You can close this page and come back; the job keeps going and your clips are saved.</span></p>
</form><div id="msg" class="err"></div></div>

<div class="card" id="jobs-card"><h2 style="margin-top:0">Jobs &amp; library</h2>
<div id="jobs"><p class="small">Loading…</p></div></div>
</main>
<script>
const $=s=>document.querySelector(s);
fetch('/api/brands').then(r=>r.json()).then(d=>{
  const sel=$('#brand');
  d.kits.forEach(k=>{const o=document.createElement('option');
    o.value=k.file;o.textContent=k.name;sel.appendChild(o);});
});
$('#mode').addEventListener('change',e=>{
  $('#times').style.display=e.target.value==='explicit'?'grid':'none';});
$('#demoBtn').addEventListener('click',async ()=>{
  const m=$('#demoMsg');m.textContent='Starting your sample clip…';
  const r=await fetch('/api/demo',{method:'POST'});
  const d=await r.json();
  if(!r.ok){m.textContent=d.error||'Something went wrong';return;}
  m.textContent='Your sample clip is in the queue below — watch its progress under Jobs & library.';
  refresh();
  document.getElementById('jobs-card').scrollIntoView({behavior:'smooth'});
});
$('#f').addEventListener('submit',async e=>{
  e.preventDefault();$('#msg').textContent='';
  // turn the plain wrong-word/right-word lines into the corrections list
  const pairs={};
  document.querySelectorAll('.corrpair').forEach(row=>{
    const w=row.querySelector('.cw').value.trim();
    const r2=row.querySelector('.cr').value.trim();
    if(w&&r2)pairs[w]=r2;
  });
  const ta=document.querySelector('textarea[name=corrections]');
  if(Object.keys(pairs).length){
    let existing={};
    if(ta.value.trim()){try{existing=JSON.parse(ta.value);}catch(err){}}
    ta.value=JSON.stringify(Object.assign({},existing,pairs));
  }
  const fd=new FormData(e.target);
  if($('#mode').value==='auto'){fd.delete('start');fd.delete('end');}
  const r=await fetch('/api/jobs',{method:'POST',body:fd});
  const d=await r.json();
  if(!r.ok){$('#msg').textContent=d.error||'Something went wrong';return;}
  e.target.reset();$('#times').style.display='none';refresh();
});
function badge(s){return {queued:'q',running:'r',done:'d',failed:'f'}[s]}
function fmtDur(s){if(s==null)return '';s=Math.max(0,Math.round(s));
  const m=Math.floor(s/60),r=s%60;
  return m? m+' min '+r+' s' : r+' s';}
const ESTIMATE='How long does this take? Roughly 10–20× the clip’s length on a typical laptop — a 45-second clip usually takes about 8–15 minutes. Your very first clip also downloads a small speech model, which adds a few minutes.';
async function refresh(){
  const d=await (await fetch('/api/jobs')).json();
  const w=$('#welcome');
  if(w)w.style.display=d.jobs.length?'none':'block';
  const el=$('#jobs');
  if(!d.jobs.length){el.innerHTML='<p class="small">No clips yet. Try the sample button at the top, or paste a link above and press “Make my clip”.</p>';return;}
  el.innerHTML=d.jobs.map(j=>{
    const r=j.result||{};let h=
    '<div class="job"><span class="badge '+badge(j.status)+'">'+j.status+'</span> '+
    '<b>'+(j.params.input_label||j.params.input)+'</b> '+
    '<span class="small">'+j.created+' · job '+j.id+'</span><br>';
    if(j.status==='queued'){
      h+='<span class="small">Stage:</span> <b>Waiting in line</b> — clips are made one at a time, yours is next in the queue.<br>';
    }
    if(j.status==='running'){
      h+='<span class="small">Stage:</span> <b>'+(j.stage||'Working')+'</b>'+
      (j.elapsed_secs!=null?' · <span class="small">working for '+fmtDur(j.elapsed_secs)+'</span>':'')+
      '<br><span class="small">'+ESTIMATE+'</span><br>';
    }
    if(r.hook)h+='<span class="small">Opening line:</span> '+r.hook+'<br>';
    if(r.peak_line)h+='<span class="small">Best line:</span> “'+r.peak_line+'”<br>';
    if(j.status==='done'){
      if(r.qa_status)h+='Face check: <b>passed ✓</b> — the speaker’s face is visible the whole way through<br>';
      if(r.loudness_lufs)h+='Sound level: <b>good for social apps ✓</b> — set to the level YouTube, Instagram and TikTok expect<br>';
      if(j.elapsed_secs!=null)h+='<span class="small">Took '+fmtDur(j.elapsed_secs)+' to make.</span><br>';
    }
    if(j.status==='failed')h+='<div class="err">'+(j.error||'')+'</div>';
    if(j.status==='done'||(j.status==='failed'&&j.result)){
      h+='<video controls preload="none" src="/download/'+j.id+'/mp4?inline=1"></video><br>'+
      '<a class="dl main" href="/download/'+j.id+'/mp4">⬇ Download MP4</a>'+
      '<a class="dl" href="/download/'+j.id+'/srt">Captions file (for YouTube etc.)</a>'+
      '<a class="dl" href="/download/'+j.id+'/json">Technical details</a>'+
      '<button class="sec" onclick="editCaps(\''+j.id+'\')">Edit captions &amp; re-render</button>'+
      '<div id="caps-'+j.id+'"></div>';
    }
    return h+'</div>';}).join('');
}
async function editCaps(id){
  const box=document.getElementById('caps-'+id);
  const d=await (await fetch('/api/jobs/'+id+'/cues')).json();
  if(!d.cues){box.innerHTML='<p class="err">No captions found for this job.</p>';return;}
  box.innerHTML='<p class="small">Fix any misheard words below — the timings stay the same. Saving makes a NEW clip with your words burned in.</p>'+
    d.cues.map((c,i)=>'<div class="cue"><span class="small">'+c.start.toFixed(1)+'s – '+c.end.toFixed(1)+'s</span>'+
    '<input type="text" id="cue-'+id+'-'+i+'" value="'+c.text.replace(/"/g,'&quot;')+'"></div>').join('')+
    '<button onclick="saveCaps(\''+id+'\','+d.cues.length+')">Save captions &amp; re-render</button>';
  box.dataset.cues=JSON.stringify(d.cues);
}
async function saveCaps(id,n){
  const box=document.getElementById('caps-'+id);
  const orig=JSON.parse(box.dataset.cues);
  const cues=orig.map((c,i)=>({start:c.start,end:c.end,
    text:document.getElementById('cue-'+id+'-'+i).value}));
  const r=await fetch('/api/jobs/'+id+'/rerender-captions',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({cues})});
  const d=await r.json();
  if(!r.ok){box.innerHTML='<p class="err">'+(d.error||'failed')+'</p>';return;}
  box.innerHTML='<p class="small">Re-render queued as job '+d.job.id+' — watch it above.</p>';
  refresh();
}
refresh();setInterval(refresh,4000);
</script></body></html>
"""


MONEY_PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Make money with Clip Studio</title><link rel="icon" href="/favicon.ico">
<style>""" + _CSS + """</style></head><body>
<header><img src="/logo.png" alt="logo"><div>
<h1>Make money with this</h1><p>How a clipping side hustle actually works — the buyers, the prices, the message</p>
</div><a class="nav" href="/">← Back to Clip Studio</a></header>
<main>

<div class="card"><h2 style="margin-top:0">The idea, in one minute</h2>
<p>Thousands of people run podcasts to win clients for their business —
coaches, consultants, finance advisers, agency owners. Their long episodes
sit on YouTube barely watched, while short clips (YouTube Shorts, Instagram
Reels, TikToks) are where new listeners actually find them.</p>
<p>Almost none of them have time to make those clips. <b>That gap is the
business.</b> You take one of their episodes, hand back finished clips with
Clip Studio — the tool you’re looking at — and charge for it monthly. You
don’t need to be a video editor; the app does the editing. Your job is
finding the podcasters and pressing the button.</p></div>

<div class="card"><h2 style="margin-top:0">Who buys clips</h2>
<p><b>Business, finance and coaching podcasters who sell something</b> —
coaching programmes, consulting, courses, professional services. Clips pay
for themselves when a new listener can become a paying client, so these
hosts happily budget for them.</p>
<p>Skip entertainment, comedy, sport and true-crime shows: they mostly earn
from adverts, and clips rarely pay them back — so they won’t pay you.</p></div>

<div class="card"><h2 style="margin-top:0">What to charge</h2>
<p>These are the prices our own studio (Podcast Clips That Convert) works
to. Month to month, no contract — that makes “yes” easy for the client.</p>
<table class="ladder">
<tr><th>Step</th><th>Price</th><th>What they get</th></tr>
<tr><td><b>Trial</b></td><td><b>$49</b>, one-off</td>
<td>5 finished clips from one episode. The low-risk way in — most clients start here.</td></tr>
<tr><td><b>Core</b> (most popular)</td><td><b>$497/month</b></td>
<td>20 clips a month, made from their weekly episodes.</td></tr>
<tr><td><b>Pro</b></td><td><b>$997/month</b></td>
<td>40 clips a month — for shows posting twice a week, or posting everywhere at once.</td></tr>
</table>
<p class="help">Deliver monthly plans in weekly batches (about 5 clips a
week on Core), so the client starts posting in the first week instead of
waiting a month for a pile of files.</p></div>

<div class="card"><h2 style="margin-top:0">Where to find podcasters</h2>
<p>Search YouTube for <b>“business podcast”</b>, <b>“finance podcast”</b> or
<b>“[a topic you know] coaching podcast”</b>. Also browse the Business and
Investing charts in Apple Podcasts, then check the show has a YouTube
channel. A good fit looks like this:</p>
<ul>
<li>A new episode in the last 30 days, with full episodes on YouTube</li>
<li>The host sells something — coaching, consulting, a course, a service</li>
<li><b>Little or no Shorts activity</b> — that gap is exactly what you’re selling</li>
<li>Roughly 1,000–200,000 subscribers: big enough to afford you, small enough that the host still reads their own messages</li>
<li>A business email on the channel’s About page, their website, or an active Instagram/LinkedIn profile</li>
</ul>
<p class="help">Skip shows already posting polished captioned shorts every
day (they already have an editor) and shows with no episode in two months
(they’ve stopped).</p></div>

<div class="card"><h2 style="margin-top:0">The message that gets replies</h2>
<p>The trick is simple: <b>don’t pitch editing — offer free clips.</b> A
finished clip from their own episode sells better than any sales letter.
Copy this, and <b>always fill in the [brackets] with a real moment you
actually heard</b> — a message with a specific moment in it gets replies; a
blank template gets deleted.</p>
<textarea class="script" id="script" rows="17" readonly>Subject: 3 clips from your [Episode topic] episode

Hi [FirstName],

I listened to your [Episode topic] episode of [Podcast] this week. The part around [specific moment — e.g. “where you explain why you stopped discounting your coaching offer”] is a short clip waiting to happen — strong point, clean story, works with the sound off.

I turn business podcasts into ready-to-post Shorts, Reels and TikToks — opening line, captions and titles done. To show you instead of telling you, I’ll cut 3 clips from that episode for you, free. Finished files, yours to post whether you ever hire me or not.

Just reply “go” and I’ll send them within 48 hours. If they’re not good enough to post, you’ve lost nothing.

If they are good enough, the usual next step is a 5-clip trial for $49, then $497/mo for 20 clips a month from your weekly episodes — month to month, no contract. But the 3 free ones come first either way.

[Your name]</textarea>
<p><button class="sec" id="copyBtn" type="button">Copy the message</button>
<span class="small" id="copyMsg"></span></p>
<p class="help">No reply? Follow up once after 3 days, naming the 3 moments
you’d clip. Once more 4–5 days after that, politely close the loop. Then
move on — there are always more shows.</p></div>

<div class="card"><h2 style="margin-top:0">Your first-client checklist</h2>
<ul class="check">
<li>☐ &nbsp;Press the sample button on the home page and watch the clip it makes — that’s your product</li>
<li>☐ &nbsp;Make one more clip from a real business podcast episode, so you have something to show</li>
<li>☐ &nbsp;Write down 20 shows that fit the “good fit” list above</li>
<li>☐ &nbsp;For each show, watch or listen to an episode and note ONE great moment, with its timestamp</li>
<li>☐ &nbsp;Send the message above — with the real moment filled in, every time</li>
<li>☐ &nbsp;When someone replies “go”, make their 3 clips here in Clip Studio and send them within 48 hours</li>
<li>☐ &nbsp;Follow up once after 3 days, and once more 4–5 days later, if there’s no reply</li>
<li>☐ &nbsp;When they’re happy, offer the $49 trial — then the monthly plan</li>
<li>☐ &nbsp;Keep a simple list (a spreadsheet is fine): who you contacted, when, and what they said</li>
</ul></div>

<div class="card"><div class="note"><b>Real talk.</b> This is a hustle, not
a lottery ticket. Most messages get no reply — that is normal, and it’s why
you work a list instead of pinning hopes on one show. What makes it work is
the free clips: a host who sees their own episode turned into a sharp,
captioned short understands the value instantly. We can’t promise you’ll
earn anything — nobody honestly can. What you earn depends on how many
good-fit shows you contact and how reliably you deliver. The tool is ready
when you are: <a href="/">make your first clip →</a></div></div>

</main>
<script>
document.getElementById('copyBtn').addEventListener('click',async ()=>{
  const t=document.getElementById('script');
  const m=document.getElementById('copyMsg');
  try{await navigator.clipboard.writeText(t.value);m.textContent='Copied — paste it into your email and fill in the [brackets].';}
  catch(e){t.select();document.execCommand('copy');m.textContent='Copied — paste it into your email and fill in the [brackets].';}
});
</script></body></html>
"""


def main():
    load_jobs()
    threading.Thread(target=worker_loop, daemon=True).start()
    srv = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    print(f'Clip Studio is running: http://localhost:{PORT}')
    print('Leave this window open while clips render. Ctrl+C to stop.')
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
