"""Tables and figures. Every number here is computed from the scored cells."""

from __future__ import annotations

import logging

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from .config import FIGURES_DIR, RESULTS_DIR  # noqa: E402
from .scoring import (  # noqa: E402
    failure_breakdown,
    overall,
    per_field_accuracy,
    wilson_interval,
)

log = logging.getLogger(__name__)

METHOD_ORDER = ["rules", "llm", "hybrid"]
OUTCOME_ORDER = ["correct", "wrong_period", "scaling_error", "wrong_row",
                 "parse_error", "not_found"]
OUTCOME_COLOURS = {
    "correct": "#4c7d4c", "wrong_period": "#b8894a", "scaling_error": "#a05252",
    "wrong_row": "#7a5c8f", "parse_error": "#5a7c94", "not_found": "#8c8c8c",
}
METHOD_COLOURS = {"rules": "#4a6fa5", "llm": "#b06a3b", "hybrid": "#4c7d4c"}


def load_latency() -> pd.DataFrame:
    path = RESULTS_DIR / "latency.csv"
    if path.exists():
        return pd.read_csv(path)
    return pd.DataFrame(columns=["doc_id", "field", "method", "seconds"])


def cost_summary(scored: pd.DataFrame, latency: pd.DataFrame | None = None) -> pd.DataFrame:
    """Accuracy, money and wall clock side by side, per method.

    Cost per correct field is the number that matters operationally: a method
    that is cheap per filing but wrong half the time costs more per usable
    figure than an expensive one that is right.
    """
    latency = load_latency() if latency is None else latency
    seconds_by_method = latency.groupby("method")["seconds"].sum().to_dict()
    rows = []
    for method, group in scored.groupby("method"):
        filings = group["doc_id"].nunique()
        correct = int(group["correct"].sum())
        cells = len(group)
        low, high = wilson_interval(correct, cells)
        usd_total = group["usd"].sum(skipna=True)
        has_cost = bool(group["usd"].notna().any() and usd_total > 0)
        # Abstaining and answering wrongly are not the same failure. A method
        # that declines to answer leaves a gap someone can fill; a method that
        # returns a plausible wrong number puts a bad figure in the database and
        # nobody knows. Splitting recall from precision is the only way to see
        # which of the two a method is doing.
        answered = int((group["outcome"] != "not_found").sum())
        rows.append({
            "method": method,
            "filings": filings,
            "cells": cells,
            "correct": correct,
            "accuracy": correct / cells if cells else float("nan"),
            "ci_low": low,
            "ci_high": high,
            "answer_rate": answered / cells if cells else float("nan"),
            "precision_when_answering": correct / answered if answered else float("nan"),
            "seconds_per_filing": (seconds_by_method.get(method, float("nan")) / filings)
                                  if filings else float("nan"),
            "usd_per_filing": (usd_total / filings) if has_cost else float("nan"),
            "usd_per_correct_field": (usd_total / correct) if has_cost and correct else float("nan"),
            "input_tokens_per_filing": group["input_tokens"].sum() / filings if filings else float("nan"),
        })
    frame = pd.DataFrame(rows)
    frame["order"] = frame["method"].map(
        lambda m: METHOD_ORDER.index(m) if m in METHOD_ORDER else 99)
    return frame.sort_values("order").drop(columns="order")


def run_variation(scored: pd.DataFrame) -> pd.DataFrame:
    """Accuracy of each repeat run, so non determinism is visible not hidden."""
    if "run_label" not in scored.columns:
        return pd.DataFrame()
    subset = scored[scored["run_label"].notna()]
    if subset.empty:
        return pd.DataFrame()
    rows = [
        {"method": method, "run_label": label, "cells": len(group),
         "accuracy": group["correct"].mean()}
        for (method, label), group in subset.groupby(["method", "run_label"])
    ]
    frame = pd.DataFrame(rows)
    spread = (frame.groupby("method")["accuracy"]
              .agg(["min", "max", "mean", "std"]).reset_index())
    spread["spread"] = spread["max"] - spread["min"]
    return frame.merge(spread, on="method", how="left")


def figure_accuracy_by_field(scored: pd.DataFrame, path) -> None:
    table = per_field_accuracy(scored)
    fields = sorted(table["field"].unique())
    methods = [m for m in METHOD_ORDER if m in set(table["method"])]

    fig, axes = plt.subplots(1, 2, figsize=(13.5, 5.4),
                             gridspec_kw={"width_ratios": [2.5, 1]})
    ax = axes[0]
    width = 0.8 / max(len(methods), 1)
    for i, method in enumerate(methods):
        subset = table[table["method"] == method].set_index("field")
        heights = [subset["accuracy"].get(f, float("nan")) for f in fields]
        lows = [max(subset["accuracy"].get(f, 0) - subset["ci_low"].get(f, 0), 0) for f in fields]
        highs = [max(subset["ci_high"].get(f, 0) - subset["accuracy"].get(f, 0), 0) for f in fields]
        offsets = [p + i * width - 0.4 + width / 2 for p in range(len(fields))]
        ax.bar(offsets, heights, width, label=method,
               color=METHOD_COLOURS.get(method, "#888888"),
               yerr=[lows, highs], capsize=2, ecolor="#444444", error_kw={"lw": 0.8})
    ax.set_xticks(range(len(fields)))
    ax.set_xticklabels([f.replace("_", " ") for f in fields], rotation=35, ha="right")
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("accuracy against filer tagged XBRL")
    ax.set_title("Accuracy by field, with Wilson intervals")
    ax.legend(frameon=False)
    ax.grid(axis="y", alpha=0.25, lw=0.6)
    ax.set_axisbelow(True)

    ax2 = axes[1]
    pivot = (failure_breakdown(scored)
             .pivot_table(index="method", columns="outcome", values="cells", aggfunc="sum")
             .fillna(0))
    pivot = pivot.reindex([m for m in METHOD_ORDER if m in pivot.index])
    shares = pivot.div(pivot.sum(axis=1), axis=0)
    bottom = [0.0] * len(shares)
    for outcome in OUTCOME_ORDER:
        if outcome not in shares.columns:
            continue
        values = shares[outcome].tolist()
        ax2.bar(shares.index, values, bottom=bottom,
                color=OUTCOME_COLOURS[outcome], label=outcome.replace("_", " "))
        bottom = [b + v for b, v in zip(bottom, values)]
    ax2.set_ylim(0, 1)
    ax2.set_title("Where the cells go")
    ax2.legend(frameon=False, fontsize=8, loc="center left", bbox_to_anchor=(1.02, 0.5))
    ax2.grid(axis="y", alpha=0.25, lw=0.6)
    ax2.set_axisbelow(True)

    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def figure_failure_profile(scored: pd.DataFrame, path) -> None:
    breakdown = failure_breakdown(scored)
    methods = [m for m in METHOD_ORDER if m in set(breakdown["method"])]
    fig, axes = plt.subplots(1, len(methods), figsize=(5.6 * len(methods), 4.8),
                             squeeze=False)
    for ax, method in zip(axes[0], methods):
        subset = breakdown[breakdown["method"] == method]
        pivot = (subset.pivot_table(index="field", columns="outcome",
                                    values="share", aggfunc="sum").fillna(0))
        pivot = pivot[[c for c in OUTCOME_ORDER if c in pivot.columns]]
        bottom = [0.0] * len(pivot)
        for outcome in pivot.columns:
            ax.barh(pivot.index, pivot[outcome], left=bottom,
                    color=OUTCOME_COLOURS[outcome], label=outcome.replace("_", " "))
            bottom = [b + v for b, v in zip(bottom, pivot[outcome])]
        ax.set_xlim(0, 1)
        ax.set_title(f"{method}: outcome mix by field")
        ax.grid(axis="x", alpha=0.25, lw=0.6)
        ax.set_axisbelow(True)
    axes[0][0].legend(frameon=False, fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


SPLIT_COLOURS = {"dev": "#4a6fa5", "test": "#b06a3b"}


def figure_generalisation(scored: pd.DataFrame, path) -> None:
    """Development against test, per field and per method.

    With a single method this is the result worth looking at. The development
    companies are the ones whose layouts the extractor was written against, so
    the distance between the two bars is the part of the development score that
    is fitting rather than reading.
    """
    methods = [m for m in METHOD_ORDER if m in set(scored["method"])]
    fig, axes = plt.subplots(1, len(methods), figsize=(7.6 * len(methods), 5.0),
                             squeeze=False)
    for ax, method in zip(axes[0], methods):
        subset = scored[scored["method"] == method]
        table = per_field_accuracy(subset.assign(method=subset["split"]))
        fields = sorted(table["field"].unique())
        width = 0.38
        for i, split_name in enumerate(("dev", "test")):
            part = table[table["method"] == split_name].set_index("field")
            heights = [part["accuracy"].get(f, float("nan")) for f in fields]
            lows = [max(part["accuracy"].get(f, 0) - part["ci_low"].get(f, 0), 0) for f in fields]
            highs = [max(part["ci_high"].get(f, 0) - part["accuracy"].get(f, 0), 0) for f in fields]
            offsets = [p + i * width - width / 2 for p in range(len(fields))]
            ax.bar(offsets, heights, width, label=split_name,
                   color=SPLIT_COLOURS[split_name], yerr=[lows, highs],
                   capsize=2, ecolor="#444444", error_kw={"lw": 0.8})
        overall_dev = subset[subset["split"] == "dev"]["correct"].mean()
        overall_test = subset[subset["split"] == "test"]["correct"].mean()
        ax.axhline(overall_dev, color=SPLIT_COLOURS["dev"], ls="--", lw=1, alpha=0.7)
        ax.axhline(overall_test, color=SPLIT_COLOURS["test"], ls="--", lw=1, alpha=0.7)
        ax.set_xticks(range(len(fields)))
        ax.set_xticklabels([f.replace("_", " ") for f in fields], rotation=35, ha="right")
        ax.set_ylim(0, 1.05)
        ax.set_ylabel("accuracy against filer tagged XBRL")
        ax.set_title(f"{method}: seen layouts {overall_dev:.3f} against "
                     f"unseen layouts {overall_test:.3f}")
        ax.legend(frameon=False, title="company split")
        ax.grid(axis="y", alpha=0.25, lw=0.6)
        ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def build_all() -> None:
    scored_path = RESULTS_DIR / "scored.csv"
    if not scored_path.exists():
        log.warning("no scored.csv yet, nothing to report")
        return
    scored = pd.read_csv(scored_path)
    latency = load_latency()
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)

    cost_summary(scored, latency).to_csv(RESULTS_DIR / "comparison.csv", index=False)
    per_field_accuracy(scored).to_csv(RESULTS_DIR / "accuracy_by_field.csv", index=False)
    failure_breakdown(scored).to_csv(RESULTS_DIR / "failure_breakdown.csv", index=False)
    overall(scored).to_csv(RESULTS_DIR / "accuracy_overall.csv", index=False)

    variation = run_variation(scored)
    if not variation.empty:
        variation.to_csv(RESULTS_DIR / "run_variation.csv", index=False)

    for split_name in ("dev", "test"):
        subset = scored[scored["split"] == split_name]
        if subset.empty:
            continue
        per_field_accuracy(subset).to_csv(
            RESULTS_DIR / f"accuracy_by_field_{split_name}.csv", index=False)
        overall(subset).to_csv(
            RESULTS_DIR / f"accuracy_overall_{split_name}.csv", index=False)
        subset_latency = latency[
            latency.set_index(["doc_id", "field"]).index.isin(
                subset.set_index(["doc_id", "field"]).index)]
        cost_summary(subset, subset_latency).to_csv(
            RESULTS_DIR / f"comparison_{split_name}.csv", index=False)

    figure_accuracy_by_field(scored, FIGURES_DIR / "accuracy_by_field.png")
    figure_failure_profile(scored, FIGURES_DIR / "failure_profile.png")
    figure_generalisation(scored, FIGURES_DIR / "generalisation.png")
