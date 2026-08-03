"""LawStudio factory — turns a dashboard job into a finished branded video.
plan_video: storyboard (Fable 5) -> planned beats. generate_video: TTS -> stills ->
omni clips, uploading every artifact to the Supabase library, updating beats live.
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

# ---------------------------------------------------------------- style packs
# A style is DATA, not code: its look, beat grammar, director briefing, motion,
# voice and render path all live in the `styles` table. New styles can be added
# from the dashboard without touching the pipeline, and nothing is shared
# between them, so one vertical can never bleed into another.
_STYLE_CACHE = {'at': 0.0, 'rows': {}}

# Only used if the DB is unreachable — keeps old runs working.
_FALLBACK_STYLE = {
    'key': 'vyond', 'name': 'Vyond 2D', 'look_prompt': VYOND_STYLE,
    'motion_prompt': 'Subtle natural motion.', 'beat_grammar': '',
    'director_who': 'You are the director of a UK legal-explainer video studio.',
    'director_rules': 'Accurate UK employment/legal content, qualitative framing (no invented statistics).',
    'voice_name': 'Charon', 'voice_style': None, 'render_mode': 'image_to_video',
    'bg_options': [], 'default_cast': ['main'], 'palette': {},
}

def _styles():
    if time.time() - _STYLE_CACHE['at'] > 60:
        try:
            rows = supa.select('styles')
            if rows:
                _STYLE_CACHE['rows'] = {r['key']: r for r in rows}
                _STYLE_CACHE['at'] = time.time()
        except Exception:
            pass
    return _STYLE_CACHE['rows']

def _speaker_name(beat, style):
    """Display name of whoever is speaking this beat, if the plan named one."""
    key = (beat.get('meta') or {}).get('speaker')
    if not key: return None
    for c in _db_characters():
        if c.get('key') == key and (c.get('style') or style) == style:
            return c.get('name')
    return CHARACTERS.get(key, {}).get('name')

def _style_refs(style):
    """Fallback visual references for a style: the reference image of every
    character that belongs to it. Nothing is shared across styles."""
    out = []
    for c in _db_characters():
        if c.get('style') == style and c.get('ref_url'):
            img = _fetch_ref(c['ref_url'])
            if img: out.append(img)
        if len(out) >= 2: break
    if not out:  # bundled refs for the original built-ins
        for pth in STYLE_REFS.get(style, []):
            try: out.append(Path(pth).read_bytes())
            except Exception: pass
    return out

def _look_for_beat(pk, beat):
    """Look prompt for a text-to-video beat; {bg} is filled from the style's
    background options when the style uses them."""
    look = pk.get('look_prompt') or ''
    if '{bg}' in look:
        opts = pk.get('bg_options') or ['Flat mustard-yellow paper background']
        bg = (beat.get('meta') or {}).get('bg') or opts[0]
        look = look.replace('{bg}', bg)
    return look

def style_pack(key):
    """Everything the pipeline needs to render one style."""
    return _styles().get(key) or dict(_FALLBACK_STYLE, key=key or 'vyond')

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
    """Return (ref_bytes_list, descriptor_text) for a scene beat.

    A style may ONLY use characters registered to it (plus per-video custom ones
    and any deliberately style-less character). This is the hard boundary that
    stops one vertical's cast turning up in another's video — an unknown key is
    dropped rather than silently resolved against the built-in roster.
    """
    db = {c['key']: c for c in _db_characters()
          if (c.get('style') or None) in (style, None)}
    custom = (video_cast or {}).get('custom', {})   # {key: {name, desc}}
    allowed = set(db) | set(custom)

    keys = [k for k in ((beat.get('meta') or {}).get('cast') or []) if k in allowed]
    if not keys:
        keys = [k for k in (style_pack(style).get('default_cast') or []) if k in allowed]
    if not keys:
        keys = list(db)[:1]

    refs, descs = [], []
    for k in keys:
        c = db.get(k)
        if c:
            # built-ins ship their references on disk; everything else is fetched
            local = CHARACTERS.get(k, {}).get('refs') if k in CHARACTERS else None
            got = False
            for rp in (local or []):
                try: refs.append(Path(rp).read_bytes()); got = True
                except Exception: pass
            if not got and c.get('ref_url'):
                img = _fetch_ref(c['ref_url'])
                if img: refs.append(img)
            nm, desc = (c.get('name') or ''), (c.get('description') or '')
            descs.append(desc if desc.lower().startswith(nm.lower()) else f'{nm}, {desc}'.strip(', '))
        elif k in custom:
            c = custom[k]
            if c.get('ref_url'):
                img = _fetch_ref(c['ref_url'])
                if img: refs.append(img)
            nm = c.get('name') or ''
            d2 = c.get('desc') or ''
            descs.append(d2 if d2.lower().startswith(nm.lower()) else f'{nm}, {d2}'.strip(', '))
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

def _director(style, brand=None):
    """The brand's own director profile wins over the style's, so a new niche
    (maths, rhymes, science…) briefs its own way without code changes."""
    pk = style_pack(style)
    base = {'who': pk.get('director_who') or '', 'rules': pk.get('director_rules') or ''}
    if brand:
        if brand.get('director_who'): base['who'] = brand['director_who']
        if brand.get('director_rules'): base['rules'] = brand['director_rules']
    return base

def storyboard(style, topic, script, article_url=None, target_seconds=None, learnings=None, brand=None):
    # long briefs can't come back as one JSON blob — outline first, then fill in
    # each section, so a 25-minute video is just many small, reliable calls.
    if target_seconds and target_seconds > LONG_FORM_S and not script:
        return _storyboard_long(style, topic, article_url, int(target_seconds), learnings, brand)
    auto = target_seconds is None
    shot_s = float(style_pack(style).get('shot_seconds') or 9)
    n_beats = None if auto else max(3, min(60, round(target_seconds / shot_s)))
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
    guide = style_pack(style).get('beat_grammar') or ''
    scope = (f"YOU decide the right length and number of beats from the topic's depth. Each beat is ONE camera "
             f"shot of about {shot_s:.0f} seconds, so a short piece is 8-12 beats and a rich one runs longer — "
             f"cut often rather than holding one shot; do not pad or truncate."
             if auto else
             f"Around {n_beats} beats (use fewer/more only if genuinely needed), ~{target_seconds}s spoken.")
    learn = (f"\n\nLEARNINGS FROM PAST PERFORMANCE (apply these to improve reach/engagement):\n{learnings}\n"
             if learnings else "")
    # The director profile must match the vertical — a kids channel briefed as a
    # "UK legal-explainer studio" turns "wheels on the bus" into bus licence law.
    profile = _director(style, brand)
    prompt = (f"{profile['who']} Write a storyboard as JSON.\n{src}\n\n"
              f"Style: {style}. {scope} {guide}{learn}\n"
              f"{profile['rules']} "
              'Return JSON: {"title": "...", "beats": [ ... ]}')
    return json.loads(lib.text_gen(prompt))

def _storyboard_long(style, topic, article_url, target_seconds, learnings, brand=None):
    """Long form: outline the sections, then storyboard each one and stitch the
    beats together. Keeps every model call small enough to be reliable."""
    src = topic or ''
    if article_url:
        src = (fetch_article(article_url) + ("\n\nANGLE: " + topic if topic else ''))
    n_sections = max(2, round(target_seconds / SECTION_S))
    profile = _director(style, brand)
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
                          target_seconds=SECTION_S, learnings=learnings, brand=brand)
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

def _veo_brief(pk, beat, speaker_desc):
    """Veo renders the shot AND performs the line, so the mouth matches the words.
    The prompt therefore carries the spoken line, not just the motion."""
    line = (beat.get('vo_text') or '').strip()
    who = speaker_desc or (beat.get('meta') or {}).get('speaker_name') \
        or 'the character who is speaking in this shot'
    shot = (beat.get('meta') or {}).get('shot') or 'medium shot'
    return (f"{shot}. {(beat.get('scene_prompt') or '').strip()}\n\n"
            f"{who} speaks this line out loud, clearly and in character, with accurate lip sync: "
            f"\"{line}\"\n\n"
            f"{pk.get('motion_prompt') or ''} {(beat.get('motion_prompt') or '')}\n"
            f"Keep the art style, character design and colours exactly as in the reference image. "
            f"Natural blinks and eyebrow movement. No on-screen text, captions, subtitles or watermarks. "
            f"No background music.")

def _motion_brief(style, motion_prompt, speaker=None):
    """Animation direction for the image-to-video pass — each style says how much
    life its characters should have. When we know who is talking, say so: the
    speaker's mouth carries the line and everyone else reacts."""
    keep = ("Animate this exact image as a flat 2D animated clip. Keep the art style, characters, "
            "colours and layout exactly as shown. No new elements, no text. ")
    talk = ''
    if speaker:
        talk = (f" {speaker} is the one speaking this line: their mouth opens and closes in time with "
                "natural speech rhythm, jaw and lips moving clearly through the whole clip, eyebrows and "
                "head moving with the words. Every other character stays quiet with a closed mouth and "
                "listens, reacting with small nods and expressions.")
    return keep + (motion_prompt or '') + ' ' + (style_pack(style).get('motion_prompt') or '') + talk

def _voice_for(style, speaker_key=None):
    """TTS voice + delivery. A character with their own voice speaks in it, so a
    scene reads as dialogue rather than one narrator reading every part; the
    style's voice is the narrator and the fallback."""
    pk = style_pack(style)
    out = {'voice': pk.get('voice_name') or 'Charon'}
    if pk.get('voice_style'): out['style'] = pk['voice_style']
    if speaker_key:
        for c in _db_characters():
            if c.get('key') == speaker_key and (c.get('style') or style) == style:
                if c.get('voice_name'): out['voice'] = c['voice_name']
                if c.get('voice_style'): out['style'] = c['voice_style']
                break
    return out

def _beat_plan(kind, brand_name, style):
    if kind == 'scene':
        pk = style_pack(style)
        cast = ', '.join(pk.get('default_cast') or []) or 'the chosen cast'
        who = f"{cast} in the {pk.get('name', style)} style"
        return {'makes': 'AI-generated animated scene', 'uses': f'{who} + {style} style refs',
                'how': 'nano-banana still → Omni image-to-video, VO + burned captions'}
    if kind == 'board':
        return {'makes': 'Icon-checklist board', 'uses': f'{brand_name or "brand"} palette (deterministic render)',
                'how': 'title + icon rows revealed in sync with VO'}
    if kind == 'stat':
        return {'makes': 'Big-number stat card', 'uses': f'{brand_name or "brand"} palette (deterministic render)',
                'how': 'figure + label, gentle push-in, VO'}
    return {'makes': kind, 'uses': '', 'how': ''}

def _best_set(still, sets):
    """Match a shot to a locked set by word overlap when the plan didn't name one."""
    words = set(w for w in still.lower().split() if len(w) > 3)
    best, score = None, 0
    for k, v in sets.items():
        hit = len(words & set(w for w in (v['name'] + ' ' + v['desc']).lower().split() if len(w) > 3))
        if hit > score: best, score = k, hit
    return best

BIBLE_BUDGET_S = 150      # hard cap on all reference-image work during planning

def _ref_image(prompt, timeout=90):
    """One attempt, short timeout — a reference image is a nice-to-have, never a
    reason for planning to hang."""
    import base64
    d = lib._post(f'{lib.BASE}/models/gemini-3-pro-image:generateContent',
                  {'contents': [{'parts': [{'text': prompt}]}],
                   'generationConfig': {'responseModalities': ['IMAGE']}},
                  timeout=timeout, tries=1)
    return next(base64.b64decode(x['inlineData']['data'])
                for x in d['candidates'][0]['content']['parts'] if 'inlineData' in x)

def _build_location_bible(vid, style, beats, pk, deadline=None):
    """Lock the SETS as well as the cast.

    Locking characters stopped the people changing, but every shot still invented
    a new bus, so an episode looked like five different buses. Each recurring
    location gets ONE reference image, and every shot set there is conditioned on
    it — same vehicle, same room, same geography.
    """
    import base64
    digest = [{'still': (b.get('still') or '')[:200]} for b in beats if b.get('kind', 'scene') == 'scene']
    if not digest:
        return {}
    try:
        out = json.loads(lib.text_gen(
            "These are the shots of one animated episode. List the DISTINCT physical locations used.\n"
            f"{json.dumps(digest)}\n\n"
            "Merge shots that happen in the same place into one location. For each, write a precise "
            "description of the SET itself — architecture, layout, colours, materials, furniture and where "
            "things sit relative to each other — detailed enough that every shot filmed there matches. "
            "Describe no people.\n"
            'Return ONLY JSON: {"locations":[{"name":"...","description":"..."}]}'))
    except Exception as e:
        print(f'  location bible failed: {str(e)[:120]}', flush=True)
        return {}

    locs = {}
    for c in (out.get('locations') or [])[:2]:
        if deadline and time.time() > deadline:
            print('  set bible: out of time, continuing without the rest', flush=True); break
        nm, desc = (c.get('name') or '').strip(), (c.get('description') or '').strip()
        if not nm or not desc: continue
        key = 'l_' + ''.join(ch for ch in nm.lower() if ch.isalnum())[:16]
        try:
            prompt = (f"{pk.get('look_prompt','')}\n\nWide establishing view of an EMPTY set with no people in "
                      f"it: {desc}. Show the whole space clearly so its layout is unmistakable. "
                      f"No text, letters, numbers, logos or watermarks.")
            img = _ref_image(prompt)
            ref = RUNS / vid / 'sets'; ref.mkdir(parents=True, exist_ok=True)
            fp = ref / f'{key}.png'; fp.write_bytes(img)
            a = supa.upload_asset(str(fp), 'graphic', title=f'SET {nm} ({style})', tags=['set', style], style=style)
            locs[key] = {'name': nm, 'desc': desc, 'ref_url': supa.public_url(a['storage_path'])}
            print(f'  set locked: {nm}', flush=True)
        except Exception as e:
            print(f'  set ref failed for {nm}: {str(e)[:100]}', flush=True)
    return locs


def _build_cast_bible(vid, style, beats, pk, deadline=None):
    """Lock the story's characters before anything is rendered.

    Without this every shot is drawn from scratch and the model invents a new
    boy, a new girl and a new driver each time. We ask who recurs, generate ONE
    reference image each, and every scene is then conditioned on those images —
    which is what keeps a character the same person for a whole episode.
    """
    import base64
    names = []
    for b in beats:
        for n in ([b.get('speaker')] if b.get('speaker') else []):
            if isinstance(n, str) and n not in names: names.append(n)
    if not names:
        return {}
    digest = [{'speaker': b.get('speaker'), 'still': (b.get('still') or '')[:220]} for b in beats]
    try:
        bible = json.loads(lib.text_gen(
            "These are the shots of one animated episode. List the RECURRING characters who appear in it.\n"
            f"{json.dumps(digest)}\n\n"
            "For each, write a precise visual description that will keep them identical in every shot: "
            "age, build, hair, face, skin tone, exact clothing and colours. No names of real people.\n"
            'Return ONLY JSON: {"characters":[{"name":"...","description":"..."}]}'))
    except Exception as e:
        print(f'  cast bible failed: {str(e)[:120]}', flush=True)
        return {}

    custom = {}
    for c in (bible.get('characters') or [])[:3]:
        if deadline and time.time() > deadline:
            print('  cast bible: out of time, continuing without the rest', flush=True); break
        nm, desc = (c.get('name') or '').strip(), (c.get('description') or '').strip()
        if not nm or not desc: continue
        key = 'c_' + ''.join(ch for ch in nm.lower() if ch.isalnum())[:16]
        try:
            prompt = (f"{pk.get('look_prompt','')}\n\nFull body character reference, facing forward, neutral "
                      f"friendly pose, centred on a plain solid white background. Character: {desc}. "
                      f"No text, letters, numbers, logos or watermarks.")
            img = _ref_image(prompt)
            ref = RUNS / vid / 'cast'; ref.mkdir(parents=True, exist_ok=True)
            fp = ref / f'{key}.png'; fp.write_bytes(img)
            a = supa.upload_asset(str(fp), 'char_ref', title=f'{nm} ({style})', tags=['cast', style], style=style)
            custom[key] = {'name': nm, 'desc': desc, 'ref_url': supa.public_url(a['storage_path'])}
            print(f'  cast locked: {nm}', flush=True)
        except Exception as e:
            print(f'  cast ref failed for {nm}: {str(e)[:100]}', flush=True)
    return custom


PLAN_MAX_S = 600   # planning must never hang; fail loudly instead

def plan_video(job):
    """Cheap planning phase (Fable 5 only): storyboard -> planned beats -> plan_review. No generation."""
    payload = job.get('payload') or {}
    vid = job['video_id']
    supa.update('videos', vid, {'status': 'planning'})
    style = payload.get('style', 'vyond')
    brand_name, brand = None, None
    if payload.get('brand_id'):
        try:
            brand = supa.select('brands', id=f"eq.{payload['brand_id']}")[0]
            brand_name = brand.get('name')
            # a brand can pin its own style (a maths channel isn't a legal one)
            if brand.get('default_style') and not payload.get('style'): style = brand['default_style']
        except Exception: pass
    supa._rest('DELETE', 'video_beats', params={'video_id': f'eq.{vid}'})
    ts = payload.get('target_seconds')
    # a series can pin a fixed cast; also allow user-created characters as valid keys
    series_cast = payload.get('cast_keys') or ((supa.select('videos', id=f'eq.{vid}')[0].get('progress') or {}).get('cast_keys')) or []
    known_cast = set(CHARACTERS) | {c['key'] for c in _db_characters()}
    learnings = None
    try:
        g = supa.select('growth', order='created_at.desc', limit='1')
        if g and g[0].get('guidelines'):
            learnings = "\n".join(f"- {x}" for x in g[0]['guidelines'])
    except Exception:
        learnings = None
    sb = storyboard(style, payload.get('topic'), payload.get('script'),
                    article_url=payload.get('article_url'),
                    target_seconds=int(ts) if ts else None, learnings=learnings, brand=brand)
    beats = sb['beats']
    pk_plan = style_pack(style)
    style_has_cast = any((c.get('style') or style) == style for c in _db_characters())
    _deadline = time.time() + BIBLE_BUDGET_S
    bible = {} if style_has_cast else _build_cast_bible(vid, style, beats, pk_plan, _deadline)
    name_to_key = {v['name'].lower(): k for k, v in bible.items()}
    sets = _build_location_bible(vid, style, beats, pk_plan, _deadline) if pk_plan.get('lock_sets') else {}
    set_names = {v['name'].lower(): k for k, v in sets.items()}
    counts = {'scene': 0, 'board': 0, 'stat': 0}
    est_total = 0.0; est_cost = 0.0
    for i, b in enumerate(beats):
        kind = b.get('kind', 'scene'); counts[kind] = counts.get(kind, 0) + 1
        dur = _est_secs(b['vo']); est_total += dur
        if kind == 'scene': est_cost += 1.2
        meta = {'bg': b.get('bg', ''), 'plan': _beat_plan(kind, brand_name, style)}
        if b.get('cast'): meta['cast'] = [c for c in b['cast'] if c in known_cast]
        if b.get('speaker') in known_cast: meta['speaker'] = b['speaker']
        if b.get('shot'): meta['shot'] = b['shot']
        # styles without a registered cast still name who talks, as free text
        if b.get('speaker') and b['speaker'] not in known_cast: meta['speaker_name'] = str(b['speaker'])
        if bible and kind == 'scene':
            # cast this shot from the locked bible so the same people appear throughout
            want = [str(x) for x in (b.get('cast') or [])] + ([str(b['speaker'])] if b.get('speaker') else [])
            keys = [name_to_key[w.lower()] for w in want if w.lower() in name_to_key]
            meta['cast'] = list(dict.fromkeys(keys)) or list(bible)[:2]
            spk = str(b.get('speaker') or '').lower()
            if spk in name_to_key: meta['speaker'] = name_to_key[spk]
        if sets and kind == 'scene':
            want = str(b.get('location') or '').lower()
            meta['location'] = set_names.get(want) or _best_set(b.get('still') or '', sets)
        # a series pins its cast, so every episode uses the same characters
        if series_cast and kind == 'scene':
            meta['cast'] = [c for c in (meta.get('cast') or []) if c in series_cast] or list(series_cast)
        if kind in ('board', 'stat'): meta['no_trim'] = True; meta[kind] = b.get(kind) or {}
        supa.insert('video_beats', {
            'video_id': vid, 'idx': i, 'kind': kind, 'vo_text': b['vo'],
            'scene_prompt': b.get('still') or b.get('scene', ''), 'motion_prompt': b.get('motion', ''),
            'dur_s': round(dur + 0.6, 2), 'status': 'planned', 'meta': meta})
    plan = {'cast': {'custom': bible} if bible else {}, 'sets': sets,
            'title': sb.get('title'), 'style': style, 'brand': brand_name,
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
    speaker_key = (b.get('meta') or {}).get('speaker')
    pk = style_pack(style)
    veo_voice = (pk.get('video_model') or 'omni').startswith('veo') and kind == 'scene'
    vo_asset = None
    if not veo_voice:
        if not wav.exists(): lib.tts(b['vo_text'], str(wav), **_voice_for(style, speaker_key))
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
    if pk.get('render_mode', 'image_to_video') == 'image_to_video':
        import base64
        cast_refs, cast_desc = _cast_for_beat(b, video_cast, style)
        if not cast_refs: cast_refs = _style_refs(style)
        parts = [{'inline_data': {'mime_type': 'image/png', 'data': base64.b64encode(r).decode()}} for r in cast_refs]
        extra = (" Featured character(s): " + cast_desc + ".") if cast_desc else ""
        # the locked set goes in as a reference as well — same vehicle, same room,
        # same layout in every shot filmed there
        set_note = ""
        lk = (b.get('meta') or {}).get('location')
        sets = (v.get('progress') or {}).get('sets') or {}
        if lk and lk in sets:
            simg = _fetch_ref(sets[lk].get('ref_url') or '')
            if simg:
                parts.append({'inline_data': {'mime_type': 'image/png', 'data': base64.b64encode(simg).decode()}})
                set_note = (f" The final reference image is THE SET: this shot takes place in that exact "
                            f"{sets[lk]['name']} — match its layout, colours and materials precisely, and keep "
                            f"the geography consistent (things stay where they are).")
        parts.append({'text': pk.get('look_prompt', '') + extra + set_note +
                      "\n\nScene (wide 16:9): " + (b['scene_prompt'] or '')})
        d = lib._post(f'{lib.BASE}/models/gemini-3-pro-image:generateContent',
                      {'contents': [{'parts': parts}], 'generationConfig': {'responseModalities': ['IMAGE']}}, timeout=300)
        img = next(base64.b64decode(p['inlineData']['data']) for p in d['candidates'][0]['content']['parts'] if 'inlineData' in p)
        still_path.write_bytes(img); cost += 0.14
        vm = pk.get('video_model') or 'omni'
        if vm.startswith('veo'):
            model = {'veo': 'veo-3.1-generate-preview', 'veo-fast': 'veo-3.1-fast-generate-preview',
                     'veo-lite': 'veo-3.1-lite-generate-preview'}.get(vm, vm)
            clip = lib.veo_i2v(img, _veo_brief(pk, b, cast_desc or _speaker_name(b, style)), model=model)
            cost += 1.20
            veo_used = True
        else:
            clip = lib.omni_i2v(img, _motion_brief(style, b.get('motion_prompt'), _speaker_name(b, style)))
            veo_used = False
    else:
        clip = lib.omni_video(_look_for_beat(pk, b) + "\n\nScene: " + (b['scene_prompt'] or '')); img = None
    if not locals().get('veo_used'): cost += 1.05   # veo already billed above
    clip_path = wd/'clips'/f'{i:02d}.mp4'; clip_path.write_bytes(clip)

    if vo_asset is None:
        # Veo performed the line — lift its audio out as this beat's voice track
        subprocess.run(['ffmpeg', '-nostdin', '-y', '-i', str(clip_path), '-vn',
                        '-ac', '1', '-ar', '24000', str(wav)], capture_output=True, text=True)
        if not wav.exists() or wav.stat().st_size < 1000:
            lib.tts(b['vo_text'], str(wav), **_voice_for(style, speaker_key))
        vo_asset = supa.upload_asset(str(wav), 'vo', title=f'{vid[:8]} beat{i} vo (in-clip)',
                                     tags=['vo', 'veo'], duration_s=_dur(wav))

    # optional lip sync: match the speaking character's mouth to their line
    if pk.get('lip_sync') and speaker_key:
        try:
            from pipeline import lipsync
            raw = supa.upload_asset(str(clip_path), 'clip', title=f'{vid[:8]} beat{i} pre-sync',
                                    tags=['raw', style], style=style)
            synced = lipsync.sync(supa.public_url(raw['storage_path']),
                                  supa.public_url(vo_asset['storage_path']))
            clip_path.write_bytes(synced)
            cost += 0.30
            print(f'  beat {i}: lip synced', flush=True)
        except Exception as e:
            # never fail a beat over lip sync — keep the un-synced clip
            print(f'  beat {i}: lip sync skipped ({str(e)[:120]})', flush=True)

    is_veo = (pk.get('video_model') or 'omni').startswith('veo') and kind == 'scene'
    dur = round(_dur(clip_path), 2) if is_veo else round(_dur(wav) + 0.6, 2)
    patch = {'status': 'done', 'vo_asset': vo_asset['id'], 'dur_s': dur}
    if is_veo:
        patch['meta'] = {**(b.get('meta') or {}), 'no_trim': True, 'in_clip_audio': True}
    if img:
        sa = supa.upload_asset(str(still_path), 'still', title=f'{vid[:8]} beat{i}', tags=['auto', style], style=style, cost=0.14)
        patch['still_asset'] = sa['id']
    ca = supa.upload_asset(str(clip_path), 'clip', title=f'{vid[:8]} beat{i}', tags=['auto', style], style=style, duration_s=_dur(clip_path), cost=1.05)
    patch['clip_asset'] = ca['id']
    supa.update('video_beats', bid, patch)
    return cost


def _fetch_asset(asset_id, dest):
    row = supa.select('assets', id=f'eq.{asset_id}')[0]
    url = supa.public_url(row['storage_path'])
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    import requests
    with requests.get(url, stream=True, timeout=300) as r:
        with open(dest, 'wb') as f:
            for c in r.iter_content(1 << 16): f.write(c)
    return dest

def _clip_fps(path):
    try:
        r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                            '-show_entries', 'stream=r_frame_rate', '-of', 'csv=p=0', str(path)],
                           capture_output=True, text=True).stdout.strip()
        n, d = (r.split('/') + ['1'])[:2]
        return float(n) / float(d or 1)
    except Exception:
        return 0.0

def assemble_video(video_id):
    # flip the row to 'rendering' straight away so the dashboard reflects it even
    # if the click-time update was missed
    supa.update('videos', video_id, {'status': 'rendering'})
    _stage(video_id, 'assembling', assemble_started_at=_now_iso())
    v = supa.select('videos', id=f'eq.{video_id}')[0]
    beats = sorted(supa.select('video_beats', video_id=f'eq.{video_id}'), key=lambda b: b['idx'])
    beats = [b for b in beats if b['status'] == 'done']
    wd = RUNS / video_id; segs = wd/'segs'
    for d in (segs, wd/'clips', wd/'audio', wd/'stills'): d.mkdir(parents=True, exist_ok=True)
    FPS = int(style_pack(v.get('style')).get('output_fps') or 24)
    brand = supa.select('brands', id=f"eq.{v['brand_id']}")[0] if v.get('brand_id') else None

    order = []
    if brand and brand.get('intro_asset'):
        ip = wd/'intro.mp4'
        if not ip.exists(): _fetch_asset(brand['intro_asset'], ip)
        out = segs/'intro.mp4'
        _run(['ffmpeg','-nostdin','-y','-i',str(ip),'-vf',f'scale=1920:1080:flags=lanczos,fps={FPS},format=yuv420p',
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
            vf = f"trim={trim}:{trim+d},setpts=PTS-STARTPTS,scale=1920:1080:flags=lanczos,unsharp=3:3:0.35,fps={FPS},format=yuv420p"
        else:
            factor = min(d/max(sd, 0.1), 1.5); hold = max(0.0, d - sd*factor)
            vf = (f"trim={trim},setpts=(PTS-STARTPTS)*{factor:.4f},scale=1920:1080:flags=lanczos,unsharp=3:3:0.35,"
                  f"tpad=stop_mode=clone:stop_duration={hold:.2f},trim=0:{d},fps={FPS},format=yuv420p")
        _run(['ffmpeg','-nostdin','-y','-i',str(src),'-vf',vf,'-an','-c:v','libx264','-crf','18',str(out)])
        b['start'] = t; t += d
        order.append(out)
    total_vo_end = t
    if brand and brand.get('outro_asset'):
        op = wd/'outro.mp4'
        if not op.exists(): _fetch_asset(brand['outro_asset'], op)
        out = segs/'zz_outro.mp4'
        _run(['ffmpeg','-nostdin','-y','-i',str(op),'-vf',f'scale=1920:1080:flags=lanczos,fps={FPS},format=yuv420p',
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
        lead = 0.0 if (b.get('meta') or {}).get('in_clip_audio') else 0.25
        lines = chunk(b['vo_text']); tot = sum(len(l) for l in lines); pos = 0
        for i, l in enumerate(lines):
            st = b['start'] + lead + vo_d*(pos/tot); pos += len(l)
            en = b['start'] + lead + vo_d*(pos/tot)
            if i == len(lines)-1: en = min(en+0.3, b['start']+float(b['dur_s']))
            ev.append(f"Dialogue: 0,{ts(st)},{ts(en-0.05)},Cap,,0,0,0,,{EFF}{l.strip().strip('–').strip()}")
    ass = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Cap,Poppins SemiBold,46,&H00FFFFFF,&H00FFFFFF,&H00382500,&H78000000,0,0,0,0,100,100,0,0,1,3,1.5,2,80,80,54,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n") + '\n'.join(ev) + '\n'
    (wd/'subs.ass').write_text(ass)
    if style_pack(v.get('style')).get('burn_captions', True):
        vf = f"ass={wd}/subs.ass:fontsdir={FONTS_DIR},eq=saturation=1.08:gamma=1.02"
    else:
        vf = "eq=saturation=1.08:gamma=1.02"
    _run(['ffmpeg','-nostdin','-y','-i',str(wd/'work.mp4'), '-vf', vf,
          '-an','-c:v','libx264','-crf','18','-preset','medium',str(wd/'work_subs.mp4')])

    # audio
    inp = ['-i', str(wd/'work_subs.mp4')]; fc = []; nl = []; i = 1
    for b in beats:
        wav = wd/'audio'/f"{b['idx']:02d}.wav"
        if not wav.exists(): _fetch_asset(b['vo_asset'], wav)
        lead = 0.0 if (b.get('meta') or {}).get('in_clip_audio') else 0.25
        dl = int((b['start'] + lead)*1000)
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
            if not wav.exists():
                lib.tts(b['vo_text'], str(wav), **_voice_for(style, (b.get('meta') or {}).get('speaker')))
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
    pk = style_pack(style)
    if pk.get('render_mode', 'image_to_video') == 'image_to_video':
        import base64
        cast_refs, cast_desc = _cast_for_beat(b, {}, style)
        refs = cast_refs or _style_refs(style)
        parts = [{'inline_data': {'mime_type': 'image/png', 'data': base64.b64encode(r).decode()}} for r in refs]
        extra = (" Featured character(s): " + cast_desc + ".") if cast_desc else ""
        parts.append({'text': pk.get('look_prompt', '') + extra + "\n\nScene (wide 16:9): " + scene})
        d = lib._post(f'{lib.BASE}/models/gemini-3-pro-image:generateContent',
                      {'contents': [{'parts': parts}], 'generationConfig': {'responseModalities': ['IMAGE']}}, timeout=300)
        img = next(base64.b64decode(p['inlineData']['data']) for p in d['candidates'][0]['content']['parts'] if 'inlineData' in p)
        (wd/'stills'/f'{i:02d}.png').write_bytes(img)
        clip = lib.omni_i2v(img, "Animate this exact image as a flat 2D explainer video clip. Keep the art style, characters, "
                            "colors and layout exactly as shown. " + (b.get('motion_prompt') or 'Subtle natural motion.') + " No new elements, no text.")
        sa = supa.upload_asset(str(wd/'stills'/f'{i:02d}.png'), 'still', title=f'reroll beat{i}', tags=['reroll', style], style=style)
    else:
        clip = lib.omni_video(_look_for_beat(pk, b) + "\n\nScene: " + scene); sa = None
    cp = wd/'clips'/f'{i:02d}.mp4'; cp.write_bytes(clip)
    spk = (b.get('meta') or {}).get('speaker')
    if pk.get('lip_sync') and spk and b.get('vo_asset'):
        try:
            from pipeline import lipsync
            raw = supa.upload_asset(str(cp), 'clip', title=f'reroll beat{i} pre-sync', tags=['raw', style], style=style)
            vo = supa.select('assets', id=f"eq.{b['vo_asset']}")[0]
            cp.write_bytes(lipsync.sync(supa.public_url(raw['storage_path']), supa.public_url(vo['storage_path'])))
            print(f'  beat {i}: lip synced', flush=True)
        except Exception as e:
            print(f'  beat {i}: lip sync skipped ({str(e)[:120]})', flush=True)
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
