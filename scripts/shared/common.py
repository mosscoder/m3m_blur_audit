"""Shared pieces of the M3M blur audit: paths, the read-only survey bucket, drone identities, photo metadata, and the
sharpness measures.  Every script imports this module; nothing here writes to the bucket."""
import re
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
FIGURES = ROOT / "figures"

BUCKET = "mpg-aerial-survey"                                   # public; read anonymously
SURVEYS = {2025: ("spring", "summer", "fall"), 2026: ("spring", "summer")}   # front country, in time order
MIN_PHOTOS = 30                                                # folders with fewer visible photos are resume stubs or tests
UTC_TO_LOCAL = pd.Timedelta(hours=-6)                          # Montana daylight time; every flight falls inside DST

# The drone is identified by the serial in each photo's XMP, not by the folder label: on 2025-10-06, eight flights filed
# under M3M-red were flown by the blue drone (README, notes for tracking down the mismatches).
DRONES = {"1581F5FK324BE00CS1SX": "blue", "1581F5FK324B100CS0ZN": "green", "1581F5FKC249B00DE0MJ": "red"}
DRONE_ORDER = ["blue", "green", "red"]
COLORS = {"blue": "#0072B2", "green": "#009E73", "red": "#D55E00"}   # Okabe-Ito; validated for colour-vision deficiency

# A flight is soft when at least 30 % of its 20 sampled photos have a median strong-edge width of 4 px or more.
SOFT_EDGE_PX = 4
SOFT_SHARE = 0.30
FRAMES_PER_FLIGHT = 20
CENTRAL_PX = 2048                                              # measure the central 2048 x 2048 px of each photo


def flight_folders(inv):
    """The inventory rows that count as flights: >= 30 visible photos, not a copy of another folder."""
    return inv[(inv.photos >= MIN_PHOTOS) & inv.duplicate_of.isna()]


def bucket():
    """The survey bucket through an anonymous client (the bucket is public; no credentials involved)."""
    from google.cloud import storage
    return storage.Client.create_anonymous_client().bucket(BUCKET)


def subdirs(bkt, prefix):
    """Immediate sub-folders of a bucket prefix, sorted."""
    it = bkt.client.list_blobs(BUCKET, prefix=prefix, delimiter="/")
    list(it)
    return sorted(it.prefixes)


def visible_photos(bkt, prefix):
    """Blob names of a flight folder's visible (RGB) photos, sorted by name, which is capture order."""
    return sorted(b.name for b in bkt.client.list_blobs(BUCKET, prefix=prefix, match_glob="**_D.JPG"))


_XMP = re.compile(rb'drone-dji:(\w+)="([^"]*)"')


def dji_xmp(raw):
    """The drone-dji XMP fields of a DJI photo (they sit in the first ~200 kB)."""
    return {k.decode(): v.decode() for k, v in _XMP.findall(raw[:300000])}


def exif(raw):
    """EXIF sub-IFD of a JPEG: aperture (33437), exposure time (33434), ISO (34855)."""
    import io
    from PIL import Image
    return Image.open(io.BytesIO(raw)).getexif().get_ifd(0x8769)


def firmware(raw):
    """Camera firmware version from the JPEG's EXIF Software tag (305)."""
    import io
    from PIL import Image
    return Image.open(io.BytesIO(raw)).getexif().get(305)


def focus_mm(xmp):
    """The DJI FocusDistance in mm: -1 means infinity; a finite focus is recorded as 46092 / n mm (46 m down to 3.3 m)."""
    v = xmp.get("FocusDistance")
    return float(v) if v not in (None, "") else np.nan


def edge_widths(F, step=4):
    """Width in px of every strong edge: along rows and columns (every `step`-th line), the length of the monotonic
    run through each local gradient maximum in the top 2 % of gradients.  Blur widens edges."""
    ws = []
    for A in (F, F.T):
        A = A[::step]
        dd = np.diff(A, axis=1)
        s = np.sign(dd)
        s[s == 0] = 1
        brk = np.ones_like(s, bool)
        brk[:, 1:] = s[:, 1:] != s[:, :-1]
        rid = np.cumsum(brk.ravel()).reshape(s.shape)
        runlen = np.bincount(rid.ravel())[rid]
        g = np.abs(dd)
        pk = g > np.percentile(g, 98)
        pk[:, 1:-1] &= (g[:, 1:-1] >= g[:, :-2]) & (g[:, 1:-1] >= g[:, 2:])
        ws.append(runlen[pk])
    return np.concatenate(ws)


def edge_mean(F, step=4):
    """Mean strong-edge width in px, each edge clipped to 1-10 px."""
    w = edge_widths(F, step)
    return float(np.clip(w, 1, 10).mean()) if len(w) else np.nan


def central(img, n=CENTRAL_PX):
    H, W = img.shape
    return img[H // 2 - n // 2:H // 2 + n // 2, W // 2 - n // 2:W // 2 + n // 2].astype(np.float32)


def sharpness(gray):
    """Sharpness of the central 2048 px of a grayscale photo.
    edge_median_px / edge_mean_px: strong-edge width (the audit's softness measure);
    fine_detail: Laplacian variance / image variance; edge_strength: Tenengrad (mean squared Sobel) / image variance."""
    F = central(gray)
    v = F.var() + 1e-6
    gx = cv2.Sobel(F, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(F, cv2.CV_32F, 0, 1, ksize=3)
    w = edge_widths(F)
    return dict(edge_median_px=float(np.median(w)), edge_mean_px=float(np.clip(w, 1, 10).mean()),
                fine_detail=float(cv2.Laplacian(F, cv2.CV_32F).var() / v), edge_strength=float((gx * gx + gy * gy).mean() / v),
                contrast_std=float(np.sqrt(v)))


def register(A, B):
    """Similarity transform M mapping photo A's pixels onto photo B's (both grayscale, full size): ORB features on
    quarter-size copies, RANSAC.  Returns (M, inliers, scale), or a string saying why it failed."""
    a = cv2.resize(A, None, fx=.25, fy=.25, interpolation=cv2.INTER_AREA)
    b = cv2.resize(B, None, fx=.25, fy=.25, interpolation=cv2.INTER_AREA)
    orb = cv2.ORB_create(5000)
    ka, da = orb.detectAndCompute(a, None)
    kb, db = orb.detectAndCompute(b, None)
    if da is None or db is None:
        return "no features"
    mm = sorted(cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(da, db), key=lambda x: x.distance)[:800]
    pa = np.float32([ka[x.queryIdx].pt for x in mm]) * 4
    pb = np.float32([kb[x.trainIdx].pt for x in mm]) * 4
    M, inl = cv2.estimateAffinePartial2D(pa, pb, method=cv2.RANSAC, ransacReprojThreshold=8.0)
    if M is None or inl.sum() < 40:
        return "no registration"
    sc = float(np.hypot(M[0, 0], M[1, 0]))
    if abs(sc - 1) > 0.15:
        return f"scale {sc:.2f}"
    return M, int(inl.sum()), sc


def shared_centres(M, shape):
    """Centre of the ground two registered photos share: the midpoint of the two photo centres, in A's and in B's
    pixels, and the largest half-width a square around it can have inside both photos."""
    H, W = shape
    ca = (np.array([W / 2, H / 2]) + cv2.invertAffineTransform(M) @ np.array([W / 2, H / 2, 1.0])) / 2
    cb = M @ np.array([ca[0], ca[1], 1.0])
    half = int(min(ca[0], ca[1], W - ca[0], H - ca[1], cb[0], cb[1], W - cb[0], H - cb[1])) - 2
    return ca, cb, half


def download(bkt, name, tries=3, **kw):
    """Blob bytes, retrying transient failures; a missing object raises NotFound at once."""
    import time
    from google.api_core.exceptions import NotFound
    for i in range(tries):
        try:
            return bkt.blob(name).download_as_bytes(**kw)
        except NotFound:
            raise
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2)


def logit(df, y, num, cats):
    """Logistic regression by Newton-Raphson: y ~ num + cats (treatment-coded).  Returns {term: (coef, se, p)}, n."""
    from scipy.stats import norm
    d = df.dropna(subset=num).copy()
    X, names = [np.ones(len(d))], ["const"]
    for k in num:
        X.append(d[k].to_numpy(float)); names.append(k)
    for c in cats:
        for lv in sorted(d[c].unique())[1:]:
            X.append((d[c] == lv).to_numpy(float)); names.append(f"{c}={lv}")
    X = np.column_stack(X)
    t = d[y].to_numpy(float)
    w = np.zeros(X.shape[1])
    for _ in range(50):
        p = 1 / (1 + np.exp(-X @ w))
        H = X.T @ (X * (p * (1 - p))[:, None]) + 1e-8 * np.eye(len(w))
        step = np.linalg.solve(H, X.T @ (t - p))
        w += step
        if np.abs(step).max() < 1e-8:
            break
    se = np.sqrt(np.diag(np.linalg.inv(H)))
    return {n: (w[i], se[i], 2 * (1 - norm.cdf(abs(w[i] / se[i])))) for i, n in enumerate(names)}, len(t)


def odds(m, term):
    """'odds ratio (95 % CI), p' for one term of a logit() fit."""
    b, se, p = m[term]
    return f"{np.exp(b):.2f} (95 % CI {np.exp(b - 1.96 * se):.2f}-{np.exp(b + 1.96 * se):.2f}), p {p:.3f}"


def load_flights():
    return pd.read_csv(DATA / "flights.csv", parse_dates=["start_utc"])
