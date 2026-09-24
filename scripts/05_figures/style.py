"""Shared look of the headline box plots: one box per drone, dodged within each x group."""
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.common import COLORS, DRONE_ORDER

INK, MUTED, GRID, SURF = "#1d1d1b", "#6b6b67", "#e6e6e3", "#fcfcfb"
WIDTH = 0.26
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10.5, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
                     "xtick.color": MUTED, "ytick.color": MUTED})


def dodged_boxes(F, group, labels, xlabel, legend):
    """Box and whisker of flight softness (edge_px) per drone within each `group` level, in `labels` order.
    Whiskers reach 1.5 x IQR; flights beyond them are drawn as points."""
    fig, ax = plt.subplots(figsize=(10, 6), dpi=180)
    fig.patch.set_facecolor(SURF); ax.set_facecolor(SURF)
    for i, lab in enumerate(labels):
        for k, drone in enumerate(DRONE_ORDER):
            y = F[(F[group] == lab) & (F.drone == drone)].edge_px.to_numpy()
            if not len(y):
                continue
            col = COLORS[drone]
            ax.boxplot(y, positions=[i + (k - 1) * (WIDTH + 0.03)], widths=WIDTH, patch_artist=True, whis=1.5,
                       medianprops=dict(color=col, lw=2.2), boxprops=dict(facecolor=matplotlib.colors.to_rgba(col, .18), edgecolor=col, lw=1.2),
                       whiskerprops=dict(color=col, lw=1.1), capprops=dict(color=col, lw=1.1),
                       flierprops=dict(marker="o", markersize=3.5, markerfacecolor=col, markeredgecolor=SURF, markeredgewidth=.4, alpha=.8))
    ax.set_xticks(range(len(labels))); ax.set_xticklabels(labels); ax.set_xlim(-0.55, len(labels) - 0.45)
    ax.set_xlabel(xlabel, labelpad=8)
    ax.set_ylabel("softness: median edge width across the flight (px)", labelpad=8)
    ax.yaxis.grid(True, color=GRID, lw=.8); ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    handles = [matplotlib.patches.Patch(facecolor=matplotlib.colors.to_rgba(COLORS[d], .18), edgecolor=COLORS[d], label=f"{d} drone") for d in DRONE_ORDER]
    ax.legend(handles=handles, frameon=False, fontsize=10, labelcolor=INK, **legend)
    fig.tight_layout()
    return fig
