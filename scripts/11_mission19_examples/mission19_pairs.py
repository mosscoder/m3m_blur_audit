"""Four examples of the same ground from a good and a bad flight: mission 19, summer 2026, blue drone.  Flight 001
(focus at infinity) is sharp; flight 002, flown next after a battery swap with the focus at 5.1 m, is soft.  Where
their coverage meets, both photographed the same ground.

Photos of flight 002 are paired with the nearest photo of flight 001 (by MRK position, within 60 m) and registered
in two steps: a similarity transform for the whole photo (ORB features), then a local search that places the square
on the same ground, which terrain relief shifts between the two viewpoints (normalized cross-correlation, both sides
blurred alike; pairs below 0.6 are dropped).  A 400 px square is cut from each photo in its own pixels, in colour;
the bad flight's is turned by a multiple of 90 degrees to match the good one, and nothing is resampled.  The four
best-matching pairs at least 80 m apart make the four rows.  Read-only.

    <- data/inventory.csv
    -> figures/mission19_same_ground.png
"""
import concurrent.futures as cf
import sys
from pathlib import Path

import cv2
import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.common import BUCKET, DATA, FIGURES, bucket, download, edge_mean, register, shared_centres

GOOD, BAD = "DJI_202606221018_001_19", "DJI_202606221045_002_19"
CROP, MAX_DIST_M, CANDIDATES, SPACING_M, ROWS, SEARCH, MIN_NCC = 400, 60, 24, 80, 4, 350, 0.6
b = bucket()
inv = pd.read_csv(DATA / "inventory.csv")
inv = inv[(inv.year == 2026) & (inv.season == "summer") & inv.folder.isin([GOOD, BAD])].set_index("folder")


def positions(folder):
    rows = []
    for bl in b.client.list_blobs(BUCKET, prefix=inv.loc[folder, "prefix"], match_glob="**.MRK"):
        for line in bl.download_as_text().splitlines():
            p = line.split("\t")
            if len(p) >= 9:
                rows.append(dict(photo=int(p[0]), lat=float(p[6].split(",")[0]), lon=float(p[7].split(",")[0])))
    P = pd.DataFrame(rows)
    names = {int(bl.name.rsplit("_", 2)[-2]): bl.name for bl in b.client.list_blobs(BUCKET, prefix=inv.loc[folder, "prefix"], match_glob="**_D.JPG")}
    return P.assign(name=P.photo.map(names)).dropna(subset=["name"])


G, B = positions(GOOD), positions(BAD)
lat0 = pd.concat([G.lat, B.lat]).median()
for P in (G, B):
    P["x"] = P.lon * 111320 * np.cos(np.radians(lat0))
    P["y"] = P.lat * 110540
d = np.hypot(B.x.to_numpy()[:, None] - G.x.to_numpy()[None], B.y.to_numpy()[:, None] - G.y.to_numpy()[None])
B["partner"], B["dist"] = G.name.to_numpy()[d.argmin(1)], d.min(1)
border = B[B.dist <= MAX_DIST_M].sort_values("dist")
cands = []                                                     # spread the candidates along the border
for r in border.itertuples():
    if all(np.hypot(r.x - c.x, r.y - c.y) >= SPACING_M / 2 for c in cands):
        cands.append(r)
cands = cands[:CANDIDATES]
print(f"{len(border)} photos of flight 002 within {MAX_DIST_M} m of flight 001; registering {len(cands)} candidates", flush=True)


def one(r):
    good = cv2.imdecode(np.frombuffer(download(b, r.partner), np.uint8), cv2.IMREAD_COLOR)[..., ::-1]
    bad = cv2.imdecode(np.frombuffer(download(b, r.name), np.uint8), cv2.IMREAD_COLOR)[..., ::-1]
    reg = register(cv2.cvtColor(good, cv2.COLOR_RGB2GRAY), cv2.cvtColor(bad, cv2.COLOR_RGB2GRAY))
    if isinstance(reg, str):
        return None
    M, inl, _ = reg
    ca, cb, half = shared_centres(M, good.shape[:2])
    h = CROP // 2
    if half < h + SEARCH:
        return None
    g = good[int(ca[1]) - h:int(ca[1]) + h, int(ca[0]) - h:int(ca[0]) + h]
    # M turns the good photo's pixels by theta onto the bad one's; np.rot90(k=1) turns a y-down image by -90 degrees,
    # so k quarter-turns, k = theta / 90, bring the bad photo back to the good one's orientation
    theta = np.degrees(np.arctan2(M[1, 0], M[0, 0]))
    k = int(np.round(theta / 90)) % 4
    w = h + SEARCH
    win = np.ascontiguousarray(np.rot90(bad[int(cb[1]) - w:int(cb[1]) + w, int(cb[0]) - w:int(cb[0]) + w], k))
    # the whole-photo fit misplaces ground that terrain relief shifts between the two viewpoints; find the square
    # locally, both sides blurred alike so the sharp and soft photos compare fairly
    blur = lambda c: cv2.GaussianBlur(gray(c).astype(np.float32), (0, 0), 4)
    _, ncc, _, (x0, y0) = cv2.minMaxLoc(cv2.matchTemplate(blur(win), blur(g), cv2.TM_CCOEFF_NORMED))
    if ncc < MIN_NCC:
        return None
    s = np.ascontiguousarray(win[y0:y0 + CROP, x0:x0 + CROP])
    return dict(x=r.x, y=r.y, inliers=inl, ncc=ncc, good=g, bad=s, good_photo=r.partner.rsplit("/", 1)[-1], bad_photo=r.name.rsplit("/", 1)[-1],
                good_edge=edge(g), bad_edge=edge(s), residual_deg=(theta - 90 * np.round(theta / 90)))


def gray(c):
    return cv2.cvtColor(np.ascontiguousarray(c), cv2.COLOR_RGB2GRAY)


def edge(c):
    return edge_mean(gray(c).astype(np.float32), step=1)


with cf.ThreadPoolExecutor(8) as ex:
    res = sorted([r for r in ex.map(one, cands) if r], key=lambda r: -r["ncc"])
pick = []
for r in res:
    if all(np.hypot(r["x"] - p["x"], r["y"] - p["y"]) >= SPACING_M for p in pick):
        pick.append(r)
pick = pick[:ROWS]

SURF, INK = "#fcfcfb", "#1d1d1b"
fig, axes = plt.subplots(len(pick), 2, figsize=(8.2, 4.1 * len(pick) + 0.4), dpi=200)
fig.patch.set_facecolor(SURF)
for i, r in enumerate(pick):
    for j, key in enumerate(("good", "bad")):
        ax = axes[i, j]
        ax.imshow(r[key], interpolation="nearest")
        ax.set_xticks([]); ax.set_yticks([])
        for s in ax.spines.values():
            s.set_visible(False)
axes[0, 0].set_title(f"good flight {GOOD[-6:-3]}", color=INK, fontsize=11)
axes[0, 1].set_title(f"bad flight {BAD[-6:-3]}", color=INK, fontsize=11)
fig.tight_layout()
fig.savefig(FIGURES / "mission19_same_ground.png", facecolor=SURF)
for i, r in enumerate(pick, 1):
    print(f"row {i}: {r['good_photo']} vs {r['bad_photo']}  match {r['ncc']:.2f}  left-over turn {r['residual_deg']:+.1f} deg  edge width {r['good_edge']:.2f} vs {r['bad_edge']:.2f} px")
