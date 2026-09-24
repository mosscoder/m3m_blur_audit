"""Headline figure 2: flight softness by survey, in time order, dodged by drone.

    <- data/flights.csv
    -> figures/season_vs_softness_box.png
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.common import FIGURES, SURVEYS, load_flights
from style import SURF, dodged_boxes

F = load_flights()
F["survey"] = F.year.astype(str) + "-" + F.season
LABELS = [f"{y}-{s}" for y, seasons in SURVEYS.items() for s in seasons]
# the legend sits above the plot: a 2025-spring outlier reaches the top-left corner
fig = dodged_boxes(F, "survey", LABELS, "survey", dict(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=3))
FIGURES.mkdir(exist_ok=True)
fig.savefig(FIGURES / "season_vs_softness_box.png", facecolor=SURF)
print(F.groupby(["survey", "drone"]).size().unstack().reindex(LABELS).to_string())
