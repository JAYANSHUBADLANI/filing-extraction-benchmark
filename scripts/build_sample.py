"""Build the small committed sample so the tests run with no network and no key.

Three filings are trimmed to their three consolidated statement tables and a
short slice of text. That is enough to exercise the whole rules path end to end
against real filing markup, and small enough to live in version control beside
the code. The cached filings themselves stay out of version control: the full
set is seven hundred megabytes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from filingbench.config import RESULTS_DIR, SAMPLE_DIR  # noqa: E402
from filingbench.prepared import _to_json, load  # noqa: E402
from filingbench.rules import find_statement_table  # noqa: E402

SAMPLE_TICKERS = ("AAPL", "MMM", "PEP")
TEXT_KEEP = 4000


def main() -> int:
    index = pd.read_csv(RESULTS_DIR / "filing_index.csv", dtype={"accession": str})
    truth = pd.read_csv(RESULTS_DIR / "ground_truth.csv", dtype={"accession": str})
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

    kept_ids = []
    for ticker in SAMPLE_TICKERS:
        rows = index[index["ticker"] == ticker]
        if rows.empty:
            continue
        filing = rows.iloc[-1]
        doc = load(filing.doc_id, filing.doc_url)

        wanted = []
        for statement in ("income_statement", "balance_sheet", "cash_flow"):
            table = find_statement_table(doc, statement)
            if table is not None:
                wanted.append(table)
        doc.tables = wanted
        doc.text = doc.text[:TEXT_KEEP]

        payload = _to_json(doc)
        payload["sample_meta"] = {
            "company": filing.company, "ticker": ticker,
            "report_date": filing.report_date, "accession": filing.accession,
            "doc_url": filing.doc_url,
        }
        path = SAMPLE_DIR / f"{filing.doc_id}.json"
        path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        kept_ids.append(filing.doc_id)
        print(f"{ticker}: {len(wanted)} tables, {path.stat().st_size / 1024:.0f} KB")

    subset = truth[truth["doc_id"].isin(kept_ids)]
    subset.to_csv(SAMPLE_DIR / "sample_truth.csv", index=False)
    print(f"sample truth rows: {len(subset)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
