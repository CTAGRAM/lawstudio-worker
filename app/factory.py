"""LawStudio factory — turns a dashboard job into a finished branded video.
produce_video: storyboard (Fable 5) -> TTS -> stills -> omni i2v clips, uploading every
artifact to the Supabase library and updating video_beats live.
assemble_video: brand intro + beats + brand outro, ASS subtitles, VO+bed master, grade, upload.
reroll_beat: regenerate one beat's still+clip with an edit prompt."""
import json, subprocess, uuid, time
from pathlib import Path
from pipeline import lib, supa

# A beat that fails is retried in place before it is marked failed — most
# failures are transient (model hiccup, rate limit, a flaky ffmpeg run).
BEAT_TRIES = 3
BEAT_RETRY_S = 20

import os as _os
ASSET_ROOT = _os.environ.get('ASSETS_ROOT')
def _asset(name, mac_default):
    return f"{ASSET_ROOT}/{name}" if ASSET_ROOT else mac_default
RUNS = Path(_os.environ.get('RUNS_DIR', '/Users/rudra/OpenMontage/runs')); RUNS.mkdir(parents=True, exist_ok=True)
FONTS_DIR = _os.environ.get('FONTS_DIR', '/Users/rudra/Library/Fonts')
CHECK_FONT = _os.environ.get('CHECK_FONT', '/System/Library/Fonts/Menlo.ttc')
_KIDS_A = '/Users/rudra/OpenMontage/worker-deploy/app/assets'
STYLE_REFS = {
    'vyond': [_asset('f13.png', '/Users/rudra/OpenMontage/projects/omni-i2v/frames/f13.png'),
              _asset('sp_05.png', '/Users/rudra/OpenMontage/projects/omni-i2v/frames/sp_05.png')],
    'kids': [_asset('kids_maya.png', f'{_KIDS_A}/kids_maya.png'),
             _asset('kids_pip.png', f'{_KIDS_A}/kids_pip.png')],
}
VYOND_STYLE = ("Flat 2D vector explainer cartoon in EXACTLY the style of the reference images: warm flat colors, "
    "simple geometric shapes, minimal facial features, soft shadows, clean uncluttered rooms. "
    "The MAIN CHARACTER is exactly the man from the reference images: dark swept hair, round glasses, "
    "light blue t-shirt, dark trousers, teal shoes — do not redesign him. Wide 16:9 composition. "
    "No text, letters, numbers, logos or watermarks anywhere.")
VOX_STYLE = ("Vox-style editorial paper collage animation, 16:9. {bg} covered in a subtle halftone dot texture, "
    "light film grain, soft vignette. Cutout elements with torn paper edges and thin white borders, "
    "black-and-white halftone photo fragments, hand-drawn black ink accents. Smooth slow camera drift. "
    "Absolutely no text, no letters, no numbers, no logos, no watermarks anywhere.")
KIDS_STYLE = ("Bright, friendly flat-2D vector cartoon for a children's educational video, in EXACTLY the style of "
    "the reference images: clean rounded shapes, thick smooth outlines, warm cheerful palette (sunny yellow, sky "
    "blue, coral, mint), soft simple shading, wholesome and polished. Use the reference characters exactly as "
    "shown — do not redesign them. Wide 16:9, simple uncluttered kid-friendly settings (sunny classroom, cozy "
    "home, outdoors, space). Big expressive happy faces. No text, letters, numbers, logos or watermarks anywhere.")

CHARACTERS = {
    'main':   {'name': 'Main (glasses, blue tee)', 'refs': [_asset('f13.png','/Users/rudra/OpenMontage/projects/omni-i2v/frames/f13.png'), _asset('sp_05.png','/Users/rudra/OpenMontage/projects/omni-i2v/frames/sp_05.png')], 'desc': 'a man with round glasses and a light blue t-shirt'},
    'client': {'name': 'Client (Sunlight)', 'refs': [_asset('cl_ref.png','/Users/rudra/OpenMontage/remotion-composer/public/golegal-assets/sun/cl_ref.png')], 'desc': 'a young male client'},
    'lawyer': {'name': 'Senior Lawyer', 'refs': [_asset('sl_ref.png','/Users/rudra/OpenMontage/remotion-composer/public/golegal-assets/sun/sl_ref.png')], 'desc': 'a senior lawyer in a bow tie'},
    'amara':  {'name': 'Amara (adviser)', 'refs': [_asset('aw_ref.png','/Users/rudra/OpenMontage/remotion-composer/public/golegal-assets/sun/aw_ref.png')], 'desc': 'a female adviser'},
    'ben':    {'name': 'Ben (manager)', 'refs': [_asset('bs_ref.png','/Users/rudra/OpenMontage/remotion-composer/public/golegal-assets/sun/bs_ref.png')], 'desc': 'an older male manager'},
    # kids-education cast
    'maya': {'name': 'Maya (kid)', 'refs': [_asset('kids_maya.png', f'{_KIDS_A}/kids_maya.png')], 'desc': 'Maya, a curious 8-year-old girl with dark curly puffs, round glasses and yellow dungarees'},
    'leo':  {'name': 'Leo (kid)', 'refs': [_asset('kids_leo.png', f'{_KIDS_A}/kids_leo.png')], 'desc': 'Leo, a cheerful 7-year-old boy in a red hoodie and blue shorts'},
    'pip':  {'name': 'Professor Pip (owl)', 'refs': [_asset('kids_pip.png', f'{_KIDS_A}/kids_pip.png')], 'desc': 'Professor Pip, a friendly owl mascot guide with a graduation cap'},
}

# which roster each style may draw from — stops a kids run pulling the legal
# cast (or vice-versa) into its scene references
STYLE_CAST = {
    'kids': {'maya', 'leo', 'pip'},
    'vyond': {'main', 'client', 'lawyer', 'amara', 'ben'},
}

_CHAR_CACHE = {'at': 0.0, 'rows': []}

def _db_characters():
    """User-created characters (name + description + one locked reference image)."""
    if time.time() - _CHAR_CACHE['at'] > 60:
        try:
            _CHAR_CACHE['rows'] = supa.select('characters')
            _CHAR_CACHE['at'] = time.time()
        except Exception:
            pass
    return _CHAR_CACHE['rows'] or []

def _fetch_ref(url):
    try:
        import requests
        r = requests.get(url, timeout=60)
        if r.ok: return r.content
    except Exception:
        pass
    return None

def _cast_for_beat(beat, video_cast, style='vyond'):
    """Return (ref_bytes_list, descriptor_text) for a scene beat honoring its chosen cast."""
    db = {c['key']: c for c in _db_characters()
          if not c.get('style') or not style or c.get('style') == style}
    keys = (beat.get('meta') or {}).get('cast')
    allowed = STYLE_CAST.get(style)
    if keys and allowed:
        custom_keys = set((video_cast or {}).get('custom', {}))
        keys = [k for k in keys if k in allowed or k in custom_keys or k in db]
    if not keys: keys = ['pip'] if style == 'kids' else ['main']
    refs, descs = [], []
    custom = (video_cast or {}).get('custom', {})   # {key: {name, desc}}
    for k in keys:
        if k in CHARACTERS:
            for rp in CHARACTERS[k]['refs']:
                try: refs.append(Path(rp).read_bytes())
                except Exception: pass
            descs.append(CHARACTERS[k]['desc'])
        elif k in db:
            c = db[k]
            if c.get('ref_url'):
                img = _fetch_ref(c['ref_url'])
                if img: refs.append(img)
            descs.append(f"{c.get('name')}, {c.get('description') or ''}".strip(', '))
        elif k in custom:
            descs.append(custom[k].get('desc', ''))
    return refs, ('; '.join([d for d in descs if d]))

def _dur(p):
    return float(subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',str(p)],
                                capture_output=True, text=True).stdout.strip() or 0)

def _ff_err(stderr):
    """ffmpeg floods stderr with progress lines, so a plain tail hides the real
    cause. Keep the lines that actually explain the failure."""
    lines = [l.strip() for l in (stderr or '').splitlines() if l.strip()]
    bad = [l for l in lines if any(k in l for k in (
        'Error', 'error', 'Invalid', 'invalid', 'No such', 'not found', 'Unable',
        'failed', 'Failed', 'Cannot', 'No space', 'Permission denied', 'Conversion failed'))]
    keep = bad[-6:] if bad else [l for l in lines if 'fps=' not in l][-6:]
    return '\n'.join(keep) or '\n'.join(lines[-4:])

def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode: raise RuntimeError('ffmpeg: ' + _ff_err(r.stderr))

def fetch_article(url):
    """Fetch an article and reduce to readable text (for summarize-and-transform scripting)."""
    import re, requests as rq
    html = rq.get(url, timeout=30, headers={'User-Agent': 'Mozilla/5.0'}).text
    html = re.sub(r'<script.*?</script>|<style.*?</style>|<!--.*?-->', ' ', html, flags=re.S | re.I)
    body = re.search(r'<article.*?</article>', html, re.S | re.I)
    txt = re.sub(r'<[^>]+>', ' ', body.group(0) if body else html)
    txt = re.sub(r'\s+', ' ', txt).strip()
    return txt[:9000]

LONG_FORM_S = 240      # above this we storyboard section by section
SECTION_S = 120        # each chunk covers roughly this much screen time

def storyboard(style, topic, script, article_url=None, target_seconds=None, learnings=None):
    # long briefs can't come back as one JSON blob — outline first, then fill in
    # each section, so a 25-minute video is just many small, reliable calls.
    if target_seconds and target_seconds > LONG_FORM_S and not script:
        return _storyboard_long(style, topic, article_url, int(target_seconds), learnings)
    auto = target_seconds is None
    n_beats = None if auto else max(3, min(30, round(target_seconds / 9)))
    if article_url:
        art = fetch_article(article_url)
        src = ("SOURCE ARTICLE (background research ONLY — write completely ORIGINAL narration in your own words; "
               "summarize and transform the facts, never copy sentences or phrasing from it):\n" + art +
               ("\n\nANGLE: " + topic if topic else ""))
    elif script:
        src = (f"USER SCRIPT (chunk into beats following its NATURAL structure — one beat per thought/paragraph, "
               f"typically {n_beats} beats but let the script decide within +/-3; keep wording close):\n{script}")
    else:
        src = f"TOPIC: {topic}"
    if style == 'vyond':
        guide = ("MIX the beat kinds like a professional explainer (roughly 5 scenes, 2 boards, 1 stat for 8 beats; "
                 "never two graphic beats in a row; open and close with a scene). Every beat has: id (b01_slug), kind, "
                 "vo (1-2 spoken sentences, UK English, plain and reassuring).\n"
                 "kind 'scene' additionally: still (wide 16:9 flat-2D scene featuring THE MAIN CHARACTER — a man with "
                 "round glasses and light blue t-shirt — in warm rooms: office/home/meeting; concrete furniture and "
                 "props; STRICT RULE: describe absolutely NO text, writing, signs, labels, charts with words or boards "
                 "with lettering — all on-screen words are added by a separate graphics layer), "
                 "motion (subtle flat-2D animation directions, static camera).\n"
                 "kind 'board' additionally: board {title (max 4 words), bullets (3-5 short items, max 4 words each)} — "
                 "a whiteboard-style build that visualises the vo.\n"
                 "kind 'stat' additionally: stat {value (a short figure or phrase like '£12.21' or 'Age 21+'), label "
                 "(max 6 words)} — one big takeaway number/fact.")
    elif style == 'kids':
        guide = ("MIX the beat kinds like a great kids' educational episode (roughly 6 scenes, 2 boards, 1 stat for "
                 "9 beats; never two graphic beats in a row; open with a hook question and close with a warm "
                 "recap). Every beat has: id (b01_slug), kind, vo (1-2 spoken sentences of simple, warm, playful "
                 "narration a 6-10 year old understands — short words, concrete examples, no jargon).\n"
                 "kind 'scene' additionally: still (wide 16:9 bright flat-2D cartoon scene featuring the CAST — "
                 "MAYA (girl, glasses, yellow dungarees), LEO (boy, red hoodie) and PROFESSOR PIP (friendly owl "
                 "teacher) — in cheerful kid settings: sunny classroom, cozy home, garden, outer space; concrete "
                 "props; STRICT RULE: describe absolutely NO text, writing, signs, labels or lettering — all "
                 "on-screen words come from a separate graphics layer), "
                 "motion (gentle playful flat-2D animation directions, static camera), "
                 "cast (array naming which of 'maya','leo','pip' appear).\n"
                 "kind 'board' additionally: board {title (max 4 words), bullets (3-5 short items, max 4 words "
                 "each)} — a colourful pin-board that visualises the vo.\n"
                 "kind 'stat' additionally: stat {value (a short fun figure like '8 legs' or '365 days'), label "
                 "(max 6 words)} — one big memorable fact.")
    else:
        guide = ("Each beat: id, kind 'scene', vo (energetic editorial narration), "
                 "scene (a paper-collage visual metaphor: cutout photos, banknotes, crowds, arrows, charts — no characters needed), "
                 "bg (one of: 'Flat mustard-yellow paper background','Flat brick-red paper background','Flat deep-navy background with halftone dots','Flat cream paper background').")
    scope = ("YOU decide the right length and number of beats (6-18) from the topic's depth — a simple point is "
             "short, a rich topic runs longer; do not pad or truncate."
             if auto else
             f"Around {n_beats} beats (use fewer/more only if genuinely needed), ~{target_seconds}s spoken.")
    learn = (f"\n\nLEARNINGS FROM PAST PERFORMANCE (apply these to improve reach/engagement):\n{learnings}\n"
             if learnings else "")
    # The director profile must match the vertical — a kids channel briefed as a
    # "UK legal-explainer studio" turns "wheels on the bus" into bus licence law.
    profile = DIRECTORS.get(style, DIRECTORS['vyond'])
    prompt = (f"{profile['who']} Write a storyboard as JSON.\n{src}\n\n"
              f"Style: {style}. {scope} {guide}{learn}\n"
              f"{profile['rules']} "
              'Return JSON: {"title": "...", "beats": [ ... ]}')
    return json.loads(lib.text_gen(prompt))

def _storyboard_long(style, topic, article_url, target_seconds, learnings):
    """Long form: outline the sections, then storyboard each one and stitch the
    beats together. Keeps every model call small enough to be reliable."""
    src = topic or ''
    if article_url:
        src = (fetch_article(article_url) + ("\n\nANGLE: " + topic if topic else ''))
    n_sections = max(2, round(target_seconds / SECTION_S))
    profile = DIRECTORS.get(style, DIRECTORS['vyond'])
    outline = json.loads(lib.text_gen(
        f"{profile['who']} Plan a {round(target_seconds/60)}-minute video on:\n{src}\n\n"
        f"Break it into exactly {n_sections} sections that flow as one continuous video "
        f"(a hook, then the substance in a logical build, then a close). {profile['rules']}\n"
        'Return ONLY JSON: {"title": "...", "sections": [{"heading": "...", "covers": '
        '"one sentence on exactly what this section explains"}]}'))
    sections = (outline.get('sections') or [])[:n_sections]
    beats, seen = [], 0
    for i, sec in enumerate(sections):
        part = storyboard(style, f"{sec.get('heading','')} — {sec.get('covers','')}", None,
                          target_seconds=SECTION_S, learnings=learnings)
        for b in (part.get('beats') or []):
            b['id'] = f"s{i:02d}_{b.get('id', 'b')}"
            beats.append(b)
        seen += 1
        print(f'  long-form: section {seen}/{len(sections)} -> {len(beats)} beats', flush=True)
    return {'title': outline.get('title') or topic, 'beats': beats}

BOARD_FONT_EB = _asset('Poppins-ExtraBold.ttf', '/Users/rudra/OpenMontage/remotion-composer/public/fonts/Poppins-ExtraBold.ttf')
BOARD_FONT_SB = _asset('Poppins-SemiBold.ttf', '/Users/rudra/OpenMontage/remotion-composer/public/fonts/Poppins-SemiBold.ttf')
MENLO = CHECK_FONT

def _hex2rgb(h):
    h = (h or '').lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4)) if len(h) == 6 else (0, 37, 56)

def _draw_icon(d, cx, cy, r, kind, navy):
    """Simple flat line icons inside a white circle (channel grammar)."""
    lw = max(5, r // 8)
    if kind == 'coins':
        for i, dy in enumerate((10, -6, -22)):
            d.ellipse([cx-r*0.55, cy+dy-8, cx+r*0.55, cy+dy+8], outline=navy, width=lw-1)
    elif kind == 'clock':
        d.ellipse([cx-r*0.6, cy-r*0.6, cx+r*0.6, cy+r*0.6], outline=navy, width=lw)
        d.line([cx, cy, cx, cy-r*0.35], fill=navy, width=lw-1)
        d.line([cx, cy, cx+r*0.28, cy+r*0.1], fill=navy, width=lw-1)
    elif kind == 'doc':
        d.rectangle([cx-r*0.42, cy-r*0.58, cx+r*0.42, cy+r*0.58], outline=navy, width=lw-1)
        for i in range(3):
            y = cy - r*0.28 + i * r*0.28
            d.line([cx-r*0.24, y, cx+r*0.24, y], fill=navy, width=lw-2)
    elif kind == 'scales':
        d.line([cx, cy-r*0.5, cx, cy+r*0.4], fill=navy, width=lw-1)
        d.line([cx-r*0.45, cy-r*0.25, cx+r*0.45, cy-r*0.25], fill=navy, width=lw-1)
        for sx in (-1, 1):
            px = cx + sx*r*0.45
            d.arc([px-r*0.2, cy-r*0.22, px+r*0.2, cy+r*0.12], 0, 180, fill=navy, width=lw-2)
        d.line([cx-r*0.25, cy+r*0.4, cx+r*0.25, cy+r*0.4], fill=navy, width=lw-1)
    elif kind == 'person':
        d.ellipse([cx-r*0.22, cy-r*0.55, cx+r*0.22, cy-r*0.1], outline=navy, width=lw-1)
        d.arc([cx-r*0.5, cy-r*0.05, cx+r*0.5, cy+r*0.85], 180, 360, fill=navy, width=lw-1)
    elif kind == 'warning':
        d.polygon([(cx, cy-r*0.55), (cx-r*0.55, cy+r*0.45), (cx+r*0.55, cy+r*0.45)], outline=navy, width=lw-1)
        d.line([cx, cy-r*0.2, cx, cy+r*0.12], fill=navy, width=lw-1)
        d.ellipse([cx-3, cy+r*0.24, cx+3, cy+r*0.24+6], fill=navy)
    else:  # check
        d.line([cx-r*0.35, cy+r*0.02, cx-r*0.08, cy+r*0.3], fill=navy, width=lw)
        d.line([cx-r*0.08, cy+r*0.3, cx+r*0.42, cy-r*0.28], fill=navy, width=lw)

def _icon_for(text):
    s = str(text).lower()
    for kws, k in [(('pay','money','wage','cost','fee','£','compensation','offer'), 'coins'),
                   (('time','notice','period','deadline','date','april','wait'), 'clock'),
                   (('document','contract','agreement','record','form','letter','clause'), 'doc'),
                   (('fair','law','legal','court','tribunal','rights'), 'scales'),
                   (('people','person','staff','employee','skill','experience','team','consult'), 'person'),
                   (('risk','warning','avoid','danger','mistake','pressure'), 'warning')]:
        if any(k_ in s for k_ in kws): return k
    return 'check'

def render_board(spec, palette, still_png, out_mp4, dur):
    """Channel-grammar board: quiet title + icon-circle rows revealing sequentially."""
    from PIL import Image, ImageDraw, ImageFont
    from pathlib import Path as _P
    navy = _hex2rgb((palette or {}).get('navy', '#002538'))
    ft = ImageFont.truetype(BOARD_FONT_SB, 66); fb = ImageFont.truetype(BOARD_FONT_SB, 54)
    base = Image.new('RGB', (1920, 1080), navy)
    d = ImageDraw.Draw(base)
    title = str(spec.get('title', ''))
    tb = d.textbbox((0, 0), title, font=ft)
    bullets = [str(b) for b in (spec.get('bullets') or [])[:5]]
    y0 = 320 if len(bullets) >= 4 else 380
    step = 132
    # centre the title+rows block vertically instead of letting it ride high
    dy = max(0, (1080 - ((y0 + max(len(bullets) - 1, 0) * step + 52) - 120)) // 2 - 120)
    y0 += dy
    d.text(((1920 - tb[2]) // 2, 120 + dy), title, font=ft, fill=(255, 255, 255))
    tmp = _P(still_png).parent
    rows = []
    for i, btxt in enumerate(bullets):
        cy = y0 + i * step
        ov = Image.new('RGBA', (1920, 1080), (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        od.ellipse([560-52, cy-52, 560+52, cy+52], fill=(255, 255, 255, 255))
        _draw_icon(od, 560, cy, 52, _icon_for(btxt), navy)
        od.text((680, cy - 32), btxt, font=fb, fill=(255, 255, 255, 255))
        rp = tmp / f'_row{i}.png'; ov.save(rp); rows.append(str(rp))
    comp = base.copy()
    for rp in rows:
        comp.alpha_composite(Image.open(rp)) if comp.mode == 'RGBA' else comp.paste(Image.open(rp), (0, 0), Image.open(rp))
    comp.convert('RGB').save(still_png)
    # Composite the reveal in PIL and encode from a concat list of stills.
    # (Chaining N full-HD -loop inputs through overlay filters made ffmpeg get
    # OOM-killed on the 1GB worker — it died right after opening the encoder.)
    state = base.copy()          # navy + title already drawn on `base`
    steps = []                   # (png_path, seconds_to_hold)
    lead = 0.6
    per = max(0.35, (dur - lead) / max(len(rows), 1))
    p0 = tmp / '_b_state0.png'; state.convert('RGB').save(p0)
    steps.append((p0, lead))
    for i, rp in enumerate(rows):
        row = Image.open(rp).convert('RGBA')
        # 3-frame alpha fade so the row eases in rather than popping
        for k, a in enumerate((0.4, 0.75, 1.0)):
            frame = state.copy().convert('RGBA')
            if a < 1.0:
                faded = row.copy()
                faded.putalpha(row.getchannel('A').point(lambda v, a=a: int(v * a)))
                frame.alpha_composite(faded)
            else:
                frame.alpha_composite(row)
            fp = tmp / f'_b_r{i}_{k}.png'; frame.convert('RGB').save(fp)
            steps.append((fp, 0.06))
        state = state.convert('RGBA'); state.alpha_composite(row)
        hold = tmp / f'_b_hold{i}.png'; state.convert('RGB').save(hold)
        steps.append((hold, max(0.1, per - 0.18)))

    lst = tmp / '_board_concat.txt'
    with open(lst, 'w') as f:
        for p, d_ in steps:
            f.write(f"file '{p}'\nduration {d_:.3f}\n")
        f.write(f"file '{steps[-1][0]}'\n")   # concat needs the last frame repeated
    r = subprocess.run(['ffmpeg', '-nostdin', '-y', '-f', 'concat', '-safe', '0', '-i', str(lst),
                        '-vf', 'fps=30,format=yuv420p', '-t', str(dur),
                        '-c:v', 'libx264', '-crf', '18', '-preset', 'veryfast', str(out_mp4)],
                       capture_output=True, text=True)
    if r.returncode: raise RuntimeError('board render: ' + _ff_err(r.stderr))

def render_stat(spec, palette, still_png, out_mp4, dur):
    from PIL import Image, ImageDraw, ImageFont
    navy = _hex2rgb((palette or {}).get('navy', '#002538'))
    acc = _hex2rgb((palette or {}).get('accent', '#2E9BD6'))
    img = Image.new('RGB', (2100, 1182), navy)
    d = ImageDraw.Draw(img)
    val = str(spec.get('value', '')); lab = str(spec.get('label', ''))
    fv = ImageFont.truetype(BOARD_FONT_EB, 300); fl = ImageFont.truetype(BOARD_FONT_SB, 64)
    vb = d.textbbox((0, 0), val, font=fv)
    d.text(((2100 - vb[2]) // 2, 330), val, font=fv, fill=acc)
    lb = d.textbbox((0, 0), lab, font=fl)
    d.text(((2100 - lb[2]) // 2, 740), lab, font=fl, fill=(255, 255, 255))
    img.save(still_png)
    _run(['ffmpeg','-nostdin','-y','-loop','1','-i',str(still_png),'-t',str(dur),
          '-vf', f"scale=7680:-2,zoompan=z='min(zoom+0.0006,1.07)':d={int(dur*30)}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1920x1080:fps=30,fade=t=in:st=0:d=0.3,format=yuv420p",
          '-r','30','-c:v','libx264','-crf','18',str(out_mp4)])

def _est_secs(vo):
    words = len((vo or '').split())
    return round(words / 2.6 + 0.6, 1)

KIDS_VOICE_STYLE = ("Read in a bright, warm, friendly storyteller voice for young children — playful and "
                    "encouraging, clear and unhurried, with gentle excitement: ")

# Per-style director profile: who is briefing the storyboard and what the content
# rules are. Keeping these separate stops one vertical bleeding into another.
DIRECTORS = {
    'vyond': {
        'who': 'You are the director of a UK legal-explainer video studio.',
        'rules': 'Accurate UK employment/legal content, qualitative framing (no invented statistics).',
    },
    'vox': {
        'who': 'You are the director of an editorial explainer studio in the style of Vox.',
        'rules': 'Accurate, well-sourced framing; no invented statistics.',
    },
    'kids': {
        'who': ("You are the director of a children's educational animation channel for ages 6-10 "
                "(think nursery rhymes, first science, early maths, languages and simple stories)."),
        'rules': ("Stay strictly on the child-friendly topic you were given — this is NOT a legal or "
                  "corporate channel, so never introduce law, licensing, regulations, contracts or adult "
                  "business framing unless the brief itself asks for it. Simple words, concrete everyday "
                  "examples, warm and playful, gently repetitive, factually correct for a child. "
                  "Give characters clear emotions and lots of physical action to animate."),
    },
}

def _now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

def _stage(vid, stage, **extra):
    """Record which stage a run is in so the dashboard can show live progress."""
    try:
        v = supa.select('videos', id=f'eq.{vid}')[0]
        prog = dict(v.get('progress') or {})
        prog.update({'stage': stage, **extra})
        supa.update('videos', vid, {'progress': prog})
    except Exception:
        pass

def _motion_brief(style, motion_prompt):
    """Animation direction for the image-to-video pass. Legal explainers want calm,
    restrained motion; a kids channel needs characters that actually move."""
    keep = ("Animate this exact image as a flat 2D animated clip. Keep the art style, characters, "
            "colours and layout exactly as shown. No new elements, no text. ")
    if style == 'kids':
        return (keep + (motion_prompt or '') +
                " Make it lively and full of life: characters move their arms and heads, bounce and "
                "gesture, blink and change expression, mouths move as they speak, props and background "
                "details (leaves, clouds, wheels) animate too. Energetic and playful throughout, but "
                "keep every character on-model.")
    return keep + (motion_prompt or 'Subtle natural motion.')

def _voice_for(style):
    """TTS kwargs per style: kids gets a warmer, playful delivery."""
    if style == 'kids':
        return {'voice': 'Kore', 'style': KIDS_VOICE_STYLE}
    return {'voice': 'Charon'}

def _beat_plan(kind, brand_name, style):
    if kind == 'scene':
        who = ('Maya, Leo & Professor Pip (locked kids cast)' if style == 'kids'
               else 'the locked cast character' if style == 'vyond' else 'paper-collage cut-outs')
        return {'makes': 'AI-generated animated scene', 'uses': f'{who} + {style} style refs',
                'how': 'nano-banana still → Omni image-to-video, VO + burned captions'}
    if kind == 'board':
        return {'makes': 'Icon-checklist board', 'uses': f'{brand_name or "brand"} palette (deterministic render)',
                'how': 'title + icon rows revealed in sync with VO'}
    if kind == 'stat':
        return {'makes': 'Big-number stat card', 'uses': f'{brand_name or "brand"} palette (deterministic render)',
                'how': 'figure + label, gentle push-in, VO'}
    return {'makes': kind, 'uses': '', 'how': ''}

def plan_video(job):
    """Cheap planning phase (Fable 5 only): storyboard -> planned beats -> plan_review. No generation."""
    payload = job.get('payload') or {}
    vid = job['video_id']
    supa.update('videos', vid, {'status': 'planning'})
    style = payload.get('style', 'vyond')
    brand_name = None
    if payload.get('brand_id'):
        try: brand_name = supa.select('brands', id=f"eq.{payload['brand_id']}")[0].get('name')
        except Exception: pass
    supa._rest('DELETE', 'video_beats', params={'video_id': f'eq.{vid}'})
    ts = payload.get('target_seconds')
    learnings = None
    try:
        g = supa.select('growth', order='created_at.desc', limit='1')
        if g and g[0].get('guidelines'):
            learnings = "\n".join(f"- {x}" for x in g[0]['guidelines'])
    except Exception:
        learnings = None
    sb = storyboard(style, payload.get('topic'), payload.get('script'),
                    article_url=payload.get('article_url'),
                    target_seconds=int(ts) if ts else None, learnings=learnings)
    beats = sb['beats']
    counts = {'scene': 0, 'board': 0, 'stat': 0}
    est_total = 0.0; est_cost = 0.0
    for i, b in enumerate(beats):
        kind = b.get('kind', 'scene'); counts[kind] = counts.get(kind, 0) + 1
        dur = _est_secs(b['vo']); est_total += dur
        if kind == 'scene': est_cost += 1.2
        meta = {'bg': b.get('bg', ''), 'plan': _beat_plan(kind, brand_name, style)}
        if b.get('cast'): meta['cast'] = [c for c in b['cast'] if c in CHARACTERS]
        if kind in ('board', 'stat'): meta['no_trim'] = True; meta[kind] = b.get(kind) or {}
        supa.insert('video_beats', {
            'video_id': vid, 'idx': i, 'kind': kind, 'vo_text': b['vo'],
            'scene_prompt': b.get('still') or b.get('scene', ''), 'motion_prompt': b.get('motion', ''),
            'dur_s': round(dur + 0.6, 2), 'status': 'planned', 'meta': meta})
    plan = {'title': sb.get('title'), 'style': style, 'brand': brand_name,
            'scenes': counts.get('scene', 0), 'boards': counts.get('board', 0), 'stats': counts.get('stat', 0),
            'beats': len(beats), 'est_seconds': round(est_total, 1), 'est_cost': round(est_cost, 2),
            'has_bookends': bool(brand_name)}
    patch = {'status': 'plan_review', 'progress': plan}
    if sb.get('title'): patch['title'] = sb['title']
    supa.update('videos', vid, patch)
    return f"planned {len(beats)} beats (~{round(est_total)}s, ~${est_cost:.0f}) -> plan_review"

def generate_video(job):
    """Generation phase: runs on ALREADY-PLANNED beats after user approves. -> review."""
    vid = job['video_id']
    v = supa.select('videos', id=f'eq.{vid}')[0]
    payload = job.get('payload') or v.get('progress') or {}
    style = payload.get('style') or (v.get('style'))
    wd = RUNS / vid; (wd/'audio').mkdir(parents=True, exist_ok=True)
    (wd/'stills').mkdir(exist_ok=True); (wd/'clips').mkdir(exist_ok=True)
    palette = None
    if v.get('brand_id'):
        try: palette = supa.select('brands', id=f"eq.{v['brand_id']}")[0].get('palette')
        except Exception: pass
    beats = sorted(supa.select('video_beats', video_id=f'eq.{vid}'), key=lambda b: b['idx'])
    beats = [b for b in beats if b['status'] in ('planned', 'pending', 'failed')]
    # stamp the start so the dashboard can show real elapsed time and an ETA
    prog = dict(v.get('progress') or {})
    prog.update({'stage': 'generating', 'gen_started_at': _now_iso(), 'gen_total': len(beats)})
    supa.update('videos', vid, {'status': 'running', 'progress': prog})
    video_cast = (v.get('progress') or {}).get('cast') or {}
    total_cost = 0.0
    done_n = 0
    for b in beats:
        bid = b['id']; i = b['idx']
        supa.update('video_beats', bid, {'status': 'pending'})
        last_err = None
        for attempt in range(1, BEAT_TRIES + 1):
            try:
                total_cost += _generate_beat(b, vid, wd, style, palette, video_cast)
                last_err = None
                break
            except Exception as e:
                last_err = e
                print(f'  beat {i} attempt {attempt}/{BEAT_TRIES} failed: {str(e)[:200]}', flush=True)
                if attempt < BEAT_TRIES:
                    time.sleep(BEAT_RETRY_S * attempt)
        if last_err is not None:
            supa.update('video_beats', bid, {'status': 'failed',
                        'meta': {**(b.get('meta') or {}), 'error': f'after {BEAT_TRIES} tries: {last_err}'[:600]}})
        _stage(vid, 'generating', gen_done=done_n + 1, gen_total=len(beats)); done_n += 1
    _stage(vid, 'awaiting_render')
    supa.update('videos', vid, {'status': 'review', 'total_cost': round(total_cost, 2)})
    return f'generated {len(beats)} beats, ${total_cost:.2f}'


def _generate_beat(b, vid, wd, style, palette, video_cast):
    """Render one beat (vo + visual) and mark it done. Returns the cost incurred.
    Raises on failure so the caller can retry."""
    bid = b['id']; i = b['idx']; kind = b['kind']
    cost = 0.0
    wav = wd/'audio'/f'{i:02d}.wav'
    if not wav.exists(): lib.tts(b['vo_text'], str(wav), **_voice_for(style))
    vo_asset = supa.upload_asset(str(wav), 'vo', title=f'{vid[:8]} beat{i} vo', tags=['vo'], duration_s=_dur(wav))
    still_path = wd/'stills'/f'{i:02d}.png'
    if kind in ('board', 'stat'):
        d_beat = round(_dur(wav) + 0.6, 2); clip_path = wd/'clips'/f'{i:02d}.mp4'
        spec = (b.get('meta') or {}).get(kind) or {}
        (render_board if kind == 'board' else render_stat)(spec, palette, str(still_path), str(clip_path), d_beat)
        sa = supa.upload_asset(str(still_path), 'graphic', title=f'{vid[:8]} beat{i} {kind}', tags=[kind, style], style=style)
        ca = supa.upload_asset(str(clip_path), 'clip', title=f'{vid[:8]} beat{i} {kind}', tags=[kind, style], style=style, duration_s=d_beat)
        supa.update('video_beats', bid, {'status': 'done', 'vo_asset': vo_asset['id'], 'dur_s': d_beat, 'still_asset': sa['id'], 'clip_asset': ca['id']})
        return cost
    if style in ('vyond', 'kids'):
        import base64
        cast_refs, cast_desc = _cast_for_beat(b, video_cast, style)
        if not cast_refs: cast_refs = [Path(p).read_bytes() for p in STYLE_REFS.get(style, [])]
        parts = [{'inline_data': {'mime_type': 'image/png', 'data': base64.b64encode(r).decode()}} for r in cast_refs]
        extra = (" Featured character(s): " + cast_desc + ".") if cast_desc else ""
        base_style = KIDS_STYLE if style == 'kids' else VYOND_STYLE
        parts.append({'text': base_style + extra + "\n\nScene (wide 16:9): " + (b['scene_prompt'] or '')})
        d = lib._post(f'{lib.BASE}/models/gemini-3-pro-image:generateContent',
                      {'contents': [{'parts': parts}], 'generationConfig': {'responseModalities': ['IMAGE']}}, timeout=300)
        img = next(base64.b64decode(p['inlineData']['data']) for p in d['candidates'][0]['content']['parts'] if 'inlineData' in p)
        still_path.write_bytes(img); cost += 0.14
        clip = lib.omni_i2v(img, _motion_brief(style, b.get('motion_prompt')))
    else:
        prompt = VOX_STYLE.format(bg=(b.get('meta') or {}).get('bg', 'Flat mustard-yellow paper background')) + "\n\nScene: " + (b['scene_prompt'] or '')
        clip = lib.omni_video(prompt); img = None
    cost += 1.05
    clip_path = wd/'clips'/f'{i:02d}.mp4'; clip_path.write_bytes(clip)
    patch = {'status': 'done', 'vo_asset': vo_asset['id'], 'dur_s': round(_dur(wav) + 0.6, 2)}
    if img:
        sa = supa.upload_asset(str(still_path), 'still', title=f'{vid[:8]} beat{i}', tags=['auto', style], style=style, cost=0.14)
        patch['still_asset'] = sa['id']
    ca = supa.upload_asset(str(clip_path), 'clip', title=f'{vid[:8]} beat{i}', tags=['auto', style], style=style, duration_s=_dur(clip_path), cost=1.05)
    patch['clip_asset'] = ca['id']
    supa.update('video_beats', bid, patch)
    return cost

def produce_video(job):
    payload = job.get('payload') or {}
    vid = job['video_id']
    wd = RUNS / vid; (wd/'audio').mkdir(parents=True, exist_ok=True)
    (wd/'stills').mkdir(exist_ok=True); (wd/'clips').mkdir(exist_ok=True)
    supa.update('videos', vid, {'status': 'running'})
    style = payload.get('style', 'vyond')
    palette = None
    if payload.get('brand_id'):
        try: palette = supa.select('brands', id=f"eq.{payload['brand_id']}")[0].get('palette')
        except Exception: palette = None

    supa._rest('DELETE', 'video_beats', params={'video_id': f'eq.{vid}'})   # idempotent re-run
    sb = storyboard(style, payload.get('topic'), payload.get('script'),
                    article_url=payload.get('article_url'),
                    target_seconds=int(payload.get('target_seconds') or 90))
    beats = sb['beats']
    if payload.get('title') is None and sb.get('title'):
        supa.update('videos', vid, {'title': sb['title']})
    rows = []
    for i, b in enumerate(beats):
        kind = b.get('kind', 'scene')
        meta = {'bg': b.get('bg', '')}
        if kind in ('board', 'stat'):
            meta['no_trim'] = True
            meta[kind] = b.get(kind) or {}
        rows.append(supa.insert('video_beats', {
            'video_id': vid, 'idx': i, 'kind': kind, 'vo_text': b['vo'],
            'scene_prompt': b.get('still') or b.get('scene', ''), 'motion_prompt': b.get('motion', ''),
            'status': 'pending', 'meta': meta}))
    refs = [Path(p).read_bytes() for p in STYLE_REFS.get(style, [])]
    total_cost = 0.0
    for i, (b, row) in enumerate(zip(beats, rows)):
        bid = row['id']
        try:
            wav = wd/'audio'/f'{i:02d}.wav'
            if not wav.exists():
                lib.tts(b['vo'], str(wav), **_voice_for(style))
            vo_asset = supa.upload_asset(str(wav), 'vo', title=f'{vid[:8]} beat{i} vo', tags=['vo'], duration_s=_dur(wav))
            still_path = wd/'stills'/f'{i:02d}.png'
            kind = b.get('kind', 'scene')
            if kind in ('board', 'stat'):
                d_beat = round(_dur(wav) + 0.6, 2)
                clip_path = wd/'clips'/f'{i:02d}.mp4'
                (render_board if kind == 'board' else render_stat)(b.get(kind) or {}, palette, str(still_path), str(clip_path), d_beat)
                sa = supa.upload_asset(str(still_path), 'graphic', title=f'{vid[:8]} beat{i} {kind}', tags=[kind, style], style=style)
                ca = supa.upload_asset(str(clip_path), 'clip', title=f'{vid[:8]} beat{i} {kind}', tags=[kind, style], style=style, duration_s=d_beat)
                supa.update('video_beats', bid, {'status': 'done', 'vo_asset': vo_asset['id'], 'dur_s': d_beat,
                                                 'still_asset': sa['id'], 'clip_asset': ca['id']})
                continue
            if style == 'vyond':
                import base64
                parts = [{'inline_data': {'mime_type': 'image/png', 'data': base64.b64encode(r).decode()}} for r in refs]
                parts.append({'text': VYOND_STYLE + "\n\nScene (wide 16:9): " + b['still']})
                d = lib._post(f'{lib.BASE}/models/gemini-3-pro-image:generateContent',
                              {'contents': [{'parts': parts}], 'generationConfig': {'responseModalities': ['IMAGE']}}, timeout=300)
                img = next(base64.b64decode(p['inlineData']['data']) for p in d['candidates'][0]['content']['parts'] if 'inlineData' in p)
                still_path.write_bytes(img); total_cost += 0.14
                clip = lib.omni_i2v(img, "Animate this exact image as a flat 2D explainer video clip. Keep the art style, "
                                    "characters, colors and layout exactly as shown. " + b.get('motion', 'Subtle natural motion.') +
                                    " No new elements, no text.")
            else:
                prompt = VOX_STYLE.format(bg=b.get('bg', 'Flat mustard-yellow paper background')) + "\n\nScene: " + b['scene']
                clip = lib.omni_video(prompt); img = None
            total_cost += 1.05
            clip_path = wd/'clips'/f'{i:02d}.mp4'; clip_path.write_bytes(clip)
            patch = {'status': 'done', 'vo_asset': vo_asset['id'], 'dur_s': round(_dur(wav) + 0.6, 2)}
            if img:
                sa = supa.upload_asset(str(still_path), 'still', title=f'{vid[:8]} beat{i}', tags=['auto', style], style=style, cost=0.14)
                patch['still_asset'] = sa['id']
            ca = supa.upload_asset(str(clip_path), 'clip', title=f'{vid[:8]} beat{i}', tags=['auto', style], style=style,
                                   duration_s=_dur(clip_path), cost=1.05)
            patch['clip_asset'] = ca['id']
            supa.update('video_beats', bid, patch)
        except Exception as e:
            supa.update('video_beats', bid, {'status': 'failed', 'meta': {'error': str(e)[:300]}})
    supa.update('videos', vid, {'status': 'review', 'total_cost': round(total_cost, 2)})
    return f'produced {len(beats)} beats, ${total_cost:.2f}'

def _fetch_asset(asset_id, dest):
    row = supa.select('assets', id=f'eq.{asset_id}')[0]
    url = supa.public_url(row['storage_path'])
    import requests
    with requests.get(url, stream=True, timeout=300) as r:
        with open(dest, 'wb') as f:
            for c in r.iter_content(1 << 16): f.write(c)
    return dest

def assemble_video(video_id):
    _stage(video_id, 'assembling', assemble_started_at=_now_iso())
    v = supa.select('videos', id=f'eq.{video_id}')[0]
    beats = sorted(supa.select('video_beats', video_id=f'eq.{video_id}'), key=lambda b: b['idx'])
    beats = [b for b in beats if b['status'] == 'done']
    wd = RUNS / video_id; segs = wd/'segs'; segs.mkdir(parents=True, exist_ok=True)
    brand = supa.select('brands', id=f"eq.{v['brand_id']}")[0] if v.get('brand_id') else None

    order = []
    if brand and brand.get('intro_asset'):
        ip = wd/'intro.mp4'
        if not ip.exists(): _fetch_asset(brand['intro_asset'], ip)
        out = segs/'intro.mp4'
        _run(['ffmpeg','-nostdin','-y','-i',str(ip),'-vf','scale=1920:1080:flags=lanczos,fps=30,format=yuv420p',
              '-an','-c:v','libx264','-crf','18',str(out)])
        order.append(out)
    intro_d = _dur(order[0]) if order else 0.0

    t = intro_d
    for b in beats:
        src = wd/'clips'/f"{b['idx']:02d}.mp4"
        if not src.exists(): _fetch_asset(b['clip_asset'], src)
        trim = 0.0 if (b.get('meta') or {}).get('no_trim') else 1.0
        d = float(b['dur_s']); sd = _dur(src) - trim
        out = segs/f"{b['idx']:02d}.mp4"
        if sd >= d:
            vf = f"trim={trim}:{trim+d},setpts=PTS-STARTPTS,scale=1920:1080:flags=lanczos,unsharp=3:3:0.35,fps=30,format=yuv420p"
        else:
            factor = min(d/max(sd, 0.1), 1.5); hold = max(0.0, d - sd*factor)
            vf = (f"trim={trim},setpts=(PTS-STARTPTS)*{factor:.4f},scale=1920:1080:flags=lanczos,unsharp=3:3:0.35,"
                  f"tpad=stop_mode=clone:stop_duration={hold:.2f},trim=0:{d},fps=30,format=yuv420p")
        _run(['ffmpeg','-nostdin','-y','-i',str(src),'-vf',vf,'-an','-c:v','libx264','-crf','18',str(out)])
        b['start'] = t; t += d
        order.append(out)
    total_vo_end = t
    if brand and brand.get('outro_asset'):
        op = wd/'outro.mp4'
        if not op.exists(): _fetch_asset(brand['outro_asset'], op)
        out = segs/'zz_outro.mp4'
        _run(['ffmpeg','-nostdin','-y','-i',str(op),'-vf','scale=1920:1080:flags=lanczos,fps=30,format=yuv420p',
              '-an','-c:v','libx264','-crf','18',str(out)])
        order.append(out); t += _dur(out)
    total = t

    concat = segs/'concat.txt'
    concat.write_text('\n'.join(f"file '{p}'" for p in order))
    _run(['ffmpeg','-nostdin','-y','-f','concat','-safe','0','-i',str(concat),'-c','copy',str(wd/'work.mp4')])

    # subtitles
    def chunk(text, maxlen=40):
        words, out, cur = text.replace('—','–').split(), [], ''
        for w in words:
            cand = (cur+' '+w).strip()
            if len(cand) > maxlen and cur: out.append(cur); cur = w
            else: cur = cand
            if len(cur) >= 24 and cur[-1] in '.!?;:': out.append(cur); cur = ''
        if cur: out.append(cur)
        return out
    def ts(s):
        s = max(0.0, s); return f'{int(s//3600)}:{int(s%3600//60):02d}:{s%60:05.2f}'
    EFF = r'{\fad(100,70)\fscx90\fscy90\t(0,110,\fscx100\fscy100)}'
    ev = []
    for b in beats:
        vo_d = float(b['dur_s']) - 0.6
        lines = chunk(b['vo_text']); tot = sum(len(l) for l in lines); pos = 0
        for i, l in enumerate(lines):
            st = b['start'] + 0.25 + vo_d*(pos/tot); pos += len(l)
            en = b['start'] + 0.25 + vo_d*(pos/tot)
            if i == len(lines)-1: en = min(en+0.3, b['start']+float(b['dur_s']))
            ev.append(f"Dialogue: 0,{ts(st)},{ts(en-0.05)},Cap,,0,0,0,,{EFF}{l.strip().strip('–').strip()}")
    ass = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Cap,Poppins SemiBold,46,&H00FFFFFF,&H00FFFFFF,&H00382500,&H78000000,0,0,0,0,100,100,0,0,1,3,1.5,2,80,80,54,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n") + '\n'.join(ev) + '\n'
    (wd/'subs.ass').write_text(ass)
    _run(['ffmpeg','-nostdin','-y','-i',str(wd/'work.mp4'),
          '-vf', f"ass={wd}/subs.ass:fontsdir={FONTS_DIR},eq=saturation=1.08:gamma=1.02",
          '-an','-c:v','libx264','-crf','18','-preset','medium',str(wd/'work_subs.mp4')])

    # audio
    inp = ['-i', str(wd/'work_subs.mp4')]; fc = []; nl = []; i = 1
    for b in beats:
        wav = wd/'audio'/f"{b['idx']:02d}.wav"
        if not wav.exists(): _fetch_asset(b['vo_asset'], wav)
        dl = int((b['start'] + 0.25)*1000)
        inp += ['-i', str(wav)]
        fc.append(f'[{i}:a]adelay={dl}|{dl}[n{i}]'); nl.append(f'[n{i}]'); i += 1
    fc.append(''.join(nl) + f'amix=inputs={len(nl)}:normalize=0:duration=longest,apad,atrim=0:{total}[narr]')
    inp += ['-i', _asset('bed.mp3', '/Users/rudra/OpenMontage/pipelines/flat2d-studio/brand/music/bed.mp3')]
    fc.append(f'[{i}:a]atrim=0:{total},asetpts=PTS-STARTPTS,volume=0.09,afade=t=in:st=0:d=1,afade=t=out:st={total-2.5}:d=2.5[mus]')
    fc.append('[narr][mus]amix=inputs=2:normalize=0:duration=first,loudnorm=I=-15.5:TP=-1.4,alimiter=limit=0.92[a]')
    r = subprocess.run(['ffmpeg','-nostdin','-y',*inp,'-filter_complex',';'.join(fc),
                        '-map','0:v','-map','[a]','-c:v','copy','-c:a','aac','-b:a','192k',
                        '-movflags','+faststart',str(wd/'final.mp4')], capture_output=True, text=True)
    if r.returncode: raise RuntimeError('mux: ' + _ff_err(r.stderr))

    fa = supa.upload_asset(str(wd/'final.mp4'), 'video', title=v.get('title') or 'video',
                           tags=['final'], style=v['style'], brand_id=v.get('brand_id'), duration_s=total)
    _stage(video_id, 'ready', finished_at=_now_iso())
    supa.update('videos', video_id, {'status': 'done', 'final_asset': fa['id'], 'duration_s': round(total, 1)})
    return f'assembled {total:.1f}s -> asset {fa["id"][:8]}'

def reroll_beat(payload):
    bid = payload['beat_id']; note = payload.get('prompt', '')
    b = supa.select('video_beats', id=f'eq.{bid}')[0]
    v = supa.select('videos', id=f"eq.{b['video_id']}")[0]
    wd = RUNS / b['video_id']; (wd/'stills').mkdir(parents=True, exist_ok=True); (wd/'clips').mkdir(parents=True, exist_ok=True)
    supa.update('video_beats', bid, {'status': 'pending'})
    style = v['style']; i = b['idx']
    kind = b.get('kind', 'scene')
    if kind in ('board', 'stat'):
        # deterministic re-render (free); apply note via Fable 5 spec revision if given
        spec = (b.get('meta') or {}).get(kind) or {}
        if note:
            try:
                spec = json.loads(lib.text_gen(
                    f"Revise this {kind} card spec per the user's note. Keep the same JSON shape.\n"
                    f"SPEC: {json.dumps(spec)}\nNOTE: {note}\nReturn ONLY the revised JSON."))
            except Exception: pass
        palette = None
        if v.get('brand_id'):
            try: palette = supa.select('brands', id=f"eq.{v['brand_id']}")[0].get('palette')
            except Exception: palette = None
        # a beat that failed mid-generation may never have stored its voiceover
        patch = {}
        if not b.get('vo_asset') and b.get('vo_text'):
            (wd/'audio').mkdir(parents=True, exist_ok=True)
            wav = wd/'audio'/f'{i:02d}.wav'
            if not wav.exists(): lib.tts(b['vo_text'], str(wav), **_voice_for(style))
            va = supa.upload_asset(str(wav), 'vo', title=f'reroll beat{i} vo', tags=['vo'], duration_s=_dur(wav))
            patch['vo_asset'] = va['id']
            b['dur_s'] = round(_dur(wav) + 0.6, 2); patch['dur_s'] = b['dur_s']
        d_beat = float(b.get('dur_s') or 6.0)
        sp = wd/'stills'/f'{i:02d}.png'; cp = wd/'clips'/f'{i:02d}.mp4'
        (render_board if kind == 'board' else render_stat)(spec, palette, str(sp), str(cp), d_beat)
        sa = supa.upload_asset(str(sp), 'graphic', title=f'reroll beat{i} {kind}', tags=['reroll', kind], style=style)
        ca = supa.upload_asset(str(cp), 'clip', title=f'reroll beat{i} {kind}', tags=['reroll', kind], style=style, duration_s=d_beat)
        meta = dict(b.get('meta') or {}); meta[kind] = spec; meta.pop('error', None)
        supa.update('video_beats', bid, {'status': 'done', 'still_asset': sa['id'], 'clip_asset': ca['id'],
                                         'meta': meta, **patch})
        return f'rerolled {kind} beat {i}'
    scene = b['scene_prompt'] + (f"\nADJUSTMENT: {note}" if note else '')
    if style in ('vyond', 'kids'):
        import base64
        cast_refs, cast_desc = _cast_for_beat(b, {}, style)
        refs = cast_refs or [Path(p).read_bytes() for p in STYLE_REFS.get(style, STYLE_REFS['vyond'])]
        parts = [{'inline_data': {'mime_type': 'image/png', 'data': base64.b64encode(r).decode()}} for r in refs]
        extra = (" Featured character(s): " + cast_desc + ".") if cast_desc else ""
        parts.append({'text': (KIDS_STYLE if style == 'kids' else VYOND_STYLE) + extra + "\n\nScene (wide 16:9): " + scene})
        d = lib._post(f'{lib.BASE}/models/gemini-3-pro-image:generateContent',
                      {'contents': [{'parts': parts}], 'generationConfig': {'responseModalities': ['IMAGE']}}, timeout=300)
        img = next(base64.b64decode(p['inlineData']['data']) for p in d['candidates'][0]['content']['parts'] if 'inlineData' in p)
        (wd/'stills'/f'{i:02d}.png').write_bytes(img)
        clip = lib.omni_i2v(img, "Animate this exact image as a flat 2D explainer video clip. Keep the art style, characters, "
                            "colors and layout exactly as shown. " + (b.get('motion_prompt') or 'Subtle natural motion.') + " No new elements, no text.")
        sa = supa.upload_asset(str(wd/'stills'/f'{i:02d}.png'), 'still', title=f'reroll beat{i}', tags=['reroll', style], style=style)
    else:
        bg = (b.get('meta') or {}).get('bg', 'Flat mustard-yellow paper background')
        clip = lib.omni_video(VOX_STYLE.format(bg=bg) + "\n\nScene: " + scene); sa = None
    cp = wd/'clips'/f'{i:02d}.mp4'; cp.write_bytes(clip)
    ca = supa.upload_asset(str(cp), 'clip', title=f'reroll beat{i}', tags=['reroll', style], style=style, duration_s=_dur(cp))
    patch = {'status': 'done', 'clip_asset': ca['id']}
    if sa: patch['still_asset'] = sa['id']
    if (b.get('meta') or {}).get('error'):
        m = dict(b['meta']); m.pop('error', None); patch['meta'] = m
    supa.update('video_beats', bid, patch)
    return f'rerolled beat {i}'


def edit_video(job):
    """AI editor: map a natural-language instruction to concrete ops, execute, re-assemble."""
    payload = job.get('payload') or {}
    vid = job['video_id']; instruction = payload.get('instruction', '')
    v = supa.select('videos', id=f'eq.{vid}')[0]
    beats = sorted(supa.select('video_beats', video_id=f'eq.{vid}'), key=lambda b: b['idx'])
    digest = [{'idx': b['idx'], 'kind': b['kind'], 'vo': b['vo_text'], 'scene': (b.get('scene_prompt') or '')[:120]}
              for b in beats if b['status'] == 'done']
    prompt = (
        "You are the editor of a finished explainer video. Map the user's editing instruction to a JSON list of ops.\n"
        f"BEATS: {json.dumps(digest)}\n"
        f"INSTRUCTION: {instruction}\n\n"
        "Allowed ops (use the fewest that satisfy the instruction):\n"
        "- {\"op\": \"reroll_beat\", \"idx\": N, \"note\": \"visual change to apply\"}\n"
        "- {\"op\": \"update_vo\", \"idx\": N, \"new_text\": \"replacement narration\"}\n"
        "- {\"op\": \"remove_beat\", \"idx\": N}\n"
        "Return JSON: {\"ops\": [...], \"summary\": \"one line of what you changed\"}")
    plan = json.loads(lib.text_gen(prompt))
    done = []
    by_idx = {b['idx']: b for b in beats}
    for op in plan.get('ops', [])[:6]:
        b = by_idx.get(op.get('idx'))
        if not b: continue
        if op['op'] == 'remove_beat':
            supa.update('video_beats', b['id'], {'status': 'removed'})
            done.append(f"removed beat {b['idx']}")
        elif op['op'] == 'update_vo':
            wd = RUNS / vid; (wd/'audio').mkdir(parents=True, exist_ok=True)
            wav = wd/'audio'/f"{b['idx']:02d}.wav"
            lib.tts(op['new_text'], str(wav), **_voice_for(v.get('style')))
            va = supa.upload_asset(str(wav), 'vo', title=f'edit vo beat {b["idx"]}', tags=['edit'], duration_s=_dur(wav))
            supa.update('video_beats', b['id'], {'vo_text': op['new_text'], 'vo_asset': va['id'],
                                                 'dur_s': round(_dur(wav) + 0.6, 2)})
            done.append(f"revoiced beat {b['idx']}")
        elif op['op'] == 'reroll_beat':
            reroll_beat({'beat_id': b['id'], 'prompt': op.get('note', '')})
            done.append(f"rerolled beat {b['idx']}")
    msg = assemble_video(vid)
    return f"{plan.get('summary', 'edited')} [{', '.join(done)}] -> {msg}"


def snippets(job):
    """Auto-cut vertical (9:16) Shorts from a finished video's best moments.
    AI picks contiguous beat ranges; each becomes a short video row (kind='short')
    reusing final_asset, so it publishes through the normal YouTube flow."""
    payload = job.get('payload') or {}
    vid = payload.get('video_id') or job.get('video_id')
    v = supa.select('videos', id=f'eq.{vid}')[0]
    if not v.get('final_asset'):
        raise RuntimeError('source video has no final render')
    beats = sorted(supa.select('video_beats', video_id=f'eq.{vid}'), key=lambda b: b['idx'])
    beats = [b for b in beats if b['status'] == 'done']
    if not beats:
        raise RuntimeError('no beats to snip')
    wd = RUNS / vid / 'shorts'; wd.mkdir(parents=True, exist_ok=True)

    # recompute each beat's start time exactly like assemble_video (intro offset + cumulative)
    intro_d = 0.0
    brand = supa.select('brands', id=f"eq.{v['brand_id']}")[0] if v.get('brand_id') else None
    if brand and brand.get('intro_asset'):
        ip = RUNS / vid / 'intro.mp4'
        try:
            if not ip.exists(): _fetch_asset(brand['intro_asset'], ip)
            intro_d = _dur(ip)
        except Exception: intro_d = 0.0
    t = intro_d
    for b in beats:
        b['_start'] = t; t += float(b['dur_s'])

    lines = "\n".join(f"{i}: ({b['dur_s']}s) {b['vo_text']}" for i, b in enumerate(beats))
    prompt = (f'These are the ordered beats of a finished UK legal explainer titled "{v.get("title")}". '
              "Pick 2-4 self-contained vertical SHORTS. Each short is a CONTIGUOUS run of beats (by index) that "
              "stands alone as a 15-45 second clip with a strong hook. One punchy point per short.\n"
              f"Beats:\n{lines}\n\n"
              'Return ONLY JSON: {"shorts":[{"start_idx":int,"end_idx":int,"title":"punchy <=80 char title",'
              '"caption":"one-line hook"}]}')
    shorts = (json.loads(lib.text_gen(prompt)).get('shorts') or [])[:4]

    final = RUNS / vid / 'final.mp4'
    if not final.exists(): _fetch_asset(v['final_asset'], final)

    made = 0
    for k, s in enumerate(shorts):
        try:
            si = max(0, int(s['start_idx'])); ei = min(len(beats) - 1, int(s['end_idx']))
            if ei < si: si, ei = ei, si
            seg_start = beats[si]['_start']
            seg_end = min(beats[ei]['_start'] + float(beats[ei]['dur_s']), seg_start + 60)
            dur = round(seg_end - seg_start, 2)
            if dur < 5: continue
            out = wd / f'short_{k:02d}.mp4'
            vf = ("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,gblur=sigma=25[bg];"
                  "[0:v]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]")
            r = subprocess.run(['ffmpeg', '-nostdin', '-y', '-ss', f'{seg_start:.2f}', '-i', str(final),
                                '-t', f'{dur:.2f}', '-filter_complex', vf, '-map', '[v]', '-map', '0:a?',
                                '-c:v', 'libx264', '-crf', '20', '-c:a', 'aac', '-b:a', '160k',
                                '-movflags', '+faststart', str(out)], capture_output=True, text=True)
            if r.returncode: continue
            fa = supa.upload_asset(str(out), 'clip', title=(s.get('title') or f'Short {k+1}')[:120],
                                   tags=['short', v['style']], style=v['style'], brand_id=v.get('brand_id'), duration_s=dur)
            supa.insert('videos', {'title': (s.get('title') or f'Short {k+1}')[:120],
                                   'topic': s.get('caption') or v.get('title'), 'style': v['style'],
                                   'brand_id': v.get('brand_id'), 'kind': 'short', 'status': 'done',
                                   'final_asset': fa['id'], 'duration_s': dur,
                                   'progress': {'short_of': vid, 'caption': s.get('caption')}})
            made += 1
        except Exception:
            continue
    supa.update('videos', vid, {'progress': {**(v.get('progress') or {}), 'shorts_made': made}})
    return f'made {made} shorts from {vid[:8]}'
