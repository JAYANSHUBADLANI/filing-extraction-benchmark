"""Phase 4: read the cases every method got wrong and characterise them.

A failure table that only says "wrong" is not analysis. For each sampled error
this pulls the statement row the extractor actually read and the rows around it,
so the case can be judged: whether the document was ambiguous, whether the
filer's own tagging was unusual, or whether the extractor simply misread a page
that a person would have read correctly. That last distinction is the one that
decides whether a method is worth fixing or worth replacing.
"""

from __future__ import annotations

import pandas as pd

from .config import RESULTS_DIR, load_fields, rng
from .prepared import load as load_prepared
from .rules import find_statement_table, row_label
from .sec import SecClient

SAMPLE_PER_GROUP = 4
CONTEXT_ROWS = 2


def sample_failures(scored: pd.DataFrame, per_group: int = SAMPLE_PER_GROUP) -> pd.DataFrame:
    """A reproducible sample of wrong cells, spread across method and outcome.

    Sampling is drawn from the documented root seed rather than taken from the
    head of the table, because the head is ordered by company and would make the
    sample a study of whichever company sorts first.
    """
    wrong = scored[~scored["correct"]].copy()
    if wrong.empty:
        return wrong
    generator = rng(2)
    picks = []
    for _, group in wrong.groupby(["method", "outcome"]):
        size = min(per_group, len(group))
        chosen = generator.choice(len(group), size=size, replace=False)
        picks.append(group.iloc[sorted(chosen)])
    return pd.concat(picks, ignore_index=True)


def annotate(sample: pd.DataFrame, index: pd.DataFrame,
             client: SecClient | None = None) -> pd.DataFrame:
    """Attach the printed rows around whatever the extractor read."""
    client = client or SecClient()
    fields = load_fields()
    urls = index.set_index("doc_id")["doc_url"].to_dict()

    notes = []
    for row in sample.itertuples():
        spec = fields[row.field]
        note = {"statement_table": None, "read_row": None, "nearby_rows": None}
        url = urls.get(row.doc_id)
        if url:
            doc = load_prepared(row.doc_id, url, client)
            table = find_statement_table(doc, spec.statement)
            if table is not None:
                note["statement_table"] = table.index
                labels = [row_label(r) for r in table.rows]
                target = None
                if isinstance(row.matched_label, str):
                    plain = row.matched_label.replace("[section] ", "")
                    target = next((i for i, l in enumerate(labels) if l == plain), None)
                if target is not None:
                    note["read_row"] = " | ".join(
                        c for c in table.rows[target] if c.strip())
                    low = max(target - CONTEXT_ROWS, 0)
                    high = min(target + CONTEXT_ROWS + 1, len(table.rows))
                    note["nearby_rows"] = " // ".join(
                        labels[i] for i in range(low, high) if labels[i])
                else:
                    note["nearby_rows"] = " // ".join(l for l in labels if l)[:400]
        notes.append(note)

    annotated = sample.copy().reset_index(drop=True)
    for key in ("statement_table", "read_row", "nearby_rows"):
        annotated[key] = [n[key] for n in notes]
    return annotated


def run(scored: pd.DataFrame, index: pd.DataFrame,
        client: SecClient | None = None) -> pd.DataFrame:
    sample = sample_failures(scored)
    if sample.empty:
        return sample
    annotated = annotate(sample, index, client)
    columns = ["method", "outcome", "company", "report_date", "field", "concept",
               "predicted", "true_value", "status", "matched_label",
               "column_basis", "scale", "statement_table", "read_row", "nearby_rows"]
    annotated = annotated[[c for c in columns if c in annotated.columns]]
    annotated.to_csv(RESULTS_DIR / "failure_sample.csv", index=False)
    return annotated
