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

# Trim dead time WITHOUT stutter. The old approach decimated idle spans (keep 1
# frame, drop N-1, repeat) — that shredded every slow scroll and cursor move into
# jerky jumps (the "drunk" motion). Instead: keep EVERY active frame and every
# short pause at full native smoothness, and for a LONG dead pause keep only a
# brief head then CUT the rest out entirely (one clean cut, never interleaved
# frame-dropping). Motion inside every kept span stays perfectly smooth.
MAX_DEAD = max(1, int(FPS * float(_os.environ.get('MAX_DEAD', '0.9'))))   # a pause longer than this is trimmed
HEAD = max(1, int(FPS * float(_os.environ.get('DEAD_HEAD', '0.45'))))     # ...down to just this brief hold
keep = np.ones(N, bool)
f = 0
while f < N:
    if act[f] < thr:
        j = f
        while j < N and act[j] < thr:
            j += 1
        if (j - f) > MAX_DEAD:
            keep[f + HEAD:j] = False       # drop only the TAIL of a long pause (a clean cut)
        f = j
    else:
        f += 1
kept = np.nonzero(keep)[0]
n_out = len(kept)
print(f'{N}->{n_out} frames ({N/FPS:.0f}s->{n_out/FPS:.0f}s), dead-time trimmed (no decimation)', flush=True)

ZOOM_ON = ZMAX > 1.001

# FIXED camera. A screen-recording walkthrough has many short scenes, each with
# activity in a different spot. Chasing them made the frame lurch to a new corner
# every ~2s — that was the "drunk" wander (and the zoom crop magnified it). So we
# do NOT pan at all: pick ONE framing, centred on the frame (nudged slightly
# toward where the action generally is), and hold it dead still for the whole
# clip. Perfectly smooth by construction — the only motion left is the real
# on-screen action itself.
if n_out:
    ak = act[kept]; m = ak > thr
    axm = float(np.median(cx[kept][m])) if m.any() else W / 2.0
    aym = float(np.median(cy[kept][m])) if m.any() else H / 2.0
else:
    axm, aym = W / 2.0, H / 2.0
fixedx = 0.75 * (W / 2.0) + 0.25 * axm            # mostly centred, a gentle nudge toward the action
fixedy = 0.75 * (H / 2.0) + 0.25 * aym
camx = np.full(n_out, fixedx)
camy = np.full(n_out, fixedy)

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
