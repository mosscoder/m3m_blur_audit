"""One row per flight from its 20 sampled photos: which drone flew it, how soft it is, and the focus it recorded.

    edge_px             softness: median over the sampled photos of the mean strong-edge width (px); the y-axis of
                        both headline figures
    wide_share, soft    share of sampled photos whose median edge width is >= 4 px; soft when >= 30 %
    focus_mm            median of the finite FocusDistance values (mm), empty when every sampled photo records
                        infinity (-1); FocusDistance is constant within 383 of the 391 flights
    focus_finite_share  share of sampled photos with a finite FocusDistance

    <- data/inventory.csv, data/frames.csv.gz
    -> data/flights.csv
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.common import DATA, DRONES, SOFT_EDGE_PX, SOFT_SHARE, flight_folders

inv = pd.read_csv(DATA / "inventory.csv", dtype={"date": str})
R = pd.read_csv(DATA / "frames.csv.gz")
R["utc"] = pd.to_datetime(R.utc, utc=True)
R["wide"] = R.edge_median_px >= SOFT_EDGE_PX
finite = R.focus_mm.where(R.focus_mm != -1)

F = R.assign(finite=finite).groupby("prefix").agg(
    drone_serial=("drone_serial", lambda s: s.mode().iloc[0]), camera_serial=("camera_serial", lambda s: s.mode().iloc[0]),
    frames=("photo", "size"), photos=("photos", "first"), start_utc=("utc", "min"),
    edge_px=("edge_mean_px", "median"), fine_detail=("fine_detail", "median"), wide_share=("wide", "mean"), fnumber=("fnumber", "median"),
    focus_mm=("finite", "median"), focus_finite_share=("finite", lambda s: s.notna().mean()), focus_values=("focus_mm", "nunique")).reset_index()
F["drone"] = F.drone_serial.map(DRONES)
F["soft"] = F.wide_share >= SOFT_SHARE
F = flight_folders(inv)[["prefix", "year", "season", "label", "folder", "date"]].merge(F, on="prefix", validate="1:1")
season_order = {s: i for i, s in enumerate(["spring", "summer", "fall"])}
F = F.sort_values(["year", "season", "label", "start_utc"], key=lambda c: c.map(season_order) if c.name == "season" else c)
F.to_csv(DATA / "flights.csv", index=False)

assert F.drone.notna().all(), "a photo carries a drone serial outside the three M3Ms"
print(f"{len(F)} flights, {int(F.soft.sum())} soft")
print(F.groupby(["year", "season", "drone"]).agg(flights=("soft", "size"), soft=("soft", "sum")).unstack("drone").to_string())
mis = F[F.label.str.replace("M3M-", "") != F.drone]
print(f"\nflights filed under another drone's folder: {len(mis)}")
print(mis.groupby(["year", "season", "label", "drone"]).size().to_string())
