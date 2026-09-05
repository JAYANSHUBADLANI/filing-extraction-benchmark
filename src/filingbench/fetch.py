"""Phase 1: build the filing index and pull every document to disk.

Three things get cached: the company submission history, the primary document
of each annual report, and the company's full XBRL fact set. Rerunning does no
network work because every URL is served from the cache.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

import pandas as pd

from .config import RESULTS_DIR, ensure_dirs, load_universe, load_universe_meta
from .sec import SecClient, companyfacts_url, document_url, submissions_url

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Filing:
    cik: int
    company: str
    ticker: str
    form: str
    filing_date: str
    report_date: str
    accession: str
    primary_document: str
    doc_url: str
    is_inline_xbrl: int

    @property
    def doc_id(self) -> str:
        """Stable identifier used as the key in every downstream table."""
        return f"{self.cik:010d}_{self.accession}"


def _recent_frames(client: SecClient, cik: int) -> list[dict]:
    """All filings for a CIK, including the older paginated slices."""
    payload = client.get_json(submissions_url(cik))
    name = payload.get("name", "")
    tickers = payload.get("tickers") or [""]

    blocks = [payload["filings"]["recent"]]
    for extra in payload["filings"].get("files", []):
        blocks.append(
            client.get_json(f"https://data.sec.gov/submissions/{extra['name']}")
        )

    rows: list[dict] = []
    for block in blocks:
        count = len(block.get("accessionNumber", []))
        for i in range(count):
            rows.append(
                {
                    "cik": cik,
                    "company": name,
                    "ticker": tickers[0],
                    "form": block["form"][i],
                    "filing_date": block["filingDate"][i],
                    "report_date": block["reportDate"][i],
                    "accession": block["accessionNumber"][i],
                    "primary_document": block.get("primaryDocument", [""] * count)[i],
                    "is_inline_xbrl": block.get("isInlineXBRL", [0] * count)[i],
                }
            )
    return rows


def build_filing_index(client: SecClient | None = None) -> pd.DataFrame:
    """One row per annual report in the universe and year window."""
    ensure_dirs()
    client = client or SecClient()
    meta = load_universe_meta()
    form = meta["form_type"]
    year_from, year_to = meta["report_year_from"], meta["report_year_to"]

    filings: list[Filing] = []
    for company in load_universe():
        cik = int(company["cik"])
        for row in _recent_frames(client, cik):
            if row["form"] != form:
                continue
            if not row["report_date"] or not row["primary_document"]:
                continue
            year = int(row["report_date"][:4])
            if not (year_from <= year <= year_to):
                continue
            if not row["primary_document"].lower().endswith((".htm", ".html")):
                continue
            filings.append(
                Filing(
                    cik=cik,
                    company=company["name"],
                    ticker=company["ticker"],
                    form=row["form"],
                    filing_date=row["filing_date"],
                    report_date=row["report_date"],
                    accession=row["accession"],
                    primary_document=row["primary_document"],
                    doc_url=document_url(cik, row["accession"], row["primary_document"]),
                    is_inline_xbrl=int(row["is_inline_xbrl"]),
                )
            )
        log.info("indexed %s", company["name"])

    frame = pd.DataFrame([asdict(f) for f in filings])
    if frame.empty:
        return frame
    frame["doc_id"] = frame.apply(
        lambda r: f"{int(r['cik']):010d}_{r['accession']}", axis=1
    )
    # A company can amend, so keep one filing per company and period end:
    # the earliest filed, which is the original annual report for that period.
    frame = frame.sort_values(["cik", "report_date", "filing_date"])
    frame = frame.drop_duplicates(subset=["cik", "report_date"], keep="first")
    return frame.sort_values(["company", "report_date"]).reset_index(drop=True)


def download_documents(frame: pd.DataFrame, client: SecClient | None = None) -> pd.DataFrame:
    """Pull every primary document. Records size and any failure."""
    client = client or SecClient()
    sizes, statuses = [], []
    for row in frame.itertuples():
        try:
            body = client.get_bytes(row.doc_url)
            sizes.append(len(body))
            statuses.append("ok")
        except Exception as exc:  # noqa: BLE001
            sizes.append(0)
            statuses.append(f"failed: {type(exc).__name__}")
            log.warning("document failed %s %s", row.doc_id, exc)
    out = frame.copy()
    out["doc_bytes"] = sizes
    out["doc_status"] = statuses
    return out


def download_companyfacts(frame: pd.DataFrame, client: SecClient | None = None) -> None:
    client = client or SecClient()
    for cik in sorted(frame["cik"].unique()):
        try:
            client.get_bytes(companyfacts_url(int(cik)))
        except Exception as exc:  # noqa: BLE001
            log.warning("companyfacts failed for %s: %s", cik, exc)


def run(client: SecClient | None = None) -> pd.DataFrame:
    client = client or SecClient()
    index = build_filing_index(client)
    index = download_documents(index, client)
    download_companyfacts(index, client)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    index.to_csv(RESULTS_DIR / "filing_index.csv", index=False)
    log.info(
        "fetch done: %d filings, %d network requests, %d cache hits, %.1f MB downloaded",
        len(index),
        client.network_requests,
        client.cache_hits,
        client.bytes_downloaded / 1e6,
    )
    return index
