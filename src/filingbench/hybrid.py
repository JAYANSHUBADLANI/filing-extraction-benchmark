"""Phase 4: rules first, the model only on what rules could not resolve.

The routing rule is deliberately narrow. The model is asked only about cells
where the rules extractor returned nothing at all, never about cells where it
returned a number. That is the only rule a real pipeline can implement, because
at run time nobody knows which of the returned numbers are wrong: if the
routing could see the score it would be reading the answer key. So the hybrid
inherits every silent rules error, and its ceiling is the rules accuracy plus
whatever the model recovers from the cells rules abstained on. Saying that in
advance is the difference between a measurement and a demonstration.
"""

from __future__ import annotations

import pandas as pd

UNRESOLVED_STATUSES = {"statement_not_found", "row_not_found", "column_unresolved",
                       "no_value_in_row", "not_attempted"}


def unresolved_by_document(rules_predictions: pd.DataFrame) -> dict[str, list[str]]:
    """The cells a rules run abstained on, keyed by document."""
    unresolved = rules_predictions[
        rules_predictions["predicted"].isna()
        | rules_predictions["status"].isin(UNRESOLVED_STATUSES)
    ]
    return {
        doc_id: sorted(group["field"].tolist())
        for doc_id, group in unresolved.groupby("doc_id")
    }


def combine(rules_predictions: pd.DataFrame, llm_predictions: pd.DataFrame) -> pd.DataFrame:
    """Rules answers kept, model answers filled into the gaps.

    Cost accumulates: a hybrid cell costs the rules attempt plus, where the
    model was called, the model call. The rules attempt is close to free but it
    is not zero, and reporting it as zero would flatter the hybrid.
    """
    resolved = rules_predictions[
        rules_predictions["predicted"].notna()
        & ~rules_predictions["status"].isin(UNRESOLVED_STATUSES)
    ].copy()

    gaps = unresolved_by_document(rules_predictions)
    wanted = {(doc_id, field) for doc_id, fields in gaps.items() for field in fields}

    if llm_predictions is None or llm_predictions.empty:
        filled = llm_predictions.copy() if llm_predictions is not None else pd.DataFrame()
    else:
        keys = list(zip(llm_predictions["doc_id"], llm_predictions["field"]))
        filled = llm_predictions[[k in wanted for k in keys]].copy()

    if not filled.empty:
        rules_cost = rules_predictions.set_index(["doc_id", "field"])
        for column in ("seconds", "input_tokens", "output_tokens", "usd"):
            if column not in filled.columns:
                continue
            series = rules_cost[column]
            extra = [series.get(key, 0.0) for key in zip(filled["doc_id"], filled["field"])]
            filled[column] = filled[column].to_numpy() + pd.Series(extra).to_numpy()

    combined = pd.concat([resolved, filled], ignore_index=True) if not filled.empty else resolved

    # Cells neither method answered still have to appear, or the hybrid would be
    # scored on a smaller, easier set of cells than the methods it is compared to.
    answered = set(zip(combined["doc_id"], combined["field"]))
    remaining = [k not in answered
                 for k in zip(rules_predictions["doc_id"], rules_predictions["field"])]
    missing = rules_predictions[remaining].copy()
    if not missing.empty:
        combined = pd.concat([combined, missing], ignore_index=True)

    combined["method"] = "hybrid"
    return combined
