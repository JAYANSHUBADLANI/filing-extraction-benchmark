"""The period end trap, the deduplication rule, and the concept preference order.

These are the three ways a ground truth builder can be silently wrong on this
data, and a silently wrong label set looks exactly like a bad extractor.
"""

import pandas as pd

from filingbench.groundtruth import _select

# Apple's fiscal 2024 annual report carries three revenue facts under one
# fiscal year, differing only in period end, because the statement prints two
# comparatives beside the current year.
REVENUE_FACTS = [
    {"start": "2021-09-26", "end": "2022-09-24", "val": 394328000000,
     "fy": 2024, "fp": "FY", "form": "10-K", "accn": "0000320193-24-000123"},
    {"start": "2022-09-25", "end": "2023-09-30", "val": 383285000000,
     "fy": 2024, "fp": "FY", "form": "10-K", "accn": "0000320193-24-000123"},
    {"start": "2023-10-01", "end": "2024-09-28", "val": 391035000000,
     "fy": 2024, "fp": "FY", "form": "10-K", "accn": "0000320193-24-000123"},
    # The same period, repeated in the following year's report as a comparative.
    {"start": "2023-10-01", "end": "2024-09-28", "val": 391035000000,
     "fy": 2025, "fp": "FY", "form": "10-K", "accn": "0000320193-25-000079"},
]


def test_period_end_decides_not_fiscal_year():
    value, reason = _select(REVENUE_FACTS, "0000320193-24-000123", "2024-09-28",
                            duration=True)
    assert reason == "ok"
    assert value == 391035000000, "matching on fiscal year alone picks a comparative"


def test_a_filing_is_the_source_only_for_its_own_period():
    """The fiscal 2025 filing must not answer for the fiscal 2024 period."""
    value, reason = _select(REVENUE_FACTS, "0000320193-25-000079", "2025-09-27",
                            duration=True)
    assert value is None
    assert reason == "no_period_end_match"


def test_quarterly_facts_are_not_accepted_for_an_annual_field():
    facts = [{"start": "2024-06-30", "end": "2024-09-28", "val": 94930000000,
              "fy": 2024, "fp": "FY", "form": "10-K", "accn": "A"}]
    value, reason = _select(facts, "A", "2024-09-28", duration=True)
    assert value is None
    assert reason == "no_annual_duration"


def test_instant_facts_ignore_the_duration_check():
    facts = [{"start": None, "end": "2024-09-28", "val": 364980000000,
              "fy": 2024, "fp": "FY", "form": "10-K", "accn": "A"}]
    value, reason = _select(facts, "A", "2024-09-28", duration=False)
    assert reason == "ok"
    assert value == 364980000000


def test_conflicting_values_are_refused_rather_than_picked_arbitrarily():
    facts = [
        {"start": None, "end": "2024-09-28", "val": 1.0, "form": "10-K", "accn": "A"},
        {"start": None, "end": "2024-09-28", "val": 2.0, "form": "10-K", "accn": "A"},
    ]
    value, reason = _select(facts, "A", "2024-09-28", duration=False)
    assert value is None
    assert reason == "conflicting_values"


def test_a_fact_from_another_filing_is_not_used():
    value, reason = _select(REVENUE_FACTS, "9999999999-99-999999", "2024-09-28",
                            duration=True)
    assert value is None
    assert reason == "no_fact_in_filing"
