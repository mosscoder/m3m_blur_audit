"""Headline figure 1: flight softness by the focus distance the camera recorded, dodged by drone.

    <- data/flights.csv
    -> figures/focus_vs_softness_box.png
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.common import FIGURES, load_flights
from style import SURF, dodged_boxes

LABELS = ["∞", "16–46 m", "12–16 m", "8–12 m", "5–8 m", "3.3–5 m"]
EDGES_MM = [0, 5000, 8000, 12000, 16000, 50000, 2e9]

F = load_flights()
F["focus_bin"] = pd.cut(F.focus_mm.fillna(1e9), EDGES_MM, labels=LABELS[::-1])
fig = dodged_boxes(F, "focus_bin", LABELS, "focus distance recorded in the flight's photos", dict(loc="upper left"))
FIGURES.mkdir(exist_ok=True)
fig.savefig(FIGURES / "focus_vs_softness_box.png", facecolor=SURF)
print(F.groupby(["focus_bin", "drone"], observed=False).size().unstack().reindex(LABELS).to_string())
