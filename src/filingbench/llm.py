"""Phase 3: the language model extractor.

Same inputs, same output schema, same scorer as the rules extractor. Nothing
here is stubbed and nothing here fabricates a result: with no API key present
the runner raises rather than inventing predictions.

Three choices in this file materially affect any number it produces, so they
are named here rather than buried.

Context selection. A 10-K runs to hundreds of thousands of characters once
stripped, which is large but not impossible for a long context model, and
expensive at every call. Two modes are provided. In "document" mode the model
receives the stripped document text, truncated to a character budget, and is
told nothing about where the statements are. In "statements" mode it receives
only the tables the shared statement locator found. The second is far cheaper
and is what a cost conscious pipeline would really do, but it hands the model
the rules extractor's first stage for free, so a win under that mode is a win
on row and column resolution only, not on finding the statement. The default is
"document" because that is the honest comparison. Whichever mode is used is
recorded on every row of output.

Truncation. At the default budget nothing in this corpus truncates, which was
worth measuring rather than assuming: the largest filing is 667,000 characters
against a budget of one million. The fallback still exists for corpora that do
exceed it, and it excerpts around the densest run of statement vocabulary rather
than cutting from the front, because the front of a 10-K is the business
description and the risk factors. Truncation is recorded per document either
way, since a silently truncated input looks exactly like a model failure.

Determinism. Temperature is fixed at zero and the exact model id, the prompt
version and every parameter are written onto each row, because a language model
result without them is not reproducible.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass

import pandas as pd

from .config import CONFIG_DIR, FieldSpec, load_fields
from .providers import NoEndpoint, get_provider, provider_for
from .docprep import PreparedDocument
from .prepared import load as load_prepared
from .rules import find_statement_table
from .sec import SecClient

log = logging.getLogger(__name__)

# Which model runs is a configuration value, not a hard coded vendor choice,
# because the question this benchmark asks is about language models rather than
# about one vendor's. The id selects the provider on its own, in providers.py.
# Whatever is used is written onto every output row alongside the provider that
# served it.
DEFAULT_MODEL = os.environ.get("FILINGBENCH_MODEL", "").strip() or "google/gemini-2.5-flash-lite"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_TOKENS = 2048
DEFAULT_CONTEXT_MODE = "document"
# Measured against this corpus: the largest stripped filing is 666,703
# characters, roughly 167,000 tokens, so a one million character budget
# truncates nothing at all. At the 400,000 I first chose, 81 of 144 filings were
# cut, which would have made truncation the dominant confound in any model
# result rather than a footnote to it.
DEFAULT_CHAR_BUDGET = 1_000_000

# Bumped whenever the prompt text changes. Every revision is listed in
# docs/prompt_revisions.md with what it changed and why. Revisions are made
# against the development split only. Currently one: the prompt has been run
# against the full test split and not revised since, so the reported test
# number is not the product of any selection against it.
PROMPT_VERSION = 1

STATEMENT_VOCABULARY = (
    "total assets", "total liabilities", "net sales", "total revenue",
    "operating income", "net income", "net earnings", "stockholders",
    "operating activities", "cash and cash equivalents", "per share",
)

SYSTEM_PROMPT = (
    "You read annual reports filed with the United States Securities and "
    "Exchange Commission and report figures exactly as the consolidated "
    "financial statements print them.\n"
    "Rules you must follow:\n"
    "1. Report the value for the reporting period stated in the request, not "
    "for a comparative period printed alongside it.\n"
    "2. Return the value in whole units of currency. Statements are usually "
    "printed in thousands or millions, so apply the scale stated in the table "
    "caption. Per share amounts are never scaled.\n"
    "3. A figure in parentheses is negative.\n"
    "4. Use the consolidated statements, never a segment note or a five year "
    "summary.\n"
    "5. If a field is genuinely not present in the consolidated statements, "
    "return null for it. Do not derive it, estimate it, or substitute a "
    "related line.\n"
    "Answer with a single JSON object and nothing else."
)


@dataclass
class LlmSettings:
    model: str = DEFAULT_MODEL
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int = DEFAULT_MAX_TOKENS
    context_mode: str = DEFAULT_CONTEXT_MODE
    char_budget: int = DEFAULT_CHAR_BUDGET
    prompt_version: int = PROMPT_VERSION


# The CLI catches this by name; the provider layer's NoEndpoint is the same
# exception, so a missing project id or a missing gcloud login surface the same
# way to the caller.
NoApiKey = NoEndpoint


def load_pricing() -> dict:
    return json.loads((CONFIG_DIR / "pricing.json").read_text(encoding="utf-8"))


def price_call(model: str, input_tokens: float, output_tokens: float) -> float | None:
    """No rate in the config means no cost figure, never a guessed one."""
    pricing = load_pricing().get("models", {}).get(model)
    if not pricing or pricing.get("usd_per_million_input") is None:
        return None
    return (input_tokens / 1e6) * pricing["usd_per_million_input"] + (
        output_tokens / 1e6
    ) * pricing["usd_per_million_output"]


def render_table(table) -> str:
    lines = [f"[caption] {table.preamble[-200:].strip()}"]
    for row in table.rows:
        cells = [c for c in row if c.strip()]
        if cells:
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def select_context(doc: PreparedDocument, settings: LlmSettings) -> tuple[str, bool]:
    """Return the text sent to the model and whether it had to be truncated."""
    if settings.context_mode == "statements":
        blocks = []
        for statement in ("income_statement", "balance_sheet", "cash_flow"):
            table = find_statement_table(doc, statement)
            if table is not None:
                blocks.append(f"### {statement}\n{render_table(table)}")
        text = "\n\n".join(blocks) if blocks else doc.text
    else:
        text = doc.text

    if len(text) <= settings.char_budget:
        return text, False
    return _densest_window(text, settings.char_budget), True


def _densest_window(text: str, budget: int, stride: int = 20_000) -> str:
    """Excerpt around the densest run of statement vocabulary.

    Cutting from the front of a 10-K throws away the statements and keeps the
    risk factors, which would make truncation look like a model failure.
    """
    lowered = text.lower()
    best_start, best_score = 0, -1
    for start in range(0, max(len(text) - budget, 0) + 1, stride):
        window = lowered[start : start + budget]
        score = sum(window.count(term) for term in STATEMENT_VOCABULARY)
        if score > best_score:
            best_start, best_score = start, score
    return text[best_start : best_start + budget]


def build_user_prompt(fields: dict[str, FieldSpec], company: str, report_date: str,
                      context: str) -> str:
    schema = {
        key: (
            f"{spec.label} for the period ending {report_date}"
            + (" (per share, not scaled)" if spec.unit == "USD/shares"
               else " (whole units of currency)")
        )
        for key, spec in fields.items()
    }
    return (
        f"Company: {company}\n"
        f"Reporting period end: {report_date}\n\n"
        "Return one JSON object with exactly these keys, each a number or null:\n"
        f"{json.dumps(schema, indent=2)}\n\n"
        "Document text follows.\n"
        "-----\n"
        f"{context}\n"
        "-----\n"
        "JSON object only:"
    )


def parse_response(text: str, fields: dict[str, FieldSpec]) -> tuple[dict, str]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {}, "unparseable_response"
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}, "unparseable_response"
    if not isinstance(payload, dict):
        return {}, "unparseable_response"

    cleaned: dict[str, float | None] = {}
    for key in fields:
        value = payload.get(key)
        if value is None or isinstance(value, bool):
            cleaned[key] = None
        elif isinstance(value, (int, float)):
            cleaned[key] = float(value)
        elif isinstance(value, str):
            try:
                cleaned[key] = float(value.replace(",", "").replace("$", "").strip())
            except ValueError:
                cleaned[key] = None
        else:
            cleaned[key] = None
    return cleaned, "ok"


def _client(settings: LlmSettings | None = None):
    """The provider that serves the configured model id.

    Which vendor is used follows from the model id and nothing else, so a run
    cannot silently be served by a different model than the one recorded.
    """
    settings = settings or LlmSettings()
    return get_provider(settings.model)


def check_endpoint(settings: LlmSettings | None = None) -> dict:
    """One small live call, so that a claimed model id is a verified model id."""
    settings = settings or LlmSettings()
    provider = _client(settings)
    started = time.perf_counter()
    reply = provider.complete(
        system="Reply exactly as asked.",
        user="Reply with the word OK and nothing else.",
        max_tokens=16,
        temperature=settings.temperature,
    )
    return {
        "provider": provider.name,
        "model_requested": settings.model,
        "model_returned": reply.model_returned,
        "text": reply.text,
        "input_tokens": reply.input_tokens,
        "output_tokens": reply.output_tokens,
        "seconds": time.perf_counter() - started,
    }


def run_llm(index: pd.DataFrame, settings: LlmSettings | None = None,
            client: SecClient | None = None, run_label: str = "run1",
            only_fields: dict[str, list[str]] | None = None) -> pd.DataFrame:
    """Predictions for every filing in the index. Raises if no endpoint exists.

    ``only_fields`` restricts the request per document, which is how the hybrid
    asks about just the fields the rules extractor could not resolve.
    """
    settings = settings or LlmSettings()
    fields = load_fields()
    sec = client or SecClient()
    api = _client(settings)

    records: list[dict] = []
    for filing in index.itertuples():
        wanted = fields
        if only_fields is not None:
            keys = only_fields.get(filing.doc_id, [])
            if not keys:
                continue
            wanted = {k: v for k, v in fields.items() if k in keys}

        doc = load_prepared(filing.doc_id, filing.doc_url, sec)
        context, truncated = select_context(doc, settings)
        prompt = build_user_prompt(wanted, filing.company, filing.report_date, context)

        started = time.perf_counter()
        status, values, usage_in, usage_out = "ok", {}, 0, 0
        try:
            reply = api.complete(
                system=SYSTEM_PROMPT,
                user=prompt,
                max_tokens=settings.max_tokens,
                temperature=settings.temperature,
            )
            usage_in = reply.input_tokens
            usage_out = reply.output_tokens
            values, status = parse_response(reply.text, wanted)
        except Exception as exc:  # noqa: BLE001
            status = "llm_error"
            log.warning("llm call failed for %s: %s", filing.doc_id, exc)

        elapsed = time.perf_counter() - started
        usd = price_call(settings.model, usage_in, usage_out)
        share = max(len(wanted), 1)

        for key in wanted:
            value = values.get(key) if status == "ok" else None
            records.append({
                "doc_id": filing.doc_id,
                "field": key,
                "predicted": value,
                "status": "ok" if (status == "ok" and value is not None)
                          else ("llm_no_answer" if status == "ok" else status),
                "method": "llm",
                "seconds": elapsed / share,
                "input_tokens": usage_in / share,
                "output_tokens": usage_out / share,
                "usd": (usd / share) if usd is not None else float("nan"),
                "provider": api.name,
                "model": settings.model,
                "temperature": settings.temperature,
                "prompt_version": settings.prompt_version,
                "context_mode": settings.context_mode,
                "context_chars": len(context),
                "truncated": truncated,
                "run_label": run_label,
            })
    return pd.DataFrame(records)
