"""Run the rules half twice and prove the two result tables are identical.

The language model half cannot pass a check like this, which is exactly why its
run to run variation is reported rather than hidden. The rules half has no
excuse, so it is checked.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from filingbench.config import RESULTS_DIR  # noqa: E402
from filingbench.evaluate import evaluate, load_inputs  # noqa: E402
from filingbench.extractors import run_rules  # noqa: E402

DETERMINISTIC_COLUMNS = ["doc_id", "field", "predicted", "status", "outcome",
                         "true_value", "matched_label", "table_index", "scale",
                         "column_index", "column_basis"]


def digest(frame: pd.DataFrame) -> str:
    columns = [c for c in DETERMINISTIC_COLUMNS if c in frame.columns]
    ordered = frame[columns].sort_values(["doc_id", "field"]).reset_index(drop=True)
    return hashlib.sha256(ordered.to_csv(index=False).encode("utf-8")).hexdigest()


def main() -> int:
    index, truth, all_periods = load_inputs()
    digests = []
    for attempt in (1, 2):
        scored = evaluate(run_rules(index), truth, all_periods)
        digests.append(digest(scored))
        print(f"run {attempt}: {digests[-1]}  accuracy={scored['correct'].mean():.4f}")

    if digests[0] == digests[1]:
        print("identical")
        (RESULTS_DIR / "determinism.txt").write_text(
            f"rules extraction digest: {digests[0]}\ntwo consecutive runs identical\n",
            encoding="utf-8")
        return 0
    print("DIFFERENT")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
