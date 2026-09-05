"""Cache of the stripped, parsed form of each filing.

Parsing a four megabyte filing takes seconds, and both extractors plus the
tests need the same parsed form many times over, so the result is written to
the cache once. This lives under data/cache and is derived, never committed.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .config import CACHE_DIR
from .docprep import DocTable, PreparedDocument, prepare
from .sec import SecClient, cache_path_for

log = logging.getLogger(__name__)

PREPARED_DIR = CACHE_DIR / "prepared"


def _path(doc_id: str) -> Path:
    return PREPARED_DIR / f"{doc_id}.json"


def _to_json(doc: PreparedDocument) -> dict:
    return {
        "doc_id": doc.doc_id,
        "ix_facts_removed": doc.ix_facts_removed,
        "raw_chars": doc.raw_chars,
        "stripped_chars": doc.stripped_chars,
        "text": doc.text,
        "tables": [
            {
                "index": t.index,
                "rows": t.rows,
                "preamble": t.preamble,
                "statement": t.statement,
                "scale": t.scale,
                "scale_phrase": t.scale_phrase,
            }
            for t in doc.tables
        ],
    }


def _from_json(payload: dict) -> PreparedDocument:
    doc = PreparedDocument(
        doc_id=payload["doc_id"],
        text=payload["text"],
        ix_facts_removed=payload["ix_facts_removed"],
        raw_chars=payload["raw_chars"],
        stripped_chars=payload["stripped_chars"],
    )
    doc.tables = [
        DocTable(
            index=t["index"], rows=[[str(c) for c in r] for r in t["rows"]],
            preamble=t["preamble"], statement=t["statement"],
            scale=t["scale"], scale_phrase=t["scale_phrase"],
        )
        for t in payload["tables"]
    ]
    return doc


def load(doc_id: str, doc_url: str, client: SecClient | None = None,
         force: bool = False) -> PreparedDocument:
    path = _path(doc_id)
    if path.exists() and not force:
        return _from_json(json.loads(path.read_text(encoding="utf-8")))

    client = client or SecClient()
    html = client.get_text(doc_url)
    doc = prepare(html, doc_id=doc_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_to_json(doc)), encoding="utf-8")
    return doc


def build_all(index, client: SecClient | None = None, force: bool = False) -> dict:
    client = client or SecClient()
    stats = {"documents": 0, "tables": 0, "ix_facts_removed": 0}
    for row in index.itertuples():
        doc = load(row.doc_id, row.doc_url, client, force=force)
        stats["documents"] += 1
        stats["tables"] += len(doc.tables)
        stats["ix_facts_removed"] += doc.ix_facts_removed
    return stats


def raw_html_path(doc_url: str) -> Path:
    return cache_path_for(doc_url)
