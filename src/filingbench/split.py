"""The development and test split, frozen before any extractor was written.

The split is by company, not by filing. Layout is a property of the company and
its filing agent, so splitting by filing would leak a company's house style from
development into test and would flatter any method that learned that style.

Apple is placed in development by hand and not by the draw. Its fiscal 2024
annual report was the document read while designing the table parser, before
this split existed, so it cannot honestly sit on the test side. Every other
company is assigned by a stream derived from the single root seed.
"""

from __future__ import annotations

import pandas as pd

from .config import RESULTS_DIR, load_universe, rng

DEV_COMPANY_COUNT = 10
DISCLOSED_DEV_CIK = 320193  # Apple, inspected during parser design


def company_split() -> pd.DataFrame:
    companies = sorted(load_universe(), key=lambda c: int(c["cik"]))
    remaining = [c for c in companies if int(c["cik"]) != DISCLOSED_DEV_CIK]

    generator = rng(1)
    order = generator.permutation(len(remaining))
    drawn = {int(remaining[i]["cik"]) for i in order[: DEV_COMPANY_COUNT - 1]}
    dev = drawn | {DISCLOSED_DEV_CIK}

    return pd.DataFrame(
        [
            {
                "cik": int(c["cik"]),
                "ticker": c["ticker"],
                "company": c["name"],
                "industry": c["industry"],
                "split": "dev" if int(c["cik"]) in dev else "test",
            }
            for c in companies
        ]
    ).sort_values(["split", "company"])


def write() -> pd.DataFrame:
    frame = company_split()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    frame.to_csv(RESULTS_DIR / "split.csv", index=False)
    return frame


def attach(frame: pd.DataFrame) -> pd.DataFrame:
    split = company_split()[["cik", "split"]]
    return frame.merge(split, on="cik", how="left")
