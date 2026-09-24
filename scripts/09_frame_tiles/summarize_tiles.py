"""Is a soft flight soft everywhere in the frame, or in one place?

Per flight, the tile grid averaged over its photos.  Each soft flight is compared with its own drone's sharp flights:
the share of tiles whose edges are wider by more than 0.3 px, and the worst 3 x 3 patch of fine detail against the
rest of the frame (a local smudge would show as a deep patch).

    <- data/frame_tiles.csv.gz
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.common import DATA, load_flights

D = pd.read_csv(DATA / "frame_tiles.csv.gz")
drone = load_flights().set_index("prefix").drone
D["log_fine"] = np.log(D.fine_detail)
D["pattern"] = D.log_fine - D.groupby("photo").log_fine.transform("median")        # detail relative to the photo's own median


def grid(g, col):
    return g.groupby(["row", "col"])[col].mean().unstack().to_numpy()


maps = {p: dict(kind=g.kind.iloc[0], drone=drone[p], edge=grid(g, "edge_px"), pattern=grid(g, "pattern")) for p, g in D.groupby("prefix")}
sharp = {d: [m for m in maps.values() if m["kind"] == "sharp" and m["drone"] == d] for d in set(drone[list(maps)])}


def worst_patch(d):
    """mean of the worst 3 x 3 patch minus the frame's median"""
    return min(d[max(0, i - 1):i + 2, max(0, j - 1):j + 2].mean() for i in range(d.shape[0]) for j in range(d.shape[1])) - np.median(d)


rows = []
for p, m in maps.items():
    base = [s for s in sharp.get(m["drone"], []) if s is not m]
    if not base:
        continue
    edge0, pat0 = np.mean([s["edge"] for s in base], 0), np.mean([s["pattern"] for s in base], 0)
    rows.append(dict(kind=m["kind"], drone=m["drone"], edge_frame=np.median(m["edge"]), edge_tile_min=m["edge"].min(), edge_tile_max=m["edge"].max(),
                     tiles_wider=float((m["edge"] > edge0 + 0.3).mean()), worst_patch=worst_patch(m["pattern"] - pat0)))
T = pd.DataFrame(rows)
print("per flight, against the same drone's other sharp flights (medians over flights)")
print(T.groupby("kind").agg(flights=("drone", "size"), edge_frame_px=("edge_frame", "median"), edge_tile_min_px=("edge_tile_min", "median"),
                            edge_tile_max_px=("edge_tile_max", "median"), share_of_tiles_wider=("tiles_wider", "median"),
                            worst_3x3_patch=("worst_patch", "median")).round(2).T.to_string())
s = T[T.kind == "soft"]
print(f"\nsoft flights with wider edges in at least 90 % of tiles: {int((s.tiles_wider >= .9).sum())} of {len(s)}")
