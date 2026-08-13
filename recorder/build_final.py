#!/usr/bin/env python3
"""Assemble a finished explainer: branded intro -> body (VO + burned captions)
-> outro CTA. Env-driven so it works both locally (whisper captions, Remotion
cards passed in) and in the lean cloud service (text captions, ffmpeg cards).

    python3 build_final.py <body_zoom.mp4> <vo.wav> <out.mp4> [intro.mp4] [outro.mp4]

Env:
  FONTS_DIR          dir holding Poppins-ExtraBold.ttf / Poppins-SemiBold.ttf
  CAPTION_TEXT       narration text -> proportional captions (skips whisper)
  CARD_INTRO_TITLE / _TAGLINE / _KICKER   intro card text
  CARD_OUTRO_TITLE / _TAGLINE / _KICKER   outro card text
"""
import sys, os, re, subprocess

BODY, VO, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
INTRO_IN = sys.argv[4] if len(sys.argv) > 4 else None
OUTRO_IN = sys.argv[5] if len(sys.argv) > 5 else None
FONTS = os.environ.get('FONTS_DIR', '/Users/rudra/OpenMontage/remotion-composer/public/fonts')
EXTRA = f'{FONTS}/Poppins-ExtraBold.ttf'
SEMI = f'{FONTS}/Poppins-SemiBold.ttf'
MODEL = os.environ.get('WHISPER_MODEL', '/Users/rudra/.cache/whisper-cpp/ggml-base.en.bin')
TMP = os.path.dirname(OUT)
VO_OFFSET = 0.35
ACCENT = '0x7C4DFF'
E = os.environ.get


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.stderr.write(' '.join(map(str, cmd))[:180] + '\n' + r.stderr[-1200:] + '\n')
        raise SystemExit(f'cmd failed ({r.returncode})')
    return r


def dur(f):
    return float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
        'format=duration', '-of', 'csv=p=0', f]).strip())


def srt_t(t):
    h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f'{h}:{m:02d}:{s:05.2f}'


# ---- captions: from known narration text (no whisper) OR whisper on the VO ----
def captions_from_text(text, vo_dur):
    words = re.sub(r'\s+', ' ', text).strip().split(' ')
    groups, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= 4:
            groups.append(cur); cur = []
    if cur:
        groups.append(cur)
    total = max(1, len(words))
    caps, t = [], VO_OFFSET
    for g in groups:
        d = len(g) / total * vo_dur
        caps.append((t, t + d, ' '.join(g)))
        t += d
    return caps


def captions_from_whisper():
    wav16 = os.path.join(TMP, '_vo16.wav')
    run(['ffmpeg', '-nostdin', '-y', '-i', VO, '-ac', '1', '-ar', '16000', wav16])
    run(['whisper-cli', '-m', MODEL, '-f', wav16, '-ml', '1', '-osrt', '-of', os.path.join(TMP, '_vo')])
    words = []
    for b in open(os.path.join(TMP, '_vo.srt'), encoding='utf-8', errors='ignore').read().strip().split('\n\n'):
        ls = [l for l in b.splitlines() if l.strip()]
        if len(ls) < 2:
            continue
        line = ls[1] if '-->' in ls[1] else ls[0]
        m = re.search(r'(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)', line)
        if not m:
            continue
        g = list(map(int, m.groups()))
        st = g[0]*3600+g[1]*60+g[2]+g[3]/1000 + VO_OFFSET
        en = g[4]*3600+g[5]*60+g[6]+g[7]/1000 + VO_OFFSET
        txt = ' '.join(ls[2:]).strip() if '-->' in ls[1] else ' '.join(ls[1:]).strip()
        if txt and en > st:
            words.append((st, en, txt))
    caps, cur = [], []
    for w in words:
        if cur and (len(cur) >= 4 or w[1]-cur[0][0] > 2.2 or w[0]-cur[-1][1] > 0.5):
            caps.append((cur[0][0], cur[-1][1] + 0.12, ' '.join(x[2] for x in cur))); cur = []
        cur.append(w)
    if cur:
        caps.append((cur[0][0], cur[-1][1] + 0.12, ' '.join(x[2] for x in cur)))
    return caps


ASS_HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Poppins ExtraBold,58,&H00FFFFFF,&H00FFFFFF,&H00200A0E,&HB4000000,-1,0,0,0,100,100,0.4,0,1,4,2,2,200,200,90,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
POP = r"{\fad(90,60)\fscx86\fscy86\t(0,140,\fscx100\fscy100)}"

vo_dur = dur(VO)
caps = captions_from_text(E('CAPTION_TEXT'), vo_dur) if E('CAPTION_TEXT') else captions_from_whisper()
ass = os.path.join(TMP, '_caps.ass')
ev = []
for st, en, text in caps:
    text = re.sub(r'\s+([,.?!;:])', r'\1', text).replace('{', '(').replace('}', ')').upper()
    ev.append(f'Dialogue: 0,{srt_t(st)},{srt_t(max(en, st+0.4))},Cap,,0,0,0,,{POP}{text}')
open(ass, 'w', encoding='utf-8').write(ASS_HEAD + '\n'.join(ev) + '\n')
print(f'{len(caps)} captions', flush=True)

# ---- body: VO (delayed) + burned captions ----
bodyF = os.path.join(TMP, '_bodyF.mp4')
run(['ffmpeg', '-nostdin', '-y', '-i', BODY, '-i', VO,
     '-filter_complex',
     f"[0:v]ass={ass}:fontsdir={FONTS}[v];"
     f"[1:a]adelay={int(VO_OFFSET*1000)}|{int(VO_OFFSET*1000)},aresample=async=1,"
     f"loudnorm=I=-16:TP=-1.5:LRA=11,apad[a]",
     '-map', '[v]', '-map', '[a]', '-r', '30',
     '-c:v', 'libx264', '-crf', '19', '-preset', 'medium', '-pix_fmt', 'yuv420p',
     '-c:a', 'aac', '-b:a', '192k', '-shortest', bodyF])


# ---- branded intro / outro cards (ffmpeg gradient ground) ----
def card(path, secs, title, tagline, kicker=None):
    def tf(nm, s):
        p = os.path.join(TMP, f'_txt_{nm}.txt'); open(p, 'w').write(s or ''); return p
    vf = f"format=yuv420p,drawbox=x=760:y=636:w=400:h=7:color={ACCENT}:t=fill,"
    if kicker:
        vf += (f"drawtext=fontfile={SEMI}:textfile={tf('k',kicker)}:fontcolor={ACCENT}:fontsize=34:"
               f"x=(w-tw)/2:y=392,")
    vf += (f"drawtext=fontfile={EXTRA}:textfile={tf('t',title)}:fontcolor=white:fontsize=120:"
           f"x=(w-tw)/2:y=458,"
           f"drawtext=fontfile={SEMI}:textfile={tf('g',tagline)}:fontcolor=0xC8C6E0:fontsize=46:"
           f"x=(w-tw)/2:y=672")
    run(['ffmpeg', '-nostdin', '-y',
         '-f', 'lavfi', '-i', f'gradients=s=1920x1080:c0=0x17122E:c1=0x0A0713:x0=0:y0=0:x1=1920:y1=1080:d={secs}',
         '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-vf', vf, '-t', str(secs),
         '-c:v', 'libx264', '-crf', '19', '-preset', 'medium', '-pix_fmt', 'yuv420p',
         '-c:a', 'aac', '-b:a', '192k', '-shortest', path])


def norm_card(inp, path):
    run(['ffmpeg', '-nostdin', '-y', '-i', inp, '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
         '-map', '0:v', '-map', '1:a', '-r', '30', '-c:v', 'libx264', '-crf', '19', '-preset', 'medium',
         '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-shortest', path])


intro = os.path.join(TMP, '_intro.mp4')
outro = os.path.join(TMP, '_outro.mp4')
if INTRO_IN and os.path.exists(INTRO_IN):
    norm_card(INTRO_IN, intro)
else:
    card(intro, 2.8, E('CARD_INTRO_TITLE', 'Go Legal AI'), E('CARD_INTRO_TAGLINE', 'Legal help, in minutes'),
         kicker=E('CARD_INTRO_KICKER', "UK'S #1 CONSUMER LEGAL TECH"))
if OUTRO_IN and os.path.exists(OUTRO_IN):
    norm_card(OUTRO_IN, outro)
else:
    card(outro, 3.3, E('CARD_OUTRO_TITLE', 'Start for free'), E('CARD_OUTRO_TAGLINE', 'go-legal.ai'),
         kicker=E('CARD_OUTRO_KICKER', 'GET STARTED TODAY'))

# ---- concat intro + body + outro ----
run(['ffmpeg', '-nostdin', '-y', '-i', intro, '-i', bodyF, '-i', outro,
     '-filter_complex', '[0:v][0:a][1:v][1:a][2:v][2:a]concat=n=3:v=1:a=1[v][a]',
     '-map', '[v]', '-map', '[a]', '-r', '30', '-c:v', 'libx264', '-crf', '19', '-preset', 'medium',
     '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', OUT])
print(f'wrote {OUT}  ({dur(OUT):.1f}s)', flush=True)
