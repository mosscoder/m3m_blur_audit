"""Every M3M flight folder of the 2025 and 2026 front-country surveys: its visible-photo count and first/last capture
time (from the DJI file names, drone clock, local time).  Read-only listing of gs://mpg-aerial-survey.

A folder whose photos all reappear, by file name, in a larger folder of the same survey is a copy: duplicate_of names
the original.  (Two drones firing in the same second can share a handful of names by chance; a copy shares all.)

    -> data/inventory.csv  one row per flight folder, stubs and copies included; later steps keep folders with
                           >= 30 photos that are not copies
"""
import concurrent.futures as cf
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.common import DATA, MIN_PHOTOS, SURVEYS, bucket, subdirs

b = bucket()
folders = []
for year, seasons in SURVEYS.items():
    for season in seasons:
        for drone_dir in subdirs(b, f"surveys/{year}_front_country/{season}/data_collection/"):
            label = drone_dir.rstrip("/").rsplit("/", 1)[-1]
            if not label.startswith("M3M"):
                continue
            base = drone_dir + "DCIM/" if subdirs(b, drone_dir + "DCIM/") else drone_dir
            folders += [(year, season, label, f) for f in subdirs(b, base)]


def one(x):
    year, season, label, prefix = x
    names = [bl.name.rsplit("/", 1)[-1] for bl in b.client.list_blobs(b.name, prefix=prefix, match_glob="**_D.JPG")]
    times = sorted(m.group(1) for n in names if (m := re.match(r"DJI_(\d{14})_", n)))
    return dict(year=year, season=season, label=label, folder=prefix.rstrip("/").rsplit("/", 1)[-1], prefix=prefix, photos=len(names),
                first=times[0] if times else None, last=times[-1] if times else None, names=set(names))


with cf.ThreadPoolExecutor(16) as ex:
    inv = pd.DataFrame(list(ex.map(one, folders)))
inv["date"] = inv["first"].str[:8]
inv["duplicate_of"] = None
for _, g in inv.groupby(["year", "season"]):
    for r in g[g.photos > 0].itertuples():
        bigger = g[(g.photos > r.photos) & g.names.map(lambda s: r.names <= s)]
        if len(bigger):
            inv.loc[r.Index, "duplicate_of"] = bigger.prefix.iloc[0]
inv = inv.drop(columns="names")
DATA.mkdir(exist_ok=True)
inv.to_csv(DATA / "inventory.csv", index=False)
fl = inv[(inv.photos >= MIN_PHOTOS) & inv.duplicate_of.isna()]
print(f"{len(inv)} folders, {len(fl)} flights with >= {MIN_PHOTOS} visible photos that are not copies")
print("copies:", inv.dropna(subset=["duplicate_of"])[["prefix", "duplicate_of"]].to_string(index=False))
print(fl.groupby(["year", "season", "label"]).agg(flights=("folder", "size"), photos=("photos", "sum"), days=("date", "nunique")).to_string())
