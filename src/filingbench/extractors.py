"""Runners that turn an extractor into a predictions table, with timing."""

from __future__ import annotations

import logging
import time
from dataclasses import asdict

import pandas as pd

from .config import load_fields
from .prepared import load as load_prepared
from .rules import extract_document
from .sec import SecClient

log = logging.getLogger(__name__)


def run_rules(index: pd.DataFrame, client: SecClient | None = None) -> pd.DataFrame:
    client = client or SecClient()
    records: list[dict] = []
    for filing in index.itertuples():
        started = time.perf_counter()
        doc = load_prepared(filing.doc_id, filing.doc_url, client)
        extractions = extract_document(doc, filing.report_date)
        elapsed = time.perf_counter() - started
        per_field_seconds = elapsed / max(len(extractions), 1)
        for item in extractions:
            record = asdict(item)
            record.update({
                "doc_id": filing.doc_id,
                "method": "rules",
                "predicted": record.pop("value"),
                "seconds": per_field_seconds,
                "input_tokens": 0,
                "output_tokens": 0,
                "usd": 0.0,
            })
            records.append(record)
    frame = pd.DataFrame(records)
    return frame.rename(columns={"field": "field"})


def empty_predictions() -> pd.DataFrame:
    columns = ["doc_id", "field", "predicted", "status", "method", "seconds",
               "input_tokens", "output_tokens", "usd"]
    return pd.DataFrame(columns=columns)


def field_keys() -> list[str]:
    return list(load_fields().keys())
