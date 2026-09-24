"""Same ground, two drones: do the multispectral cameras agree, and does the reflectance?

Pairs of multispectral captures of the same ground from two different drones (and, as a control, from the same drone
on two different flights): the same survey, at most 20 m and 2 days apart, within an hour of each other in time of
day, both in clear sun by their own sunlight sensor, and both on the mapping legs (5-95 % of the flight).  Each band
is calibrated as DJI's Mavic 3M Image Processing Guide (2023) describes:

    signal       = (DN - BlackLevel) x V(r)                    V(r) = 1 + sum k_i r^(i+1), the vignetting correction
    camera       = signal / 2^16 / (SensorGain x ExposureTime)  what the multispectral camera measured, per unit exposure
    reflectance  = camera x SensorGainAdjustment / Irradiance   relative reflectance, divided by the sunlight sensor

The pair is registered on the green band (ORB features, similarity transform) and each quantity is the median over
the square of ground both captures share.  Read-only.

    <- data/inventory.csv, data/flights.csv
    -> data/ms_pairs.csv  one row per pair
"""
import concurrent.futures as cf
import datetime as dt
import re
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.common import BUCKET, DATA, DRONE_ORDER, bucket, download, flight_folders

BANDS = ("G", "R", "RE", "NIR")
CROSS, SAME, MAX_DIST_M, MAX_DAYS, MAX_CLOCK_MIN, SEED = 30, 15, 20, 2, 60, 9
GPS0 = dt.datetime(1980, 1, 6, tzinfo=dt.timezone.utc)
b = bucket()
F = pd.read_csv(DATA / "flights.csv")
inv = flight_folders(pd.read_csv(DATA / "inventory.csv"))
F = F[F.prefix.isin(inv.prefix)]


def positions(prefix):
    """Every capture on the flight's mapping legs: photo number, UTC time, position (MRK)."""
    rows = []
    for bl in b.client.list_blobs(BUCKET, prefix=prefix, match_glob="**.MRK"):
        for line in bl.download_as_text().splitlines():
            p = line.split("\t")
            try:
                week = int(re.search(r"\[(\d+)\]", p[2]).group(1))
                rows.append(dict(prefix=prefix, photo=int(p[0]), t=GPS0 + dt.timedelta(weeks=week, seconds=float(p[1]) - 18),
                                 lat=float(p[6].split(",")[0]), lon=float(p[7].split(",")[0])))
            except (IndexError, ValueError, AttributeError):
                pass
    P = pd.DataFrame(rows)
    return P.iloc[int(len(P) * .05):int(len(P) * .95)] if len(P) else P


with cf.ThreadPoolExecutor(16) as ex:
    P = pd.concat([p for p in ex.map(positions, F.prefix) if len(p)], ignore_index=True)
P = P.merge(F[["prefix", "drone", "year", "season"]], on="prefix")
P["t"] = pd.to_datetime(P.t, utc=True)
P["clock"] = P.t.dt.hour * 60 + P.t.dt.minute
lat0 = P.lat.median()
P["x"] = (P.lon - P.lon.median()) * 111320 * np.cos(np.radians(lat0))
P["y"] = (P.lat - lat0) * 110540
rng = np.random.default_rng(SEED)


def candidates(X, Y, n, kind):
    out, yx, yy = [], Y.x.to_numpy(), Y.y.to_numpy()
    for i in rng.permutation(len(X))[:6000]:
        r = X.iloc[i]
        d = np.hypot(yx - r.x, yy - r.y)
        ok = ((d <= MAX_DIST_M) & (Y.year.to_numpy() == r.year) & (Y.season.to_numpy() == r.season) & (Y.prefix.to_numpy() != r.prefix)
              & (np.abs((Y.t - r.t).dt.total_seconds().to_numpy()) <= MAX_DAYS * 86400) & (np.abs(Y.clock.to_numpy() - r.clock) <= MAX_CLOCK_MIN))
        if not ok.any():
            continue
        o = Y.iloc[np.flatnonzero(ok)[np.argmin(d[ok])]]
        out.append(dict(kind=kind, drone_1=r.drone, drone_2=o.drone, prefix_1=r.prefix, photo_1=int(r.photo), prefix_2=o.prefix, photo_2=int(o.photo),
                        year=r.year, season=r.season, dist_m=float(d[ok].min()), hours_apart=abs((o.t - r.t).total_seconds()) / 3600))
        if len(out) >= 3 * n:
            break
    return out


C = []
for d1, d2 in (("red", "blue"), ("red", "green"), ("blue", "green")):
    C += candidates(P[P.drone == d1], P[P.drone == d2], CROSS, "cross")
for d in DRONE_ORDER:
    C += candidates(P[P.drone == d], P[P.drone == d], SAME, "same drone")
C = pd.DataFrame(C)


def stems(prefix):
    """photo number -> blob stem (…/DJI_<time>_<nnnn>) of the multispectral capture"""
    return prefix, {int(bl.name.rsplit("_", 3)[-3]): bl.name.rsplit("_", 2)[0]
                    for bl in b.client.list_blobs(BUCKET, prefix=prefix, match_glob="**_MS_G.TIF")}


with cf.ThreadPoolExecutor(16) as ex:
    names = dict(ex.map(stems, set(C.prefix_1) | set(C.prefix_2)))
C["stem_1"] = [names[p].get(n) for p, n in zip(C.prefix_1, C.photo_1)]
C["stem_2"] = [names[p].get(n) for p, n in zip(C.prefix_2, C.photo_2)]
C = C.dropna(subset=["stem_1", "stem_2"])


def xmp(raw):
    return dict(re.findall(r'drone-dji:(\w+)="([^"]*)"', raw[:65536].decode("latin-1")))


def irradiance(stem):
    return float(xmp(download(b, stem + "_MS_G.TIF", start=0, end=16383))["Irradiance"])


with cf.ThreadPoolExecutor(16) as ex:
    C["irradiance_1"] = list(ex.map(irradiance, C.stem_1))
    C["irradiance_2"] = list(ex.map(irradiance, C.stem_2))
# clear sun: each capture's sunlight reading above the 40th percentile of its drone's candidates in that survey
for i in ("1", "2"):
    thr = C.groupby([f"drone_{i}", "year", "season"])[f"irradiance_{i}"].transform(lambda s: s.quantile(.4))
    C = C[C[f"irradiance_{i}"] >= thr]
C = pd.concat([g.head(CROSS if k == "cross" else SAME) for (k, _, _), g in C.groupby(["kind", "drone_1", "drone_2"])])
print("pairs:", C.groupby(["kind", "drone_1", "drone_2"]).size().to_dict(), flush=True)


def calibrate(stem, band):
    raw = download(b, f"{stem}_MS_{band}.TIF")
    d = xmp(raw)
    dn = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED).astype(np.float32)
    signal = np.maximum(dn - float(d["BlackLevel"]), 0)
    k = [float(v) for v in d["VignettingData"].split(",")]
    yy, xx = np.mgrid[0:dn.shape[0], 0:dn.shape[1]]
    r = np.hypot(xx - float(d["CalibratedOpticalCenterX"]), yy - float(d["CalibratedOpticalCenterY"]))
    signal *= (1 + sum(ki * r ** (i + 1) for i, ki in enumerate(k))).astype(np.float32)
    camera = signal / 2 ** 16 / (float(d["SensorGain"]) * float(d["ExposureTime"]) / 1e6)
    return dict(dn=dn - float(d["BlackLevel"]), camera=camera, reflectance=camera * float(d["SensorGainAdjustment"]) / float(d["Irradiance"]))


def to8(x):
    lo, hi = np.percentile(x, [1, 99])
    return np.clip((x - lo) / (hi - lo + 1e-9) * 255, 0, 255).astype(np.uint8)


def shared_square(A, B):
    """Registration of two green-band captures; the square both share, in each capture's pixels."""
    orb = cv2.ORB_create(4000)
    ka, da = orb.detectAndCompute(to8(A), None)
    kb, db = orb.detectAndCompute(to8(B), None)
    if da is None or db is None:
        return None
    m = sorted(cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(da, db), key=lambda z: z.distance)[:600]
    M, inl = cv2.estimateAffinePartial2D(np.float32([ka[z.queryIdx].pt for z in m]), np.float32([kb[z.trainIdx].pt for z in m]),
                                         method=cv2.RANSAC, ransacReprojThreshold=3.0)
    if M is None or inl.sum() < 30 or abs(np.hypot(M[0, 0], M[1, 0]) - 1) > 0.1:
        return None
    H, W = A.shape
    ca = (np.array([W / 2, H / 2]) + cv2.invertAffineTransform(M) @ np.array([W / 2, H / 2, 1.0])) / 2
    cb = M @ np.array([ca[0], ca[1], 1.0])
    h = int(min(450, ca[0], ca[1], W - ca[0], H - ca[1], cb[0], cb[1], W - cb[0], H - cb[1])) - 2
    if h < 150:
        return None
    return (slice(int(ca[1]) - h, int(ca[1]) + h), slice(int(ca[0]) - h, int(ca[0]) + h)), (slice(int(cb[1]) - h, int(cb[1]) + h), slice(int(cb[0]) - h, int(cb[0]) + h))


def one(r):
    out = {k: r[k] for k in ("kind", "drone_1", "drone_2", "prefix_1", "photo_1", "prefix_2", "photo_2", "year", "season", "dist_m", "hours_apart",
                             "irradiance_1", "irradiance_2")}
    try:
        c1, c2 = {bd: calibrate(r["stem_1"], bd) for bd in BANDS}, {bd: calibrate(r["stem_2"], bd) for bd in BANDS}
    except Exception as err:
        return {**out, "ok": False, "why": str(err)[:60]}
    sq = shared_square(c1["G"]["reflectance"], c2["G"]["reflectance"])
    if sq is None:
        return {**out, "ok": False, "why": "no registration"}
    for bd in BANDS:
        for q in ("dn", "camera", "reflectance"):
            out[f"{bd}_{q}_1"] = float(np.median(c1[bd][q][sq[0]]))
            out[f"{bd}_{q}_2"] = float(np.median(c2[bd][q][sq[1]]))
    return {**out, "ok": True}


t0 = time.time()
with cf.ThreadPoolExecutor(8) as ex:
    Q = pd.DataFrame(list(ex.map(one, C.to_dict("records"))))
Q.to_csv(DATA / "ms_pairs.csv", index=False)
print(f"registered {int(Q.ok.sum())} of {len(Q)} pairs in {time.time() - t0:.0f} s")
