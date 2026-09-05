"""One scorer, used identically by every method.

A single accuracy number hides what an extractor actually does wrong, so each
cell is classified into a failure kind. The kinds are chosen because they carry
different operational meaning:

  correct        the value agrees with the filer's own tagging
  scaling_error  the right figure off by a power of ten, the caption was missed
  wrong_period   the right figure from the wrong column, a comparative year
  wrong_row      a different number entirely, the label resolved to a bad row
  not_found      nothing returned, the statement or row was never located
  parse_error    a row was located but its digits could not be read

A scaling error and a wrong row are both incorrect, but they mean different
things about the extractor and call for different fixes, so they are counted
apart.
"""

from __future__ import annotations

import math

import pandas as pd

RELATIVE_TOLERANCE = 0.005
ABSOLUTE_TOLERANCE_PER_SHARE = 0.005
POWERS_OF_TEN = [10 ** k for k in (-9, -6, -3, -2, -1, 1, 2, 3, 6, 9)]

NOT_FOUND_STATUSES = {"statement_not_found", "row_not_found", "column_unresolved",
                      "not_attempted", "llm_no_answer", "llm_error"}
PARSE_ERROR_STATUSES = {"no_value_in_row", "unparseable_response"}


def values_agree(predicted: float, truth: float, unit: str) -> bool:
    if unit == "USD/shares":
        if abs(predicted - truth) <= ABSOLUTE_TOLERANCE_PER_SHARE:
            return True
    if truth == 0:
        return abs(predicted) <= ABSOLUTE_TOLERANCE_PER_SHARE
    return abs(predicted - truth) / abs(truth) <= RELATIVE_TOLERANCE


def is_scaling_error(predicted: float, truth: float) -> bool:
    if predicted == 0 or truth == 0:
        return False
    ratio = predicted / truth
    return any(math.isclose(ratio, power, rel_tol=RELATIVE_TOLERANCE)
               for power in POWERS_OF_TEN)


def classify(predicted: float | None, status: str, truth: float, unit: str,
             other_period_values: list[float]) -> str:
    if predicted is None:
        if status in PARSE_ERROR_STATUSES:
            return "parse_error"
        return "not_found"
    if values_agree(predicted, truth, unit):
        return "correct"
    if is_scaling_error(predicted, truth):
        return "scaling_error"
    for other in other_period_values:
        if values_agree(predicted, other, unit):
            return "wrong_period"
    return "wrong_row"


def score(predictions: pd.DataFrame, truth: pd.DataFrame,
          all_periods: pd.DataFrame) -> pd.DataFrame:
    """Join predictions onto ground truth and label every cell.

    Only cells that have ground truth are scored. A field a company never
    tagged, because the subtotal is not printed on its balance sheet, is not a
    question anyone can be marked wrong on.
    """
    # Diagnostic columns ride along where an extractor produced them, so the
    # failure analysis can say which table and which printed label a wrong
    # answer came from rather than only that it was wrong.
    diagnostics = [c for c in ("table_index", "matched_label", "scale",
                               "column_index", "column_period", "column_basis",
                               "model", "prompt_version", "context_mode",
                               "truncated", "run_label")
                   if c in predictions.columns]
    merged = truth.merge(
        predictions[["doc_id", "field", "predicted", "status", "method", *diagnostics]],
        on=["doc_id", "field"], how="left",
    )
    merged["status"] = merged["status"].fillna("not_attempted")
    merged["method"] = merged["method"].ffill().bfill()

    lookup: dict[tuple[int, str], list[tuple[str, float]]] = {}
    for row in all_periods.itertuples():
        lookup.setdefault((row.cik, row.field), []).append((row.period_end, row.value))

    outcomes = []
    for row in merged.itertuples():
        others = [v for end, v in lookup.get((row.cik, row.field), [])
                  if end != row.report_date]
        outcomes.append(
            classify(row.predicted if pd.notna(row.predicted) else None,
                     row.status, row.true_value, row.unit, others)
        )
    merged["outcome"] = outcomes
    merged["correct"] = merged["outcome"] == "correct"
    return merged


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval. Reported because a per field cell count is small."""
    if total == 0:
        return (float("nan"), float("nan"))
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def per_field_accuracy(scored: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (method, field), group in scored.groupby(["method", "field"]):
        successes, total = int(group["correct"].sum()), len(group)
        low, high = wilson_interval(successes, total)
        rows.append({
            "method": method, "field": field, "n": total, "correct": successes,
            "accuracy": successes / total, "ci_low": low, "ci_high": high,
        })
    return pd.DataFrame(rows).sort_values(["method", "field"])


def failure_breakdown(scored: pd.DataFrame) -> pd.DataFrame:
    table = (scored.groupby(["method", "field", "outcome"]).size()
             .rename("cells").reset_index())
    totals = table.groupby(["method", "field"])["cells"].transform("sum")
    table["share"] = (table["cells"] / totals).round(4)
    return table.sort_values(["method", "field", "cells"], ascending=[True, True, False])


def overall(scored: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for method, group in scored.groupby("method"):
        successes, total = int(group["correct"].sum()), len(group)
        low, high = wilson_interval(successes, total)
        rows.append({"method": method, "cells": total, "correct": successes,
                     "accuracy": successes / total, "ci_low": low, "ci_high": high})
    return pd.DataFrame(rows).sort_values("accuracy", ascending=False)
