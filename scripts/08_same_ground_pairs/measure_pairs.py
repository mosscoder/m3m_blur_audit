"""Same ground, two flights: is a soft flight's softness in the photo, or in the scene?  2026 spring and summer.

For every soft flight and 12 random sharp flights (the controls), up to 6 photos spread through the flight, each
paired with the nearest photo (within 35 m, by MRK position) from a sharp flight of the same season.  The pair is
registered (ORB features, similarity transform by RANSAC) and the shared ground is cropped from each photo in its
own pixels, no resampling.  On the two crops: fine detail, edge width, the dark floor (1st / 50th percentile, which
a dust or haze veil raises) and the overall contrast.  Read-only.

    <- data/inventory.csv, data/flights.csv
    -> data/same_ground_pairs.csv  one row per pair
"""
import concurrent.futures as cf
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.common import BUCKET, DATA, bucket, download, edge_mean, register, shared_centres

YEAR, CONTROLS, PER_FLIGHT, MAX_DIST_M, SEED = 2026, 12, 6, 35, 5
b = bucket()
inv = pd.read_csv(DATA / "inventory.csv")
inv = inv[(inv.year == YEAR) & inv.duplicate_of.isna()]          # copies of other folders can't be partners
F = pd.read_csv(DATA / "flights.csv")
F = F[F.year == YEAR]                                          # ordered by season, folder label, start


def mrk(prefix):
    rows = []
    for bl in b.client.list_blobs(BUCKET, prefix=prefix, match_glob="**.MRK"):
        for line in bl.download_as_text().splitlines():
            p = line.split("\t")
            if len(p) < 9:
                continue
            try:
                rows.append(dict(prefix=prefix, photo=int(p[0]), lat=float(p[6].split(",")[0]), lon=float(p[7].split(",")[0])))
            except ValueError:
                pass
    return rows


with cf.ThreadPoolExecutor(16) as ex:                          # every folder, stubs included, can supply a partner
    P = pd.DataFrame([r for rows in ex.map(mrk, inv.prefix) for r in rows])
P = P.merge(inv[["prefix", "season"]], on="prefix").merge(F[["prefix", "soft"]], on="prefix", how="left")
P["soft"] = P.soft.eq(True)                                    # folders without a flight row (stubs) count as sharp
lat0 = P.lat.median()
P["x"] = (P.lon - P.lon.median()) * 111320 * np.cos(np.radians(lat0))
P["y"] = (P.lat - lat0) * 110540

controls = list(np.random.default_rng(SEED).choice(F[~F.soft].prefix.to_numpy(), CONTROLS, replace=False))
jobs = []
for kind, flights in (("soft", F[F.soft].prefix.tolist()), ("control", controls)):
    for pre in flights:
        mine = P[P.prefix == pre].sort_values("photo")
        others = P[(P.season == mine.season.iloc[0]) & (P.prefix != pre) & ~P.soft]
        ox, oy = others.x.to_numpy(), others.y.to_numpy()
        got = 0
        for i in np.linspace(int(len(mine) * .1), int(len(mine) * .9), 14).round().astype(int):
            m = mine.iloc[i]
            d = np.hypot(ox - m.x, oy - m.y)
            j = int(np.argmin(d))
            if d[j] > MAX_DIST_M:
                continue
            o = others.iloc[j]
            jobs.append(dict(kind=kind, prefix=pre, photo=int(m.photo), partner_prefix=o.prefix, partner_photo=int(o.photo), dist_m=float(d[j])))
            got += 1
            if got == PER_FLIGHT:
                break
J = pd.DataFrame(jobs)
print("pairs:", J.groupby("kind").size().to_dict(), "flights:", J.groupby("kind").prefix.nunique().to_dict(), flush=True)


def listing(prefix):
    """photo number -> blob name of the visible photo"""
    return prefix, {int(bl.name.rsplit("_", 2)[-2]): bl.name for bl in b.client.list_blobs(BUCKET, prefix=prefix, match_glob="**_D.JPG")
                    if bl.name.rsplit("_", 2)[-2].isdigit()}


with cf.ThreadPoolExecutor(16) as ex:
    names = dict(ex.map(listing, set(J.prefix) | set(J.partner_prefix)))


def measure(o):
    F_ = o.astype(np.float32)
    v = F_.var() + 1e-6
    p1, p50, p99 = np.percentile(F_, [1, 50, 99])
    return dict(fine=float(cv2.Laplacian(F_, cv2.CV_32F).var() / v), ew=edge_mean(F_), floor=float(p1 / p50), span=float((p99 - p1) / p50),
                cv=float(np.sqrt(v) / F_.mean()))


def one(j):
    try:
        A = cv2.imdecode(np.frombuffer(download(b, names[j["prefix"]][j["photo"]]), np.uint8), cv2.IMREAD_GRAYSCALE)
        B = cv2.imdecode(np.frombuffer(download(b, names[j["partner_prefix"]][j["partner_photo"]]), np.uint8), cv2.IMREAD_GRAYSCALE)
    except Exception as err:
        return {**j, "ok": False, "why": str(err)[:60]}
    reg = register(A, B)
    if isinstance(reg, str):
        return {**j, "ok": False, "why": reg}
    M, inl, sc = reg
    # a square around the midpoint of the two photo centres, cut from each photo in its own pixels
    ca, cb, half = shared_centres(M, A.shape)
    half = min(half, 998)
    if half < 400:
        return {**j, "ok": False, "why": "small overlap"}
    ma = measure(A[int(ca[1]) - half:int(ca[1]) + half, int(ca[0]) - half:int(ca[0]) + half])
    mb = measure(B[int(cb[1]) - half:int(cb[1]) + half, int(cb[0]) - half:int(cb[0]) + half])
    return {**j, "ok": True, "scale": sc, "inliers": inl, "fine_detail_ratio": ma["fine"] / mb["fine"], "edge_px": ma["ew"],
            "partner_edge_px": mb["ew"], "dark_floor": ma["floor"], "partner_dark_floor": mb["floor"], "contrast_span_ratio": ma["span"] / mb["span"],
            "contrast_cv_ratio": ma["cv"] / mb["cv"]}


t0 = time.time()
with cf.ThreadPoolExecutor(12) as ex:
    V = pd.DataFrame(list(ex.map(one, J.to_dict("records"))))
V.to_csv(DATA / "same_ground_pairs.csv", index=False)
print(f"registered {int(V.ok.sum())} of {len(V)} pairs in {time.time() - t0:.0f} s")
