#!/usr/bin/env python3
"""Branded 16:9 thumbnail/poster for a video — consistent per brand: the brand
navy gradient, accent bar, logo, a bold title and a short hook. ffmpeg only
(no PIL), so it runs in the lean recorder image.

    python3 thumbnail.py <out.png> <title> <hook> <accent 0xRRGGBB> <navy 0xRRGGBB> [logo.png]
Env: FONTS_DIR
"""
import sys, os, subprocess, textwrap

OUT, TITLE, HOOK = sys.argv[1], sys.argv[2], sys.argv[3]
ACCENT = sys.argv[4] if len(sys.argv) > 4 else '0xF6BB54'
NAVY = sys.argv[5] if len(sys.argv) > 5 else '0x12202E'
LOGO = sys.argv[6] if len(sys.argv) > 6 and os.path.exists(sys.argv[6]) else None
FONTS = os.environ.get('FONTS_DIR', '/Users/rudra/OpenMontage/remotion-composer/public/fonts')
EXTRA, SEMI = f'{FONTS}/Poppins-ExtraBold.ttf', f'{FONTS}/Poppins-SemiBold.ttf'
TMP = os.path.dirname(OUT) or '.'


def darker(hexc, f=0.45):
    h = hexc.replace('0x', '').replace('#', '')
    r, g, b = (int(int(h[i:i+2], 16) * f) for i in (0, 2, 4))
    return f'0x{r:02X}{g:02X}{b:02X}'


def tf(nm, s):
    p = os.path.join(TMP, f'_th_{nm}.txt'); open(p, 'w').write(s); return p


title = '\n'.join((textwrap.wrap(TITLE.strip(), width=16) or [TITLE.strip()])[:2])

# drawbox/drawtext chain applied to the gradient bg
draws = (
    f"format=yuv420p,"
    f"drawbox=x=0:y=0:w=14:h=720:color={ACCENT}:t=fill,"
    f"drawbox=x=96:y=470:w=250:h=10:color={ACCENT}:t=fill,"
    f"drawtext=fontfile={EXTRA}:textfile={tf('t', title)}:fontcolor=white:fontsize=104:line_spacing=8:x=96:y=180,"
    f"drawtext=fontfile={SEMI}:textfile={tf('h', HOOK.strip())}:fontcolor={ACCENT}:fontsize=44:x=96:y=506"
)
grad = f'gradients=s=1280x720:c0={NAVY}:c1={darker(NAVY)}:x0=0:y0=0:x1=1280:y1=720:d=1'

cmd = ['ffmpeg', '-nostdin', '-y', '-f', 'lavfi', '-i', grad]
if LOGO:
    cmd += ['-i', LOGO, '-filter_complex',
            f"[0:v]{draws}[base];[1:v]scale=-1:150[lg];[base][lg]overlay=W-w-70:70[out]",
            '-map', '[out]']
else:
    cmd += ['-vf', draws]
cmd += ['-frames:v', '1', OUT]
subprocess.run(cmd, check=True, capture_output=True)
print(f'wrote {OUT}', flush=True)
