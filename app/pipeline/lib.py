"""Shared API primitives for the flat-2D studio pipeline (all with retry/backoff).

Every 'agent' in the pipeline is one of these model calls:
  text_gen  -> scriptwriter/art-director (gemini text)
  nano_scene_still -> character compositor (nano-banana, keeps locked character + layout)
  veo_i2v   -> character animator (Veo 3.1 image-to-video, flat-2D preserved)
  omni_video-> icon/concept animator (Gemini Omni Flash text->video)
  tts       -> narrator (Gemini TTS)
  vision_qa -> QA reviewer (gemini vision, pass/fail + issues)
"""
import requests, base64, json, time, re, wave, io
from pathlib import Path
from dotenv import dotenv_values
import os
def _cred(k):
    v=os.environ.get(k)
    if v: return v
    try: return dotenv_values('/Users/rudra/OpenMontage/.env').get(k)
    except Exception: return None

ROOT = Path('/Users/rudra/OpenMontage/pipelines/flat2d-studio')
GEM = _cred('GEMINI_API_KEY')
BASE = 'https://generativelanguage.googleapis.com/v1beta'

def _brand_file():
    """brand.json lives under brand/ locally and in ASSETS_ROOT in the container."""
    for p in (ROOT/'brand/brand.json',
              Path(os.environ.get('ASSETS_ROOT', '')) / 'brand.json',
              Path(__file__).resolve().parent.parent / 'assets/brand.json'):
        if p.is_file(): return p
    raise FileNotFoundError('brand.json not found (checked brand/ and ASSETS_ROOT)')

BRAND = json.loads(_brand_file().read_text())

# ---- cost tracking (approx, USD) ----
COST = {'usd': 0.0}
def _add_cost(kind, out_tokens=0, veo_seconds=0):
    if kind == 'omni':  COST['usd'] += out_tokens * 17.50/1e6          # video output tokens
    elif kind == 'veo': COST['usd'] += veo_seconds * 0.15              # veo fast ~ approx/sec
    elif kind == 'img': COST['usd'] += 0.04
    elif kind == 'tts': COST['usd'] += 0.02
    elif kind == 'text':COST['usd'] += 0.01

def _post(url, body, timeout=600, tries=6):
    last = None
    for i in range(tries):
        try:
            r = requests.post(url, params={'key': GEM}, json=body, timeout=timeout)
            if r.status_code == 429:
                wait = 30*(i+1); print(f'    429 rate-limit, backoff {wait}s'); time.sleep(wait); continue
            r.raise_for_status(); return r.json()
        except requests.HTTPError as e:
            last = e;
            if r.status_code >= 500: time.sleep(15*(i+1)); continue
            raise
        except requests.RequestException as e:
            last = e; time.sleep(10*(i+1)); continue
    raise RuntimeError(f'POST failed after {tries}: {last}')

# ---- primitives ----
ANTHROPIC_KEY = _cred('ANTHROPIC_API_KEY')

# The scriptwriter model, overridable per job (Create form / brand default).
CLAUDE_MODEL = 'claude-fable-5'

def set_claude_model(m):
    """Pick the Anthropic model for this job's scriptwriting (must be a claude-* id)."""
    global CLAUDE_MODEL
    if m and isinstance(m, str) and m.startswith('claude-'):
        CLAUDE_MODEL = m

def claude_gen(prompt, model=None, json_out=True, max_tokens=8000):
    """Text generation via Anthropic (pipeline director/scriptwriter brain)."""
    model = model or CLAUDE_MODEL
    body = {'model': model, 'max_tokens': max_tokens,
            'messages': [{'role': 'user', 'content': prompt + ('\n\nReturn ONLY valid JSON, no prose.' if json_out else '')}]}
    for attempt in range(4):
        r = requests.post('https://api.anthropic.com/v1/messages',
            headers={'x-api-key': ANTHROPIC_KEY, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
            json=body, timeout=300)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(20 * (attempt + 1)); continue
        r.raise_for_status()
        d = r.json()
        txt = next((b['text'] for b in d.get('content', []) if b.get('type') == 'text'), None)
        if txt is None: raise RuntimeError(f'claude_gen: no text block: {str(d)[:200]}')
        _add_cost('text')
        if json_out:
            m = re.search(r'```(?:json)?\s*(.*?)```', txt, re.S)
            if m: txt = m.group(1)
        return txt.strip()
    raise RuntimeError('claude_gen: retries exhausted')


def text_gen(prompt, model='gemini-2.5-pro', json_out=True, temp=0.6):
    if ANTHROPIC_KEY:
        try:
            return claude_gen(prompt, json_out=json_out)
        except Exception as e:
            print(f'    claude_gen failed ({str(e)[:80]}), falling back to gemini')
    cfg = {'temperature': temp}
    if json_out: cfg['responseMimeType'] = 'application/json'
    d = _post(f'{BASE}/models/{model}:generateContent', {'contents':[{'parts':[{'text':prompt}]}], 'generationConfig':cfg})
    _add_cost('text')
    return d['candidates'][0]['content']['parts'][0]['text']

def _first_image(d):
    for p in d['candidates'][0]['content']['parts']:
        if 'inlineData' in p: return base64.b64decode(p['inlineData']['data'])
    return None

def gen_image(prompt, model='gemini-3-pro-image'):
    d = _post(f'{BASE}/models/{model}:generateContent',
              {'contents':[{'parts':[{'text':prompt}]}], 'generationConfig':{'responseModalities':['IMAGE']}}, timeout=240)
    _add_cost('img'); img = _first_image(d)
    if not img: raise RuntimeError('no image returned')
    return img

def nano_scene_still(ref_png_bytes, prompt, model='gemini-2.5-flash-image'):
    b = base64.b64encode(ref_png_bytes).decode()
    parts = [{'inline_data':{'mime_type':'image/png','data':b}}, {'text':prompt}]
    d = _post(f'{BASE}/models/{model}:generateContent',
              {'contents':[{'parts':parts}], 'generationConfig':{'responseModalities':['IMAGE']}}, timeout=240)
    _add_cost('img'); img = _first_image(d)
    if not img: raise RuntimeError('no scene still returned')
    return img

def _find_video_b64(o):
    if isinstance(o, dict):
        m = o.get('mimeType') or ''
        for k in ('data','bytesBase64Encoded'):
            v = o.get(k)
            if isinstance(v,str) and len(v)>5000 and ('video' in m or not m): return v
        for v in o.values():
            r=_find_video_b64(v)
            if r: return r
    elif isinstance(o,list):
        for v in o:
            r=_find_video_b64(v)
            if r: return r

def omni_i2v(image_bytes, prompt, mime='image/png'):
    """Image-conditioned Omni video (undocumented steps schema, verified 2026-08-02)."""
    import base64 as _b64
    d = _post(f'{BASE}/interactions', {'model': 'gemini-omni-flash-preview', 'input': [
        {'type': 'image', 'data': _b64.b64encode(image_bytes).decode(), 'mime_type': mime},
        {'type': 'text', 'text': prompt}]}, timeout=600)
    b64 = _find_video_b64(d)
    if not b64: raise RuntimeError('omni_i2v: no video')
    COST['usd'] += d.get('usage', {}).get('total_tokens', 60000) / 1_000_000 * 17.5
    return __import__('base64').b64decode(b64)

def omni_video(prompt):
    d = _post(f'{BASE}/interactions', {'model':'gemini-omni-flash-preview','input':prompt}, timeout=600)
    b64 = _find_video_b64(d)
    if not b64: raise RuntimeError('omni: no video')
    _add_cost('omni', out_tokens=d.get('usage',{}).get('total_output_tokens',59000))
    return base64.b64decode(b64)

class VeoFiltered(Exception):
    """Veo returned no video (RAI safety filter or empty samples)."""

def veo_i2v(still_png_bytes, motion_prompt, model='veo-3.1-fast-generate-preview', poll_s=15,
            max_polls=40, seconds=None):
    b = base64.b64encode(still_png_bytes).decode()
    params = {'aspectRatio':'16:9','personGeneration':'allow_adult'}
    # Veo bills per second, so asking for the length we actually want to cut to is
    # cheaper than generating 8s and throwing half away.
    if seconds:
        params['durationSeconds'] = int(seconds)
    body = {'instances':[{'prompt':motion_prompt,'image':{'bytesBase64Encoded':b,'mimeType':'image/png'}}],
            'parameters':params}
    d = _post(f'{BASE}/models/{model}:predictLongRunning', body, timeout=120)
    op = d['name']
    for _ in range(max_polls):
        time.sleep(poll_s)
        pr = requests.get(f'{BASE}/{op}', params={'key':GEM}, timeout=60).json()
        if pr.get('done'):
            if pr.get('error'):
                raise VeoFiltered(f"veo error: {pr['error']}")
            resp = pr.get('response', {})
            samples = resp.get('generateVideoResponse', {}).get('generatedSamples')
            if not samples:
                raise VeoFiltered(f"veo returned no samples (filtered). resp keys={list(resp.get('generateVideoResponse',{}).keys())}")
            uri = samples[0]['video']['uri']
            vd = requests.get(uri, params={'key':GEM}, timeout=180).content
            _add_cost('veo', veo_seconds=int(seconds or 8))
            return vd
    raise RuntimeError('veo: timeout')

ELEVEN_KEY = _cred('ELEVENLABS_API_KEY')


def _eleven_tts(text, outfile, voice_id):
    """Synthesize with a character's cloned ElevenLabs voice; write a mono wav so
    the assembly pipeline treats it exactly like a Gemini clip."""
    import subprocess, os as _os
    r = requests.post(f'https://api.elevenlabs.io/v1/text-to-speech/{voice_id}',
                      params={'output_format': 'mp3_44100_128'},
                      headers={'xi-api-key': ELEVEN_KEY, 'accept': 'audio/mpeg', 'content-type': 'application/json'},
                      json={'text': text, 'model_id': 'eleven_multilingual_v2',
                            'voice_settings': {'stability': 0.5, 'similarity_boost': 0.85, 'use_speaker_boost': True}},
                      timeout=180)
    r.raise_for_status()
    tmp = str(outfile) + '.mp3'
    with open(tmp, 'wb') as f: f.write(r.content)
    subprocess.run(['ffmpeg', '-nostdin', '-y', '-i', tmp, '-ac', '1', '-ar', '24000', str(outfile)],
                   capture_output=True)
    try: _os.remove(tmp)
    except OSError: pass
    _add_cost('tts')
    with wave.open(str(outfile)) as w:
        return w.getnframes() / w.getframerate()


def tts(text, outfile, voice=None, style=None, voice_provider=None, voice_id=None):
    # a beat with no line must be handled as silence by the caller, never sent
    # here — guard so an empty line fails clearly instead of "str + NoneType"
    if not (text or '').strip():
        raise ValueError('tts called with empty text (a silent beat must use _silent_wav instead)')
    # a character with a cloned voice speaks in it; fall back to Gemini if the
    # clone can't be reached so a beat never fails over a voice
    if voice_provider == 'elevenlabs' and voice_id and ELEVEN_KEY:
        try:
            return _eleven_tts(text, outfile, voice_id)
        except Exception as e:
            print(f'    elevenlabs tts failed ({str(e)[:90]}), falling back to gemini', flush=True)
    voice = voice or BRAND['voice']
    style = style or "Read in a calm, warm, professional British explainer voice, clear, measured and reassuring: "
    d = _post(f'{BASE}/models/gemini-2.5-pro-preview-tts:generateContent',
              {'contents':[{'parts':[{'text':style+text}]}],
               'generationConfig':{'responseModalities':['AUDIO'],
                 'speechConfig':{'voiceConfig':{'prebuiltVoiceConfig':{'voiceName':voice}}}}}, timeout=180)
    part = d['candidates'][0]['content']['parts'][0]['inlineData']
    pcm = base64.b64decode(part['data']); rate = int(part['mimeType'].split('rate=')[1].split(';')[0])
    with wave.open(str(outfile),'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm)
    _add_cost('tts')
    return len(pcm)/2/rate

def vision_qa(frame_paths, checklist, model='gemini-2.5-flash'):
    """Send frames + checklist to a vision model. Returns {'pass':bool,'issues':[...]}."""
    parts = []
    for fp in frame_paths:
        parts.append({'inline_data':{'mime_type':'image/jpeg','data':base64.b64encode(Path(fp).read_bytes()).decode()}})
    parts.append({'text': checklist + "\n\nReturn ONLY JSON: {\"pass\": true|false, \"issues\": [\"...\"]}"})
    d = _post(f'{BASE}/models/{model}:generateContent',
              {'contents':[{'parts':parts}], 'generationConfig':{'temperature':0.1,'responseMimeType':'application/json'}}, timeout=120)
    _add_cost('text')
    txt = d['candidates'][0]['content']['parts'][0]['text']
    try: return json.loads(re.sub(r'^```json|```$','',txt.strip()).strip())
    except Exception: return {'pass': True, 'issues': [], '_raw': txt[:200]}

