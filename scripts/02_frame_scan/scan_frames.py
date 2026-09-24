"""Sharpness of 20 photos spread through every flight (>= 30 visible photos), plus the photo metadata that bears on
blur.  The photos sit evenly between 5 % and 95 % of the flight, which skips the climb-out and return.  Read-only.

Per photo, on the central 2048 px of the grayscale JPEG: strong-edge width (median and mean, px), fine detail
(Laplacian share), edge strength (Tenengrad share); EXIF aperture, ISO and exposure; DJI XMP height above takeoff,
speed, UTC time, FocusDistance and the drone and camera serials.

    <- data/inventory.csv
    -> data/frames.csv.gz  one row per sampled photo

Downloads about 7,800 full-size photos (~20 min on a fast link).  --prefix and --flights limit the run and --out
redirects it, for spot checks against the saved table.
"""
import argparse
import concurrent.futures as cf
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.common import DATA, FRAMES_PER_FLIGHT, bucket, flight_folders, dji_xmp, download, exif, focus_mm, sharpness, visible_photos

ap = argparse.ArgumentParser()
ap.add_argument("--prefix", action="append", help="only this flight folder (repeatable)")
ap.add_argument("--flights", type=int, help="only the first N flights")
ap.add_argument("--out", type=Path, default=DATA / "frames.csv.gz")
args = ap.parse_args()

b = bucket()
inv = pd.read_csv(DATA / "inventory.csv")
inv = flight_folders(inv)
if args.prefix:
    inv = inv[inv.prefix.isin(args.prefix)]
if args.flights:
    inv = inv.head(args.flights)


def sample(prefix):
    names = visible_photos(b, prefix)
    n = len(names)
    idx = np.linspace(int(n * .05), int(n * .95) - 1, FRAMES_PER_FLIGHT).round().astype(int)
    return [dict(prefix=prefix, photo=names[i], idx=int(i), photos=n) for i in idx]


with cf.ThreadPoolExecutor(16) as ex:
    jobs = [j for js in ex.map(sample, inv.prefix) for j in js]
print(f"{len(jobs)} photos from {len(inv)} flights", flush=True)


def one(j):
    raw = download(b, j["photo"])
    x, e = dji_xmp(raw), exif(raw)
    gray = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)
    return {**j, "width": gray.shape[1], "height": gray.shape[0], **sharpness(gray),
            "fnumber": float(e[33437]) if e.get(33437) else np.nan, "iso": e.get(34855), "exposure_s": float(e[33434]) if e.get(33434) else np.nan,
            "rel_alt_m": float(x.get("RelativeAltitude", "nan")), "vx": float(x.get("FlightXSpeed", "nan")), "vy": float(x.get("FlightYSpeed", "nan")),
            "utc": x.get("UTCAtExposure"), "focus_mm": focus_mm(x), "drone_serial": x.get("DroneSerialNumber"), "camera_serial": x.get("CameraSerialNumber")}


t0 = time.time()
with cf.ThreadPoolExecutor(16) as ex:
    R = pd.DataFrame(list(ex.map(one, jobs)))
R.to_csv(args.out, index=False)
print(f"measured {len(R)} photos in {time.time() - t0:.0f} s -> {args.out}")
