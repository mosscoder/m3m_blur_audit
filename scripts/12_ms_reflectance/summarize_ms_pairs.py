"""Same ground, two drones: the ratio of what each multispectral camera measured, and of the reflectance after dividing
by each drone's sunlight sensor.  Median ratio over pairs, per band.  Same-drone pairs (two flights) are the control
for how much the ratio wanders when nothing about the hardware differs.

    raw DN       black-level-subtracted digital number, as recorded (depends on each capture's exposure)
    camera       DN per unit exposure (gain x time), vignetting corrected: the camera's own measurement
    reflectance  camera x SensorGainAdjustment / Irradiance: what the calibration hands on

    <- data/ms_pairs.csv
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.common import DATA

BANDS = ("G", "R", "RE", "NIR")
Q = pd.read_csv(DATA / "ms_pairs.csv")
Q = Q[Q.ok]
for q, label in (("dn", "raw DN"), ("camera", "camera (per unit exposure)"), ("reflectance", "reflectance")):
    print(f"\n{label}: drone 1 / drone 2, median ratio over pairs")
    rows = []
    for (kind, d1, d2), g in Q.groupby(["kind", "drone_1", "drone_2"]):
        row = {"pair": f"{d1} / {d2}" + (" (two flights)" if kind == "same drone" else ""), "n": len(g)}
        row.update({bd: np.exp(np.median(np.log(g[f"{bd}_{q}_1"] / g[f"{bd}_{q}_2"]))) for bd in BANDS})
        if q == "reflectance":
            ndvi = lambda i: (g[f"NIR_{q}_{i}"] - g[f"R_{q}_{i}"]) / (g[f"NIR_{q}_{i}"] + g[f"R_{q}_{i}"])
            row["NDVI diff"] = np.median(ndvi(1) - ndvi(2))
        rows.append(row)
    print(pd.DataFrame(rows).set_index("pair").round(3).to_string())
print(f"\npairs: median {Q.dist_m.median():.0f} m and {Q.hours_apart.median():.1f} h apart")
