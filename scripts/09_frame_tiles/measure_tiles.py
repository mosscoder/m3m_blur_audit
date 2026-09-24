"""Where in the frame is a soft photo soft?  2026 spring and summer.

24 photos from every soft flight and from the sharp flight flown closest in time by the same drone on the same day.
Each full photo is cut into a 12 x 9 grid of tiles in sensor coordinates; per tile: fine detail (Laplacian share),
mean strong-edge width, contrast and dark level.  A smudge, a fingerprint or dust would soften one region; a focus
error softens the whole frame.  Read-only.

    <- data/flights.csv
    -> data/frame_tiles.csv.gz  one row per photo and tile
"""
import concurrent.futures as cf
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.common import DATA, bucket, download, edge_mean, visible_photos

YEAR, PER_FLIGHT, NX, NY = 2026, 24, 12, 9
b = bucket()
F = pd.read_csv(DATA / "flights.csv", parse_dates=["start_utc"])
F = F[F.year == YEAR]
F["day"] = F.start_utc.dt.date

soft = F[F.soft]
controls = []
for r in soft.itertuples():
    cand = F[~F.soft & (F.drone == r.drone) & (F.day == r.day)]
    if len(cand):
        controls.append(cand.iloc[(cand.start_utc - r.start_utc).abs().argmin()].prefix)
flights = [(p, "soft") for p in soft.prefix] + [(p, "sharp") for p in dict.fromkeys(controls)]
print(f"flights: {len(soft)} soft, {len(dict.fromkeys(controls))} sharp", flush=True)

jobs = []
for prefix, kind in flights:
    names = visible_photos(b, prefix)
    n = len(names)
    jobs += [(prefix, kind, names[i]) for i in np.linspace(int(n * .05), int(n * .95) - 1, PER_FLIGHT).round().astype(int)]


def one(job):
    prefix, kind, photo = job
    img = cv2.imdecode(np.frombuffer(download(b, photo), np.uint8), cv2.IMREAD_GRAYSCALE).astype(np.float32)
    H, W = img.shape
    L = cv2.Laplacian(img, cv2.CV_32F)
    ys, xs = np.linspace(0, H, NY + 1).astype(int), np.linspace(0, W, NX + 1).astype(int)
    rows = []
    for i in range(NY):
        for j in range(NX):
            T = img[ys[i]:ys[i + 1], xs[j]:xs[j + 1]]
            v = T.var() + 1e-6
            rows.append(dict(prefix=prefix, kind=kind, photo=photo, row=i, col=j, fine_detail=float(L[ys[i]:ys[i + 1], xs[j]:xs[j + 1]].var() / v),
                             edge_px=edge_mean(T, step=2), contrast_std=float(np.sqrt(v)), dark=float(np.percentile(T, 2))))
    return rows


t0 = time.time()
with cf.ThreadPoolExecutor(16) as ex:
    D = pd.DataFrame([r for rows in ex.map(one, jobs) for r in rows])
D.to_csv(DATA / "frame_tiles.csv.gz", index=False)
print(f"{D.photo.nunique()} photos in {time.time() - t0:.0f} s")
