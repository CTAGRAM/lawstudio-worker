#!/usr/bin/env python3
"""Bake the Screen-Studio zoom/focus track into the recording (ffmpeg-fast path
for the polished style). Reproduces the composition's CSS transform: scale(z)
with transform-origin (fx,fy). Output is the zoomed recording at the window's
content size, ready to composite behind the Remotion overlay.

    python3 apply_focus.py <body.mp4> <focus.json> <out.mp4> [OW OH]
"""
import sys, json, subprocess
import numpy as np, cv2

SRC, FOCUS, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
OW = int(sys.argv[4]) if len(sys.argv) > 4 else 1460
OH = int(sys.argv[5]) if len(sys.argv) > 5 else 876
OW += OW % 2; OH += OH % 2   # libx264/yuv420p needs even dimensions
focus = json.load(open(FOCUS)) if FOCUS and FOCUS != '-' else []

cap = cv2.VideoCapture(SRC)
FPS = cap.get(cv2.CAP_PROP_FPS) or 30
W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
N = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
enc = ['ffmpeg', '-nostdin', '-loglevel', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{OW}x{OH}',
       '-r', f'{FPS}', '-i', 'pipe:0', '-c:v', 'libx264', '-crf', '19', '-preset', 'veryfast',
       '-pix_fmt', 'yuv420p', '-movflags', '+faststart', OUT]
ff = subprocess.Popen(enc, stdin=subprocess.PIPE)
for f in range(N):
    ok, fr = cap.read()
    if not ok:
        break
    z, fx, fy = focus[min(f, len(focus) - 1)] if focus else (1.0, 0.5, 0.5)
    if z <= 1.001:
        crop = fr
    else:
        cw, ch = W / z, H / z
        cx0 = min(max(fx * W * (1 - 1 / z), 0), W - cw)   # transform-origin scaling
        cy0 = min(max(fy * H * (1 - 1 / z), 0), H - ch)
        crop = fr[int(cy0):int(cy0 + ch), int(cx0):int(cx0 + cw)]
    ff.stdin.write(cv2.resize(crop, (OW, OH), interpolation=cv2.INTER_LINEAR).tobytes())
ff.stdin.close(); ff.wait(); cap.release()
sys.stderr.write(f'baked {N} frames -> {OW}x{OH}\n')
