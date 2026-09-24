"""How often each drone flies soft, whether that changed from 2025 to 2026, and what start time does to it.

Logistic regressions (soft flight yes/no), odds ratios with 95 % CIs.

    <- data/flights.csv, data/first_photos.csv
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.common import DATA, DRONE_ORDER, SURVEYS, load_flights, logit, odds

F = load_flights().merge(pd.read_csv(DATA / "first_photos.csv", parse_dates=["t_first"]), on="prefix")
F["survey"] = F.year.astype(str) + "-" + F.season
F["y2026"] = (F.year == 2026).astype(float)
SURVEY_ORDER = [f"{y}-{s}" for y, seasons in SURVEYS.items() for s in seasons]

print("1) soft flights / flights, by drone and survey")
t = F.groupby(["drone", "survey"]).soft.agg(lambda s: f"{int(s.sum())}/{len(s)}").unstack().reindex(columns=SURVEY_ORDER)
t["all"] = F.groupby("drone").soft.agg(lambda s: f"{int(s.sum())}/{len(s)} ({s.mean():.0%})")
print(t.to_string())

print("\n2) 2026 vs 2025, spring and summer only (fall 2026 not flown yet)")
S = F[F.season.isin(["spring", "summer"])]
m, n = logit(S, "soft", ["y2026"], ["season", "drone"])
print(f"   all drones, soft ~ year + season + drone (n {n}): 2026 odds ratio {odds(m, 'y2026')}")
for d in DRONE_ORDER:
    g = S[S.drone == d]
    m, n = logit(g, "soft", ["y2026"], ["season"])
    m2, _ = logit(g, "soft", ["y2026", "local_hour"], ["season"])
    print(f"   {d:6s} soft ~ year + season (n {n}): 2026 odds ratio {odds(m, 'y2026')};  adding start hour: {odds(m2, 'y2026')}")

print("\n3) start time, blue and green drones, all surveys")
AB = F[F.drone.isin(["blue", "green"])].copy()
AB["hour_bin"] = pd.cut(AB.local_hour, [0, 10.5, 11.5, 12.5, 24], labels=["before 10:30", "10:30-11:30", "11:30-12:30", "after 12:30"])
print(AB.groupby("hour_bin", observed=True).soft.agg(flights="size", soft="sum", rate="mean").round(2).to_string())
AB["order"] = AB.sort_values("t_first").groupby(["survey", "drone", "date"]).cumcount() + 1
AB["first_of_day"] = AB.order.eq(1).astype(float)
AB["log2_light"] = np.log2(AB.irradiance0)
for nums in (["local_hour"], ["local_hour", "log2_light", "temp_c", "first_of_day"]):
    m, n = logit(AB, "soft", nums, ["survey", "drone"])
    print(f"   soft ~ {' + '.join(nums)} + survey + drone (n {n}):")
    for k in nums:
        print(f"      {k:14s} odds ratio per unit {odds(m, k)}")
