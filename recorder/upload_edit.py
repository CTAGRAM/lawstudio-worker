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
IDLE_SPEED = 4          # dead frames kept 1-in-N
ZMAX = 1.28             # gentle magnification toward the action
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

# smooth centre; carry last centre through still frames
def ema(a, al=0.15):
    o = a.copy()
    for i in range(1, len(a)):
        o[i] = al * a[i] + (1 - al) * o[i - 1]
    return o
cx, cy = ema(cx), ema(cy)
thr = max(30.0, np.percentile(act[act > 0], 35) if (act > 0).any() else 30.0)   # "dead" below this

# decide kept frames (speed dead spans)
keep = np.zeros(N, bool); run = 0
for f in range(N):
    if act[f] >= thr:
        keep[f] = True; run = 0
    else:
        keep[f] = (run % IDLE_SPEED == 0); run += 1
n_out = int(keep.sum())
print(f'{N}->{n_out} frames ({N/FPS:.0f}s->{n_out/FPS:.0f}s), dead-time trimmed', flush=True)

# render kept frames with a gentle zoom toward the smoothed activity centre
cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
ff = subprocess.Popen(['ffmpeg', '-nostdin', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24',
    '-s', f'{W}x{H}', '-r', f'{FPS}', '-i', 'pipe:0', '-c:v', 'libx264', '-crf', '20',
    '-preset', 'veryfast', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', OUT], stdin=subprocess.PIPE)
z = ZMAX
cw, ch = W / z, H / z
for f in range(N):
    ok, fr = cap.read()
    if not ok:
        break
    if not keep[f]:
        continue
    x0 = min(max(cx[f] - cw / 2, 0), W - cw)
    y0 = min(max(cy[f] - ch / 2, 0), H - ch)
    crop = fr[int(y0):int(y0 + ch), int(x0):int(x0 + cw)]
    ff.stdin.write(cv2.resize(crop, (W, H), interpolation=cv2.INTER_LINEAR).tobytes())
ff.stdin.close(); ff.wait(); cap.release()
print(f'wrote {OUT}', flush=True)
