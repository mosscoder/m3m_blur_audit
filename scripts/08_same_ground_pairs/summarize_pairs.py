"""Same ground, two flights: a soft flight against a sharp partner, and a sharp control against a sharp partner.

If softness were in the scene (terrain, vegetation, light), the soft flight's photo and its partner of the same ground
would agree.  If it is in the photo, the soft flight loses fine detail and widens its edges against the partner.  A
dust or haze veil would also raise the dark floor and flatten contrast.

    <- data/same_ground_pairs.csv
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.common import DATA

V = pd.read_csv(DATA / "same_ground_pairs.csv")
V = V[V.ok]
V["floor_raised"] = (V.dark_floor - V.partner_dark_floor) > 0.02
print("medians over registered pairs (ratios: this flight / sharp partner)")
print(V.groupby("kind").agg(pairs=("fine_detail_ratio", "size"), flights=("prefix", "nunique"), fine_detail_ratio=("fine_detail_ratio", "median"),
                            edge_px=("edge_px", "median"), partner_edge_px=("partner_edge_px", "median"), dark_floor=("dark_floor", "median"),
                            partner_dark_floor=("partner_dark_floor", "median"), floor_raised_share=("floor_raised", "mean"),
                            contrast_span_ratio=("contrast_span_ratio", "median")).reindex(["soft", "control"]).round(3).T.to_string())
