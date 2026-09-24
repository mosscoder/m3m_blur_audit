"""The camera's state at each flight's first mission photo, by which point its focus is already set.

From the flight folder, read-only: the first line of the MRK timestamp file (GPS time, position, ellipsoid height),
the GNSS log's first-observation time (when the drone powered up), the first visible photo's header (gimbal, speed,
height above takeoff, FocusDistance, aperture, ISO, camera firmware) and the matching multispectral photo's
sunlight-sensor reading.
Outside sources, all public: USGS 3DEP 5 m ground elevation, NOAA GEOID18 geoid height, Open-Meteo hourly air
temperature at the ranch.

    agl_true0   height above the ground at the first photo = ellipsoid height - geoid height - 3DEP ground (m)
    local_hour  first-photo time, Montana daylight time, in hours

    <- data/inventory.csv
    -> data/first_photos.csv
"""
import concurrent.futures as cf
import datetime as dt
import json
import re
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.common import BUCKET, DATA, UTC_TO_LOCAL, bucket, dji_xmp, exif, firmware, flight_folders, focus_mm

b = bucket()
inv = pd.read_csv(DATA / "inventory.csv")
inv = flight_folders(inv)
GPS0 = dt.datetime(1980, 1, 6, tzinfo=dt.timezone.utc)
LEAP = 18                                                        # GPS - UTC, seconds


def one(prefix):
    out = {"prefix": prefix}
    blobs = list(b.client.list_blobs(BUCKET, prefix=prefix))
    mrk = [x for x in blobs if x.name.endswith(".MRK")]
    obs = [x for x in blobs if x.name.lower().endswith(".obs")]
    jpgs = sorted(x.name for x in blobs if x.name.endswith("_D.JPG"))
    if mrk:
        lines = [l.split("\t") for l in mrk[0].download_as_text().splitlines() if l.strip()]
        first, last = lines[0], lines[-1]
        week = int(re.search(r"\[(\d+)\]", first[2]).group(1))
        out.update(photo0=int(first[0]), t_first=GPS0 + dt.timedelta(weeks=week, seconds=float(first[1]) - LEAP),
                   t_last=GPS0 + dt.timedelta(weeks=week, seconds=float(last[1]) - LEAP),
                   lat0=float(first[6].split(",")[0]), lon0=float(first[7].split(",")[0]), ellh0=float(first[8].split(",")[0]))
    if obs:
        head = obs[0].download_as_bytes(start=0, end=16383).decode("latin-1")
        m = re.search(r"^\s*(\d{4})\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)\s+GPS\s+TIME OF FIRST OBS", head, re.M)
        if m:
            y, mo, d, h, mi, s = m.groups()
            out["t_obs0"] = dt.datetime(int(y), int(mo), int(d), int(h), int(mi), int(float(s)), tzinfo=dt.timezone.utc) - dt.timedelta(seconds=LEAP)
    if jpgs:
        raw = b.blob(jpgs[0]).download_as_bytes(start=0, end=262143)
        x, e = dji_xmp(raw), exif(raw)
        out.update(first_photo=jpgs[0].rsplit("/", 1)[-1], gimbal_pitch=float(x.get("GimbalPitchDegree", "nan")),
                   gimbal_roll=float(x.get("GimbalRollDegree", "nan")), flight_pitch=float(x.get("FlightPitchDegree", "nan")),
                   speed0=float(np.hypot(float(x.get("FlightXSpeed", "nan")), float(x.get("FlightYSpeed", "nan")))),
                   rel_alt0=float(x.get("RelativeAltitude", "nan")), focus_mm0=focus_mm(x),
                   fnumber0=float(e.get(33437) or np.nan), iso0=e.get(34855), firmware=firmware(raw))
        try:
            h = b.blob(jpgs[0].replace("_D.JPG", "_MS_G.TIF")).download_as_bytes(start=0, end=16383).decode("latin-1")
            out["irradiance0"] = float(re.search(r'drone-dji:Irradiance="([^"]*)"', h).group(1))
        except Exception:
            pass
    return out


with cf.ThreadPoolExecutor(16) as ex:
    W = pd.DataFrame(list(ex.map(one, inv.prefix)))
print(f"{len(W)} flights read; with MRK {W.t_first.notna().sum()}, with GNSS log {W.t_obs0.notna().sum()}")

# ---- ground under the first photo (3DEP, NAVD88) and the geoid there (GEOID18) -> height above ground
import rasterio
from pyproj import Transformer

X, Y = Transformer.from_crs(4326, 6514, always_xy=True).transform(W.lon0.to_numpy(), W.lat0.to_numpy())
xmin, xmax, ymin, ymax, res = np.nanmin(X) - 300, np.nanmax(X) + 300, np.nanmin(Y) - 300, np.nanmax(Y) + 300, 5.0
q = dict(bbox=f"{xmin},{ymin},{xmax},{ymax}", bboxSR=6514, imageSR=6514, size=f"{int((xmax - xmin) / res)},{int((ymax - ymin) / res)}",
         format="tiff", pixelType="F32", interpolation="RSP_BilinearInterpolation", f="image")
url = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage?" + urllib.parse.urlencode(q)
with tempfile.NamedTemporaryFile(suffix=".tif") as tmp:
    tmp.write(urllib.request.urlopen(url, timeout=300).read()); tmp.flush()
    with rasterio.open(tmp.name) as s:
        W["ground0"] = [v[0] for v in s.sample(zip(X, Y))]
lat, lon = W.lat0.median(), W.lon0.median()
W["geoid_n"] = float(json.loads(urllib.request.urlopen(f"https://geodesy.noaa.gov/api/geoid/ght?lat={lat:.5f}&lon={lon:.5f}", timeout=60).read())["geoidHeight"])
W["agl_true0"] = W.ellh0 - W.geoid_n - W.ground0

# ---- air temperature at the start (hourly, at the ranch)
t = pd.to_datetime(W.t_first, utc=True)
url = (f"https://archive-api.open-meteo.com/v1/archive?latitude=46.68&longitude=-114.0&start_date={t.min():%Y-%m-%d}&end_date={t.max():%Y-%m-%d}"
       "&hourly=temperature_2m&timezone=UTC")
h = json.loads(urllib.request.urlopen(url, timeout=120).read())["hourly"]
T = pd.Series(h["temperature_2m"], index=pd.to_datetime(h["time"]).tz_localize("UTC")).dropna()
W["temp_c"] = np.interp(t.astype("int64"), T.index.astype("int64"), T.to_numpy())
lt = t + UTC_TO_LOCAL
W["local_hour"] = lt.dt.hour + lt.dt.minute / 60

W.to_csv(DATA / "first_photos.csv", index=False)
print(f"geoid height {W.geoid_n.iloc[0]:.2f} m")
print(W[["agl_true0", "rel_alt0", "gimbal_pitch", "speed0", "local_hour", "temp_c"]].describe().round(1).to_string())
