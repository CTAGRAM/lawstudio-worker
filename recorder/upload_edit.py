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

# CLEAN camera (Screen-Studio / genie.ai): HOLD a fixed framing, dead still, and
# only RE-TARGET with a single deliberate, fully-eased move when the action
# clearly relocates and STAYS there. No continuous cursor-follow — that constant
# micro-drift is what read as a "drunk" wandering camera.
txr, tyr = cx[kept].astype(np.float64), cy[kept].astype(np.float64)
def _ema(a, al):
    o = a.copy()
    for i in range(1, len(a)):
        o[i] = al * a[i] + (1 - al) * o[i - 1]
    return o
txr, tyr = _ema(txr, 0.05), _ema(tyr, 0.05)      # heavy smoothing kills jitter

camx = np.empty(n_out); camy = np.empty(n_out)
RETARGET = 0.11 * W                              # only move if action shifts > 11% of width
MIN_HOLD = max(1, int(FPS * 0.8))                # ...and stays shifted this long (0.8s)
TRANS = max(1, int(FPS * 0.7))                   # a move takes 0.7s, cosine-eased, then settles
if n_out:
    seed = min(n_out, MIN_HOLD)
    lockx = float(np.median(txr[:seed])); locky = float(np.median(tyr[:seed]))
else:
    lockx, locky = W / 2.0, H / 2.0
i = 0
dwell = MIN_HOLD                                 # frames held since the last move (start "rested")
while i < n_out:
    dev = ((txr[i] - lockx) ** 2 + (tyr[i] - locky) ** 2) ** 0.5
    if dev > RETARGET and dwell >= MIN_HOLD:     # ...and we've settled a beat since the last move
        end = min(n_out, i + MIN_HOLD)
        stayed = all(((txr[k] - lockx) ** 2 + (tyr[k] - locky) ** 2) ** 0.5 > RETARGET * 0.6
                     for k in range(i, end))
        if stayed:
            newx = float(np.median(txr[i:end])); newy = float(np.median(tyr[i:end]))
            for t in range(TRANS):
                if i + t >= n_out:
                    break
                s = 0.5 - 0.5 * np.cos(np.pi * (t + 1) / TRANS)   # ease-in-out
                camx[i + t] = lockx + (newx - lockx) * s
                camy[i + t] = locky + (newy - locky) * s
            lockx, locky = newx, newy
            i += TRANS
            dwell = 0
            continue
    camx[i] = lockx; camy[i] = locky
    i += 1; dwell += 1

# CLEAN zoom: ease in ONCE to a steady level and HOLD it — no per-activity
# pulsing. A calm, constant push-in reads as intentional, not nervous.
if ZOOM_ON:
    zt = np.full(n_out, ZMAX)
    ramp = min(n_out, max(1, int(FPS * 0.6)))
    for t in range(ramp):
        s = 0.5 - 0.5 * np.cos(np.pi * (t + 1) / ramp)
        zt[t] = 1.0 + (ZMAX - 1.0) * s
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
