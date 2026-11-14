#!/usr/bin/env python3
"""Clip Engine — turn ANY video (URL or local file) into a studio-standard
vertical podcast clip (1080x1920), implementing Part 1 of the studio editing
standard (editing-standard-and-video-review.md):

  * speech + big face + text hook from frame one
  * ONE smooth opening push-in that settles (supersampled scale+crop,
    NO ffmpeg zoompan), speaker switches as clean CUTS
  * at most ONE punch-in, on the peak line, which then HOLDS
  * no zoom oscillation, no unmotivated effects
  * captions: 2-3 words per screen, PIL Manrope, current word Signal Amber,
    pro line-break rules (never split article+noun, name, number+unit)
  * thin progress bar, 1s brand end card (Two Clips logo)
  * audio normalised to -14 LUFS / -1 dBTP (two-pass measured loudnorm)
  * optional point-and-appear visual overlay (--overlay, max ONE per
    clip): an image of what the speaker is talking about flies in beside
    the speaker (scale + slide, ease-out), holds for the line, then
    fades/slides out — rendered as per-frame PIL alpha frames at full
    fps, never over the face or the caption band
  * MIXED-LAYOUT sources are handled per passage: stills are classified
    over time (full-screen vs source-split), and inside a passage where
    the SOURCE is itself split-screen every crop stays inside ONE panel
    (the active speaker's) — a crop may never straddle the source seam
  * OUTPUT QA GATE: the rendered clip is face-checked (YuNet) at
    sampled times during speech; around every segment/passage
    boundary it samples densely (0.25s steps over +/-1.5s, 0.05s
    steps within +/-0.3s) and every
    such sample must show the face — a few-frame faceless flash at a
    handoff cannot slip through. A frame with no FULLY-visible face
    triggers one repair pass (re-frame + re-render), and a clip that
    still fails is marked FAILED in its .build.json, never delivered
    as good
  * MIXED-LAYOUT handoffs are frame-accurate: passage boundaries are
    refined with 0.05s classification probes and placed at the first
    confirmed instant of the incoming class (the coarse 0.4s probing
    misplaced one edge by ~0.2s and flashed a panel crop over the
    next composition), the mezzanine window is converted to the
    output frame rate once so all pieces share one sampling phase,
    pieces are capped at their exact frame counts, and the join
    trims every piece to that count (piece container durations run
    long — AAC priming and last-frame stretching — and an untrimmed
    concat duplicates tail frames at joints, landing later cuts
    ~0.1s late)
  * STYLE REGISTERS (--style): 'standard' is the approved recipe,
    unchanged; 'bar' and 'minimal' vary EXPRESSION only (hook
    treatment, caption accent behaviour, attribution and end-card
    styling) — timings, chunking and the motion rules never change.
    '--style random' picks a register from a seeded RNG; the choice
    and the seed land in the .build.json, so any clip is reproducible
  * BRAND KITS (--brand kit.json): name / logo / accent / end-card
    name drive the end card, hook accent, progress fill and caption
    highlight. The studio kit in brands/ is the default
  * EDITABLE CAPTIONS: --corrections map.json fixes transcript words
    (e.g. a misheard name) before captions are built;
    --captions-file (an .srt or JSON cue list) burns THOSE words on
    the original word timings — the fix-a-word-and-re-render path,
    no re-transcription
  * SPLIT LAYOUT RULE (Pupa): no punch-in and no zoom/push of any
    kind — both panels sit at a steady, wider "own camera" framing
    for the entire clip

Usage:
  clip_engine.py INPUT [--start S --end E | --auto] [--duration 45]
                 [--layout cutaway|split] [--hook "TEXT"] [--out PATH]
                 [--overlay IMG --overlay-at SECONDS
                  [--overlay-dur SECONDS] [--overlay-pos POS]]
                 [--style standard|bar|minimal|random] [--seed N]
                 [--brand KIT.json] [--corrections MAP.json]
                 [--captions-file CUES.srt|CUES.json]
                 [--keep-work]
INPUT is a local file path or an http(s) URL (downloaded with yt-dlp).

Layouts: 'cutaway' (default) cuts between speakers full-frame; 'split'
stacks BOTH speakers of a two-panel source into fixed top/bottom panels
of the 9:16 frame (each face-cropped into its own panel) and refuses
single-speaker sources.
"""
import argparse, json, math, os, random, re, shutil, subprocess, sys
import tempfile, time

ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(ENGINE_DIR, 'assets')
VENDOR = os.path.join(ENGINE_DIR, 'vendor')
if os.path.isdir(VENDOR):
    sys.path.insert(0, VENDOR)
# faster-whisper lives in the shared tools dir on this machine
for cand in (os.environ.get('CLIP_FW_PATH'), os.path.expanduser('~/workspace/tools/fw')):
    if cand and os.path.isdir(os.path.join(cand, 'faster_whisper')):
        if cand not in sys.path:
            sys.path.insert(0, cand)
        break

YTDLP_CANDIDATES = [os.path.expanduser('~/workspace/tools/yt-dlp'), 'yt-dlp']

AMBER = (255, 176, 32, 255)
WHITE = (255, 255, 255, 255)
OUT_W, OUT_H, FPS = 1080, 1920, 24
TXT_FPS = 12

def log(*a):
    print('[clip-engine]', *a, flush=True)

def run(cmd, check=True, capture=True):
    r = subprocess.run(cmd, capture_output=capture, text=True)
    if check and r.returncode != 0:
        sys.stderr.write((r.stdout or '')[-1200:] + '\n' + (r.stderr or '')[-2500:] + '\n')
        raise SystemExit(f'command failed: {cmd[0]} ...')
    return r

# ---------------------------------------------------------------- ingest
def is_url(s):
    return s.startswith('http://') or s.startswith('https://')

def ingest(inp, workdir):
    """Return (local_path, title). Title is the video's real title
    for URL inputs (via yt-dlp); for a local file it is None — a
    filename is not a show name and must never be burned into the
    clip as the attribution (a test file named ep984_section.mkv
    rendered as an 'EP984_SECTION' title card). Pass --title to set
    an attribution explicitly."""
    if not is_url(inp):
        p = os.path.abspath(os.path.expanduser(inp))
        if not os.path.isfile(p):
            raise SystemExit(f'input file not found: {inp}')
        return p, None
    ytdlp = next((c for c in YTDLP_CANDIDATES
                  if os.path.isfile(c) or shutil.which(c)), None)
    if not ytdlp:
        raise SystemExit('yt-dlp not found (looked in ~/workspace/tools and PATH)')
    out_tmpl = os.path.join(workdir, 'source.%(ext)s')
    run([ytdlp, '--no-playlist', '-f',
         'b[height<=720]/bv*[height<=720]+ba/b[height<=480]/b',
         '--merge-output-format', 'mp4', '-o', out_tmpl, inp])
    files = [f for f in os.listdir(workdir) if f.startswith('source.')]
    if not files:
        raise SystemExit('yt-dlp download produced no file')
    path = os.path.join(workdir, files[0])
    title = None
    r = run([ytdlp, '--no-playlist', '--get-title', inp], check=False)
    if r.returncode == 0 and r.stdout.strip():
        title = r.stdout.strip().splitlines()[0]
    return path, (title or 'UNTITLED')

def probe(path):
    r = run(['ffprobe', '-v', 'error', '-print_format', 'json',
             '-show_streams', '-show_format', path])
    info = json.loads(r.stdout)
    v = next((s for s in info['streams'] if s['codec_type'] == 'video'), None)
    a = next((s for s in info['streams'] if s['codec_type'] == 'audio'), None)
    if not v:
        raise SystemExit('no video stream in input')
    if not a:
        raise SystemExit('no audio stream in input')
    num, den = v.get('avg_frame_rate', '24/1').split('/')
    fps = float(num) / float(den) if float(den) else 24.0
    return {'w': int(v['width']), 'h': int(v['height']), 'fps': fps,
            'dur': float(info['format']['duration'])}

# ---------------------------------------------------------------- transcribe
def transcribe(src, workdir, model_name='base.en'):
    wav = os.path.join(workdir, 'audio16k.wav')
    run(['ffmpeg', '-v', 'error', '-i', src, '-vn', '-ac', '1', '-ar', '16000',
         wav, '-y'])
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise SystemExit('faster_whisper not importable — expected it in '
                         '~/workspace/tools/fw (or set CLIP_FW_PATH)')
    # decode via stdlib wave -> float32 array (avoids PyAV version drift in
    # faster_whisper.decode_audio on this machine)
    import wave as wave_mod
    import numpy as np
    with wave_mod.open(wav, 'rb') as wf:
        pcm = wf.readframes(wf.getnframes())
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    model = WhisperModel(model_name, device='cpu', compute_type='int8')
    segments, _info = model.transcribe(audio, word_timestamps=True,
                                       beam_size=1, language='en')
    words = []
    for seg in segments:
        for w in (seg.words or []):
            t = w.word.strip()
            if t:
                words.append({'w': t, 's': round(w.start, 3), 'e': round(w.end, 3)})
    if not words:
        raise SystemExit('transcription produced no words')
    log(f'transcribed {len(words)} words '
        f'({words[0]["s"]:.1f}s–{words[-1]["e"]:.1f}s)')
    return words

# ---------------------------------------------------------------- scoring
NUM_WORDS = {'hundred', 'thousand', 'million', 'billion', 'trillion'}
SUPERLATIVES = {'best', 'worst', 'biggest', 'smallest', 'fastest', 'slowest',
                'first', 'last', 'greatest', 'strongest', 'hardest', 'easiest',
                'most', 'least', 'never', 'always', 'every', 'none'}
CLAIM_WORDS = {'secret', 'truth', 'mistake', 'nobody', 'everybody', 'everyone',
               'no', 'stop', 'wrong', 'lie', 'lies', 'guarantee', 'proven',
               'need', 'must', 'have'}
STORY_WORDS = {'started', 'began', 'went', 'turned', 'lost', 'failed',
               'broke', 'built', 'grew', 'quit', 'fired', 'bankrupt',
               'homeless', 'died', 'survived', 'escaped', 'became'}
INTRO_WORDS = {'welcome', 'hello', 'introducing', 'episode', 'podcast',
               'sponsor', 'subscribe'}
MONEY_RE = re.compile(r'(\$|dollars?|bucks|percent|%|revenue|profit|money|'
                      r'cash|salary|income|price|cost)', re.I)

def _tok(w):
    return w.lower().strip('.,!?;:"()')

def score_span(span):
    """Transparent hook-strength score for a list of word dicts."""
    if not span:
        return -1e9, {}
    text = ' '.join(w['w'] for w in span)
    low = [_tok(w['w']) for w in span]
    feats = {}
    nums = sum(1 for t in low if any(c.isdigit() for c in t) or t in NUM_WORDS)
    feats['numbers'] = nums * 3.0
    feats['money'] = len(MONEY_RE.findall(text)) * 2.0
    feats['superlatives'] = min(sum(t in SUPERLATIVES for t in low), 4) * 1.2
    feats['claims'] = min(sum(t in CLAIM_WORDS for t in low), 4) * 1.2
    feats['story'] = min(sum(t in STORY_WORDS for t in low), 3) * 1.6
    feats['question'] = 2.0 if '?' in text else 0.0
    dur = span[-1]['e'] - span[0]['s']
    wps = len(span) / max(dur, 0.1)
    feats['density'] = 1.5 if 2.0 <= wps <= 3.4 else (0.0 if wps >= 1.4 else -2.0)
    gaps = [span[i + 1]['s'] - span[i]['e'] for i in range(len(span) - 1)]
    feats['pauses'] = -2.0 if gaps and max(gaps) > 1.2 else 0.0
    feats['clean_end'] = 2.0 if span[-1]['w'].rstrip().endswith(('.', '!', '?')) else 0.0
    feats['intro_penalty'] = -3.0 * sum(t in INTRO_WORDS for t in low[:6])
    return sum(feats.values()), feats

def split_sentences(words):
    sents, cur = [], []
    for w in words:
        cur.append(w)
        if w['w'].rstrip().endswith(('.', '!', '?')):
            sents.append(cur); cur = []
    if cur:
        sents.append(cur)
    return sents

def pick_window(words, target):
    """Sliding windows over word boundaries; returns (candidates, chosen)."""
    cands = []
    n = len(words)
    for i in range(n):
        s = words[i]['s']
        j = i
        while j + 1 < n and words[j + 1]['e'] - s <= target * 1.15:
            j += 1
        span = words[i:j + 1]
        dur = span[-1]['e'] - s
        if dur < target * 0.75:
            continue
        sc, feats = score_span(span)
        # prefer ending exactly on a sentence boundary near the target
        if abs(dur - target) <= target * 0.12:
            sc += 0.8
        cands.append({'i': i, 'j': j, 's': s, 'e': span[-1]['e'],
                      'score': round(sc, 2), 'feats': feats, 'span': span})
        if words[-1]['e'] - s < target * 0.75:
            break
    if not cands:
        span = words
        sc, feats = score_span(span)
        cands = [{'i': 0, 'j': n - 1, 's': span[0]['s'], 'e': span[-1]['e'],
                  'score': round(sc, 2), 'feats': feats, 'span': span}]
    cands.sort(key=lambda c: -c['score'])
    # de-duplicate near-identical windows for the top-3 printout
    top, seen = [], []
    for c in cands:
        if all(abs(c['s'] - s0) > 4.0 for s0 in seen):
            top.append(c); seen.append(c['s'])
        if len(top) == 3:
            break
    return top, cands[0]

def peak_sentence(span):
    sents = [s for s in split_sentences(span) if len(s) >= 4]
    if not sents:
        return span
    return max(sents, key=lambda s: score_span(s)[0])

def derive_hook(peak):
    """<=9-word hook from the peak sentence: pick the contiguous span with
    the strongest token weights (numbers/claims/story words heavy, filler
    light, glue-word edges penalised). Returns [(TEXT, is_amber), ...]."""
    raw = [w['w'] for w in peak]
    clean = [re.sub(r'[.,!?;:"()]', '', t) for t in raw]
    clean = [t for t in clean if t]
    joined = []
    for t in clean:
        if joined and t.startswith('-'):
            joined[-1] += t
        else:
            joined.append(t)
    clean = joined
    FILLER = {'the', 'a', 'an', 'of', 'to', 'at', 'in', 'on', 'and', 'but',
              'so', 'just', 'like', 'actually', 'really', 'very', 'then'}
    EDGE_BAD = FILLER | {'able', 'been', 'be', 'what', 'how', 'when', 'who',
                         'was', 'were', 'is', 'are', 'had', 'has', 'have',
                         'do', 'does', 'did', 'gonna', 'wanna'}
    def is_hot(t):
        tl = t.lower()
        return (any(c.isdigit() for c in t) or tl in NUM_WORDS
                or tl in SUPERLATIVES or MONEY_RE.search(t))
    def weight(t):
        tl = t.lower()
        if is_hot(t):
            return 3.0
        if tl in STORY_WORDS or tl in CLAIM_WORDS:
            return 2.0
        if tl in FILLER:
            return 0.2
        return 1.0
    toks = clean
    if len(toks) > 9:
        best, best_sc = None, -1e9
        for L in range(9, 5, -1):
            for i in range(0, len(toks) - L + 1):
                span = toks[i:i + L]
                sc = sum(weight(t) for t in span) + 0.3 * L
                if any(any(c.isdigit() for c in t) for t in span):
                    sc += 1.0          # number-anchor bonus
                if span[0].lower() in EDGE_BAD:
                    sc -= 1.5
                if span[-1].lower() in EDGE_BAD:
                    sc -= 1.5
                if any(c.isdigit() for c in span[0]):
                    sc -= 1.0          # don't open mid-phrase on a bare number
                if sc > best_sc:
                    best, best_sc = span, sc
        toks = best
    # drop a filler 'like' sitting next to a number ("age of like 61")
    toks = [t for k, t in enumerate(toks)
            if not (t.lower() == 'like'
                    and ((k + 1 < len(toks) and any(c.isdigit() for c in toks[k + 1]))
                         or (k > 0 and any(c.isdigit() for c in toks[k - 1]))))]
    return [(t.upper(), is_hot(t)) for t in toks]

# ------------------------------------------------------- style registers
# A register varies EXPRESSION only: hook treatment, caption accent
# behaviour, attribution and end-card styling. Timings, caption
# chunking, framing and the motion rules (one push-in that settles,
# one punch-in that holds, no oscillation) are identical in every
# register. 'standard' IS the approved recipe, unchanged:
#   standard — stacked hook, hot words in the brand accent, drop
#              shadow + slide-in; tracked-caps attribution; navy end
#              card with the kit's logo artwork
#   bar      — hook lines sit on solid navy bars; attribution on a
#              navy pill; end card adds the brand name under the logo
#   minimal  — plain bold all-white hook (no hot-word colour, no
#              slide, thin outline instead of shadow); captions mark
#              the current word with an accent underline instead of a
#              colour change; no attribution; paper end card
REGISTERS = ('standard', 'bar', 'minimal')

def resolve_style(name, seed):
    """Resolve --style to (register, seed). 'random' picks from a
    seeded RNG — the register AND the seed are recorded in the build
    log, so any clip can be reproduced exactly."""
    if name == 'random':
        if seed is None:
            seed = int(time.time()) % 1000000
        return random.Random(seed).choice(list(REGISTERS)), seed
    return name, seed

# ------------------------------------------------------------ brand kits
def _hex_rgb(hx):
    m = re.fullmatch(r'#?([0-9a-fA-F]{6})', (hx or '').strip())
    if not m:
        raise SystemExit(f'brand kit: bad accent colour {hx!r} '
                         '(want #RRGGBB)')
    v = m.group(1)
    return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16), 255)

def load_brand(path):
    """Load a brand kit JSON: {name, logo, accent, end_card_name}
    plus optional logo_icon (a bare mark, preferred by the styled
    end cards so the drawn name never duplicates a full lockup).
    Logo paths resolve relative to the kit file. The kit drives the
    end card, the hook accent, the progress-bar fill and the
    caption current-word highlight."""
    try:
        with open(path) as f:
            kit = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f'brand kit unreadable: {path} ({e})')
    base = os.path.dirname(os.path.abspath(path))

    def resolve(p):
        if not p:
            return None
        return p if os.path.isabs(p) else os.path.normpath(
            os.path.join(base, p))
    logo = resolve(kit.get('logo'))
    if not logo or not os.path.isfile(logo):
        raise SystemExit(f"brand kit {path}: logo not found: "
                         f"{kit.get('logo')!r}")
    accent = _hex_rgb(kit.get('accent', '#FFB020'))
    icon = resolve(kit.get('logo_icon'))
    return {'name': kit.get('name') or 'Untitled',
            'logo': logo,
            'logo_icon': icon if icon and os.path.isfile(icon)
            else None,
            'accent': accent,
            'accent_hex': '#' + ''.join(f'{c:02X}' for c in accent[:3]),
            'end_card_name': kit.get('end_card_name')
                             or kit.get('name') or '',
            'file': os.path.abspath(path)}

DEFAULT_BRAND = os.path.join(ENGINE_DIR, 'brands',
                             'podcast-clips-that-convert.json')

# ------------------------------------------- corrections + caption edits
def apply_corrections(words, cmap):
    """Apply a word->replacement map to transcript words, in place.
    Matching ignores case and trailing punctuation; the replacement
    inherits both. This is the per-show correction memory: fix a
    misheard name once (e.g. {"Monocero": "Manassero"}) and every
    later render of that show gets it right. Returns
    {normalised_wrong: count} actually applied, for the build log."""
    norm = {}
    for k, v in cmap.items():
        key = k.strip().lower().strip('.,!?;:"()')
        if key:
            norm[key] = v
    applied = {}
    for wd in words:
        m = re.match(r'^(.*?)([.,!?;:]*)$', wd['w'])
        core, punct = m.group(1), m.group(2)
        if core.lower() in norm:
            rep = norm[core.lower()]
            if core[:1].isupper():
                rep = rep[:1].upper() + rep[1:]
            if len(core) > 1 and core.isupper():
                rep = rep.upper()
            wd['w'] = rep + punct
            applied[core.lower()] = applied.get(core.lower(), 0) + 1
    return applied

def parse_captions_file(path):
    """--captions-file: an .srt or a .json list of
    {start, end, text} cues on the CLIP timeline.
    Returns [(start, end, text), ...]."""
    if not os.path.isfile(path):
        raise SystemExit(f'captions file not found: {path}')
    if path.lower().endswith('.json'):
        try:
            data = json.load(open(path))
            return [(float(c['start']), float(c['end']),
                     str(c['text'])) for c in data]
        except (json.JSONDecodeError, KeyError, TypeError,
                ValueError) as e:
            raise SystemExit(f'captions file {path}: bad JSON cue '
                             f'list ({e})')
    cues = []
    text = open(path, encoding='utf-8-sig').read()
    for block in re.split(r'\n\s*\n', text):
        m = re.search(r'(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*'
                      r'(\d+):(\d+):(\d+)[,.](\d+)', block)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        s = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000.0
        e = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000.0
        body = ' '.join(l.strip() for l in block[m.end():].splitlines()
                        if l.strip() and not l.strip().isdigit())
        if body:
            cues.append((s, e, body))
    if not cues:
        raise SystemExit(f'captions file {path}: no cues parsed')
    return cues

def _distribute(toks, t0, t1):
    """Give tokens timings across [t0, t1] proportional to length."""
    weights = [max(len(t), 1) for t in toks]
    total = float(sum(weights))
    out, cur = [], t0
    for t, wgt in zip(toks, weights):
        nxt = cur + (t1 - t0) * wgt / total
        out.append({'w': t, 's': round(cur, 3), 'e': round(nxt, 3)})
        cur = nxt
    if out:
        out[-1]['e'] = round(t1, 3)
    return out

def apply_caption_override(span, ov_cues, ws):
    """Burn the user's edited words on the original word timings —
    the fix-a-word-and-re-render path (no re-transcription). Each
    transcript word is claimed by the override cue it overlaps most;
    the cue's text replaces its claimed words — word-for-word on the
    original timings when the counts match, otherwise distributed
    across the claimed span by token length. A cue claiming no words
    inserts its text across its own interval; unclaimed words pass
    through untouched. The merged stream is re-chunked by the usual
    caption builder, so the output .srt is exactly what was burned.
    Returns (new_span_in_source_times, stats)."""
    clip = [{'w': w['w'], 's': w['s'] - ws, 'e': w['e'] - ws}
            for w in span]
    owner = {}
    for i, wd in enumerate(clip):
        best, best_ov = None, 0.0
        for ci, (cs, ce, _t) in enumerate(ov_cues):
            ov = min(wd['e'], ce) - max(wd['s'], cs)
            if ov > best_ov:
                best, best_ov = ci, ov
        if best is not None:
            owner[i] = best
    by_cue = {}
    for i, ci in owner.items():
        by_cue.setdefault(ci, []).append(i)
    inserts, n_replaced = [], 0
    for ci, (cs, ce, txt) in enumerate(ov_cues):
        toks = txt.split()
        if not toks:
            continue
        members = sorted(by_cue.get(ci, []))
        if members:
            if len(toks) == len(members):
                new = [{'w': t, 's': clip[i]['s'], 'e': clip[i]['e']}
                       for t, i in zip(toks, members)]
            else:
                new = _distribute(toks, clip[members[0]]['s'],
                                  clip[members[-1]]['e'])
            inserts.append((members[0], new))
            n_replaced += len(members)
        else:
            inserts.append((len(clip), _distribute(toks, cs, ce)))
    at = {}
    for idx, new_words in inserts:
        at.setdefault(idx, []).extend(new_words)
    merged = []
    for i, wd in enumerate(clip):
        if i in at:
            merged.extend(at[i])
        if i not in owner:
            merged.append(wd)
    if len(clip) in at:
        merged.extend(at[len(clip)])
    merged.sort(key=lambda w: w['s'])
    out = [{'w': w['w'], 's': w['s'] + ws, 'e': w['e'] + ws}
           for w in merged]
    return out, {'cues': len(ov_cues), 'words_replaced': n_replaced,
                 'words_total': len(out)}

# ---------------------------------------------------------------- framing
def extract_stills(src, times, workdir, tag, width=None):
    paths = []
    for k, t in enumerate(times):
        p = os.path.join(workdir, f'{tag}_{k:03d}.jpg')
        vf = f'scale={width}:-2' if width else 'scale=iw:ih'
        run(['ffmpeg', '-v', 'error', '-ss', f'{t:.2f}', '-i', src,
             '-frames:v', '1', '-vf', vf, '-q:v', '4', p, '-y'])
        if os.path.isfile(p):
            paths.append((t, p))
    return paths

def _load_gray(path):
    from PIL import Image
    import numpy as np
    return np.asarray(Image.open(path).convert('L'), dtype=np.float32)

def detect_faces(stills):
    """Returns list of (t, [(x,y,w,h), ...]) in still-pixel coords, or None
    if no face backend is available. Backend: OpenCV FaceDetectorYN (YuNet)
    — the OpenCV 5 headless wheel on this machine ships no
    CascadeClassifier, so YuNet (model in assets/) is the detector."""
    try:
        import cv2
    except ImportError:
        return None
    model = os.path.join(ASSETS, 'face_detection_yunet.onnx')
    if not (hasattr(cv2, 'FaceDetectorYN') and os.path.isfile(model)):
        return None
    det = cv2.FaceDetectorYN.create(model, '', (320, 320),
                                    score_threshold=0.6, nms_threshold=0.3,
                                    top_k=50)
    out = []
    for t, p in stills:
        img = cv2.imread(p)
        if img is None:
            continue
        det.setInputSize((img.shape[1], img.shape[0]))
        _rv, faces = det.detect(img)
        boxes = []
        if faces is not None:
            for f in faces:
                boxes.append(tuple(int(round(v)) for v in f[:4]))
        out.append((t, boxes))
    return out

def seam_x(stills, lo, hi):
    """Strongest vertical edge column in [lo,hi] (panel boundary heuristic)."""
    import numpy as np
    acc = None
    for _t, p in stills:
        g = _load_gray(p)
        edge = np.abs(np.diff(g, axis=1)).mean(axis=0)
        acc = edge if acc is None else acc + edge
    if acc is None:
        return None
    seg = acc[lo:hi]
    return lo + int(np.argmax(seg)), float(seg.max()), float(np.median(acc))

def classify_passages(src, stills, det, scale, w, h, ws, we, workdir):
    """Per-passage SOURCE layout over the window. A programme may cut
    between full-screen and split-screen compositions inside one clip
    window (Modern CTO Ep. #984 does exactly this); a single global
    layout decision then frames the split passage with a crop sized for
    the full-screen face, straddling the seam and showing NEITHER face
    (the 2026-11-13 overlay-test defect). So: classify every sampled
    still, refine class boundaries at 0.4s steps, absorb blips, and
    return [{s, e, kind: 'split'|'full'|'none', split, faces}] with
    times/coords in SOURCE units. [] when no face backend ran."""
    import numpy as np
    if not det:
        return []

    def classify_faces(fs, t, path):
        if not fs:
            return 'none', {}, None
        L = [f for f in fs if f[0] + f[2] / 2 < w * 0.5]
        R = [f for f in fs if f[0] + f[2] / 2 > w * 0.5]
        if L and R:
            fl = max(L, key=lambda f: f[2] * f[3])
            fr = max(R, key=lambda f: f[2] * f[3])
            seam = None
            lo_s, hi_s = int((fl[0] + fl[2]) * scale), int(fr[0] * scale)
            if hi_s - lo_s >= 8 and path:
                sx, _st, _me = seam_x([(t, path)], lo_s, hi_s)
                if sx is not None:
                    seam = sx / scale
            if seam is None:
                seam = (fl[0] + fl[2] + fr[0]) / 2
            seam = min(max(seam, fl[0] + fl[2] * 0.9),
                       fr[0] + fr[2] * 0.1)
            return 'split', {'L': fl, 'R': fr}, float(seam)
        return 'full', {'C': max(fs, key=lambda f: f[2] * f[3])}, None

    def rows_for(still_list):
        d = detect_faces(still_list)
        if d is None:
            return []
        m = {round(t, 3): fs for t, fs in d}
        out = []
        for t, p in still_list:
            fs = [tuple(v / scale for v in f)
                  for f in m.get(round(t, 3), [])]
            k, fc, sm = classify_faces(fs, t, p)
            out.append([t, k, fc, sm])
        return out

    rows = rows_for(stills)
    if not rows:
        return []
    # ---- refine boundaries between differing classes (0.4s probes)
    bounds = []
    for i in range(len(rows) - 1):
        t0, k0 = rows[i][0], rows[i][1]
        t1, k1 = rows[i + 1][0], rows[i + 1][1]
        if k0 == k1:
            continue
        b = (t0 + t1) / 2
        probes = [round(t0 + 0.4 * j, 2)
                  for j in range(1, 6) if t0 + 0.4 * j < t1 - 0.05]
        if probes:
            pst = extract_stills(src, probes, workdir, 'pb',
                                 width=int(w * scale))
            prows = rows_for(pst)
            prev_t = t0
            for pt, pk, _pf, _ps in prows:
                if pk != k0:
                    b = (prev_t + pt) / 2
                    break
                prev_t = pt
            else:
                if prows:
                    b = (prev_t + t1) / 2
        bounds.append(b)
    # ---- fine refinement (0.05s probes): the coarse boundary above is
    # the midpoint between probes 0.4s apart, so it can sit ~0.2s from
    # the source's real composition cut. The edit plan switches crop
    # families exactly at the boundary, so a late boundary keeps the
    # OLD passage's panel crop running over the NEW composition for
    # the whole error — the faceless flash Pupa caught at clip 22.5s
    # in the overlay-v2 build (edge mapped at src 121.93, the source
    # actually cuts at ~121.75). Re-probe each boundary densely and
    # place it at the first CONFIRMED instant of the incoming class
    # (the first probe of the first run of 3 consecutive incoming-
    # class probes): a probe time is a real source frame showing the
    # new composition, while a midpoint between probes can quantise
    # to an output frame whose source frame is still the old
    # composition (measured: one seam-slice frame at the handoff).
    # If the change is a genuine dissolve (a contiguous run of
    # high frame-difference steps starting at the flip), the boundary
    # moves to the blend's end instead — the incoming face is only a
    # ghost until then.
    if bounds:
        try:
            half_frame = 0.5 / float(probe(src)['fps'])
        except Exception:
            half_frame = 0.5 / 30.0
        refined = []
        for b in bounds:
            left_rows = [r for r in rows if r[0] <= b]
            if not left_rows:
                refined.append(b)
                continue
            k_left, prev_t = left_rows[-1][1], left_rows[-1][0]
            lo_t = max(ws + 0.05, b - 0.5)
            hi_t = min(we - 0.05, b + 0.5)
            probes = [round(lo_t + 0.05 * j, 2)
                      for j in range(int((hi_t - lo_t) / 0.05) + 1)]
            probes = [t for t in probes
                      if all(abs(t - r[0]) > 0.011 for r in rows)]
            new_b = b
            if probes:
                pst = extract_stills(src, probes, workdir, 'fb',
                                     width=int(w * scale))
                prows = [(pt, pk) for pt, pk, _pf, _ps in rows_for(pst)]
                run_at = None
                for j in range(len(prows) - 2):
                    if all(prows[j + m][1] != k_left for m in range(3)):
                        run_at = j
                        break
                if run_at is not None:
                    # Boundary = the first CONFIRMED instant of the
                    # new class (the stable run's first probe), not
                    # the midpoint between probes: the midpoint can
                    # quantise to an output frame whose source frame
                    # is still the old composition (measured: the
                    # split->full edge probed split at 121.73 and
                    # full at 121.78; the midpoint 121.755 landed the
                    # new piece's frame 0 on the last split frame —
                    # one seam-slice frame). A probe time is a real
                    # source frame showing the new class, so starting
                    # the new piece there is correct-side by
                    # construction. Shift back half a source frame:
                    # the probe certifies the source frame ACTIVE at
                    # that instant, which started up to one frame
                    # earlier; without the shift the OLD piece's last
                    # output frame samples that same first new frame
                    # through the old crop (measured failures at both
                    # passage edges of the feature-round build).
                    new_b = prows[run_at][0] - half_frame
                else:
                    for pt, pk in prows:
                        if pk != k_left:
                            new_b = pt - half_frame
                            break
                        prev_t = pt
                # Dissolve-end adjustment: class labels can stabilise
                # BEFORE a fast dissolve visually completes (YuNet
                # stops seeing the outgoing faces while the incoming
                # face is still a ghost — measured at the Modern CTO
                # 22.4s handoff: labels stable at 121.75, blend
                # running to ~121.9, and the new crop showed a
                # faceless slice for ~2 frames). While a blend runs,
                # consecutive probe frames differ globally far above
                # talking-head motion. If the step INTO the new class
                # is part of a contiguous run of such hot steps, the
                # boundary moves to that run's end. A hard cut is a
                # single hot step, so this reduces to the first-flip
                # placement there; a later unrelated gesture is not
                # contiguous with the flip step and cannot extend it.
                flip_at = next((j for j, (_pt, pk) in enumerate(prows)
                                if pk != k_left), None)
                if flip_at and len(pst) == len(prows) and len(pst) > 3:
                    grays = [_load_gray(p) for _t, p in pst]
                    diffs = [float(np.abs(grays[j + 1] - grays[j]).mean())
                             for j in range(len(grays) - 1)]
                    thr = max(4.0, 2.0 * float(np.median(diffs)))
                    hot = {j for j, d in enumerate(diffs) if d > thr}
                    if flip_at - 1 in hot:
                        end = flip_at - 1
                        while end + 1 in hot:
                            end += 1
                        # >=2 hot steps = a real blend; a single hot
                        # step is just a hard cut — no adjustment.
                        if end > flip_at - 1 and end + 1 < len(prows):
                            cand = prows[end + 1][0] - 0.025
                            if cand > new_b:
                                log(f'  dissolve at {b:.2f}: boundary '
                                    f'{new_b:.2f}->{cand:.2f} '
                                    f'(blend end)')
                                new_b = cand
            refined.append(round(new_b, 3))
        if refined != bounds:
            log('passage boundaries refined: '
                + ', '.join(f'{a:.2f}->{b:.2f}'
                            for a, b in zip(bounds, refined)
                            if abs(a - b) > 0.005))
        bounds = refined
    # ---- group rows into passages
    runs = []
    bi = 0
    for i, r in enumerate(rows):
        if i == 0:
            runs.append([r])
        elif bi < len(bounds) and r[0] > bounds[bi]:
            runs.append([r]); bi += 1
        else:
            runs[-1].append(r)
    med = lambda bs, i: float(np.median([b[i] for b in bs]))
    passages = []
    for gi, run in enumerate(runs):
        s = ws if gi == 0 else bounds[gi - 1]
        e = we if gi == len(runs) - 1 else bounds[gi]
        kind = run[0][1]
        faces, seam = {}, None
        if kind == 'split':
            for side in ('L', 'R'):
                bs = [r[2][side] for r in run if side in r[2]]
                if bs:
                    faces[side] = tuple(med(bs, i) for i in range(4))
            ss = [r[3] for r in run if r[3] is not None]
            if ss:
                seam = float(np.median(ss))
        elif kind == 'full':
            bs = [r[2]['C'] for r in run if 'C' in r[2]]
            if bs:
                faces['C'] = tuple(med(bs, i) for i in range(4))
        passages.append({'s': round(float(s), 2), 'e': round(float(e), 2),
                         'kind': kind, 'split': seam, 'faces': faces})
    # ---- absorb blips: <1.2s passages merge into the longer neighbour;
    # short 'none' gaps between two passages of the same kind take it
    changed = True
    while changed and len(passages) > 1:
        changed = False
        for i, p in enumerate(passages):
            dur = p['e'] - p['s']
            same_flank = (0 < i < len(passages) - 1
                          and passages[i - 1]['kind']
                          == passages[i + 1]['kind'] != p['kind'])
            if dur < 1.2 or (p['kind'] == 'none' and dur < 3.0
                             and same_flank):
                if same_flank and p['kind'] == 'none':
                    j = i - 1
                elif i == 0:
                    j = 1
                elif i == len(passages) - 1:
                    j = i - 1
                else:
                    j = i - 1 if (passages[i - 1]['e']
                                  - passages[i - 1]['s']
                                  >= passages[i + 1]['e']
                                  - passages[i + 1]['s']) else i + 1
                nb = passages[j]
                nb['s'], nb['e'] = min(nb['s'], p['s']), max(nb['e'], p['e'])
                passages.pop(i)
                changed = True
                break
    # ---- borrow any missing face boxes from the nearest same-kind
    # passage; a passage that still lacks a face it needs is unusable
    for i, p in enumerate(passages):
        need = ('L', 'R') if p['kind'] == 'split' else \
               ('C',) if p['kind'] == 'full' else ()
        for side in need:
            if side not in p['faces']:
                donors = sorted(
                    (q for q in passages
                     if q['kind'] == p['kind'] and side in q['faces']),
                    key=lambda q: abs(q['s'] - p['s']))
                if donors:
                    p['faces'][side] = donors[0]['faces'][side]
                    if p['kind'] == 'split' and p['split'] is None:
                        p['split'] = donors[0]['split']
        if any(side not in p['faces'] for side in need):
            p['kind'] = 'none'
    for p in passages:
        p['faces'] = {k: tuple(float(v) for v in box)
                      for k, box in p['faces'].items()}
    return passages

def analyse_layout(src, w, h, ws, we, workdir):
    """Decide layout over the chosen window: returns dict with 'kind'
    ('two'|'single'), 'split' (source px), 'faces' {side: median box},
    'method' (how it was decided). Coords in SOURCE pixels."""
    import numpy as np
    times = [ws + k * 2.0 for k in range(int((we - ws) // 2) + 1)]
    scale = min(1.0, 800 / w)
    stills = extract_stills(src, times, workdir, 'lay', width=int(w * scale))
    sw = int(w * scale)
    det = detect_faces(stills)
    passages = classify_passages(src, stills, det, scale, w, h, ws, we,
                                 workdir) if det else []
    if passages:
        log('source passages: ' + ', '.join(
            f"{p['s'] - ws:.1f}–{p['e'] - ws:.1f}s {p['kind']}"
            for p in passages))
    facesL, facesR, facesC = [], [], []
    if det:
        for _t, faces in det:
            for f in faces:
                cx = (f[0] + f[2] / 2) / scale
                box = tuple(v / scale for v in f)
                if cx < w * 0.42:
                    facesL.append(box)
                elif cx > w * 0.58:
                    facesR.append(box)
                else:
                    facesC.append(box)
    n_stills = max(len(stills), 1)
    if len(facesL) >= n_stills * 0.4 and len(facesR) >= n_stills * 0.4:
        med = lambda bs: tuple(float(np.median([b[i] for b in bs])) for i in range(4))
        fl, fr = med(facesL), med(facesR)
        # the panel seam must lie BETWEEN the two faces — search that band
        # (in STILL pixel coords, then convert back to source coords)
        lo_s, hi_s = int((fl[0] + fl[2]) * scale), int(fr[0] * scale)
        if hi_s - lo_s >= 8:
            sx, _st, _me = seam_x(stills, lo_s, hi_s)
            split = sx / scale if sx else (fl[0] + fl[2] + fr[0]) / 2
        else:
            split = (fl[0] + fl[2] + fr[0]) / 2
        split = min(max(split, fl[0] + fl[2] * 0.9), fr[0] + fr[2] * 0.1)
        return {'kind': 'two', 'split': float(split),
                'faces': {'L': fl, 'R': fr}, 'method': 'yunet-two-clusters',
                'passages': passages}
    allf = facesL + facesR + facesC
    if allf:
        med = tuple(float(np.median([b[i] for b in allf])) for i in range(4))
        return {'kind': 'single', 'split': None,
                'faces': {'C': med}, 'method': 'yunet-single',
                'passages': passages}
    # ---- fallback: no faces found (or no cv2) — seam + assumed face boxes
    sx, strength, med_edge = seam_x(stills, int(sw * 0.35), int(sw * 0.65))
    if sx and strength > 3.0 * max(med_edge, 1e-6):
        split = sx / scale
        fw_ = split * 0.30
        return {'kind': 'two', 'split': float(split), 'method': 'seam-fallback',
                'faces': {'L': (split * 0.5 - fw_ / 2, h * 0.16, fw_, fw_ * 1.15),
                          'R': (split + (w - split) * 0.5 - fw_ / 2, h * 0.16,
                                fw_, fw_ * 1.15)}}
    fw_ = w * 0.26
    return {'kind': 'single', 'split': None, 'method': 'centre-fallback',
            'faces': {'C': (w / 2 - fw_ / 2, h * 0.15, fw_, fw_ * 1.15)}}

def active_speaker_turns(src, layout, ws, we, workdir):
    """Two-panel only: mouth-region frame-diff motion per panel at 4fps,
    smoothed; returns [(clip_rel_time, 'L'|'R')] switch points, switches
    aligned to just BEFORE the incoming speaker's first word (needs words)."""
    import numpy as np
    from PIL import Image
    dur = we - ws
    mdir = tempfile.mkdtemp(prefix='motion-', dir=workdir)
    run(['ffmpeg', '-v', 'error', '-ss', f'{ws:.2f}', '-i', src, '-t', f'{dur:.2f}',
         '-vf', 'fps=4,scale=320:-2', os.path.join(mdir, 'm%04d.png'), '-y'])
    frames = sorted(f for f in os.listdir(mdir) if f.endswith('.png'))
    if len(frames) < 8:
        return []
    mw = Image.open(os.path.join(mdir, frames[0])).width
    mh = Image.open(os.path.join(mdir, frames[0])).height
    srcw = layout['_srcw']
    kx, ky = mw / srcw, mh / layout['_srch']
    def region(side, y0f, y1f, x0f=0.20, x1f=0.80):
        fx, fy, fw_, fh = layout['faces'][side]
        return (int((fx + fw_ * x0f) * kx), int((fy + fh * y0f) * ky),
                int((fx + fw_ * x1f) * kx), int((fy + fh * y1f) * ky))
    mouth = {s: region(s, 0.60, 0.98) for s in ('L', 'R')}
    brow = {s: region(s, 0.02, 0.32) for s in ('L', 'R')}
    # audio gate: per-frame (0.25s) RMS of the window audio, normalised —
    # mouth motion only counts while somebody is actually speaking
    import wave as wave_mod
    awav = os.path.join(mdir, 'win_audio.wav')
    run(['ffmpeg', '-v', 'error', '-ss', f'{ws:.2f}', '-i', src,
         '-t', f'{dur:.2f}', '-vn', '-ac', '1', '-ar', '8000', awav, '-y'])
    with wave_mod.open(awav, 'rb') as wf:
        pcm = np.frombuffer(wf.readframes(wf.getnframes()),
                            dtype=np.int16).astype(np.float32) / 32768.0
    hop = 8000 // 4
    rms = np.array([float(np.sqrt((pcm[i * hop:(i + 1) * hop] ** 2).mean()))
                    if len(pcm[i * hop:(i + 1) * hop]) else 0.0
                    for i in range(len(frames))])
    if rms.max() > 0:
        rms = rms / rms.max()
    prev, act = None, {'L': [], 'R': []}
    for f in frames:
        g = np.asarray(Image.open(os.path.join(mdir, f)).convert('L'),
                       dtype=np.float32)
        if prev is not None:
            d = np.abs(g - prev)
            for side in ('L', 'R'):
                x0, y0, x1, y1 = mouth[side]
                bx0, by0, bx1, by1 = brow[side]
                # mouth-specific motion: talking moves the mouth MORE than
                # the brow; nodding/gesturing moves both together
                act[side].append(max(float(d[y0:y1, x0:x1].mean())
                                     - 0.7 * float(d[by0:by1, bx0:bx1].mean()),
                                     0.0))
        prev = g
    n = len(act['L'])
    if n < 8:
        return []
    gate = rms[1:n + 1] if len(rms) > n else np.pad(rms, (0, n - len(rms)))
    for side in ('L', 'R'):
        act[side] = (np.array(act[side]) * gate).tolist()
    def smooth(v, k=4):
        return np.convolve(v, np.ones(k) / k, mode='same')
    aL, aR = smooth(np.array(act['L'])), smooth(np.array(act['R']))
    # block-vote: 0.5s blocks; CONSERVATIVE switching — a cut to the other
    # panel needs a 1.45x motion margin and a >=2.5s run. Mouth-motion
    # diarisation is a heuristic (measured ~2/3 of 0.5s blocks correct on
    # the Best Ever test window, where one speaker's mic occludes his
    # mouth); a late switch reads as a reaction shot, a wrong one does not.
    blocks = []
    for b in range(n // 2):
        ml = float(aL[2 * b:2 * b + 2].mean())
        mr = float(aR[2 * b:2 * b + 2].mean())
        blocks.append('L' if ml > mr * 1.45 else
                      ('R' if mr > ml * 1.45 else None))
    last = next((x for x in blocks if x), 'L')
    filled = []
    for x in blocks:
        if x:
            last = x
        filled.append(last)
    runs = []
    for i, x in enumerate(filled):
        if runs and runs[-1][1] == x:
            runs[-1][2] = i
        else:
            runs.append([i, x, i])
    changed = True
    while changed and len(runs) > 1:
        changed = False
        for i, r in enumerate(runs):
            if r[2] - r[0] + 1 < 5:
                if i == 0:
                    runs[1][0] = r[0]
                else:
                    runs[i - 1][2] = r[2]
                runs.pop(i)
                changed = True
                break
        merged = []
        for r in runs:
            if merged and merged[-1][1] == r[1]:
                merged[-1][2] = r[2]
            else:
                merged.append(r)
        runs = merged
    if not runs:
        return []
    return [(0.0, runs[0][1])] + [(r[0] * 0.5, r[1]) for r in runs[1:]]

def fit_crop(face, region, k_h, out_ratio=9 / 16, eye_frac=0.33):
    """Crop box (x,y,w,h) in source px at out_ratio aspect: height =
    face_h * k_h, eyes on the eye_frac line, fitted inside region
    (x,y,w,h)."""
    fx, fy, fw_, fh = face
    rx, ry, rw, rh = region
    ch = fh * k_h
    cw = ch * out_ratio
    if cw > rw:
        cw = rw; ch = cw / out_ratio
    if ch > rh:
        ch = rh; cw = ch * out_ratio
    eyes_y = fy + fh * 0.4
    cy = eyes_y - ch * eye_frac
    cx = (fx + fw_ / 2) - cw / 2
    cx = min(max(cx, rx), rx + rw - cw)
    cy = min(max(cy, ry), ry + rh - ch)
    return (int(round(cx)), int(round(cy)), int(round(cw)), int(round(ch)))

PANEL_W, PANEL_H = OUT_W, OUT_H // 2          # split panels: 1080x960 each
SEAM_Y = PANEL_H                              # divider line sits at y=960
NAVY = '0x0C1425'                             # brand ink navy (end card)
# Split layout framing (Pupa's rule): a STEADY, wider "own camera"
# framing — head-and-shoulders, panels equal and calm for the whole
# clip. No punch-in and no zoom/push of any kind in split mode.
SPLIT_K = 4.0

def expand_crop(crop, region, f):
    """Grow a crop box by factor f around its centre, clamped (by shift,
    never shrink) inside region — the settled crop always stays fully
    inside the expanded one, so a push-in between them never samples
    outside the wide box."""
    x, y, w, h = crop
    rx, ry, rw, rh = region
    nw, nh = min(w * f, rw), min(h * f, rh)
    cx, cy = x + w / 2, y + h / 2
    nx = min(max(cx - nw / 2, rx), rx + rw - nw)
    ny = min(max(cy - nh / 2, ry), ry + rh - nh)
    return (int(round(nx)), int(round(ny)), int(round(nw)), int(round(nh)))

def plan_split_panels(layout, k=SPLIT_K):
    """Split layout: L speaker -> TOP panel, R speaker -> BOTTOM panel,
    fixed for the whole clip. Each panel is face-cropped at panel aspect
    (1080x960) with the eyes lower (0.44) than the cutaway anchor so the
    hook keeps a clear strip at the top and the seam caption band stays
    clear of the top face's mouth and the bottom face's eyes. Framing
    is the steady SPLIT_K 'own camera' width — per Pupa's split rule
    there is NO punch-in and NO push/zoom in split mode, so the 'wide'
    box (kept for signature compatibility) is never animated.
    Returns {'top': {...}, 'bottom': {...}} with 'crop' (settled) and
    'wide' boxes per panel, in source px."""
    w, h = layout['_srcw'], layout['_srch']
    split = layout['split']
    regions = {'top': (0, 0, split, h), 'bottom': (split, 0, w - split, h)}
    sides = {'top': 'L', 'bottom': 'R'}
    panels = {}
    for posn, side in sides.items():
        region = regions[posn]
        crop = fit_crop(layout['faces'][side], region, k,
                        out_ratio=PANEL_W / PANEL_H, eye_frac=0.44)
        panels[posn] = {'side': side, 'region': region, 'crop': crop,
                        'wide': expand_crop(crop, region, 1.14)}
    return panels

# ------------------------------------------------------- passage framing
# When the SOURCE's own composition changes inside the window (a split-
# screen passage inside an otherwise full-screen programme, or the
# reverse), segments inside the differing passage are re-framed from the
# PASSAGE's faces/regions — never from the global layout. fit_crop
# clamps inside its region, so a passage crop cannot straddle the seam.
_MODE_K = {'MED': 3.6, 'PUSH': 3.6, 'PUNCH': 2.9, 'ALT': 3.25}

def _passage_framing(p, side, mode, w, h):
    """(crop, wide) for one segment part inside passage p, framing
    `side` at `mode`'s tightness, entirely inside the passage region
    (one panel for split passages, the full frame for full passages)."""
    if p['kind'] == 'split':
        split = p['split']
        region = (0, 0, split, h) if side == 'L' \
            else (split, 0, w - split, h)
        face = p['faces'][side]
    else:
        region, face = (0, 0, w, h), p['faces']['C']
    crop = fit_crop(face, region, _MODE_K.get(mode, 3.6))
    if mode == 'ALT':      # mirror plan_segments' ALT nudge, clamped
        rx, ry, rw, rh = region
        crop = (int(min(max(crop[0] + rw * 0.02, rx), rx + rw - crop[2])),
                crop[1], crop[2], crop[3])
    return crop, fit_crop(face, region, 4.7)

def _side_at(p, t, sg, layout):
    """Active speaker side at source time t inside a split passage:
    the passage's own mouth-motion turns when present; else the plan
    segment's side when it names a panel; else the panel whose face
    width best matches the programme's full-screen subject."""
    turns = p.get('turns') or []
    if turns:
        side = turns[0][1]
        for tt, ts in turns:
            if tt <= t:
                side = ts
        return side
    if sg['side'] in ('L', 'R'):
        return sg['side']
    cface = (layout.get('faces') or {}).get('C')
    if cface:
        return min(('L', 'R'),
                   key=lambda s: abs(p['faces'][s][2] - cface[2]))
    return 'L'

def _split_by_turns(a, b, p, sg, layout):
    """Split [a,b] at the passage's turn boundaries; yields
    (s, e, side) parts."""
    turns = sorted(p.get('turns') or [])
    if not turns:
        return [(a, b, _side_at(p, a, sg, layout))]
    cuts = [(a, _side_at(p, a, sg, layout))]
    for tt, ts in turns:
        if a + 0.05 < tt < b - 0.05:
            cuts.append((tt, ts))
    cuts.append((b, None))
    return [(cuts[i][0], cuts[i + 1][0], cuts[i][1])
            for i in range(len(cuts) - 1)]

def apply_passage_framing(merged, layout):
    """Re-frame the parts of each planned segment that fall inside a
    source passage whose composition DIFFERS from the global layout
    (split passage in a single-speaker window, full passage in a
    two-panel window). Parts get '_crop'/'_wide'; untouched parts keep
    '_crop' = None and use the global crop family as before. When no
    passage differs (the common case) the plan is returned unchanged,
    so established layouts render identically."""
    passages = layout.get('passages') or []
    if not passages:
        return merged
    w, h = layout['_srcw'], layout['_srch']
    gkind = layout['kind']

    def differs(p):
        return (p['kind'] == 'split' and gkind != 'two') \
            or (p['kind'] == 'full' and gkind == 'two')
    if not any(differs(p) for p in passages):
        return merged
    out = []
    for sg in merged:
        edges = {sg['s'], sg['e']}
        for p in passages:
            if p['e'] > sg['s'] + 0.05 and p['s'] < sg['e'] - 0.05:
                edges.add(max(p['s'], sg['s']))
                edges.add(min(p['e'], sg['e']))
        pts = sorted(edges)
        for a, b in zip(pts, pts[1:]):
            if b - a < 0.05:
                continue
            mid = (a + b) / 2
            p = next((q for q in passages
                      if q['s'] - 0.01 <= mid <= q['e'] + 0.01), None)
            if p is not None and differs(p) and p['kind'] == 'split':
                for sa, sb, side in _split_by_turns(a, b, p, sg, layout):
                    crop, wide = _passage_framing(p, side, sg['mode'],
                                                  w, h)
                    out.append({'s': sa, 'e': sb, 'side': side,
                                'mode': sg['mode'], '_crop': crop,
                                '_wide': wide})
            elif p is not None and differs(p):
                crop, wide = _passage_framing(p, sg['side'], sg['mode'],
                                              w, h)
                out.append({'s': a, 'e': b, 'side': sg['side'],
                            'mode': sg['mode'], '_crop': crop,
                            '_wide': wide})
            else:
                out.append({'s': a, 'e': b, 'side': sg['side'],
                            'mode': sg['mode'], '_crop': None,
                            '_wide': None})
    # absorb <0.5s slivers into the previous part (its framing covers
    # the sliver — bounded and momentary), then re-merge neighbours
    # that ended up with identical framing
    fixed = []
    for part in out:
        if fixed and part['e'] - part['s'] < 0.5:
            fixed[-1]['e'] = part['e']
        else:
            fixed.append(part)
    merged_out = []
    for part in fixed:
        if merged_out and part['mode'] == merged_out[-1]['mode'] \
                and part['side'] == merged_out[-1]['side'] \
                and part['_crop'] == merged_out[-1]['_crop']:
            merged_out[-1]['e'] = part['e']
        else:
            merged_out.append(part)
    return merged_out

def plan_segments(layout, turns, peak, ws, we, words):
    """Build the edit plan: list of dicts {s, e (source times), crop key,
    mode} with modes PUSH (opening), MED, PUNCH. Rules from the standard:
    opening push-in settles; speaker switches are cuts; ONE punch-in on the
    peak line, then hold; no segment < 1.2s (merged)."""
    kind = layout['kind']
    w, h = layout['_srcw'], layout['_srch']
    if kind == 'two':
        split = layout['split']
        regions = {'L': (0, 0, split, h), 'R': (split, 0, w - split, h)}
    else:
        regions = {'C': (0, 0, w, h)}
    crops = {}
    for side, face in layout['faces'].items():
        crops[(side, 'MED')] = fit_crop(face, regions[side], 3.6)
        crops[(side, 'WIDE')] = fit_crop(face, regions[side], 4.7)
        crops[(side, 'PUNCH')] = fit_crop(face, regions[side], 2.9)
        alt = fit_crop(face, regions[side], 3.25)
        rx, ry, rw, rh = regions[side]
        alt = (int(min(max(alt[0] + rw * 0.02, rx), rx + rw - alt[2])),
               alt[1], alt[2], alt[3])
        crops[(side, 'ALT')] = alt
    # timeline boundaries: (time, side_or_None, force_mode)
    bounds = [(ws, None, 'PUSH')]
    peak_s = max(peak[0]['s'], ws)
    peak_e = peak[-1]['e']
    for t_rel, side in turns:
        # the punch-in HOLDS through the peak line — no speaker cut may
        # interrupt it
        if peak_s - 0.05 <= ws + t_rel < peak_e:
            continue
        bounds.append((ws + t_rel, side, 'MED'))
    if kind == 'single':
        # synthetic angle alternation at sentence boundaries (standard:
        # alternate two crops every 5-6s on single-camera material)
        last, alt_mode = ws, False
        for sent in split_sentences(words):
            t = sent[0]['s']
            if t - last >= 5.5 and t < we - 3.0:
                bounds.append((t, None, 'ALT' if alt_mode else 'MED'))
                alt_mode = not alt_mode
                last = t
    peak_s = max(peak[0]['s'], ws)
    bounds.append((peak_s, None, 'PUNCH'))
    bounds.sort(key=lambda b: b[0])
    segs, cur_side = [], ('L' if kind == 'two' else 'C')
    if turns:
        cur_side = turns[0][1]
    cur = None
    for t, side, mode in bounds:
        if side:
            cur_side = side
        if cur is None:
            cur = {'s': t, 'side': cur_side, 'mode': mode}
            continue
        if t - cur['s'] < 0.05:
            cur['side'], cur['mode'] = cur_side, mode if mode == 'PUNCH' else cur['mode']
            continue
        cur['e'] = t
        segs.append(cur)
        cur = {'s': t, 'side': cur_side, 'mode': mode}
    cur['e'] = we
    segs.append(cur)
    # merge short segments (keep their span, take neighbour's framing) —
    # except the punch-in, which is protected
    merged = []
    for sg in segs:
        if merged and sg['mode'] != 'PUNCH' and sg['e'] - sg['s'] < 1.2:
            merged[-1]['e'] = sg['e']
        elif merged and merged[-1]['mode'] != 'PUNCH' and sg['e'] - sg['s'] >= 1.2 \
                and merged[-1]['e'] - merged[-1]['s'] < 1.2:
            sg['s'] = merged[-1]['s']; merged[-1] = sg
        else:
            merged.append(sg)
    if merged:
        merged[0]['mode'] = 'PUSH' if merged[0]['e'] - merged[0]['s'] >= 2.0 else 'MED'
        merged[0]['s'] = ws
        merged[-1]['e'] = we
    merged = apply_passage_framing(merged, layout)
    plan = []
    for sg in merged:
        if sg.get('_crop'):
            plan.append({'s': round(sg['s'], 2), 'e': round(sg['e'], 2),
                         'side': sg['side'], 'mode': sg['mode'],
                         'crop': sg['_crop'], 'wide': sg['_wide'],
                         'passage': True})
            continue
        key = (sg['side'], 'MED' if sg['mode'] == 'PUSH' else sg['mode'])
        plan.append({'s': round(sg['s'], 2), 'e': round(sg['e'], 2),
                     'side': sg['side'], 'mode': sg['mode'],
                     'crop': crops[key],
                     'wide': crops[(sg['side'], 'WIDE')]})
    return plan, crops

def _eye_y_out(face, box, out_h):
    """The speaker's eye line in output px for a source face seen
    through crop `box` scaled to `out_h` (eyes at face_y + 0.4*face_h,
    the fit_crop convention)."""
    _fx, fy, _fw, fh = face
    _bx, by, _bw, bh = box
    return (fy + fh * 0.4 - by) * (out_h / bh)

def hook_eye_limit(plan, layout, ws, panels=None):
    """The highest (smallest) speaker eye line, in output px, over
    the framings on screen while the hook is up (clip t 0–3.3s),
    minus a safety margin — the hook block's bottom edge must stay
    above it. For a PUSH segment the crop animates wide->settled and
    the eye line moves monotonically between the two endpoints, so
    the min of the endpoints is the binding value. Split layout:
    the top panel's face. Returns None when no face is known."""
    margin = 16
    if panels is not None:
        top = panels['top']
        face = layout['faces'].get(top['side'])
        if face is None:
            return None
        return _eye_y_out(face, top['crop'], PANEL_H) - margin
    passages = layout.get('passages') or []
    best = None
    for sg in plan or []:
        if sg['s'] - ws >= 3.3:
            continue
        face = None
        if sg.get('passage'):
            for ps in passages:
                if ps['s'] - 0.01 <= sg['s'] < ps['e'] + 0.01:
                    face = ps['faces'].get(sg['side'])
                    break
        if face is None:
            face = layout['faces'].get(sg['side'])
        if face is None:
            continue
        for box in (sg['crop'], sg['wide']):
            ey = _eye_y_out(face, box, OUT_H)
            best = ey if best is None else min(best, ey)
    return None if best is None else best - margin

# ---------------------------------------------------------------- captions
GLUE_FORWARD = {'the', 'a', 'an', 'of', 'to', 'in', 'on', 'at', 'for', 'with',
                'is', 'are', 'was', 'were', 'have', 'has', 'had', 'will',
                'would', 'can', 'could', 'do', 'does', 'did', 'my', 'your',
                'his', 'her', 'their', 'our', 'this', 'that'}

def build_cues(words, offset):
    """Group words into 2-3 word cues on the CLIP timeline (offset subtracted).
    Breaks on: 3 words, a >0.4s pause, or sentence end."""
    # merge whisper's hyphen-split tokens ("60" + "-minute" -> "60-minute")
    merged = []
    for w in words:
        if merged and w['w'].startswith('-'):
            merged[-1] = {**merged[-1], 'w': merged[-1]['w'] + w['w'],
                          'e': w['e']}
        else:
            merged.append(w)
    words = merged
    cues, cur = [], []
    for w in words:
        if cur:
            prev = cur[-1]
            if (len(cur) >= 3 or w['s'] - prev['e'] > 0.4
                    or prev['w'].rstrip().endswith(('.', '!', '?'))):
                cues.append(cur); cur = []
        cur.append(w)
    if cur:
        cues.append(cur)
    out = []
    for c in cues:
        ws_ = [(w['w'], w['s'] - offset, w['e'] - offset) for w in c]
        out.append((ws_[0][1], max(ws_[-1][2] + 0.08, ws_[0][1] + 0.45), ws_))
    return out

def wrap_cue(draw, toks, font, max_w=980):
    """Wrap cue tokens into <=2 lines honouring glue rules: never end a line
    on a GLUE_FORWARD word (article/aux/preposition) and never split a
    number+unit pair or a Capitalised name pair."""
    def glued(i):
        t = toks[i].lower().strip('.,!?;:"')
        nxt = toks[i + 1] if i + 1 < len(toks) else ''
        if t in GLUE_FORWARD:
            return True
        if any(c.isdigit() for c in toks[i]) and nxt:
            return True                       # number + unit
        if toks[i][:1].isupper() and nxt[:1].isupper():
            return True                       # name pair
        return False
    if draw.textlength(' '.join(toks), font=font) <= max_w or len(toks) == 1:
        return [toks]
    for cut in range(len(toks) - 1, 0, -1):
        if not glued(cut - 1):
            l1, l2 = toks[:cut], toks[cut:]
            if (draw.textlength(' '.join(l1), font=font) <= max_w
                    and draw.textlength(' '.join(l2), font=font) <= max_w):
                return [l1, l2]
    return [toks[:len(toks) // 2], toks[len(toks) // 2:]]

# ---------------------------------------------------------------- text layer
def render_text_frames(workdir, total, main_d, cues, hook_toks, title,
                       cap_y=1450, hook_y=196, hook_max_size=104,
                       hook_max_lines=3, hide_title_with_hook=False,
                       accent=AMBER, style='standard', eye_limit=None):
    """PIL text layer at TXT_FPS. Defaults are the cutaway geometry
    (captions low-centre, hook under the top attribution). Split layout
    passes cap_y centred on the seam, a higher/smaller hook, and hides
    the attribution while the hook is up (they would collide).
    `accent` is the brand kit's colour (hook hot words, progress
    fill, caption highlight); `style` is the register — see
    REGISTERS. With style='standard' and the default accent this
    renders exactly the approved recipe.
    `eye_limit` (from hook_eye_limit) is the output-space y the hook
    block's bottom edge must stay above — the speaker's eye line
    while the hook is up. When the block at the recipe position
    would cross it, the fit first parks the block in the clear
    strip under the attribution, then shrinks the font, and as a
    last resort caps the hook at 2 lines — so a hook never sits on
    the speaker's forehead/eyes."""
    from PIL import Image, ImageDraw, ImageFont
    fd = ASSETS
    def mfont(size):
        f = ImageFont.truetype(os.path.join(fd, 'Manrope.ttf'), size)
        f.set_variation_by_name('ExtraBold')
        return f
    def ifont(size):
        f = ImageFont.truetype(os.path.join(fd, 'Inter.ttf'), size)
        f.set_variation_by_axes([32, 600])
        return f
    cap_f, attr_f = mfont(62), ifont(30)
    tmp = ImageDraw.Draw(Image.new('RGBA', (10, 10)))
    # ---- hook fit: <=990px per line, <=hook_max_lines lines, and
    # the whole block must clear the speaker's eye line (eye_limit).
    # Per-register line geometry: in 'bar' each line sits in its own
    # bar slot — bar height contains the full ascender+descender
    # extent plus padding, and consecutive slots are separated by a
    # clear gap (the old code advanced lines by 1.08*size while each
    # bar spanned size+10, so adjacent bars overlapped by 2-6px and
    # merged into one blob). 'standard'/'minimal' keep the approved
    # 1.08 advance; their block extent uses the true text height.
    words_h = [t for t, _ in hook_toks]

    def _wrap(hf):
        lines, cur = [], []
        for t in words_h:
            trial = cur + [t]
            if tmp.textlength(' '.join(trial), font=hf) <= 990 or not cur:
                cur = trial
            else:
                lines.append(cur); cur = [t]
        if cur:
            lines.append(cur)
        return lines

    def _geom(hf, size, n):
        """(line pitch, block height) for n hook lines at `size`."""
        asc, desc = hf.getmetrics()
        if style == 'bar':
            bar_h = asc + desc + 2 * max(8, round(size * 0.10))
            pitch = bar_h + max(12, round(size * 0.16))
            return pitch, (pitch * (n - 1) + bar_h) if n else 0
        pitch = int(size * 1.08)
        return pitch, (pitch * (n - 1) + asc + desc) if n else 0

    # If the block at the recipe position would cross the eye line,
    # the first lever is POSITION: park the block in the clear strip
    # just under the attribution (top_floor) — the eye limit comes
    # from the median face box, and real head motion raises the eyes
    # above it, so the headroom matters. Then shrink, then cap lines.
    top_floor = min(hook_y, 108)

    def _attempt(max_lines, truncate):
        size = hook_max_size
        while size >= 42:
            hf = mfont(size)
            lines = _wrap(hf)
            if truncate and len(lines) > max_lines:
                lines = lines[:max_lines]
            pitch, block_h = _geom(hf, size, len(lines))
            if len(lines) <= max_lines:
                if eye_limit is None or \
                        hook_y + block_h <= eye_limit:
                    return hf, size, lines, pitch, block_h, hook_y
                if top_floor + block_h <= eye_limit:
                    return hf, size, lines, pitch, block_h, top_floor
            size -= 6
        return None

    fit = _attempt(hook_max_lines, truncate=False)
    if fit is None and hook_max_lines > 2:
        # last resort: cap the hook at 2 lines (overflow words drop)
        fit = _attempt(2, truncate=True)
        if fit is not None:
            log('hook fit: capped at 2 lines to clear the eye line')
    if fit is None:
        # extreme geometry: smallest size, truncated — never crash
        hf = mfont(42)
        lines = _wrap(hf)[:hook_max_lines]
        pitch, block_h = _geom(hf, 42, len(lines))
        fit = (hf, 42, lines, pitch, block_h, hook_y)
        log('hook fit: WARNING — could not fully clear the eye line')
    hook_f, hsize, hook_lines, hook_pitch, hook_block_h, hook_top = fit
    if hsize < hook_max_size or len(hook_lines) < hook_max_lines \
            or hook_top != hook_y:
        log(f'hook fit: {hsize}px, {len(hook_lines)} line(s), block '
            f'{hook_block_h}px at y={hook_top}'
            + (f' (eye limit {eye_limit:.0f})'
               if eye_limit is not None else ''))
    hot = {t for t, h_ in hook_toks if h_}
    title_txt = re.sub(r'\s+', ' ', title or '').strip().upper()[:40]

    def draw_tracked(d, cx, y, text, fnt, fill, tracking):
        widths = [d.textlength(ch, font=fnt) for ch in text]
        x = cx - (sum(widths) + tracking * (len(text) - 1)) / 2
        for ch, wch in zip(text, widths):
            d.text((x, y), ch, font=fnt, fill=fill)
            x += wch + tracking

    def state(t):
        img = Image.new('RGBA', (OUT_W, OUT_H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        pw = int(OUT_W * min(t / main_d, 1.0))
        d.rectangle([0, 1912, OUT_W, 1918], fill=(255, 255, 255, 55))
        if pw > 0:
            d.rectangle([0, 1912, pw, 1918], fill=accent)
        if title_txt and t < main_d - 0.05 \
                and not (hide_title_with_hook and t <= 3.45) \
                and style != 'minimal':
            if style == 'bar':
                tw_ = sum(d.textlength(ch, font=attr_f)
                          for ch in title_txt) \
                    + 7 * (len(title_txt) - 1)
                d.rounded_rectangle(
                    [540 - tw_ / 2 - 18, 40, 540 + tw_ / 2 + 18, 92],
                    radius=14, fill=(12, 20, 37, 215))
                draw_tracked(d, 540, 54, title_txt, attr_f,
                             (255, 255, 255, 235), 7)
            else:
                draw_tracked(d, 541, 56, title_txt, attr_f,
                             (0, 0, 0, 120), 7)
                draw_tracked(d, 540, 54, title_txt, attr_f,
                             (255, 255, 255, 215), 7)
        if 0.10 <= t <= 3.30:
            if t < 0.45:
                p = (t - 0.10) / 0.35
                alpha = p
                dy = int((1 - p) * 26) if style == 'standard' else 0
            elif t > 3.05:
                alpha, dy = max((3.30 - t) / 0.25, 0.0), 0
            else:
                alpha, dy = 1.0, 0
            layer = Image.new('RGBA', (OUT_W, 720), (0, 0, 0, 0))
            ld = ImageDraw.Draw(layer)
            hot_col = accent if style != 'minimal' else WHITE
            y = 0
            for line in hook_lines:
                line_w = ld.textlength(' '.join(line), font=hook_f)
                x = (OUT_W - line_w) / 2
                if style == 'bar':
                    # one bar per line in its own slot (geometry from
                    # the hook fit): the bar contains the full
                    # ascender+descender extent, the text is centred
                    # in it vertically, and the next slot starts a
                    # clear gap below — bars can never overlap
                    asc, desc = hook_f.getmetrics()
                    bar_h = asc + desc + 2 * max(8, round(hsize * 0.10))
                    ld.rounded_rectangle(
                        [x - 26, y, x + line_w + 26, y + bar_h],
                        radius=18, fill=(12, 20, 37, 238))
                    cy = y + bar_h / 2
                    for tok in line:
                        col = hot_col if tok in hot else WHITE
                        ld.text((x, cy), tok, font=hook_f, fill=col,
                                anchor='lm')
                        x += ld.textlength(tok + ' ', font=hook_f)
                else:
                    for tok in line:
                        col = hot_col if tok in hot else WHITE
                        if style == 'standard':
                            ld.text((x + 3, y + 4), tok, font=hook_f,
                                    fill=(0, 0, 0, 130))
                            ld.text((x, y), tok, font=hook_f, fill=col)
                        else:      # minimal: plain bold, thin outline
                            ld.text((x, y), tok, font=hook_f, fill=col,
                                    stroke_width=2,
                                    stroke_fill=(10, 14, 24, 170))
                        x += ld.textlength(tok + ' ', font=hook_f)
                y += hook_pitch
            if alpha < 1.0:
                layer.putalpha(layer.split()[3].point(lambda v: int(v * alpha)))
            img.alpha_composite(layer, (0, hook_top + dy))
        for cs, ce, cwords in cues:
            if cs - 0.02 <= t < ce:
                fade = min(max((t - cs) / 0.10, 0.0),
                           max((ce - t) / 0.10, 0.0), 1.0)
                active = 0
                for j, (_w, ws_, _we) in enumerate(cwords):
                    if t >= ws_:
                        active = j
                layer = Image.new('RGBA', (OUT_W, 340), (0, 0, 0, 0))
                ld = ImageDraw.Draw(layer)
                toks = [w for (w, _s, _e) in cwords]
                lines = wrap_cue(ld, toks, cap_f)
                lh, idx = 80, 0
                y0 = 170 - (len(lines) * lh) // 2
                for li, line in enumerate(lines):
                    x = (OUT_W - ld.textlength(' '.join(line), font=cap_f)) / 2
                    act_box = None
                    for tok in line:
                        col = accent if (idx == active
                                         and style != 'minimal') \
                            else WHITE
                        ld.text((x + 2, y0 + li * lh + 4), tok, font=cap_f,
                                fill=(0, 0, 0, 150), stroke_width=6,
                                stroke_fill=(0, 0, 0, 150))
                        ld.text((x, y0 + li * lh), tok, font=cap_f, fill=col,
                                stroke_width=6, stroke_fill=(10, 14, 24, 255))
                        if idx == active:
                            act_box = (x, ld.textlength(tok, font=cap_f))
                        x += ld.textlength(tok + ' ', font=cap_f)
                        idx += 1
                    if style == 'minimal' and act_box:
                        # minimal marks the spoken word with an accent
                        # underline instead of a colour change
                        ax, aw = act_box
                        ld.rectangle([ax, y0 + li * lh + 70,
                                      ax + aw, y0 + li * lh + 77],
                                     fill=accent)
                if fade < 1.0:
                    layer.putalpha(layer.split()[3].point(lambda v: int(v * fade)))
                img.alpha_composite(layer, (0, cap_y))
                break
        return img

    fdir = os.path.join(workdir, 'txtframes')
    os.makedirs(fdir, exist_ok=True)
    n = math.ceil(total * TXT_FPS)
    for i in range(n):
        state(i / TXT_FPS + 1 / (2 * TXT_FPS)).save(os.path.join(fdir, f'f{i:04d}.png'))
    log(f'text layer: {n} frames')
    return fdir

# ------------------------------------------------- point-and-appear overlay
# ONE visual overlay per clip, by design: when the speaker talks about a
# concrete thing, an image of it flies in beside them (as if summoned —
# speakers often point at the air where it appears), holds while the line
# plays, then leaves. Animation is rendered as PIL frames at FULL fps
# (not the 12fps text layer) with sub-pixel positions every frame, so
# there is no stepping/glitch. The settle box is clamped inside the
# cross-platform safe box and must never cover the caption band.
OV_ENTRY = 0.45          # fly-in duration (s)
OV_EXIT = 0.30           # fade+slide-out duration (s)
OV_W = 320               # settled overlay image width (output px)
OV_PAD = 30              # transparent padding around the card (shadow room)
OV_SAFE = (120, 288, 888, 1248)   # x0, y0, x1, y1 — cross-platform safe box
OV_SLIDE = 40.0          # entry/exit slide distance (px)

def _ease_out_cubic(p):
    return 1.0 - (1.0 - p) ** 3

def _ease_in_cubic(p):
    return p ** 3

def fetch_overlay_image(spec, workdir):
    """--overlay value -> local file path. http(s) URLs are downloaded
    into the workdir; anything else must be an existing local path."""
    if is_url(spec):
        import urllib.request
        ext = os.path.splitext(spec.split('?')[0])[1].lower()
        if ext not in ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'):
            ext = '.png'
        dst = os.path.join(workdir, 'overlay_src' + ext)
        req = urllib.request.Request(
            spec, headers={'User-Agent': 'Mozilla/5.0 (clip-engine)'})
        with urllib.request.urlopen(req, timeout=60) as r, \
                open(dst, 'wb') as f:
            shutil.copyfileobj(r, f)
        return dst
    p = os.path.abspath(os.path.expanduser(spec))
    if not os.path.isfile(p):
        raise SystemExit(f'overlay image not found: {spec}')
    return p

def prepare_overlay_tile(path, width=OV_W):
    """Build the overlay 'card': the source image at settle size with a
    soft drop shadow, on a transparent tile padded by OV_PAD.
    Transparent PNGs (logos, product shots) keep their own alpha and get
    a silhouette shadow; opaque photos get rounded corners.
    Returns (tile, (pad, pad, img_w, img_h))."""
    from PIL import Image, ImageDraw, ImageFilter, ImageOps
    im = ImageOps.exif_transpose(Image.open(path)).convert('RGBA')
    w, h = im.size
    nh = max(1, int(round(width * h / w)))
    im = im.resize((width, nh), Image.LANCZOS)
    alpha = im.split()[3]
    if alpha.getextrema()[0] < 250:      # real transparency — keep it
        mask = alpha
    else:
        mask = Image.new('L', (width, nh), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, width - 1, nh - 1], radius=min(26, nh // 6), fill=255)
        im.putalpha(mask)
    tile = Image.new('RGBA', (width + 2 * OV_PAD, nh + 2 * OV_PAD),
                     (0, 0, 0, 0))
    sh_mask = Image.new('L', tile.size, 0)
    sh_mask.paste(mask, (OV_PAD + 6, OV_PAD + 10))
    sh_mask = sh_mask.filter(ImageFilter.GaussianBlur(10))
    shadow = Image.new('RGBA', tile.size, (0, 0, 0, 255))
    shadow.putalpha(sh_mask.point(lambda v: int(v * 0.42)))
    tile.alpha_composite(shadow)
    tile.alpha_composite(im, (OV_PAD, OV_PAD))
    return tile, (OV_PAD, OV_PAD, width, nh)

def overlay_settle(pos, img_w, img_h):
    """Resolve --overlay-pos to the settle top-left (x, y) of the IMAGE
    box on the 1080x1920 canvas, clamped inside the safe box. Named
    anchors sit beside the speaker's head (upper) or above the caption
    band (lower); 'X,Y' is an explicit image top-left."""
    x0s, y0s, x1s, y1s = OV_SAFE
    if ',' in pos:
        try:
            x, y = (float(v) for v in pos.split(','))
        except ValueError:
            raise SystemExit(f"bad --overlay-pos {pos!r} (want a named "
                             "anchor or 'X,Y')")
    elif pos == 'upper-right':
        x, y = x1s - img_w, y0s + 8
    elif pos == 'upper-left':
        x, y = x0s, y0s + 8
    elif pos == 'lower-right':
        x, y = x1s - img_w, y1s - img_h
    elif pos == 'lower-left':
        x, y = x0s, y1s - img_h
    else:
        raise SystemExit(f"bad --overlay-pos {pos!r} (upper-right, "
                         "upper-left, lower-right, lower-left or 'X,Y')")
    x = min(max(x, x0s), max(x0s, x1s - img_w))
    y = min(max(y, y0s), max(y0s, y1s - img_h))
    return int(round(x)), int(round(y))

def render_overlay_frames(workdir, dur, tile, img_box, settle_xy, slide_sign):
    """PIL frames at full FPS covering ONLY the overlay window:
    fly-in (scale 0.70->1.00 + OV_SLIDE px slide, ease-out cubic, quick
    alpha ramp so frame one never pops), hold at settle, then exit
    (fade + slide back out, ease-in cubic). Scale is anchored on the
    image centre so the card grows in place. Returns (dir, n_frames)."""
    from PIL import Image
    pad_x, pad_y, img_w, img_h = img_box
    tw, th = tile.size
    n = max(1, int(math.ceil(dur * FPS)))
    fdir = os.path.join(workdir, 'ovframes')
    os.makedirs(fdir, exist_ok=True)
    for i in range(n):
        t = (i + 0.5) / FPS            # frame-centre time in the window
        if t < OV_ENTRY:
            p = _ease_out_cubic(t / OV_ENTRY)
            scale = 0.70 + 0.30 * p
            off = (1.0 - p) * OV_SLIDE * slide_sign
            alpha = min(1.0, t / (OV_ENTRY * 0.6))
        elif t > dur - OV_EXIT:
            p = _ease_in_cubic((t - (dur - OV_EXIT)) / OV_EXIT)
            scale, off, alpha = 1.0, p * OV_SLIDE * slide_sign, 1.0 - p
        else:
            scale, off, alpha = 1.0, 0.0, 1.0
        frame = Image.new('RGBA', (OUT_W, OUT_H), (0, 0, 0, 0))
        if alpha > 0.003:
            nw = max(1, int(round(tw * scale)))
            nh = max(1, int(round(th * scale)))
            timg = tile.resize((nw, nh), Image.LANCZOS)
            if alpha < 1.0:
                timg.putalpha(timg.split()[3].point(
                    lambda v: int(v * alpha)))
            cx = settle_xy[0] + img_w / 2.0 + off
            cy = settle_xy[1] + img_h / 2.0
            x = int(round(cx - (pad_x + img_w / 2.0) * scale))
            y = int(round(cy - (pad_y + img_h / 2.0) * scale))
            frame.alpha_composite(timg, (x, y))
        frame.save(os.path.join(fdir, f'f{i:04d}.png'))
    log(f'overlay layer: {n} frames ({dur:.2f}s window)')
    return fdir, n

# ---------------------------------------------------------------- render
def render_window(src, ws, dur, workdir):
    win = os.path.join(workdir, 'window.mp4')
    # The window is converted to the OUTPUT frame rate here, once.
    # Pieces then seek into an already-24fps file at frame-aligned
    # times, so piece frame j is window frame start_f + j by
    # construction and adjacent pieces tile on one shared phase.
    # When pieces each converted the 30fps window themselves, every
    # piece's fps phase anchored at its own seek and the SAME plan
    # boundary frame sampled different sides of a source cut in the
    # two adjacent pieces (one faceless frame at the joint in three
    # consecutive builds, alternating sides). The +0.15s tail guards
    # the last piece's final frame against conversion rounding.
    run(['ffmpeg', '-v', 'error', '-i', src, '-ss', f'{ws:.2f}',
         '-t', f'{dur + 0.15:.2f}',
         '-map', '0:v', '-map', '0:a', '-vf', f'fps={FPS}',
         '-c:v', 'libx264', '-preset', 'fast',
         '-crf', '16', '-g', '24', '-c:a', 'aac', '-b:a', '160k',
         '-af', 'aresample=48000', win, '-y'])
    return win

def push_chain(wide, settled, out_w, out_h, n_push):
    """Filter chain for the opening push-in: the sampled window
    interpolates from the `wide` crop box to the `settled` crop box
    (both source px) with an ease-out over n_push frames, output
    out_w x out_h, landing EXACTLY on the settled box so the next
    static piece continues seamlessly.

    Why not an animated crop? ffmpeg's crop filter evaluates its w/h
    expressions once at init in this build — a crop=w='...n...' chain
    silently freezes at the wide box for the whole piece and then
    jumps the full travel at the piece boundary (measured 2026-11-13
    on a static synthetic pattern: zero motion within the piece).
    The two primitives that DO evaluate per frame are scale with
    eval=frame and crop x/y, so: supersample the wide box, dynamically
    scale it by the settled/current size ratios, then crop the FIXED
    settled size at animated offsets (offsets scaled by the same
    ratios). Verified on the synthetic pattern: smooth travel,
    endpoint matches the static framing to sub-pixel."""
    x, y, w, h = wide
    mx, my, mw, mh = settled
    ss = max(1, min(4, int(2800 / max(w, 1))))
    e = f"(1-pow(1-min(n\\,{n_push})/{n_push}\\,2))"
    fx = f"({mw}/({w}+({mw}-{w})*{e}))"
    fy = f"({mh}/({h}+({mh}-{h})*{e}))"
    return (
        f"crop={w}:{h}:{x}:{y},scale={w * ss}:{h * ss}:flags=lanczos,"
        f"scale=w='{w * ss}*{fx}':h='{h * ss}*{fy}':eval=frame:flags=lanczos,"
        f"crop=w={mw * ss}:h={mh * ss}"
        f":x='{(mx - x) * ss}*{e}*{fx}':y='{(my - y) * ss}*{e}*{fy}',"
        f"scale={out_w}:{out_h}:flags=lanczos")

def render_piece(win, seg, idx, workdir):
    out = os.path.join(workdir, f'p{idx:02d}.mp4')
    # Frame-exact joints: seek and length are quantised to whole
    # output frames AND the video is capped with -frames:v. A plain
    # -t of n/24s is NOT frame-exact after the fps conversion — it
    # overran by +1 frame on most pieces, and the overruns accumulate
    # down the concat, so later plan cuts land increasingly late
    # (measured: the 16.58s handoff rendered at 16.67s, flashing the
    # old crop over the new composition — the second half of the
    # 22.5s-flash defect class).
    start_f = round((seg['s'] - seg['_ws']) * FPS)
    end_f = round((seg['e'] - seg['_ws']) * FPS)
    n_frames = max(1, end_f - start_f)
    rel_s = start_f / FPS
    dur = n_frames / FPS
    base = ['ffmpeg', '-v', 'error', '-ss', f'{rel_s:.2f}',
            '-i', win, '-map', '0:v', '-map', '0:a',
            '-af', f'aresample=48000,atrim=end={dur:.3f}',
            '-frames:v', str(n_frames),
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '17',
            '-c:a', 'aac', '-b:a', '160k', out, '-y']
    if seg['mode'] == 'PUSH':
        n_push = min(int(2.5 * FPS), max(int(dur * FPS) - 1, 1))
        vf = (push_chain(seg['wide'], seg['crop'], OUT_W, OUT_H, n_push)
              + f",fps={FPS},setsar=1,format=yuv420p")
    else:
        x, y, w, h = seg['crop']
        vf = (f"crop={w}:{h}:{x}:{y},scale={OUT_W}:{OUT_H}:flags=lanczos,"
              f"fps={FPS},setsar=1,format=yuv420p")
    base.insert(base.index('-map'), '-vf')
    base.insert(base.index('-map'), vf)
    run(base)
    return out

def render_split_piece(win, spec, idx, workdir):
    """One piece of a split-layout clip: BOTH panels stacked (top=L,
    bottom=R), each face-cropped to 1080x960, fixed all clip, with the
    navy divider drawn on the seam. Mode 'SPLIT_PUSH' animates the
    opening settle: both panels run the SAME supersampled ease-out
    crop interpolation in one filter graph (identical frame numbering
    => perfectly synchronised, sub-pixel smooth, no zoompan). Mode
    'SPLIT' is the static settled framing. No other animated zooms
    exist in split mode, and the punch-in rule does not apply (both
    faces are already tight)."""
    out = os.path.join(workdir, f'p{idx:02d}.mp4')
    # frame-exact joints, same rule as render_piece (-frames:v cap,
    # never a bare -t, which overruns by a frame after fps conversion)
    start_f = round((spec['s'] - spec['_ws']) * FPS)
    end_f = round((spec['e'] - spec['_ws']) * FPS)
    n_frames = max(1, end_f - start_f)
    rel_s = start_f / FPS
    dur = n_frames / FPS
    panels = spec['panels']
    push = spec['mode'] == 'SPLIT_PUSH'

    def chain(posn):
        if not push:
            x, y, w, h = panels[posn]['crop']
            return (f"crop={w}:{h}:{x}:{y},"
                    f"scale={PANEL_W}:{PANEL_H}:flags=lanczos")
        n_push = min(int(2.5 * FPS), max(int(dur * FPS) - 1, 1))
        return push_chain(panels[posn]['wide'], panels[posn]['crop'],
                          PANEL_W, PANEL_H, n_push)

    fc = (f"[0:v]split=2[sa][sb];"
          f"[sa]{chain('top')}[tp];[sb]{chain('bottom')}[bt];"
          f"[tp][bt]vstack=inputs=2,"
          f"drawbox=x=0:y={SEAM_Y - 4}:w={OUT_W}:h=8:color={NAVY}:t=fill,"
          f"fps={FPS},setsar=1,format=yuv420p[v]")
    run(['ffmpeg', '-v', 'error', '-ss', f'{rel_s:.2f}',
         '-i', win, '-filter_complex', fc, '-map', '[v]', '-map', '0:a',
         '-af', f'aresample=48000,atrim=end={dur:.3f}',
         '-frames:v', str(n_frames),
         '-c:v', 'libx264', '-preset', 'fast',
         '-crf', '17', '-c:a', 'aac', '-b:a', '160k', out, '-y'])
    return out

def render_card(workdir, brand=None, style='standard'):
    """1s brand end card, composed in PIL from the brand kit.
    'standard' reproduces the approved card exactly: navy field,
    the kit's logo artwork at 940px, no drawn text. 'bar' keeps the
    navy card, uses the kit's bare logo_icon when present (seated on
    a paper plate — kit marks are drawn for a light ground, and the
    studio mark's navy frame + paper play-triangle would vanish on
    navy), and draws the kit's end_card_name under the logo, fitted
    to the card width. 'minimal' flips to a paper card with navy
    text."""
    from PIL import Image, ImageDraw, ImageFont
    if style == 'minimal':
        bg, fg = (248, 250, 252, 255), (12, 20, 37, 255)
    else:
        bg, fg = (12, 20, 37, 255), (255, 255, 255, 255)
    img = Image.new('RGBA', (OUT_W, OUT_H), bg)
    logo_path = (brand or {}).get('logo') \
        or os.path.join(ASSETS, 'logo-stacked.png')
    if style != 'standard' and (brand or {}).get('logo_icon'):
        logo_path = brand['logo_icon']
    logo = Image.open(logo_path).convert('RGBA')
    if style == 'standard':
        logo = logo.resize((940, 940), Image.LANCZOS)
        img.alpha_composite(logo, (70, 430))
    else:
        logo = logo.resize((800, 800), Image.LANCZOS)
        if style == 'bar':
            # seat the icon on a paper plate: kit icons are marks on
            # transparency, usually designed for a light ground — on
            # the navy card the studio mark's navy frame disappears
            # and its paper play-triangle knocks out to navy, leaving
            # a meaningless gold slab
            pd = ImageDraw.Draw(img)
            pd.rounded_rectangle([140 - 36, 380 - 36, 140 + 836,
                                  380 + 836], radius=48,
                                 fill=(248, 250, 252, 255))
        img.alpha_composite(logo, (140, 380))
        name = (brand or {}).get('end_card_name') or ''
        if name:
            d = ImageDraw.Draw(img)
            txt = name.upper()
            # fit the tracked name inside the card: shrink the font
            # (tracking scales with it) until the whole line fits —
            # at 56px the studio name ran off both edges
            size = 56
            while size > 24:
                f = ImageFont.truetype(os.path.join(ASSETS,
                                                    'Inter.ttf'),
                                       size)
                f.set_variation_by_axes([32, 600])
                tracking = max(4, round(10 * size / 56))
                widths = [d.textlength(ch, font=f) for ch in txt]
                if sum(widths) + tracking * (len(txt) - 1) \
                        <= OUT_W - 80:
                    break
                size -= 4
            x = 540 - (sum(widths)
                       + tracking * (len(txt) - 1)) / 2
            for ch, wch in zip(txt, widths):
                d.text((x, 1310), ch, font=f, fill=fg)
                x += wch + tracking
    card_png = os.path.join(workdir, 'card.png')
    img.convert('RGB').save(card_png)
    card = os.path.join(workdir, 'card.mp4')
    run(['ffmpeg', '-v', 'error',
         '-loop', '1', '-t', '1.0', '-i', card_png,
         '-f', 'lavfi', '-t', '1.0', '-i', 'anullsrc=r=48000:cl=stereo',
         '-r', str(FPS), '-c:v', 'libx264', '-preset', 'fast',
         '-crf', '17', '-c:a', 'aac', '-b:a', '160k', card, '-y'])
    return card

def join_pieces(pieces, card, main_d, workdir, counts=None):
    inputs = []
    for f in pieces + [card]:
        inputs += ['-i', f]
    fc = []
    for i in range(len(pieces) + 1):
        if counts is not None and i < len(counts):
            # Frame-true join: a piece's CONTAINER duration runs
            # longer than its video frame count (AAC priming + the
            # muxer stretching the last frame to the file duration),
            # and a bare fps/concat chain honours that duration —
            # it duplicates tail frames to fill it, so every joint
            # after the first lands increasingly late (measured:
            # +0.11s by the 4th joint, enough to flash the old crop
            # over a hard composition cut). Trim each piece to its
            # exact planned frame count instead.
            n = counts[i]
            fc.append(f"[{i}:v]trim=end_frame={n},setpts=PTS-STARTPTS,"
                      f"settb=AVTB,setsar=1,format=yuv420p[vn{i}]")
            fc.append(f"[{i}:a]aresample=48000,atrim=end={n / FPS:.3f},"
                      f"asetpts=PTS-STARTPTS,asettb=AVTB[an{i}]")
        else:
            fc.append(f"[{i}:v]fps={FPS},settb=AVTB,setsar=1,"
                      f"format=yuv420p[vn{i}]")
            fc.append(f"[{i}:a]aresample=48000,asettb=AVTB[an{i}]")
    vcur, acur = '[vn0]', '[an0]'
    for i in range(1, len(pieces)):
        fc.append(f"{vcur}[vn{i}]concat=n=2:v=1:a=0,settb=AVTB[v{i}]")
        fc.append(f"{acur}[an{i}]concat=n=2:v=0:a=1,asettb=AVTB[a{i}]")
        vcur, acur = f'[v{i}]', f'[a{i}]'
    n = len(pieces)
    fc.append(f"{vcur}[vn{n}]xfade=transition=fade:duration=0.2:"
              f"offset={main_d - 0.2:.2f},settb=AVTB[vj]")
    fc.append(f"{acur}[an{n}]acrossfade=d=0.2,asettb=AVTB[aj]")
    joined = os.path.join(workdir, 'joined.mp4')
    run(['ffmpeg', '-v', 'error'] + inputs +
        ['-filter_complex', ';'.join(fc), '-map', '[vj]', '-map', '[aj]',
         '-c:v', 'libx264', '-preset', 'fast', '-crf', '17',
         '-c:a', 'aac', '-b:a', '160k', joined, '-y'])
    return joined

def loudnorm_measure(path):
    r = run(['ffmpeg', '-i', path, '-vn', '-af',
             'loudnorm=I=-14:TP=-1.0:LRA=11:print_format=json',
             '-f', 'null', '-'], check=False)
    m = re.findall(r'\{[^{}]*"input_i"[^{}]*\}', r.stderr or '', re.S)
    if not m:
        return None
    return json.loads(m[-1])

def final_composite(joined, fdir, total, out, ov=None):
    meas = loudnorm_measure(joined)
    ln = 'loudnorm=I=-14:TP=-1.0:LRA=11'
    if meas:
        ln += (f":measured_I={meas['input_i']}:measured_TP={meas['input_tp']}"
               f":measured_LRA={meas['input_lra']}"
               f":measured_thresh={meas['input_thresh']}"
               f":offset={meas['target_offset']}:linear=true")
    vchain = '[0:v][1:v]overlay=0:0:format=auto'
    if ov:      # point-and-appear overlay frames, offset to their window;
        # eof_action=pass so the base video runs clean before/after it
        vchain += '[vt];[vt][2:v]overlay=0:0:format=auto:eof_action=pass'
    graph = (f'{vchain},fps={FPS},format=yuv420p[vout];'
             f'[0:a]{ln}[aout]')
    cmd = ['ffmpeg', '-v', 'error', '-i', joined,
           '-framerate', str(TXT_FPS), '-i', os.path.join(fdir, 'f%04d.png')]
    if ov:
        cmd += ['-itsoffset', f"{ov['at']:.3f}", '-framerate', str(FPS),
                '-i', os.path.join(ov['dir'], 'f%04d.png')]
    cmd += ['-filter_complex', graph,
            '-map', '[vout]', '-map', '[aout]', '-c:v', 'libx264',
            '-preset', 'fast', '-crf', '17', '-c:a', 'aac', '-b:a', '160k',
            '-t', f'{total:.2f}', out, '-y']
    r = run(cmd, check=False)
    if r.returncode != 0:      # linear=true can refuse; retry standard mode
        cmd[cmd.index('-filter_complex') + 1] = graph.replace(
            ln, 'loudnorm=I=-14:TP=-1.0:LRA=11')
        run(cmd)
    return meas

# ---------------------------------------------------------------- QA gate
def qa_gate(out_path, plan, ws, main_d, span, passages, expected_faces,
            workdir):
    """Face-check the RENDERED clip. Routine samples every ~3s plus
    just after every plan segment start; a routine sample passes when
    ANY of the frames at t-0.4/t/t+0.4 shows the expected face(s).

    BOUNDARY samples are stricter (tightened 2026-11-13 after Pupa
    caught a ~0.2s faceless flash at the split->full passage edge of
    the overlay-v2 build — the triplet rule let it slip through):
    around every plan-segment edge and every passage edge, samples
    are taken at 0.25s steps across +/-1.5s (offset 0.125s from the
    edge itself, so no sample sits on the knife-edge frame where a
    one-frame handoff ambiguity lives), tightening to 0.05s steps
    within +/-0.3s of the edge after a 2-frame gap was measured
    threading the 0.25 grid, and EACH boundary sample
    must show the expected number of FULLY-visible faces in the
    single frame at that time — no triplet rescue.

    A sample counts only while speech is happening (word timings)
    and the source had a face to frame then (samples inside faceless
    source passages are skipped). 'Fully visible' = YuNet box at
    least 8 output px from every frame edge — a face sliced by a
    crop sits flush on an edge; the seam-straddle defect shows no
    face at all — both fail. Returns a report dict; status
    'skipped' when no face detector is available on this machine."""
    samples = set()
    t = 0.4
    while t < main_d - 0.2:
        samples.add(round(t, 2))
        t += 3.0
    for sg in plan or []:
        rt = sg['s'] - ws
        if rt > 0.3:
            samples.add(round(rt + 0.35, 2))
    for p in passages or []:
        if p.get('kind') == 'split':
            for edge in (p['s'], p['e']):
                rt = edge - ws + 0.35
                if 0.3 < rt < main_d - 0.2:
                    samples.add(round(rt, 2))
    samples = sorted(s for s in samples if s < main_d - 0.15)
    # ---- boundary samples: dense + strict around every handoff
    edges = set()
    for sg in plan or []:
        for e in (sg['s'], sg['e']):
            rt = round(e - ws, 2)
            if 0.2 < rt < main_d - 0.2:
                edges.add(rt)
    for p in passages or []:
        for e in (p['s'], p['e']):
            rt = round(e - ws, 2)
            if 0.2 < rt < main_d - 0.2:
                edges.add(rt)
    boundary = set()
    for rt in edges:
        for k in range(12):
            t = round(rt - 1.375 + 0.25 * k, 2)
            if 0.15 < t < main_d - 0.15:
                boundary.add(t)
        # Dense pass hugging the edge itself: a sub-0.1s flash can
        # thread between 0.25-spaced samples (measured: a 2-frame
        # faceless gap at a dissolve handoff passed the 0.25 grid in
        # the first feature-round build), so within +/-0.3s of an
        # edge the sampling step tightens to 0.05s.
        for d in (0.05, 0.10, 0.15, 0.20, 0.25, 0.30):
            for sgn in (-1, 1):
                t = round(rt + sgn * d, 2)
                if 0.15 < t < main_d - 0.15:
                    boundary.add(t)
    all_samples = sorted(set(samples) | boundary)

    def speaking(tt):
        return any((wd['s'] - ws) - 0.15 <= tt <= (wd['e'] - ws) + 0.35
                   for wd in span)

    def no_source_face(tt):
        ts = ws + tt
        return any(p.get('kind') == 'none'
                   and p['s'] - 0.01 <= ts <= p['e'] + 0.01
                   for p in (passages or []))

    routine = [t for t in samples if t not in boundary]
    todo_r = [t for t in routine
              if speaking(t) and not no_source_face(t)]
    todo_b = [t for t in sorted(boundary)
              if speaking(t) and not no_source_face(t)]
    todo = sorted(set(todo_r) | set(todo_b))
    frames_at = sorted(
        {round(min(max(t + dt, 0.05), main_d - 0.1), 2)
         for t in todo_r for dt in (-0.4, 0.0, 0.4)}
        | {round(min(max(t, 0.05), main_d - 0.1), 2) for t in todo_b})
    det = None
    if frames_at:
        stills = extract_stills(out_path, frames_at, workdir, 'qa',
                                width=540)
        det = detect_faces(stills)
    if det is None:
        return {'status': 'skipped', 'reason': 'no face detector '
                'available', 'samples': len(all_samples),
                'failures': []}
    by_t = {round(tt, 3): fs for tt, fs in det}
    scale = OUT_W / 540.0

    def fully(f):
        x, y, fw_, fh = (v * scale for v in f)
        return (x >= 8 and y >= 8 and x + fw_ <= OUT_W - 8
                and y + fh <= OUT_H - 8)

    def faces_at(tt):
        tt = round(min(max(tt, 0.05), main_d - 0.1), 2)
        return sum(1 for f in by_t.get(round(tt, 3), []) if fully(f))

    detail, failures = [], []
    for t in all_samples:
        if not speaking(t):
            detail.append({'t': t, 'result': 'skipped_no_speech'})
            continue
        if no_source_face(t):
            detail.append({'t': t, 'result': 'skipped_no_source_face'})
            continue
        if t in boundary:
            # strict: the single frame at t must show the face(s)
            n = faces_at(t)
            ok = n >= expected_faces
            detail.append({'t': t, 'kind': 'boundary',
                           'result': 'pass' if ok else 'FAIL',
                           'faces_ok': n})
            if not ok:
                failures.append(t)
            continue
        ok, best = False, 0
        for dt in (-0.4, 0.0, 0.4):
            n = faces_at(t + dt)
            best = max(best, n)
            if n >= expected_faces:
                ok = True
                break
        detail.append({'t': t, 'kind': 'routine',
                       'result': 'pass' if ok else 'FAIL',
                       'faces_ok': best})
        if not ok:
            failures.append(t)
    return {'status': 'pass' if not failures else 'fail',
            'samples': len(all_samples), 'checked': len(todo),
            'boundary_checked': len(todo_b),
            'expected_faces': expected_faces, 'failures': failures,
            'detail': detail}

def repair_segments(plan, fail_ts, layout, src, info, workdir, ws):
    """One repair attempt for QA-failing segments: re-detect faces on
    the SOURCE at each failing segment's midpoint and re-fit its crop
    around the correct face — inside the segment's panel when the
    moment falls in a source-split passage — one width step wider than
    the failed framing. Mutates the plan entries; returns the repaired
    segment indices."""
    todo = set()
    for t in fail_ts:
        ts = ws + t
        for i, sg in enumerate(plan):
            if sg['s'] - 0.01 <= ts <= sg['e'] + 0.01:
                todo.add(i)
    fixed = []
    scale = min(1.0, 800 / info['w'])
    for i in sorted(todo):
        sg = plan[i]
        mid = (sg['s'] + sg['e']) / 2
        stills = extract_stills(src, [mid], workdir, f'rp{i}',
                                width=int(info['w'] * scale))
        det = detect_faces(stills)
        faces = []
        if det:
            faces = [tuple(v / scale for v in f)
                     for _t, fs in det for f in fs]
        if not faces:
            continue
        region, cand = None, faces
        for p in layout.get('passages') or []:
            if p.get('kind') == 'split' \
                    and p['s'] - 0.01 <= mid <= p['e'] + 0.01:
                split = p['split']
                panels = {'L': (0, 0, split, info['h']),
                          'R': (split, 0, info['w'] - split, info['h'])}
                if sg['side'] in ('L', 'R'):
                    region = panels[sg['side']]
                    in_panel = [f for f in faces
                                if region[0] - 1 <= f[0] + f[2] / 2
                                <= region[0] + region[2] + 1]
                    cand = in_panel or faces
                else:
                    ocx = sg['crop'][0] + sg['crop'][2] / 2
                    f0 = min(faces,
                             key=lambda f: abs(f[0] + f[2] / 2 - ocx))
                    region = panels['L'] if f0[0] + f0[2] / 2 < split \
                        else panels['R']
                    cand = [f0]
                break
        if region is None:
            region = (0, 0, info['w'], info['h'])
            ocx = sg['crop'][0] + sg['crop'][2] / 2
            ocy = sg['crop'][1] + sg['crop'][3] / 2
            cand = [min(faces, key=lambda f: (f[0] + f[2] / 2 - ocx) ** 2
                        + (f[1] + f[3] / 2 - ocy) ** 2)]
        face = max(cand, key=lambda f: f[2] * f[3])
        k = {'PUNCH': 3.25, 'ALT': 3.8}.get(sg['mode'], 4.2)
        sg['crop'] = fit_crop(face, region, k)
        sg['wide'] = fit_crop(face, region, 5.2)
        sg['repaired'] = True
        fixed.append(i)
        log(f'QA repair: segment {i} re-framed on face {face} '
            f'in region {region}')
    return fixed

def fmt_srt(t):
    hh = int(t // 3600); mm = int(t % 3600 // 60); ss = t % 60
    return f"{hh:02d}:{mm:02d}:{ss:06.3f}".replace('.', ',')

# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description='Clip Engine — any video in, '
                                             'studio-standard vertical clip out')
    ap.add_argument('input')
    ap.add_argument('--start', type=float, default=None)
    ap.add_argument('--end', type=float, default=None)
    ap.add_argument('--auto', action='store_true',
                    help='pick the strongest moment automatically')
    ap.add_argument('--duration', type=float, default=45.0)
    ap.add_argument('--layout', choices=('cutaway', 'split'),
                    default='cutaway',
                    help="cutaway: full-frame speaker cuts (default). "
                         "split: both speakers of a two-panel source "
                         "stacked in fixed top/bottom panels")
    ap.add_argument('--hook', default=None, help='hook text override')
    ap.add_argument('--title', default=None,
                    help='attribution text burned in at the top of '
                         'the clip (e.g. the show name). Default: the '
                         'video title for URL inputs; none for local '
                         'files — a filename is never used')
    ap.add_argument('--overlay', default=None, metavar='IMAGE',
                    help='point-and-appear visual overlay: image path or '
                         'http(s) URL (max ONE per clip)')
    ap.add_argument('--overlay-at', type=float, default=None,
                    help='clip-relative seconds when the overlay flies in '
                         '(required with --overlay)')
    ap.add_argument('--overlay-dur', type=float, default=3.5,
                    help='overlay on-screen seconds incl. in/out '
                         'animation (default 3.5)')
    ap.add_argument('--overlay-pos', default='upper-right',
                    help="settle position: upper-right (default, beside "
                         "the speaker's head), upper-left, lower-right, "
                         "lower-left, or explicit 'X,Y'")
    ap.add_argument('--overlay-w', type=int, default=OV_W,
                    help='settled overlay image width in px '
                         f'(default {OV_W}) — go smaller when the '
                         'speaker fills the frame')
    ap.add_argument('--style', choices=('standard', 'bar', 'minimal',
                                        'random'),
                    default='standard',
                    help="style register: 'standard' is the approved "
                         "recipe; 'bar'/'minimal' vary expression only "
                         "(hook treatment, caption accent behaviour, "
                         "end-card styling); 'random' picks one from "
                         "a seeded RNG (see --seed) and records both "
                         "in the build log")
    ap.add_argument('--seed', type=int, default=None,
                    help='seed for --style random (default: derived '
                         'from the clock, recorded in the build log)')
    ap.add_argument('--brand', default=None, metavar='KIT.json',
                    help='brand kit JSON {name, logo, accent, '
                         'end_card_name} (default: the studio kit '
                         'in brands/)')
    ap.add_argument('--corrections', default=None, metavar='MAP.json',
                    help='word->replacement JSON map applied to the '
                         'transcript before captions are built '
                         '(correction memory for misheard names)')
    ap.add_argument('--captions-file', default=None,
                    metavar='CUES',
                    help='edited captions (.srt or .json cue list, '
                         'clip timeline): those words are burned on '
                         'the original word timings — no '
                         're-transcription')
    ap.add_argument('--out', default=None)
    ap.add_argument('--model', default='base.en')
    ap.add_argument('--keep-work', action='store_true')
    args = ap.parse_args()

    out = args.out or os.path.join(
        os.getcwd(), re.sub(r'[^A-Za-z0-9]+', '-',
                            os.path.splitext(os.path.basename(
                                args.input.split('?')[0]))[0])[:40] + '-clip.mp4')
    runs_root = os.path.join(ENGINE_DIR, 'runs')
    os.makedirs(runs_root, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix='clip-engine-', dir=runs_root)
    log(f'workdir {workdir}')
    build = {'input': args.input, 'tool': 'clip-engine v1'}
    # ---- brand kit + style register (fail fast, before the
    # expensive stages); both are recorded so the clip is
    # reproducible from its build log alone
    brand = load_brand(args.brand or DEFAULT_BRAND)
    style_name, style_seed = resolve_style(args.style, args.seed)
    build['brand'] = {'name': brand['name'],
                      'accent': brand['accent_hex'],
                      'end_card_name': brand['end_card_name'],
                      'logo': brand['logo'], 'file': brand['file']}
    build['style'] = {'requested': args.style, 'name': style_name,
                      'seed': style_seed}
    log(f"style '{style_name}'"
        + (f" (random pick, seed {style_seed})"
           if args.style == 'random' else '')
        + f" | brand '{brand['name']}' accent {brand['accent_hex']}")
    corrections = None
    if args.corrections:
        try:
            with open(args.corrections) as f:
                corrections = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise SystemExit(f'corrections unreadable: '
                             f'{args.corrections} ({e})')
        if not isinstance(corrections, dict):
            raise SystemExit('corrections file must be a JSON object '
                             '{wrong_word: replacement}')
    ov_cues = (parse_captions_file(args.captions_file)
               if args.captions_file else None)
    try:
        src, title = ingest(args.input, workdir)
        title = args.title or title
        if title:
            build['title'] = title
        info = probe(src)
        build['source'] = info
        log(f"source {info['w']}x{info['h']} @{info['fps']:.1f}fps "
            f"{info['dur']:.0f}s — {title!r}")
        words = transcribe(src, workdir, args.model)
        if corrections:
            applied = apply_corrections(words, corrections)
            build['corrections'] = {
                'file': os.path.abspath(args.corrections),
                'map': corrections, 'applied': applied}
            log(f'corrections applied to transcript: {applied}')

        # ---- moment
        if args.start is not None:
            ws = max(0.0, args.start)
            we = args.end if args.end else ws + args.duration
            span = [w for w in words if w['e'] > ws and w['s'] < we]
            if not span:
                raise SystemExit('no speech inside the requested window')
            ws, we = span[0]['s'] - 0.05, span[-1]['e'] + 0.05
            build['moment'] = {'mode': 'explicit', 'start': ws, 'end': we}
        else:
            top, chosen = pick_window(words, args.duration)
            print('\n=== AUTO MOMENT — top candidates ===')
            for c in top:
                line = ' '.join(w['w'] for w in c['span'][:14])
                print(f"  {c['s']:7.1f}s–{c['e']:7.1f}s  score {c['score']:6.2f}  "
                      f"{line}...")
            print('====================================\n')
            span = chosen['span']
            ws, we = span[0]['s'] - 0.05, span[-1]['e'] + 0.05
            build['moment'] = {'mode': 'auto', 'start': ws, 'end': we,
                               'score': chosen['score'],
                               'features': chosen['feats'],
                               'candidates': [
                                   {'start': c['s'], 'end': c['e'],
                                    'score': c['score']} for c in top]}
        peak = peak_sentence(span)
        peak_line = ' '.join(w['w'] for w in peak)
        build['peak_line'] = peak_line
        log(f'moment {ws:.1f}s–{we:.1f}s | peak line: {peak_line!r}')
        if args.hook:
            hot_words = {'million', 'billion', 'thousand'}
            hook_toks = [(t.upper(), any(c.isdigit() for c in t)
                          or t.lower() in hot_words)
                         for t in args.hook.split()]
        else:
            hook_toks = derive_hook(peak)
        build['hook'] = ' '.join(t for t, _ in hook_toks)
        log(f"hook: {build['hook']!r}")

        # ---- point-and-appear overlay (validated + frames rendered NOW,
        # before the expensive stages, so bad arguments fail fast)
        ov = None
        if args.overlay:
            if args.overlay_at is None:
                raise SystemExit('--overlay requires --overlay-at '
                                 '(clip-relative seconds)')
            clip_d = we - ws
            if not (0.0 <= args.overlay_at < clip_d):
                raise SystemExit(
                    f'--overlay-at {args.overlay_at} is outside the clip '
                    f'(0–{clip_d:.1f}s)')
            ov_dur = min(args.overlay_dur, clip_d - args.overlay_at)
            if ov_dur < args.overlay_dur:
                log(f'overlay duration clamped to {ov_dur:.2f}s (would '
                    'run past the end of the main content)')
            if ov_dur < 1.2:
                raise SystemExit('overlay window too short (<1.2s after '
                                 'clamping) — move --overlay-at earlier')
            ov_src = fetch_overlay_image(args.overlay, workdir)
            ov_tile, ov_box = prepare_overlay_tile(ov_src,
                                                   width=args.overlay_w)
            settle = overlay_settle(args.overlay_pos, ov_box[2], ov_box[3])
            slide = (1 if settle[0] + ov_box[2] / 2 >= OUT_W / 2 else -1)
            ov_dir, ov_n = render_overlay_frames(
                workdir, ov_dur, ov_tile, ov_box, settle, slide)
            ov = {'dir': ov_dir, 'at': args.overlay_at}
            build['overlay'] = {
                'source': args.overlay, 'at': args.overlay_at,
                'dur': round(ov_dur, 2), 'pos': args.overlay_pos,
                'width': args.overlay_w,
                'settle_xy': settle, 'img_wh': [ov_box[2], ov_box[3]],
                'entry_s': OV_ENTRY, 'exit_s': OV_EXIT, 'frames': ov_n}
            log(f"overlay: {args.overlay!r} at {args.overlay_at:.2f}s "
                f'for {ov_dur:.2f}s, settle {settle}, '
                f'image {ov_box[2]}x{ov_box[3]}')

        # ---- framing
        layout = analyse_layout(src, info['w'], info['h'], ws, we, workdir)
        layout['_srcw'], layout['_srch'] = info['w'], info['h']
        build['layout'] = {k: v for k, v in layout.items()
                           if not k.startswith('_')}
        # mouth-motion turns INSIDE each source-split passage, so a
        # passage framed mid-window cuts to the active speaker's panel
        for p in layout.get('passages') or []:
            if p.get('kind') == 'split' and layout['kind'] != 'two':
                pl = {'faces': p['faces'], 'split': p['split'],
                      '_srcw': info['w'], '_srch': info['h']}
                rt = active_speaker_turns(src, pl, p['s'], p['e'],
                                          workdir)
                p['turns'] = [(round(p['s'] + t, 2), side)
                              for t, side in rt]
                if p['turns']:
                    log(f"passage {p['s'] - ws:.1f}–{p['e'] - ws:.1f}s "
                        f"(clip time) turns: "
                        f"{[(round(t - ws, 1), s) for t, s in p['turns']]}")
        log(f"layout: {layout['kind']} via {layout['method']}"
            + (f" split={layout['split']:.0f}" if layout['split'] else ''))
        build['layout_mode'] = args.layout
        panels, plan = None, []
        if args.layout == 'split':
            if layout['kind'] != 'two':
                raise SystemExit(
                    '--layout split needs a two-panel (side-by-side) '
                    'source with two speakers, but this source analysed '
                    f"as single-speaker (via {layout['method']}). "
                    'Re-run with --layout cutaway (the default) — it '
                    'frames the single speaker full-frame instead.')
            panels = plan_split_panels(layout)
            build['split_panels'] = panels
            for posn in ('top', 'bottom'):
                p = panels[posn]
                log(f"  split {posn} ({p['side']}): crop={p['crop']} "
                    f"wide={p['wide']}")
        else:
            turns = []
            if layout['kind'] == 'two':
                raw_turns = active_speaker_turns(src, layout, ws, we, workdir)
                for t_rel, side in raw_turns:
                    abs_t = ws + t_rel
                    nxt = next((w for w in span if w['s'] >= abs_t - 0.35), None)
                    if nxt:      # cut 0.15s BEFORE the incoming speaker's word
                        abs_t = max(ws + 0.1, min(nxt['s'] - 0.15, we - 0.5))
                    turns.append((float(round(abs_t - ws, 2)), side))
                # dedupe consecutive same-side
                turns = [t for i, t in enumerate(turns)
                         if i == 0 or t[1] != turns[i - 1][1]]
                build['turns'] = turns
                log(f'speaker turns: {turns}')
            plan, _crops = plan_segments(layout, turns, peak, ws, we, span)
            build['plan'] = plan
            for sg in plan:
                log(f"  seg {sg['s']:.1f}–{sg['e']:.1f} {sg['side']}/{sg['mode']} "
                    f"crop={sg['crop']}")

        # ---- render
        main_d = we - ws
        total = main_d + 0.8          # 1.0s card minus 0.2s crossfade
        win = render_window(src, ws, main_d, workdir)
        pieces = []
        if panels is not None:
            # Pupa's split rule: ONE static piece — no opening push,
            # no punch-in, no zoom of any kind; both panels hold the
            # steady own-camera framing for the entire clip
            specs = [{'s': ws, 'e': we, 'mode': 'SPLIT'}]
            for i, sp in enumerate(specs):
                sp['_ws'] = ws
                sp['panels'] = panels
                pieces.append(render_split_piece(win, sp, i, workdir))
                log(f'split piece {i} rendered ({sp["mode"]})')
            build['plan'] = [{'mode': sp['mode'], 's': round(sp['s'], 2),
                              'e': round(sp['e'], 2)} for sp in specs]
        else:
            for i, sg in enumerate(plan):
                sg['_ws'] = ws
                pieces.append(render_piece(win, sg, i, workdir))
                log(f'piece {i} rendered')
        card = render_card(workdir, brand, style_name)

        cap_span = span
        if ov_cues is not None:
            cap_span, ov_stats = apply_caption_override(span, ov_cues,
                                                        ws)
            build['captions_override'] = {
                'file': os.path.abspath(args.captions_file),
                **ov_stats}
            log(f"captions override: {ov_stats['cues']} cues, "
                f"{ov_stats['words_replaced']} transcript words "
                'replaced on their original timings')
        cues = build_cues(cap_span, ws)
        # the hook must never sit on the speaker's face: compute the
        # eye line it has to clear while it is up (0–3.3s)
        if panels is not None:
            eye_limit = hook_eye_limit(None, layout, ws, panels)
        else:
            eye_limit = hook_eye_limit(plan, layout, ws)
        if eye_limit is not None:
            build['hook_eye_limit'] = round(eye_limit)
            log(f'hook eye limit: block bottom must stay above '
                f'y={eye_limit:.0f}')
        if panels is not None:
            # captions centred on the seam; hook smaller, higher, capped
            # at 2 lines so it stays in the top panel's headroom
            fdir = render_text_frames(
                workdir, total, main_d, cues, hook_toks, title,
                cap_y=SEAM_Y - 170, hook_y=52, hook_max_size=84,
                hook_max_lines=2, hide_title_with_hook=True,
                accent=brand['accent'], style=style_name,
                eye_limit=eye_limit)
        else:
            fdir = render_text_frames(workdir, total, main_d, cues,
                                      hook_toks, title,
                                      accent=brand['accent'],
                                      style=style_name,
                                      eye_limit=eye_limit)
        # ---- join + composite + QA GATE (one repair attempt allowed)
        if panels is not None:
            piece_counts = [round((we - ws) * FPS)]
        else:
            piece_counts = [round((sg['e'] - ws) * FPS)
                            - round((sg['s'] - ws) * FPS) for sg in plan]
        qa = None
        for attempt in (1, 2):
            joined = join_pieces(pieces, card, main_d, workdir,
                                 piece_counts)
            meas = final_composite(joined, fdir, total, out, ov)
            build['loudnorm_input'] = meas
            qa = qa_gate(out, build.get('plan') or plan, ws, main_d,
                         span, layout.get('passages'),
                         2 if panels is not None else 1, workdir)
            build['qa_gate'] = qa
            log(f"QA gate (attempt {attempt}): {qa['status']} — "
                f"{qa.get('checked', 0)} samples checked, "
                f"failures at {qa.get('failures')}")
            if qa['status'] != 'fail' or attempt == 2:
                break
            if panels is not None:
                wider = plan_split_panels(layout, k=SPLIT_K + 0.8)
                panels.clear()
                panels.update(wider)
                log(f'QA repair: split panels re-framed wider '
                    f'(k={SPLIT_K + 0.8})')
                for i, sp in enumerate(specs):
                    pieces[i] = render_split_piece(win, sp, i, workdir)
                build['qa_repair'] = {'mode': 'split-widen',
                                      'attempt': attempt}
            else:
                fixed = repair_segments(plan, qa['failures'], layout,
                                        src, info, workdir, ws)
                if not fixed:
                    log('QA repair: no segment could be re-framed')
                    break
                for i in fixed:
                    pieces[i] = render_piece(win, plan[i], i, workdir)
                build['qa_repair'] = {'mode': 'reframe',
                                      'segments': fixed,
                                      'attempt': attempt}
        ver = loudnorm_measure(out)
        build['loudnorm_output'] = ver
        if ver:
            log(f"loudness OUT: integrated {ver['input_i']} LUFS, "
                f"true peak {ver['input_tp']} dBTP")
        srt_path = os.path.splitext(out)[0] + '.srt'
        with open(srt_path, 'w') as f:
            for i, (cs, ce, cwords) in enumerate(cues, 1):
                f.write(f"{i}\n{fmt_srt(cs)} --> {fmt_srt(ce)}\n"
                        f"{' '.join(w for (w, _s, _e) in cwords)}\n\n")
        with open(os.path.splitext(out)[0] + '.build.json', 'w') as f:
            json.dump(build, f, indent=1, default=str)
        if qa is not None and qa['status'] == 'fail':
            raise SystemExit(
                'QA GATE FAILED: no fully-visible face during speech '
                f"at clip times {qa['failures']} — the file was written "
                'for inspection but is marked FAILED in its build log; '
                'do not ship it')
        log(f'DONE -> {out} (+ {srt_path}, build log)')
    finally:
        if not args.keep_work:
            shutil.rmtree(workdir, ignore_errors=True)

if __name__ == '__main__':
    main()
