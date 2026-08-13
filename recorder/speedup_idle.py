#!/usr/bin/env python3
"""Phase-3: cut dead time. Keeps the moments around real interactions at full
speed and time-compresses idle spans (page loads, pauses, nothing moving).
Uses the event log, then remaps event timestamps onto the new timeline so the
downstream auto-zoom still lands on the right frames.

    python3 speedup_idle.py <events.json> <in.mp4> <out.mp4> <out_events.json>
"""
import sys, json, subprocess
import numpy as np, cv2

ev_path, src, out, out_ev = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
IDLE_SPEED = 3          # idle spans play this many times faster
KEEP_BEFORE, KEEP_AFTER = 0.5, 1.3   # seconds around each event kept at 1x

meta = json.load(open(ev_path))
events = meta['events']
cap = cv2.VideoCapture(src)
FPS = cap.get(cv2.CAP_PROP_FPS) or 30
W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
N = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

# frames that are "active" (near an interaction) stay at 1x
active = np.zeros(N, bool)
for e in events:
    if e['type'] in ('click', 'move', 'scroll', 'type', 'nav'):
        t = e['t'] / 1000.0
        a = max(0, int((t - KEEP_BEFORE) * FPS)); b = min(N, int((t + KEEP_AFTER) * FPS))
        active[a:b] = True

# decide which input frames to keep; build old-frame -> new-frame map
keep = np.zeros(N, bool)
idle_run = 0
for f in range(N):
    if active[f]:
        keep[f] = True; idle_run = 0
    else:
        keep[f] = (idle_run % IDLE_SPEED == 0)   # keep 1 of every IDLE_SPEED idle frames
        idle_run += 1
new_index = np.cumsum(keep) - 1        # for each old frame, its new frame idx (if kept)
n_out = int(keep.sum())
print(f'{N} -> {n_out} frames ({N/FPS:.1f}s -> {n_out/FPS:.1f}s), '
      f'{100*(1-n_out/N):.0f}% shorter', flush=True)

# remap events to the new timeline
def remap_t(t_ms):
    f = min(N - 1, max(0, int(t_ms / 1000.0 * FPS)))
    nf = new_index[f] if keep[f] else new_index[max(0, np.where(keep[:f+1])[0][-1])] if keep[:f+1].any() else 0
    return round(nf / FPS * 1000)
new_events = []
for e in events:
    e2 = dict(e)
    if 't' in e2:
        e2['t'] = remap_t(e2['t'])
    new_events.append(e2)
meta2 = dict(meta); meta2['events'] = new_events; meta2['durationSec'] = n_out / FPS
json.dump(meta2, open(out_ev, 'w'), indent=2)

# stream kept frames to ffmpeg at the SAME fps -> idle spans are now faster
ff = subprocess.Popen(
    ['ffmpeg', '-nostdin', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{W}x{H}',
     '-r', f'{FPS}', '-i', 'pipe:0', '-c:v', 'libx264', '-crf', '19', '-preset', 'medium',
     '-pix_fmt', 'yuv420p', '-movflags', '+faststart', out], stdin=subprocess.PIPE)
for f in range(N):
    ok, fr = cap.read()
    if not ok:
        break
    if keep[f]:
        ff.stdin.write(fr.tobytes())
ff.stdin.close(); ff.wait(); cap.release()
print(f'wrote {out}', flush=True)
