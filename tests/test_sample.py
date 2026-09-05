"""End to end on real filing markup, with no network call and no API key.

The full cache is seven hundred megabytes and stays out of version control, so
three filings are trimmed to their statements and committed under data/sample.
That is enough to catch a regression in the parts that matter: statement
selection, row resolution, column resolution and scaling, against markup written
by three different filing agents rather than markup written by me.
"""

import json

import pandas as pd
import pytest

from filingbench.config import SAMPLE_DIR, load_fields
from filingbench.prepared import _from_json
from filingbench.rules import extract_document
from filingbench.scoring import values_agree

# macOS writes an AppleDouble sidecar beside every file on a filesystem with no
# native resource fork, and those sidecars are binary and end in .json too.
SAMPLE_FILES = sorted(p for p in SAMPLE_DIR.glob("*.json")
                      if not p.name.startswith("._"))


@pytest.mark.skipif(not SAMPLE_FILES, reason="run scripts/build_sample.py first")
def test_sample_documents_have_their_statements():
    for path in SAMPLE_FILES:
        payload = json.loads(path.read_text(encoding="utf-8"))
        doc = _from_json(payload)
        assert doc.tables, f"{path.name} has no tables"
        assert doc.ix_facts_removed > 0, "a modern filing is inline XBRL tagged"


@pytest.mark.skipif(not SAMPLE_FILES, reason="run scripts/build_sample.py first")
def test_rules_extractor_agrees_with_the_filer_on_the_sample():
    truth = pd.read_csv(SAMPLE_DIR / "sample_truth.csv", dtype={"accession": str})
    fields = load_fields()

    checked = correct = 0
    misses = []
    for path in SAMPLE_FILES:
        payload = json.loads(path.read_text(encoding="utf-8"))
        doc = _from_json(payload)
        report_date = payload["sample_meta"]["report_date"]
        expected = truth[truth["doc_id"] == doc.doc_id].set_index("field")

        for item in extract_document(doc, report_date):
            if item.field not in expected.index:
                continue
            checked += 1
            target = float(expected.loc[item.field, "true_value"])
            unit = fields[item.field].unit
            if item.value is not None and values_agree(item.value, target, unit):
                correct += 1
            else:
                misses.append(
                    f"{payload['sample_meta']['ticker']} {item.field} "
                    f"got {item.value} want {target} ({item.status})")

    assert checked > 0
    # A guard against regression, not a restatement of the headline result. The
    # measured accuracy on the full sample lives in results/, not in a test.
    assert correct / checked >= 0.85, "\n".join(misses)
