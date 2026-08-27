#!/usr/bin/env python3
"""Event-driven auto-zoom. Consumes a recording + its events.json (from
recorder.mjs) and produces a cinematically zoomed body: each click punches in
on the EXACT logged click coordinate, holds, and eases back out. No computer
vision — the coordinates are ground truth because we drove the browser.

Crop-and-scale re-samples the ORIGINAL frames (cubic), so zooms stay crisp.

    python3 autozoom_events.py <events.json> <in.mp4> <out.mp4> [--zmax 1.55]
"""
import sys, json, subprocess, math
import numpy as np, cv2

ev_path, src, out = sys.argv[1], sys.argv[2], sys.argv[3]
ZMAX = 1.55
if '--zmax' in sys.argv:
    ZMAX = float(sys.argv[sys.argv.index('--zmax') + 1])

meta = json.load(open(ev_path))
events = meta['events']
cap = cv2.VideoCapture(src)
FPS = cap.get(cv2.CAP_PROP_FPS) or 30
W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
N = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
# events x/y are in CSS/viewport px == recording px (1:1). Scale if recording
# was resized away from the logged viewport.
sx = W / meta.get('width', W)
sy = H / meta.get('height', H)

# anchors that deserve a zoom: clicks (strong) and field-focus 'type' beats
anchors = []
for e in events:
    if e['type'] == 'click' and 'x' in e:
        anchors.append((e['t'] / 1000.0, e['x'] * sx, e['y'] * sy, 1.0))
    elif e['type'] == 'type' and 'x' in e:
        anchors.append((e['t'] / 1000.0, e['x'] * sx, e['y'] * sy, 0.9))
print(f'{len(anchors)} zoom anchors over {N} frames ({N/FPS:.0f}s)', flush=True)

# raised-cosine envelope around each anchor: ease-in, hold, ease-out
PRE, HOLD, POST, LEAD = 0.36, 1.25, 0.55, 0.12


def env(dt):
    u = dt + LEAD
    if u < 0 or u > PRE + HOLD + POST:
        return 0.0
    if u < PRE:
        return 0.5 - 0.5 * math.cos(math.pi * u / PRE)
    if u < PRE + HOLD:
        return 1.0
    return 0.5 - 0.5 * math.cos(math.pi * (1 - (u - PRE - HOLD) / POST))


zt = np.zeros(N)          # zoom weight per frame [0,1]
cxt = np.full(N, W / 2.0)  # target center x
cyt = np.full(N, H / 2.0)
for f in range(N):
    t = f / FPS
    # Lock to the SINGLE strongest anchor rather than a weighted average of all
    # active anchors — averaging between overlapping clicks made the crop centre
    # drift around ("navigating the cursor"). Hold on one point, then move on.
    bw = 0.0; bx = by = None
    for (ta, ax, ay, strength) in anchors:
        w = env(t - ta) * strength
        if w > bw:
            bw = w; bx = ax; by = ay
    zt[f] = bw
    if bx is not None and bw > 0:
        cxt[f] = (1 - bw) * (W / 2.0) + bw * bx
        cyt[f] = (1 - bw) * (H / 2.0) + bw * by


# light smoothing so nothing kinks
def smooth(a, k=5):
    if len(a) < k:
        return a
    ker = np.ones(k) / k
    return np.convolve(a, ker, mode='same')


zt, cxt, cyt = smooth(zt), smooth(cxt), smooth(cyt)
zoom = 1.0 + (ZMAX - 1.0) * zt

# stream frames to ffmpeg (rawvideo) -> crisp h264, carrying original audio
ff = subprocess.Popen(
    ['ffmpeg', '-nostdin', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24',
     '-s', f'{W}x{H}', '-r', f'{FPS}', '-i', 'pipe:0',
     '-i', src, '-map', '0:v', '-map', '1:a?', '-c:v', 'libx264', '-crf', '19',
     '-preset', 'veryfast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest',
     '-movflags', '+faststart', out],
    stdin=subprocess.PIPE)

for f in range(N):
    ok, fr = cap.read()
    if not ok:
        break
    z = zoom[f]
    if z <= 1.001:
        outf = fr
    else:
        cw, ch = W / z, H / z
        x0 = min(max(cxt[f] - cw / 2, 0), W - cw)
        y0 = min(max(cyt[f] - ch / 2, 0), H - ch)
        crop = fr[int(y0):int(y0 + ch), int(x0):int(x0 + cw)]
        outf = cv2.resize(crop, (W, H), interpolation=cv2.INTER_LINEAR)
    ff.stdin.write(outf.tobytes())
ff.stdin.close()
ff.wait()
cap.release()
print(f'wrote {out}', flush=True)
