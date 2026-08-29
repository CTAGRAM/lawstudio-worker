#!/usr/bin/env python3
"""Find the magenta chroma-key rectangle (the window's video area) in a rendered
overlay frame. Prints: x y w h. Used by the polished hybrid composite."""
import sys
import numpy as np, cv2
im = cv2.imread(sys.argv[1])
b, g, r = im[..., 0].astype(int), im[..., 1].astype(int), im[..., 2].astype(int)
mag = (r > 200) & (b > 200) & (g < 80)
ys, xs = np.nonzero(mag)
if len(xs):
    print(int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1))
else:
    print(230, 99, 1460, 876)
