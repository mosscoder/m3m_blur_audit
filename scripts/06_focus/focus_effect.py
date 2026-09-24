"""How the recorded focus distance drives softness: how often each drone flies with a finite focus, soft rates by focus
distance, the optics check, the same-day comparison, what changes at battery swaps, and the camera's state when the
survey begins.

    <- data/flights.csv, data/first_photos.csv
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.common import DATA, DRONE_ORDER, load_flights

LABELS = ["∞", "16–46 m", "12–16 m", "8–12 m", "5–8 m", "3.3–5 m"]
F = load_flights()
P = pd.read_csv(DATA / "first_photos.csv")
F = F.merge(P[["prefix", "focus_mm0", "agl_true0", "gimbal_pitch", "speed0"]], on="prefix")
F["focus_bin"] = pd.cut(F.focus_mm.fillna(1e9), [0, 5000, 8000, 12000, 16000, 50000, 2e9], labels=LABELS[::-1])
F["close"] = F.focus_mm < 12000
F["infinity"] = F.focus_mm.isna()

print("1) FocusDistance is set once per flight")
print(f"   flights whose sampled photos record more than one value: {int((F.focus_values > 1).sum())} of {len(F)}")
same = (F.focus_mm0.where(F.focus_mm0 != -1).fillna(0) == F.focus_mm.fillna(0))
print(f"   flights whose first photo records the flight's focus: {int(same.sum())} of {len(F)}")

print("\n2) flights by drone and recorded focus distance")
print(pd.crosstab(F.drone, F.focus_bin).reindex(columns=LABELS).to_string())
print("   share of flights not focused at infinity:", F.groupby("drone").infinity.apply(lambda s: round(1 - s.mean(), 2)).to_dict())
print("   by year:", F.groupby(["drone", "year"]).infinity.apply(lambda s: round(1 - s.mean(), 2)).to_dict())

print("\n3) softness by recorded focus distance, all drones")
print(F.groupby("focus_bin", observed=True).agg(flights=("soft", "size"), soft=("soft", "sum"), soft_rate=("soft", "mean"),
                                                  median_edge_px=("edge_px", "median")).reindex(LABELS).round(2).to_string())
print("\n   by drone: soft rate focused closer than 12 m vs at infinity")
for d in DRONE_ORDER:
    g = F[F.drone == d]
    c, i = g[g.close], g[g.infinity]
    print(f"   {d:6s} closer than 12 m: {int(c.soft.sum())}/{len(c)} soft ({c.soft.mean():.0%})   infinity: {int(i.soft.sum())}/{len(i)} soft ({i.soft.mean():.0%})")

# 4) optics: the blur circle a lens focused at s puts on ground at D = 120 m, in pixels: f^2 / N * |1/s - 1/D| / pixel
FOCAL_MM, PIXEL_MM, GROUND_MM = 12.29, 0.0033, 120000.0
g = F[F.focus_finite_share > .5]
blur = FOCAL_MM ** 2 / g.fnumber * np.abs(1 / g.focus_mm - 1 / GROUND_MM) / PIXEL_MM
print(f"\n4) predicted defocus blur vs measured edge width, flights with a finite focus (n {len(g)}): Spearman {blur.corr(g.edge_px, method='spearman'):.2f}")

print("\n5) same drone, same day: flights focused closer than 12 m vs flights at infinity (edge width, px)")
for d in DRONE_ORDER:
    diffs = []
    for day, g in F[F.drone == d].groupby("date"):
        c, i = g[g.close].edge_px, g[g.infinity].edge_px
        if len(c) and len(i):
            diffs.append(c.mean() - i.mean())
    v = np.array(diffs)
    print(f"   {d:6s} {len(v)} days; close focus softer on {int((v > 0).sum())}/{len(v)}; mean +{v.mean():.2f} px (range {v.min():+.2f} to {v.max():+.2f})")

print("\n6) consecutive flights by the same drone on the same day (battery swaps): does softness switch, and does focus switch with it?")
F = F.sort_values(["drone", "date", "start_utc"])
nxt = F.groupby(["drone", "date"]).shift(-1)
S = pd.DataFrame(dict(drone=F.drone, soft=F.soft, soft_next=nxt.soft, focus=F.focus_mm.fillna(-1), focus_next=nxt.focus_mm.fillna(-1))).dropna(subset=["soft_next"])
S["soft_next"] = S.soft_next.astype(bool)
S["switch"] = S.soft != S.soft_next
S["focus_changed"] = S.focus != S.focus_next
print(f"   {len(S)} swaps; softness switches at {int(S.switch.sum())} (soft to sharp {int((S.soft & ~S.soft_next).sum())}, sharp to soft {int((~S.soft & S.soft_next).sum())})")
print(f"   the recorded focus changes at {int(S[S.switch].focus_changed.sum())} of the {int(S.switch.sum())} switches, and at {int(S[~S.switch].focus_changed.sum())} of the {int((~S.switch).sum())} swaps without one")
print("   by drone:", S.groupby("drone").agg(swaps=("switch", "size"), switches=("switch", "sum")).to_dict("index"))

print("\n7) the camera at the first photo of flights focused closer than 12 m (median, range)")
c = F[F.close]
for k, lab in (("agl_true0", "height above ground (m)"), ("gimbal_pitch", "gimbal pitch (deg)"), ("speed0", "speed (m/s)")):
    print(f"   {lab:26s} {c[k].median():7.1f}   ({c[k].min():.1f} to {c[k].max():.1f})")
