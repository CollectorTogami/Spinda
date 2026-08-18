from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[2]
PROBE_DIR = ROOT / "_tmp" / "spc3_exception_probe"
BITMAP_PATH = PROBE_DIR / "bitmaps_raw.bin"
PASS2_PATH = PROBE_DIR / "pass2_counts_shape_rng_analysis.json"
PASS3_PATH = PROBE_DIR / "pass3_interaction_correlation_analysis.json"
CACHE_PATH = PROBE_DIR / "spc3_miss_visual_counts_cache.npz"

VISUAL_AIDS_DIR = ROOT / "Artifacts" / "visual-aids"
OUT_ROOT = VISUAL_AIDS_DIR / "SPC3 Predictor Miss Graphs - Dark and Light"

ROWS = 65536
COLS = 65536
ROW_BYTES = 8192
CHUNK_ROWS = 512
TOTAL_RECORDS = ROWS * COLS


@dataclass(frozen=True)
class Theme:
    name: str
    bg: str
    panel: str
    fg: str
    muted: str
    grid: str
    accent: str
    accent2: str
    accent3: str
    warn: str
    good: str
    cmap: str
    heat_cmap: str
    pie_colors: tuple[str, ...]


THEMES = [
    Theme(
        name="light_mode",
        bg="#f8faf7",
        panel="#ffffff",
        fg="#1a1d21",
        muted="#5c6470",
        grid="#d9ded8",
        accent="#1b6f8a",
        accent2="#d9822b",
        accent3="#7b3f98",
        warn="#b73535",
        good="#2f7d4f",
        cmap="viridis",
        heat_cmap="magma",
        pie_colors=("#2f7d4f", "#b73535", "#1b6f8a", "#d9822b", "#7b3f98"),
    ),
    Theme(
        name="dark_mode",
        bg="#0d1117",
        panel="#151b23",
        fg="#e7edf3",
        muted="#aab4bf",
        grid="#2b3440",
        accent="#57c7e8",
        accent2="#f2a65a",
        accent3="#c58af9",
        warn="#ff6b6b",
        good="#68d391",
        cmap="viridis",
        heat_cmap="inferno",
        pie_colors=("#68d391", "#ff6b6b", "#57c7e8", "#f2a65a", "#c58af9"),
    ),
]


@dataclass(frozen=True)
class MissData:
    lane_counts: np.ndarray
    upper_counts: np.ndarray
    pass2: dict
    pass3: dict


def trailing_zero16(value: int) -> int:
    if value == 0:
        return 16
    return (value & -value).bit_length() - 1


def pct(value: float, total: float) -> float:
    return 100.0 * value / total if total else 0.0


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_counts() -> tuple[np.ndarray, np.ndarray]:
    if CACHE_PATH.exists() and CACHE_PATH.stat().st_mtime >= BITMAP_PATH.stat().st_mtime:
        cached = np.load(CACHE_PATH)
        return cached["lane_counts"], cached["upper_counts"]

    popcount = np.array([int(i).bit_count() for i in range(256)], dtype=np.uint8)
    lane_counts = np.empty(ROWS, dtype=np.uint32)
    upper_counts = np.zeros(COLS, dtype=np.uint32)

    with BITMAP_PATH.open("rb") as handle:
        for lane0 in range(0, ROWS, CHUNK_ROWS):
            n_rows = min(CHUNK_ROWS, ROWS - lane0)
            block = handle.read(n_rows * ROW_BYTES)
            if len(block) != n_rows * ROW_BYTES:
                raise RuntimeError(f"Short bitmap read at lane {lane0:#06x}")
            packed = np.frombuffer(block, dtype=np.uint8).reshape(n_rows, ROW_BYTES)
            lane_counts[lane0 : lane0 + n_rows] = popcount[packed].sum(
                axis=1, dtype=np.uint32
            )
            bits = np.unpackbits(packed, axis=1, bitorder="little")
            upper_counts += bits.sum(axis=0, dtype=np.uint32)

    np.savez_compressed(CACHE_PATH, lane_counts=lane_counts, upper_counts=upper_counts)
    return lane_counts, upper_counts


def load_data() -> MissData:
    lane_counts, upper_counts = load_counts()
    return MissData(
        lane_counts=lane_counts,
        upper_counts=upper_counts,
        pass2=read_json(PASS2_PATH),
        pass3=read_json(PASS3_PATH),
    )


def setup_axes(theme: Theme, title: str, subtitle: str = "", square: bool = False):
    size = (12.2, 12.0) if square else (15.88, 8.99)
    fig, ax = plt.subplots(figsize=size, dpi=200, constrained_layout=True)
    fig.patch.set_facecolor(theme.bg)
    ax.set_facecolor(theme.panel)
    ax.tick_params(colors=theme.fg, labelsize=10)
    for spine in ax.spines.values():
        spine.set_color(theme.grid)
    ax.grid(True, color=theme.grid, alpha=0.45, linewidth=0.8)
    fig.suptitle(title, color=theme.fg, fontsize=22, fontweight="bold")
    if subtitle:
        ax.set_title(subtitle, color=theme.muted, fontsize=12, pad=10)
    return fig, ax


def save(fig, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)


def style_colorbar(cbar, theme: Theme) -> None:
    cbar.ax.yaxis.set_tick_params(color=theme.fg)
    plt.setp(cbar.ax.get_yticklabels(), color=theme.fg)
    cbar.outline.set_edgecolor(theme.grid)
    cbar.ax.set_ylabel(cbar.ax.get_ylabel(), color=theme.fg)


def add_note(ax, theme: Theme, text: str) -> None:
    ax.text(
        0.01,
        -0.12,
        text,
        transform=ax.transAxes,
        color=theme.muted,
        fontsize=10,
        va="top",
    )


def rolling_mean(values: np.ndarray, window: int) -> np.ndarray:
    kernel = np.ones(window, dtype=np.float64) / window
    return np.convolve(values.astype(np.float64), kernel, mode="same")


def ordered_rates_from_matrix(matrix_rows: list[dict]) -> tuple[list[str], np.ndarray]:
    rows = [entry["row"] for entry in matrix_rows]
    matrix = np.array(
        [[cell["rate_pct"] for cell in entry["cells"]] for entry in matrix_rows],
        dtype=np.float64,
    )
    return rows, matrix


def bar_labels(ax, values: Iterable[float], theme: Theme, suffix: str = "%") -> None:
    for patch, value in zip(ax.patches, values):
        ax.text(
            patch.get_x() + patch.get_width() / 2,
            patch.get_height(),
            f"{value:.1f}{suffix}",
            ha="center",
            va="bottom",
            color=theme.fg,
            fontsize=8,
            rotation=0,
        )


def chart_00_outcome_pie(data: MissData, theme: Theme, output: Path) -> None:
    misses = int(data.upper_counts.sum(dtype=np.uint64))
    hits = TOTAL_RECORDS - misses
    fig, ax = setup_axes(
        theme,
        "SPC3 predictor outcomes",
        "All 4,294,967,296 Spinda records: hit versus IV32 exception",
        square=True,
    )
    ax.grid(False)
    wedges, _, autotexts = ax.pie(
        [hits, misses],
        labels=["Predictor hit", "Predictor miss"],
        autopct="%1.2f%%",
        startangle=90,
        colors=(theme.good, theme.warn),
        textprops={"color": theme.fg, "fontsize": 12},
        wedgeprops={"linewidth": 2, "edgecolor": theme.bg},
    )
    for text in autotexts:
        text.set_fontweight("bold")
    ax.legend(
        wedges,
        [f"Hits: {hits:,}", f"Misses: {misses:,}"],
        loc="lower center",
        bbox_to_anchor=(0.5, -0.05),
        ncol=1,
        frameon=False,
        labelcolor=theme.fg,
    )
    save(fig, output)


def chart_01_upper_heatmap(data: MissData, theme: Theme, output: Path) -> None:
    rates = data.upper_counts.reshape(256, 256) / ROWS * 100.0
    fig, ax = setup_axes(
        theme,
        "Upper-half predictor miss heatmap",
        "Rows are upper high byte; columns are upper low byte",
    )
    image = ax.imshow(rates, origin="lower", cmap=theme.heat_cmap, aspect="auto")
    ax.set_xlabel("Upper low byte", color=theme.fg)
    ax.set_ylabel("Upper high byte", color=theme.fg)
    ax.set_xticks(np.arange(0, 257, 32))
    ax.set_yticks(np.arange(0, 257, 32))
    cbar = fig.colorbar(image, ax=ax)
    cbar.set_label("Miss rate (%)")
    style_colorbar(cbar, theme)
    save(fig, output)


def chart_02_lane_heatmap(data: MissData, theme: Theme, output: Path) -> None:
    rates = data.lane_counts.reshape(256, 256) / COLS * 100.0
    fig, ax = setup_axes(
        theme,
        "Lane predictor miss heatmap",
        "Rows are lane high byte; columns are lane low byte",
    )
    image = ax.imshow(rates, origin="lower", cmap=theme.heat_cmap, aspect="auto")
    ax.set_xlabel("Lane low byte", color=theme.fg)
    ax.set_ylabel("Lane high byte", color=theme.fg)
    ax.set_xticks(np.arange(0, 257, 32))
    ax.set_yticks(np.arange(0, 257, 32))
    cbar = fig.colorbar(image, ax=ax)
    cbar.set_label("Miss rate (%)")
    style_colorbar(cbar, theme)
    save(fig, output)


def chart_03_upper_heatstrip(data: MissData, theme: Theme, output: Path) -> None:
    rates = data.upper_counts[np.newaxis, :] / ROWS * 100.0
    fig, ax = setup_axes(
        theme,
        "Upper-half miss heat strip",
        "Linear sweep from upper 0x0000 through 0xFFFF",
    )
    image = ax.imshow(rates, aspect="auto", cmap=theme.heat_cmap)
    ax.set_yticks([])
    ax.set_xlabel("Upper PID half", color=theme.fg)
    ax.set_xticks(np.linspace(0, COLS - 1, 9))
    ax.set_xticklabels([f"0x{int(x):04X}" for x in np.linspace(0, COLS - 1, 9)])
    cbar = fig.colorbar(image, ax=ax, orientation="horizontal", pad=0.16)
    cbar.set_label("Miss rate (%)")
    cbar.ax.xaxis.set_tick_params(color=theme.fg)
    plt.setp(cbar.ax.get_xticklabels(), color=theme.fg)
    cbar.outline.set_edgecolor(theme.grid)
    save(fig, output)


def chart_04_lane_heatstrip(data: MissData, theme: Theme, output: Path) -> None:
    rates = data.lane_counts[np.newaxis, :] / COLS * 100.0
    fig, ax = setup_axes(
        theme,
        "Lane miss heat strip",
        "Linear sweep from lower PID lane 0x0000 through 0xFFFF",
    )
    image = ax.imshow(rates, aspect="auto", cmap=theme.heat_cmap)
    ax.set_yticks([])
    ax.set_xlabel("Lower PID lane", color=theme.fg)
    ax.set_xticks(np.linspace(0, ROWS - 1, 9))
    ax.set_xticklabels([f"0x{int(x):04X}" for x in np.linspace(0, ROWS - 1, 9)])
    cbar = fig.colorbar(image, ax=ax, orientation="horizontal", pad=0.16)
    cbar.set_label("Miss rate (%)")
    cbar.ax.xaxis.set_tick_params(color=theme.fg)
    plt.setp(cbar.ax.get_xticklabels(), color=theme.fg)
    cbar.outline.set_edgecolor(theme.grid)
    save(fig, output)


def polar_ring(values: np.ndarray, theme: Theme, output: Path, title: str, label: str) -> None:
    rates = values.astype(np.float64) / ROWS * 100.0
    # Rendering all 65,536 values as polar bars is visually indistinguishable at
    # this output size but creates a very slow 65k-artist plot. Average into
    # 2,048 angular bins so the chart remains faithful and quick to regenerate.
    bin_count = 2048
    rates = rates.reshape(bin_count, len(rates) // bin_count).mean(axis=1)
    theta = np.linspace(0, 2 * np.pi, len(rates), endpoint=False)
    width = 2 * np.pi / len(rates)
    norm = plt.Normalize(float(rates.min()), float(rates.max()))
    cmap = plt.get_cmap(theme.heat_cmap)
    fig = plt.figure(figsize=(12.2, 12.0), dpi=200, constrained_layout=True)
    fig.patch.set_facecolor(theme.bg)
    ax = fig.add_subplot(111, projection="polar")
    ax.set_facecolor(theme.bg)
    ax.bar(
        theta,
        rates,
        width=width,
        bottom=0.0,
        color=cmap(norm(rates)),
        edgecolor="none",
        linewidth=0,
    )
    ax.set_title(title, color=theme.fg, fontsize=22, fontweight="bold", pad=28)
    ax.set_yticklabels([])
    ax.set_xticks(np.linspace(0, 2 * np.pi, 8, endpoint=False))
    ax.set_xticklabels([f"{i}/8" for i in range(8)], color=theme.muted)
    ax.grid(color=theme.grid, alpha=0.35)
    sm = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    cbar = fig.colorbar(sm, ax=ax, shrink=0.78, pad=0.08)
    cbar.set_label(label)
    style_colorbar(cbar, theme)
    save(fig, output)


def chart_05_upper_polar(data: MissData, theme: Theme, output: Path) -> None:
    polar_ring(data.upper_counts, theme, output, "Upper-half miss ring", "Miss rate (%)")


def chart_06_lane_polar(data: MissData, theme: Theme, output: Path) -> None:
    polar_ring(data.lane_counts, theme, output, "Lane miss ring", "Miss rate (%)")


def histogram_chart(
    values: np.ndarray,
    theme: Theme,
    output: Path,
    title: str,
    xlabel: str,
    denominator: int,
) -> None:
    rates = values.astype(np.float64) / denominator * 100.0
    fig, ax = setup_axes(theme, title, "Distribution of miss rates")
    ax.hist(rates, bins=80, color=theme.accent, alpha=0.88, edgecolor=theme.panel)
    ax.axvline(rates.mean(), color=theme.warn, linewidth=2.2, label=f"Mean {rates.mean():.2f}%")
    ax.axvline(np.median(rates), color=theme.accent2, linewidth=2.2, label=f"Median {np.median(rates):.2f}%")
    ax.set_xlabel(xlabel, color=theme.fg)
    ax.set_ylabel("Count", color=theme.fg)
    ax.legend(frameon=False, labelcolor=theme.fg)
    save(fig, output)


def chart_07_upper_hist(data: MissData, theme: Theme, output: Path) -> None:
    histogram_chart(data.upper_counts, theme, output, "Upper-half miss-rate histogram", "Miss rate per upper half (%)", ROWS)


def chart_08_lane_hist(data: MissData, theme: Theme, output: Path) -> None:
    histogram_chart(data.lane_counts, theme, output, "Lane miss-rate histogram", "Miss rate per lane (%)", COLS)


def ecdf_chart(
    values: np.ndarray,
    theme: Theme,
    output: Path,
    title: str,
    xlabel: str,
    denominator: int,
) -> None:
    rates = np.sort(values.astype(np.float64) / denominator * 100.0)
    y = np.linspace(0, 100, len(rates), endpoint=True)
    fig, ax = setup_axes(theme, title, "Empirical cumulative distribution")
    ax.plot(rates, y, color=theme.accent, linewidth=2.5)
    ax.set_xlabel(xlabel, color=theme.fg)
    ax.set_ylabel("Percent of items at or below rate", color=theme.fg)
    ax.set_xlim(left=0)
    ax.set_ylim(0, 100)
    save(fig, output)


def chart_09_upper_ecdf(data: MissData, theme: Theme, output: Path) -> None:
    ecdf_chart(data.upper_counts, theme, output, "Upper-half miss ECDF", "Miss rate per upper half (%)", ROWS)


def chart_10_lane_ecdf(data: MissData, theme: Theme, output: Path) -> None:
    ecdf_chart(data.lane_counts, theme, output, "Lane miss ECDF", "Miss rate per lane (%)", COLS)


def rank_curve(
    values: np.ndarray,
    theme: Theme,
    output: Path,
    title: str,
    ylabel: str,
    denominator: int,
) -> None:
    rates = np.sort(values.astype(np.float64) / denominator * 100.0)[::-1]
    fig, ax = setup_axes(theme, title, "Sorted from most inconsistent to least inconsistent")
    ax.plot(np.arange(1, len(rates) + 1), rates, color=theme.accent, linewidth=2.0)
    ax.set_xlabel("Rank", color=theme.fg)
    ax.set_ylabel(ylabel, color=theme.fg)
    ax.set_xscale("log")
    ax.set_xlim(1, len(rates))
    save(fig, output)


def chart_11_upper_rank(data: MissData, theme: Theme, output: Path) -> None:
    rank_curve(data.upper_counts, theme, output, "Upper-half rank curve", "Miss rate (%)", ROWS)


def chart_12_lane_rank(data: MissData, theme: Theme, output: Path) -> None:
    rank_curve(data.lane_counts, theme, output, "Lane rank curve", "Miss rate (%)", COLS)


def top_bar(
    values: np.ndarray,
    theme: Theme,
    output: Path,
    title: str,
    ylabel: str,
    denominator: int,
    prefix: str,
) -> None:
    order = np.argsort(values)[::-1][:25]
    rates = values[order].astype(np.float64) / denominator * 100.0
    fig, ax = setup_axes(theme, title, "Top 25 by predictor miss count")
    ax.bar(np.arange(len(order)), rates, color=theme.warn, alpha=0.9)
    ax.set_xticks(np.arange(len(order)))
    ax.set_xticklabels([f"{prefix}{int(i):04X}" for i in order], rotation=70, ha="right")
    ax.set_ylabel(ylabel, color=theme.fg)
    ax.set_ylim(0, max(100, rates.max() * 1.08))
    save(fig, output)


def chart_13_top_upper(data: MissData, theme: Theme, output: Path) -> None:
    top_bar(data.upper_counts, theme, output, "Worst upper halves", "Miss rate (%)", ROWS, "0x")


def chart_14_top_lane(data: MissData, theme: Theme, output: Path) -> None:
    top_bar(data.lane_counts, theme, output, "Worst lower PID lanes", "Miss rate (%)", COLS, "0x")


def chart_15_upper_deciles(data: MissData, theme: Theme, output: Path) -> None:
    uppers = np.arange(COLS)
    deciles = np.minimum(9, (uppers * 10) // COLS)
    rates = []
    for decile in range(10):
        mask = deciles == decile
        rates.append(pct(data.upper_counts[mask].sum(), mask.sum() * ROWS))
    fig, ax = setup_axes(theme, "Upper numeric deciles", "Miss rate by upper-half numeric range")
    ax.bar(np.arange(10), rates, color=theme.accent2)
    ax.set_xticks(np.arange(10))
    ax.set_xlabel("Upper-half numeric decile", color=theme.fg)
    ax.set_ylabel("Miss rate (%)", color=theme.fg)
    ax.set_ylim(0, max(rates) * 1.18)
    bar_labels(ax, rates, theme)
    save(fig, output)


def chart_16_upper_nibble(data: MissData, theme: Theme, output: Path) -> None:
    uppers = np.arange(COLS)
    nibbles = uppers >> 12
    rates = []
    for nibble in range(16):
        mask = nibbles == nibble
        rates.append(pct(data.upper_counts[mask].sum(), mask.sum() * ROWS))
    fig, ax = setup_axes(theme, "Upper high-nibble miss rates", "Grouping by upper bits 15..12")
    ax.bar(np.arange(16), rates, color=theme.accent)
    ax.set_xticks(np.arange(16))
    ax.set_xticklabels([f"0x{i:X}" for i in range(16)])
    ax.set_xlabel("Upper high nibble", color=theme.fg)
    ax.set_ylabel("Miss rate (%)", color=theme.fg)
    ax.set_ylim(0, max(rates) * 1.18)
    save(fig, output)


def chart_17_lane_v2(data: MissData, theme: Theme, output: Path) -> None:
    lanes = np.arange(ROWS)
    v2 = np.array([trailing_zero16(int(lane)) for lane in lanes])
    rates = []
    labels = []
    for bucket in range(17):
        mask = v2 == bucket
        labels.append(str(bucket))
        rates.append(pct(data.lane_counts[mask].sum(), mask.sum() * COLS))
    fig, ax = setup_axes(theme, "Lane trailing-zero buckets", "v2(0) is shown as bucket 16")
    ax.bar(np.arange(17), rates, color=theme.accent2)
    ax.set_xticks(np.arange(17))
    ax.set_xticklabels(labels)
    ax.set_xlabel("Trailing zero bits in lower PID lane", color=theme.fg)
    ax.set_ylabel("Miss rate (%)", color=theme.fg)
    ax.set_ylim(0, max(rates) * 1.2)
    save(fig, output)


def chart_18_lane_mod24(data: MissData, theme: Theme, output: Path) -> None:
    lanes = np.arange(ROWS)
    residues = lanes % 24
    rates = []
    for residue in range(24):
        mask = residues == residue
        rates.append(pct(data.lane_counts[mask].sum(), mask.sum() * COLS))
    fig, ax = setup_axes(theme, "Lane modulo-24 miss rates", "Lower PID lane grouped by lane % 24")
    ax.bar(np.arange(24), rates, color=theme.accent3)
    ax.set_xticks(np.arange(24))
    ax.set_xlabel("lane % 24", color=theme.fg)
    ax.set_ylabel("Miss rate (%)", color=theme.fg)
    ax.set_ylim(0, max(rates) * 1.18)
    save(fig, output)


def chart_19_source_rates(data: MissData, theme: Theme, output: Path) -> None:
    rows = data.pass2["source_summary"]
    labels = [row["source"].replace("_", "\n") for row in rows]
    rates = [row["rate_pct"] for row in rows]
    fig, ax = setup_axes(theme, "Miss rate by source", "Source/provenance shifts are visible but small")
    ax.bar(np.arange(len(rows)), rates, color=[theme.accent, theme.accent2, theme.accent3, theme.warn, theme.good])
    ax.set_xticks(np.arange(len(rows)))
    ax.set_xticklabels(labels)
    ax.set_ylabel("Miss rate (%)", color=theme.fg)
    ax.set_ylim(0, max(rates) * 1.25)
    bar_labels(ax, rates, theme)
    save(fig, output)


def matrix_heatmap(
    matrix: np.ndarray,
    row_labels: list[str],
    col_labels: list[str],
    theme: Theme,
    output: Path,
    title: str,
    subtitle: str,
    xlabel: str,
) -> None:
    fig, ax = setup_axes(theme, title, subtitle)
    image = ax.imshow(matrix, aspect="auto", cmap=theme.heat_cmap)
    ax.set_yticks(np.arange(len(row_labels)))
    ax.set_yticklabels([label.replace("_", " ") for label in row_labels])
    ax.set_xticks(np.arange(len(col_labels)))
    ax.set_xticklabels(col_labels)
    ax.set_xlabel(xlabel, color=theme.fg)
    cbar = fig.colorbar(image, ax=ax)
    cbar.set_label("Miss rate (%)")
    style_colorbar(cbar, theme)
    for y in range(matrix.shape[0]):
        for x in range(matrix.shape[1]):
            ax.text(
                x,
                y,
                f"{matrix[y, x]:.1f}",
                ha="center",
                va="center",
                color=theme.fg,
                fontsize=8,
            )
    save(fig, output)


def chart_20_source_decile_heatmap(data: MissData, theme: Theme, output: Path) -> None:
    labels, matrix = ordered_rates_from_matrix(data.pass3["source_upper_decile_matrix"])
    matrix_heatmap(
        matrix,
        labels,
        [str(i) for i in range(10)],
        theme,
        output,
        "Source by upper-decile miss heatmap",
        "Every source preserves the same high/low upper-half pattern",
        "Upper numeric decile",
    )


def chart_21_lane_class_nibble_heatmap(data: MissData, theme: Theme, output: Path) -> None:
    labels, matrix = ordered_rates_from_matrix(data.pass3["lane_class_upper_high_nibble_matrix"])
    matrix_heatmap(
        matrix,
        labels,
        [f"0x{i:X}" for i in range(16)],
        theme,
        output,
        "Lane class by upper high-nibble",
        "Miss rate by lower-lane class and upper-half high nibble",
        "Upper high nibble",
    )


def chart_22_duplicate_marker_pie(data: MissData, theme: Theme, output: Path) -> None:
    marker = next(
        row
        for row in data.pass3["upper_marker_reports"]
        if row["name"] == "corrected_helper_duplicate_lane1_iv32_diff"
    )
    misses = int(marker["exceptions"])
    hits = int(marker["possible"] - marker["exceptions"])
    fig, ax = setup_axes(
        theme,
        "Duplicate-lane marker outcomes",
        "Only the 33 upper halves where local/helper lane 0x0001 disagree",
        square=True,
    )
    ax.grid(False)
    ax.pie(
        [hits, misses],
        labels=["Hits inside marker", "Misses inside marker"],
        autopct="%1.1f%%",
        startangle=90,
        colors=(theme.good, theme.warn),
        textprops={"color": theme.fg, "fontsize": 12},
        wedgeprops={"linewidth": 2, "edgecolor": theme.bg},
    )
    add_note(ax, theme, f"Marker upper halves: {marker['upper_count']}  |  marker miss rate: {marker['rate_pct']:.2f}%")
    save(fig, output)


def chart_23_topk_concentration(data: MissData, theme: Theme, output: Path) -> None:
    ordered = np.sort(data.upper_counts.astype(np.float64))[::-1]
    cumulative = np.cumsum(ordered) / ordered.sum() * 100.0
    x = np.arange(1, len(ordered) + 1)
    fig, ax = setup_axes(theme, "Top-k upper-half miss concentration", "How much of all misses are explained by the worst upper halves")
    ax.plot(x, cumulative, color=theme.accent, linewidth=2.5)
    for k in [18, 100, 1000, 9825]:
        ax.axvline(k, color=theme.grid, linewidth=1.2)
        ax.text(k, min(99, cumulative[k - 1] + 2), f"{k:,}", color=theme.muted, rotation=90, va="bottom")
    ax.set_xscale("log")
    ax.set_xlabel("Top k upper halves", color=theme.fg)
    ax.set_ylabel("Share of all misses (%)", color=theme.fg)
    ax.set_ylim(0, 100)
    save(fig, output)


def chart_24_threshold_pie(data: MissData, theme: Theme, output: Path) -> None:
    upper = data.upper_counts
    low = upper <= 255
    high = upper >= 32768
    mid = ~(low | high)
    values = [
        int(upper[low].sum(dtype=np.uint64)),
        int(upper[mid].sum(dtype=np.uint64)),
        int(upper[high].sum(dtype=np.uint64)),
    ]
    fig, ax = setup_axes(
        theme,
        "Misses by upper-half severity band",
        "Bands are based on miss count per upper half",
        square=True,
    )
    ax.grid(False)
    ax.pie(
        values,
        labels=["Low: <=255", "Middle: 256..32767", "High: >=32768"],
        autopct="%1.1f%%",
        startangle=90,
        colors=(theme.good, theme.accent2, theme.warn),
        textprops={"color": theme.fg, "fontsize": 12},
        wedgeprops={"linewidth": 2, "edgecolor": theme.bg},
    )
    save(fig, output)


def chart_25_lorenz(data: MissData, theme: Theme, output: Path) -> None:
    sorted_counts = np.sort(data.upper_counts.astype(np.float64))
    cumulative = np.concatenate([[0.0], np.cumsum(sorted_counts) / sorted_counts.sum() * 100.0])
    x = np.linspace(0, 100, len(cumulative))
    fig, ax = setup_axes(theme, "Lorenz curve of upper-half misses", "A bowed curve means misses are concentrated in a minority of upper halves")
    ax.plot(x, cumulative, color=theme.accent, linewidth=2.5, label="Observed")
    ax.plot([0, 100], [0, 100], color=theme.grid, linewidth=1.6, linestyle="--", label="Equal distribution")
    ax.fill_between(x, cumulative, x, color=theme.accent, alpha=0.18)
    ax.set_xlabel("Share of upper halves (%)", color=theme.fg)
    ax.set_ylabel("Share of misses (%)", color=theme.fg)
    ax.legend(frameon=False, labelcolor=theme.fg)
    save(fig, output)


def chart_26_transition_geometry(data: MissData, theme: Theme, output: Path) -> None:
    geom = data.pass2["transition_geometry"]
    labels = ["Upper masks\nacross lanes", "Lane masks\nacross uppers"]
    values = [
        geom["upper_masks_across_lanes"]["actual_over_expected_mean"],
        geom["lane_masks_across_uppers"]["actual_over_expected_mean"],
    ]
    fig, ax = setup_axes(theme, "Miss-mask transition geometry", "Actual transitions divided by random expectation")
    ax.bar(np.arange(2), values, color=(theme.accent2, theme.accent))
    ax.axhline(1.0, color=theme.warn, linewidth=2.0, linestyle="--", label="Random-like")
    ax.set_xticks(np.arange(2))
    ax.set_xticklabels(labels)
    ax.set_ylabel("Actual / expected transitions", color=theme.fg)
    ax.set_ylim(0, 1.2)
    bar_labels(ax, values, theme, suffix="")
    ax.legend(frameon=False, labelcolor=theme.fg)
    save(fig, output)


def scatter_with_rolling(
    values: np.ndarray,
    theme: Theme,
    output: Path,
    title: str,
    xlabel: str,
    denominator: int,
    window: int,
) -> None:
    x = np.arange(len(values))
    rates = values.astype(np.float64) / denominator * 100.0
    roll = rolling_mean(rates, window)
    fig, ax = setup_axes(theme, title, f"Point cloud with rolling mean window {window}")
    ax.scatter(x, rates, s=2, color=theme.muted, alpha=0.28, linewidths=0)
    ax.plot(x, roll, color=theme.warn, linewidth=2.2, label="Rolling mean")
    ax.set_xlabel(xlabel, color=theme.fg)
    ax.set_ylabel("Miss rate (%)", color=theme.fg)
    ax.set_xlim(0, len(values) - 1)
    ax.legend(frameon=False, labelcolor=theme.fg)
    save(fig, output)


def chart_27_upper_scatter(data: MissData, theme: Theme, output: Path) -> None:
    scatter_with_rolling(
        data.upper_counts,
        theme,
        output,
        "Upper-half miss scatter",
        "Upper PID half",
        ROWS,
        512,
    )


def chart_28_lane_scatter(data: MissData, theme: Theme, output: Path) -> None:
    scatter_with_rolling(
        data.lane_counts,
        theme,
        output,
        "Lane miss scatter",
        "Lower PID lane",
        COLS,
        512,
    )


def chart_29_marker_lollipop(data: MissData, theme: Theme, output: Path) -> None:
    marker = next(
        row
        for row in data.pass3["upper_marker_reports"]
        if row["name"] == "corrected_helper_duplicate_lane1_iv32_diff"
    )
    members = marker["members_by_rank_first_30"]
    labels = [entry["upper"] for entry in members]
    rates = [entry["exceptions"] / ROWS * 100.0 for entry in members]
    ranks = [entry["rank"] for entry in members]
    fig, ax = setup_axes(theme, "Duplicate-marker upper halves by rank", "First 30 marker members sorted by global miss rank")
    y = np.arange(len(labels))
    ax.hlines(y, 0, rates, color=theme.grid, linewidth=1.8)
    ax.scatter(rates, y, s=64, color=theme.warn, zorder=3)
    for yi, rank in zip(y, ranks):
        ax.text(1, yi, f"rank {rank:,}", color=theme.muted, va="center", fontsize=8)
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlabel("Miss rate (%)", color=theme.fg)
    ax.set_xlim(0, 105)
    save(fig, output)


def chart_30_source_marker_bar(data: MissData, theme: Theme, output: Path) -> None:
    rows = data.pass3["source_duplicate_marker_rates"]
    labels = [row["source"].replace("_", "\n") for row in rows]
    non = [row["nonduplicate_marker_rate_pct"] for row in rows]
    marker = [row["duplicate_marker_rate_pct"] for row in rows]
    x = np.arange(len(rows))
    width = 0.38
    fig, ax = setup_axes(theme, "Duplicate-marker miss rate by source", "The fragile 33 upper halves stay fragile in every source")
    ax.bar(x - width / 2, non, width, label="Other uppers", color=theme.accent)
    ax.bar(x + width / 2, marker, width, label="33 marker uppers", color=theme.warn)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Miss rate (%)", color=theme.fg)
    ax.set_ylim(0, max(marker) * 1.22)
    ax.legend(frameon=False, labelcolor=theme.fg)
    save(fig, output)


def chart_31_laneclass_marker_bar(data: MissData, theme: Theme, output: Path) -> None:
    rows = data.pass3["lane_class_duplicate_marker_rates"]
    labels = [row["lane_class"].replace("_", "\n") for row in rows]
    non = [row["nonduplicate_marker_rate_pct"] for row in rows]
    marker = [row["duplicate_marker_rate_pct"] for row in rows]
    x = np.arange(len(rows))
    width = 0.38
    fig, ax = setup_axes(theme, "Duplicate-marker miss rate by lane class", "Marker upper halves amplify misses across lane classes")
    ax.bar(x - width / 2, non, width, label="Other uppers", color=theme.accent)
    ax.bar(x + width / 2, marker, width, label="33 marker uppers", color=theme.warn)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Miss rate (%)", color=theme.fg)
    ax.set_ylim(0, max(marker) * 1.22)
    ax.legend(frameon=False, labelcolor=theme.fg)
    save(fig, output)


CHARTS: list[tuple[str, Callable[[MissData, Theme, Path], None]]] = [
    ("00_predictor_outcome_pie.png", chart_00_outcome_pie),
    ("01_upper_miss_heatmap_256x256.png", chart_01_upper_heatmap),
    ("02_lane_miss_heatmap_256x256.png", chart_02_lane_heatmap),
    ("03_upper_miss_heatstrip_linear.png", chart_03_upper_heatstrip),
    ("04_lane_miss_heatstrip_linear.png", chart_04_lane_heatstrip),
    ("05_upper_miss_polar_ring.png", chart_05_upper_polar),
    ("06_lane_miss_polar_ring.png", chart_06_lane_polar),
    ("07_upper_miss_rate_histogram.png", chart_07_upper_hist),
    ("08_lane_miss_rate_histogram.png", chart_08_lane_hist),
    ("09_upper_miss_ecdf.png", chart_09_upper_ecdf),
    ("10_lane_miss_ecdf.png", chart_10_lane_ecdf),
    ("11_upper_rank_order_curve.png", chart_11_upper_rank),
    ("12_lane_rank_order_curve.png", chart_12_lane_rank),
    ("13_top_25_upper_halves.png", chart_13_top_upper),
    ("14_top_25_lanes.png", chart_14_top_lane),
    ("15_upper_numeric_decile_bar.png", chart_15_upper_deciles),
    ("16_upper_high_nibble_bar.png", chart_16_upper_nibble),
    ("17_lane_trailing_zero_bucket_bar.png", chart_17_lane_v2),
    ("18_lane_mod24_bar.png", chart_18_lane_mod24),
    ("19_source_miss_rate_bar.png", chart_19_source_rates),
    ("20_source_by_upper_decile_heatmap.png", chart_20_source_decile_heatmap),
    ("21_lane_class_by_upper_nibble_heatmap.png", chart_21_lane_class_nibble_heatmap),
    ("22_duplicate_lane_marker_pie.png", chart_22_duplicate_marker_pie),
    ("23_topk_upper_concentration.png", chart_23_topk_concentration),
    ("24_upper_severity_band_pie.png", chart_24_threshold_pie),
    ("25_lorenz_curve_upper_misses.png", chart_25_lorenz),
    ("26_transition_geometry_bar.png", chart_26_transition_geometry),
    ("27_upper_scatter_rolling_mean.png", chart_27_upper_scatter),
    ("28_lane_scatter_rolling_mean.png", chart_28_lane_scatter),
    ("29_duplicate_marker_lollipop.png", chart_29_marker_lollipop),
    ("30_source_duplicate_marker_bar.png", chart_30_source_marker_bar),
    ("31_laneclass_duplicate_marker_bar.png", chart_31_laneclass_marker_bar),
]


def write_manifest(files: dict[str, list[str]], data: MissData) -> None:
    misses = int(data.upper_counts.sum(dtype=np.uint64))
    manifest = {
        "schema": "spc3_predictor_miss_visuals.v1",
        "output_root": str(OUT_ROOT),
        "chart_count_per_mode": len(CHARTS),
        "modes": files,
        "data_sources": {
            "bitmap": str(BITMAP_PATH),
            "pass2": str(PASS2_PATH),
            "pass3": str(PASS3_PATH),
            "count_cache": str(CACHE_PATH),
        },
        "totals": {
            "records": TOTAL_RECORDS,
            "predictor_misses": misses,
            "predictor_hits": TOTAL_RECORDS - misses,
            "miss_rate_pct": pct(misses, TOTAL_RECORDS),
        },
    }
    with (OUT_ROOT / "manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)


def main() -> int:
    data = load_data()
    written: dict[str, list[str]] = {}
    for theme in THEMES:
        mode_dir = OUT_ROOT / theme.name
        mode_dir.mkdir(parents=True, exist_ok=True)
        written[theme.name] = []
        for filename, chart in CHARTS:
            output = mode_dir / filename
            chart(data, theme, output)
            written[theme.name].append(str(output))
            print(f"wrote {output}")
    write_manifest(written, data)
    print(f"wrote {OUT_ROOT / 'manifest.json'}")
    print(f"charts per mode: {len(CHARTS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
