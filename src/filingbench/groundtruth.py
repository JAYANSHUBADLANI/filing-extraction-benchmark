"""Phase 1: join XBRL facts to filings to get labels without hand annotation.

What ground truth means here, stated plainly: XBRL facts are tagged by the
filer, not by an independent auditor of the tagging. A value in this table is
what the company asserted it reported under that accounting concept. Accuracy
measured against it is agreement with the filer, not agreement with truth.
Where a filer tagged something oddly that is a property of the data and it
shows up in the error analysis rather than being quietly removed.

Two traps handled here.

Period matching. An annual report carries comparatives, so the same concept has
several facts under one fiscal year that differ only in period end. Matching on
fiscal year alone silently picks the wrong year for roughly half the sample.
Every match here is on the period end date.

Deduplication. The same period appears in more than one filing, since next
year's report repeats this year's figure as a comparative. The rule adopted is
that a filing is the source of truth only for its own reporting period: a fact
counts only when its period end equals the filing's report date. A later
restatement of an earlier period is therefore never used, which is correct for
this benchmark because the question is whether an extractor can read the number
printed on the page of that filing.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import date

import pandas as pd

from .config import RESULTS_DIR, load_fields
from .sec import SecClient, companyfacts_url

log = logging.getLogger(__name__)

DURATION_MIN_DAYS = 300
DURATION_MAX_DAYS = 400


def _iso(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _facts_for_concept(payload: dict, concept: str, unit: str) -> list[dict]:
    gaap = payload.get("facts", {}).get("us-gaap", {})
    entry = gaap.get(concept)
    if not entry:
        return []
    return entry.get("units", {}).get(unit, [])


def _select(facts: list[dict], accession: str, report_date: str, duration: bool) -> tuple[float | None, str]:
    """Pick the fact this filing reports for its own period end.

    Returns the value and a short reason code so that misses are explainable
    rather than silently absent.
    """
    same_filing = [f for f in facts if f.get("accn") == accession]
    if not same_filing:
        return None, "no_fact_in_filing"

    matched = [f for f in same_filing if f.get("end") == report_date]
    if not matched:
        return None, "no_period_end_match"

    if duration:
        annual = []
        for fact in matched:
            start, end = _iso(fact.get("start", "")), _iso(fact.get("end", ""))
            if start and end and DURATION_MIN_DAYS <= (end - start).days <= DURATION_MAX_DAYS:
                annual.append(fact)
        if not annual:
            return None, "no_annual_duration"
        matched = annual
    else:
        matched = [f for f in matched if not f.get("start")] or matched

    values = {round(float(f["val"]), 6) for f in matched}
    if len(values) > 1:
        return None, "conflicting_values"
    return float(matched[0]["val"]), "ok"


def build(index: pd.DataFrame, client: SecClient | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return the long ground truth table and a per filing coverage table."""
    client = client or SecClient()
    fields = load_fields()

    rows: list[dict] = []
    misses: list[dict] = []

    for cik, group in index.groupby("cik"):
        payload = client.get_json(companyfacts_url(int(cik)))
        for filing in group.itertuples():
            for key, spec in fields.items():
                value, reason, concept_used = None, "no_concept_present", None
                reasons: list[str] = []
                for concept in spec.concepts:
                    facts = _facts_for_concept(payload, concept, spec.unit)
                    if not facts:
                        continue
                    candidate, why = _select(
                        facts, filing.accession, filing.report_date,
                        duration=spec.period_type == "duration",
                    )
                    if candidate is not None:
                        value, reason, concept_used = candidate, "ok", concept
                        break
                    reasons.append(f"{concept}:{why}")
                if value is None:
                    reason = reasons[0].split(":")[-1] if reasons else "no_concept_present"
                    misses.append({
                        "doc_id": filing.doc_id, "company": filing.company,
                        "report_date": filing.report_date, "field": key, "reason": reason,
                    })
                    continue
                rows.append({
                    "doc_id": filing.doc_id,
                    "cik": int(cik),
                    "company": filing.company,
                    "ticker": filing.ticker,
                    "report_date": filing.report_date,
                    "filing_date": filing.filing_date,
                    "accession": filing.accession,
                    "field": key,
                    "concept": concept_used,
                    "unit": spec.unit,
                    "true_value": value,
                })

    truth = pd.DataFrame(rows)
    miss_frame = pd.DataFrame(misses)

    per_doc = truth.groupby("doc_id").size().rename("fields_found") if not truth.empty else pd.Series(dtype=int)
    coverage = index[["doc_id", "company", "ticker", "report_date", "accession"]].copy()
    coverage = coverage.merge(per_doc, on="doc_id", how="left")
    coverage["fields_found"] = coverage["fields_found"].fillna(0).astype(int)
    coverage["complete"] = coverage["fields_found"] == len(fields)

    return truth, coverage, miss_frame


def concept_match_counts(truth: pd.DataFrame) -> pd.DataFrame:
    """How often each acceptable concept was the one that matched."""
    counts = (truth.groupby(["field", "concept"]).size().rename("filings").reset_index())
    totals = counts.groupby("field")["filings"].transform("sum")
    counts["share"] = (counts["filings"] / totals).round(4)
    return counts.sort_values(["field", "filings"], ascending=[True, False])


def run(index: pd.DataFrame, client: SecClient | None = None) -> pd.DataFrame:
    truth, coverage, misses = build(index, client)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    truth.to_csv(RESULTS_DIR / "ground_truth.csv", index=False)
    coverage.to_csv(RESULTS_DIR / "ground_truth_coverage.csv", index=False)
    misses.to_csv(RESULTS_DIR / "ground_truth_misses.csv", index=False)
    concept_match_counts(truth).to_csv(RESULTS_DIR / "concept_match_counts.csv", index=False)
    log.info(
        "ground truth: %d rows over %d filings, %d filings complete",
        len(truth), coverage.shape[0], int(coverage["complete"].sum()),
    )
    if not misses.empty:
        log.info("miss reasons: %s", dict(Counter(misses["reason"])))
    return truth


def all_period_values(index: pd.DataFrame, client: SecClient | None = None) -> pd.DataFrame:
    """Every annual value a company reported for a field, at any period end.

    Used only by the scorer, to tell a genuinely wrong number apart from the
    right number read out of the wrong column. Reading the prior year comparative
    instead of the current year is a specific, common failure and deserves its
    own category rather than being lumped in with nonsense.
    """
    client = client or SecClient()
    fields = load_fields()
    rows: list[dict] = []
    for cik in sorted(index["cik"].unique()):
        payload = client.get_json(companyfacts_url(int(cik)))
        for key, spec in fields.items():
            seen: set[tuple[str, float]] = set()
            for concept in spec.concepts:
                for fact in _facts_for_concept(payload, concept, spec.unit):
                    if fact.get("form") != "10-K" or not fact.get("end"):
                        continue
                    if spec.period_type == "duration":
                        start, end = _iso(fact.get("start", "")), _iso(fact["end"])
                        if not start or not end:
                            continue
                        if not DURATION_MIN_DAYS <= (end - start).days <= DURATION_MAX_DAYS:
                            continue
                    elif fact.get("start"):
                        continue
                    item = (fact["end"], float(fact["val"]))
                    if item in seen:
                        continue
                    seen.add(item)
                    rows.append({"cik": int(cik), "field": key,
                                 "period_end": item[0], "value": item[1]})
    return pd.DataFrame(rows).drop_duplicates()
