"""Score a set of predictions and write every result table to results/."""

from __future__ import annotations

import logging

import pandas as pd

from .config import RESULTS_DIR
from .scoring import failure_breakdown, overall, per_field_accuracy, score
from .split import attach

log = logging.getLogger(__name__)


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    index = pd.read_csv(RESULTS_DIR / "filing_index.csv", dtype={"accession": str})
    truth = pd.read_csv(RESULTS_DIR / "ground_truth.csv", dtype={"accession": str})
    all_periods = pd.read_csv(RESULTS_DIR / "truth_all_periods.csv")
    return index, truth, all_periods


def evaluate(predictions: pd.DataFrame, truth: pd.DataFrame,
             all_periods: pd.DataFrame) -> pd.DataFrame:
    scored = score(predictions, truth, all_periods)
    scored = attach(scored)
    cost = predictions.groupby(["doc_id", "field", "method"], as_index=False)[
        ["seconds", "input_tokens", "output_tokens", "usd"]
    ].sum()
    return scored.merge(cost, on=["doc_id", "field", "method"], how="left")


def write_tables(scored: pd.DataFrame, suffix: str = "") -> None:
    """Split the measured wall clock out of the extraction result.

    Timing is the one column that cannot be identical between two runs of the
    same code on the same input, so it lives in its own file. Everything the
    extractors decided is then byte for byte reproducible, and a diff of
    scored.csv across two runs is a real regression check rather than a
    reading of how busy the machine was.
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    tag = f"_{suffix}" if suffix else ""
    if "seconds" in scored.columns:
        scored[["doc_id", "field", "method", "seconds"]].to_csv(
            RESULTS_DIR / f"latency{tag}.csv", index=False)
        scored = scored.drop(columns="seconds")
    scored.to_csv(RESULTS_DIR / f"scored{tag}.csv", index=False)
    per_field_accuracy(scored).to_csv(RESULTS_DIR / f"accuracy_by_field{tag}.csv", index=False)
    failure_breakdown(scored).to_csv(RESULTS_DIR / f"failure_breakdown{tag}.csv", index=False)
    overall(scored).to_csv(RESULTS_DIR / f"accuracy_overall{tag}.csv", index=False)


def summarise(scored: pd.DataFrame) -> str:
    lines = []
    for method, group in scored.groupby("method"):
        for split_name in ("dev", "test", "all"):
            subset = group if split_name == "all" else group[group["split"] == split_name]
            if subset.empty:
                continue
            lines.append(
                f"{method:8s} {split_name:4s} n={len(subset):5d} "
                f"accuracy={subset['correct'].mean():.4f}"
            )
    return "\n".join(lines)
