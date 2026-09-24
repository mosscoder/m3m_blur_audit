"""The sunlight (irradiance) sensor on top of each drone, compared at the same moment: when two drones fly within
90 s of each other in clear conditions, their sensors should read the same light.  Per multispectral band, the
median ratio of the red drone's reading to the other drone's.  Reflectance is the camera signal divided by this
reading, so a sensor that reads 20 % high makes that drone's reflectance come out about 20 % low.

Photos are the audit's sampled frames; the readings come from the XMP header of each capture's four multispectral
TIFFs (G, R, RE, NIR; first 16 kB only).  Read-only.

    <- data/frames.csv.gz
"""
import concurrent.futures as cf
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from google.api_core.exceptions import NotFound

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.common import DATA, DRONES, bucket, download

BANDS = ("G", "R", "RE", "NIR")
b = bucket()
R = pd.read_csv(DATA / "frames.csv.gz")
R["drone"] = R.drone_serial.map(DRONES)
R["t"] = pd.to_datetime(R.utc, utc=True)
R["year"] = R.prefix.str.extract(r"surveys/(\d{4})_", expand=False).astype(int)
R = R.dropna(subset=["t"]).sort_values("t")


def irradiance(photo, band):
    try:
        h = download(b, photo.replace("_D.JPG", f"_MS_{band}.TIF"), start=0, end=16383).decode("latin-1")
    except NotFound:                                             # a few captures have no multispectral files
        return np.nan
    m = re.search(r'drone-dji:Irradiance="([^"]*)"', h)
    return float(m.group(1)) if m else np.nan


pairs = []
for other in ("blue", "green"):
    m = pd.merge_asof(R[R.drone == "red"], R[R.drone == other], on="t", direction="nearest", tolerance=pd.Timedelta("90s"),
                      suffixes=("_red", "_other")).dropna(subset=["photo_other"])
    pairs.append(m.assign(other=other))
M = pd.concat(pairs, ignore_index=True)
jobs = [(p, bd) for p in set(M.photo_red) | set(M.photo_other) for bd in BANDS]
with cf.ThreadPoolExecutor(32) as ex:
    irr = dict(zip(jobs, ex.map(lambda j: irradiance(*j), jobs)))
for bd in BANDS:
    M[f"{bd}_red"] = [irr[(p, bd)] for p in M.photo_red]
    M[f"{bd}_other"] = [irr[(p, bd)] for p in M.photo_other]
# clear moments: both sensors above their own 40th percentile (no cloud over one of them)
M = M[M.groupby("other").G_red.transform(lambda s: s > s.quantile(.4)) & M.groupby("other").G_other.transform(lambda s: s > s.quantile(.4))]

print("red drone's sunlight reading / the other drone's, same clear moment (median ratio, interquartile range)")
groups = [((o, "both years"), g) for o, g in M.groupby("other")] + [((o, y), g) for (o, y), g in M.groupby(["other", "year_red"])]
for (other, year), g in groups:
    r = {bd: np.log(g[f"{bd}_red"] / g[f"{bd}_other"]).dropna() for bd in BANDS}
    print(f"   red / {other:5s} {str(year):10s} (n {len(g):4d}): " +
          ", ".join(f"{bd} {np.exp(v.median()):.2f} ({np.exp(v.quantile(.25)):.2f}-{np.exp(v.quantile(.75)):.2f})" for bd, v in r.items()))
