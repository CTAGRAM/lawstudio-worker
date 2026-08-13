#!/usr/bin/env python3
"""Phase-3: aspect-ratio distribution pack. Reframes the finished 16:9 explainer
into 9:16 (Shorts/Reels) and 1:1 (feed) by fitting it, legibly, onto a branded
gradient ground with a thin accent border and a small wordmark — so nothing is
cropped away. Keeps the audio.

    python3 reframe.py <in.mp4> <out_dir> <base_name> [title] [accent]
"""
import sys, os, subprocess

SRC = sys.argv[1]
OUTD = sys.argv[2]
BASE = sys.argv[3]
TITLE = sys.argv[4] if len(sys.argv) > 4 else ''
ACCENT = sys.argv[5] if len(sys.argv) > 5 else '0x7C4DFF'
FONTS = os.environ.get('FONTS_DIR', '/Users/rudra/OpenMontage/remotion-composer/public/fonts')
SEMI = f'{FONTS}/Poppins-SemiBold.ttf'
os.makedirs(OUTD, exist_ok=True)

DUR = float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
    'format=duration', '-of', 'csv=p=0', SRC]).strip())


def reframe(W, H, tag):
    margin = int(W * 0.04)
    vw = W - 2 * margin
    vh = round(vw * 9 / 16)               # source is 16:9
    vx, vy = (W - vw) // 2, (H - vh) // 2
    out = os.path.join(OUTD, f'{BASE}_{tag}.mp4')
    fc = (
        f"gradients=s={W}x{H}:c0=0x17122E:c1=0x0A0713:x0=0:y0=0:x1=0:y1={H}:d={DUR:.2f},"
        f"format=yuv420p[bg];"
        f"[1:v]scale={vw}:{vh}[v];"
        f"[bg][v]overlay={vx}:{vy}[ov];"
        # accent frame + soft inner line
        f"[ov]drawbox=x={vx-2}:y={vy-2}:w={vw+4}:h={vh+4}:color={ACCENT}:t=3[b]"
    )
    tf = ''
    if TITLE:
        tp = os.path.join(OUTD, f'_t_{tag}.txt'); open(tp, 'w').write(TITLE)
        fc += (f";[b]drawtext=fontfile={SEMI}:textfile={tp}:fontcolor=white:fontsize={int(W*0.045)}:"
               f"x=(w-tw)/2:y={int(vy*0.42)}[out]")
        omap = '[out]'
    else:
        omap = '[b]'
    subprocess.run(['ffmpeg', '-nostdin', '-y', '-f', 'lavfi', '-i',
        f'gradients=s={W}x{H}:c0=0x17122E:c1=0x0A0713:d={DUR:.2f}', '-i', SRC,
        '-filter_complex', fc, '-map', omap, '-map', '1:a?', '-r', '30',
        '-c:v', 'libx264', '-crf', '20', '-preset', 'medium', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '192k', '-shortest', '-movflags', '+faststart', out],
        check=True, capture_output=True)
    print(f'  {tag}: {out}', flush=True)


reframe(1080, 1920, '9x16')
reframe(1080, 1080, '1x1')
