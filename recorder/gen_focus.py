#!/usr/bin/env python3
"""Produce a Screen-Studio-style zoom/focus track for a screen recording, as data
(not baked pixels) — consumed by the Remotion GoLegalDemo to zoom the recording
INSIDE its window. Zoom IN on sustained activity, HOLD, ease back to the full
window; re-aim the focus only while zoomed out, so it never wanders ("drunk").

    python3 gen_focus.py <in.mp4> <focus.json> [--zmax 1.5]
Output: JSON list, one entry per frame: [z, fx, fy]  (fx,fy fractional focus 0..1)
"""
import sys, json
import numpy as np, cv2

SRC, OUT = sys.argv[1], sys.argv[2]
ZMAX = float(sys.argv[sys.argv.index('--zmax') + 1]) if '--zmax' in sys.argv else 1.5

cap = cv2.VideoCapture(SRC)
FPS = cap.get(cv2.CAP_PROP_FPS) or 30
W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
N = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
sw, sh = 320, int(320 * H / W)
prev = None
act = np.zeros(N); cx = np.full(N, W / 2.0); cy = np.full(N, H / 2.0)
for f in range(N):
    ok, fr = cap.read()
    if not ok:
        N = f; break
    g = cv2.cvtColor(cv2.resize(fr, (sw, sh)), cv2.COLOR_BGR2GRAY)
    if prev is not None:
        d = cv2.absdiff(g, prev); m = (d > 22).astype(np.float32); s = m.sum(); act[f] = s
        if s > 12:
            ys, xs = np.nonzero(m); cx[f] = xs.mean() / sw * W; cy[f] = ys.mean() / sh * H
        else:
            cx[f] = cx[f - 1]; cy[f] = cy[f - 1]
    prev = g
cap.release()
act = act[:N]; cx = cx[:N]; cy = cy[:N]

# DISCRETE zoom events (Screen-Studio style): find sustained activity bursts,
# punch in on each (ease-in, hold, ease-out), and sit at the full window in
# between. Never a continuous zoom — that just reads as "always zoomed".
sm = np.convolve(act, np.ones(13) / 13, mode='same')        # smooth activity
T = np.percentile(sm[sm > 0], 86) if (sm > 0).any() else 1e9  # only the busiest moments
busy = sm > T
# group busy frames into events, merge gaps < 1.0s, drop events < 0.9s
events = []; i = 0
gap = int(1.0 * FPS); mindur = int(0.9 * FPS)
while i < N:
    if busy[i]:
        j = i
        while j < N and (busy[j] or (j + gap < N and busy[j:j + gap].any())):
            j += 1
        if j - i >= mindur:
            events.append((i, min(j, N)))
        i = j
    else:
        i += 1

z = np.ones(N); fx = np.full(N, 0.5); fy = np.full(N, 0.5)
EIN = int(0.85 * FPS); EOUT = int(0.95 * FPS); MAXHOLD = int(5.5 * FPS)   # slow, calm eases + long holds
for (s, e) in events:
    e = min(e, s + MAXHOLD)
    # focus = median activity centre during the event
    seg = np.arange(s, e)
    active = seg[act[seg] > 12]
    cxs = cx[active] if len(active) else cx[seg]
    cys = cy[active] if len(active) else cy[seg]
    ex = float(np.median(cxs) / W); ey = float(np.median(cys) / H)
    ex = min(max(ex, 0.16), 0.84); ey = min(max(ey, 0.16), 0.84)
    for f in range(max(0, s - EIN), min(N, e + EOUT)):
        if f < s:
            w = (f - (s - EIN)) / max(EIN, 1)               # ease in
        elif f < e:
            w = 1.0                                          # hold
        else:
            w = 1.0 - (f - e) / max(EOUT, 1)                # ease out
        w = 0.5 - 0.5 * np.cos(np.pi * np.clip(w, 0, 1))     # cosine smooth
        nz = 1.0 + (ZMAX - 1.0) * w
        if nz > z[f]:
            z[f] = nz; fx[f] = ex; fy[f] = ey

track = [[round(float(z[i]), 4), round(float(fx[i]), 4), round(float(fy[i]), 4)] for i in range(N)]
payload = json.dumps(track)
zoomed = float((z > 1.06).mean()) * 100
sys.stderr.write(f'{N} frames, zoomed {zoomed:.0f}%, zmax {z.max():.2f}, events {len(events)}\n')
if OUT == '-':
    sys.stdout.write(payload)
else:
    import os as _o
    fd = _o.open(OUT, _o.O_WRONLY | _o.O_CREAT | _o.O_TRUNC, 0o644)
    _o.write(fd, payload.encode()); _o.close(fd)
