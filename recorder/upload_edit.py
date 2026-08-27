#!/usr/bin/env python3
"""Auto-edit an UPLOADED screen recording (no click log): cut/greatly speed the
dead pauses, and add a gentle 'follow the action' zoom toward wherever the screen
is changing. One computer-vision pass (frame diff). Output is a tighter, zoomed
body clip that then gets VO + captions + branding downstream.

    python3 upload_edit.py <in.mp4> <out.mp4>
"""
import sys, subprocess
import numpy as np, cv2

SRC, OUT = sys.argv[1], sys.argv[2]
import os as _os
IDLE_SPEED = int(_os.environ.get('IDLE_SPEED', '4'))   # truly-static frames kept 1-in-N (cursor frames are kept at 1x, see ACT_THR)
# Motion/zoom is a per-video option. When ON (ZOOM>1), we do a Screen-Studio /
# genie.ai-style dynamic zoom: punch in toward the action, ease back out when
# calm, with a smooth pan. When OFF (ZOOM=1.0) it's the full static frame.
ZMAX = float(_os.environ.get('ZOOM', '1.0'))
cap = cv2.VideoCapture(SRC)
FPS = cap.get(cv2.CAP_PROP_FPS) or 30
W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
N = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

# pass 1: per-frame activity + centre-of-activity (downscaled for speed)
sw, sh = 320, int(320 * H / W)
prev = None
act = np.zeros(N); cx = np.full(N, W / 2.0); cy = np.full(N, H / 2.0)
for f in range(N):
    ok, fr = cap.read()
    if not ok:
        N = f; break
    g = cv2.cvtColor(cv2.resize(fr, (sw, sh)), cv2.COLOR_BGR2GRAY)
    if prev is not None:
        d = cv2.absdiff(g, prev)
        m = (d > 22).astype(np.float32)
        s = m.sum()
        act[f] = s
        if s > 12:
            ys, xs = np.nonzero(m)
            cx[f] = xs.mean() / sw * W
            cy[f] = ys.mean() / sh * H
        else:
            cx[f] = cx[f - 1]; cy[f] = cy[f - 1]
    prev = g
act = act[:N]; cx = cx[:N]; cy = cy[:N]

# lightly de-noise the raw activity centre; carry last centre through still frames
def ema(a, al=0.10):
    o = a.copy()
    for i in range(1, len(a)):
        o[i] = al * a[i] + (1 - al) * o[i - 1]
    return o
cx, cy = ema(cx), ema(cy)
# "dead" = almost nothing changed. A LOW absolute threshold so even a moving
# mouse cursor counts as activity and is KEPT at natural speed — only truly
# static frames (reading pauses, loading) get compressed, so the cursor never
# looks sped up.
thr = float(_os.environ.get('ACT_THR', '10'))

# decide kept frames (speed dead spans)
keep = np.zeros(N, bool); run = 0
for f in range(N):
    if act[f] >= thr:
        keep[f] = True; run = 0
    else:
        keep[f] = (run % IDLE_SPEED == 0); run += 1
kept = np.nonzero(keep)[0]
n_out = len(kept)
print(f'{N}->{n_out} frames ({N/FPS:.0f}s->{n_out/FPS:.0f}s), dead-time trimmed', flush=True)

ZOOM_ON = ZMAX > 1.001

# CALM pan: over the OUTPUT (kept) timeline, glide smoothly toward the action
# (deadzone + velocity clamp) so the camera moves naturally without snapping.
DEAD = 0.06 * W
MAXV = 0.0016 * W        # ~3 px/frame @1080p -> a smooth, natural drift
tx, ty = cx[kept], cy[kept]
camx = np.empty(n_out); camy = np.empty(n_out)
pxc, pyc = float(tx[0]) if n_out else W / 2.0, float(ty[0]) if n_out else H / 2.0
for i in range(n_out):
    dx, dy = tx[i] - pxc, ty[i] - pyc
    d = (dx * dx + dy * dy) ** 0.5
    if d > DEAD:
        step = min(MAXV, d - DEAD)
        pxc += dx / d * step; pyc += dy / d * step
    camx[i], camy[i] = pxc, pyc

# DYNAMIC zoom LEVEL: punch in toward the action, ease back out when calm
# (Screen-Studio / genie.ai look). Asymmetric smoothing = quicker to zoom in,
# slow to zoom out, so it holds the close-up instead of pulsing.
if ZOOM_ON:
    ZMINZ = 1.0 + (ZMAX - 1.0) * 0.5     # a partly-zoomed baseline
    a = act[kept].astype(np.float64)
    p = np.percentile(a[a > 0], 80) if (a > 0).any() else 1.0
    envraw = np.clip(a / max(p, 1.0), 0.0, 1.0)
    zt = np.empty(n_out); e = 0.0
    for i in range(n_out):
        al = 0.05 if envraw[i] > e else 0.010
        e = al * envraw[i] + (1 - al) * e
        zt[i] = ZMINZ + (ZMAX - ZMINZ) * e
else:
    zt = np.ones(n_out)

# remove a bottom-right screen-recorder watermark (e.g. Castify free tier)
vf = []
if _os.environ.get('DELOGO_OFF') != '1':
    dw, dh = int(W * 0.24), int(H * 0.18)
    dx0, dy0 = W - dw - 6, H - dh - 4
    vf.append(f'delogo=x={dx0}:y={dy0}:w={dw}:h={dh}')

cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
enc = ['ffmpeg', '-nostdin', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24',
    '-s', f'{W}x{H}', '-r', f'{FPS}', '-i', 'pipe:0']
if vf: enc += ['-vf', ','.join(vf)]
enc += ['-c:v', 'libx264', '-crf', '20', '-preset', 'veryfast', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', OUT]
ff = subprocess.Popen(enc, stdin=subprocess.PIPE)
oi = 0
for f in range(N):
    ok, fr = cap.read()
    if not ok:
        break
    if not keep[f]:
        continue
    z = zt[oi]; ccx, ccy = camx[oi], camy[oi]; oi += 1
    if z <= 1.001:
        ff.stdin.write(fr.tobytes())   # full frame, nothing cropped, no movement
        continue
    cw, ch = W / z, H / z
    x0 = min(max(ccx - cw / 2, 0), W - cw)
    y0 = min(max(ccy - ch / 2, 0), H - ch)
    crop = fr[int(y0):int(y0 + ch), int(x0):int(x0 + cw)]
    ff.stdin.write(cv2.resize(crop, (W, H), interpolation=cv2.INTER_LINEAR).tobytes())
ff.stdin.close(); ff.wait(); cap.release()
print(f'wrote {OUT} (zoom {"on" if ZOOM_ON else "off"})', flush=True)
