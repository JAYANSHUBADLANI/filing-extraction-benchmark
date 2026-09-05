"""Paths, configuration loading and the one documented root seed.

Everything here resolves relative to the repository root so that no absolute
path from the machine that ran the code can leak into a committed file.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]

CONFIG_DIR = REPO_ROOT / "config"
DATA_DIR = REPO_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
SAMPLE_DIR = DATA_DIR / "sample"
RESULTS_DIR = REPO_ROOT / "results"
FIGURES_DIR = RESULTS_DIR / "figures"

ROOT_SEED = 20260904

DEFAULT_USER_AGENT = "Research Project research@example.com"


def seed_sequence(*stream: int) -> np.random.SeedSequence:
    """Derive a child seed sequence from the single documented root seed.

    Every random stream in the project comes from here, so a run is
    reproducible from ROOT_SEED alone.
    """
    return np.random.SeedSequence([ROOT_SEED, *stream])


def rng(*stream: int) -> np.random.Generator:
    return np.random.default_rng(seed_sequence(*stream))


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader. Existing environment variables always win."""
    path = path or (REPO_ROOT / ".env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def sec_user_agent() -> str:
    """The contact string the SEC requires. Never hard coded to a real address."""
    load_dotenv()
    return os.environ.get("SEC_USER_AGENT", DEFAULT_USER_AGENT).strip() or DEFAULT_USER_AGENT


@dataclass(frozen=True)
class FieldSpec:
    """One target field: what it means, how XBRL names it, how a page prints it."""

    key: str
    label: str
    statement: str
    period_type: str          # "duration" for flows, "instant" for balances
    unit: str                 # "USD" or "USD/shares"
    scaled: bool              # printed in thousands or millions, so scale applies
    concepts: tuple[str, ...]  # ordered, first match wins
    row_patterns: tuple[str, ...]
    # Labels that must never match, even when a row pattern would accept them.
    # "Total liabilities and stockholders' equity" is the classic trap: it ends
    # the way the equity subtotal ends and sits two rows below it.
    exclude_patterns: tuple[str, ...] = ()
    # Vetoes read from the heading above a row rather than the row itself. Kept
    # separate from exclude_patterns because a heading governs a whole block: on
    # a balance sheet "LIABILITIES AND EQUITY" sits above the total liabilities
    # subtotal, so a heading veto meant for one field would wipe out another.
    section_exclude_patterns: tuple[str, ...] = ()
    # Some statements put the distinguishing word in the section heading rather
    # than on the row. Pfizer labels its diluted earnings per share row "Net
    # income attributable to Pfizer Inc. common shareholders" and only the
    # heading above says "diluted". These patterns match that heading, and the
    # first numeric row under it is taken.
    section_patterns: tuple[str, ...] = ()
    negative_ok: bool = True


def load_fields() -> dict[str, FieldSpec]:
    raw = json.loads((CONFIG_DIR / "fields.json").read_text(encoding="utf-8"))
    out: dict[str, FieldSpec] = {}
    for key, spec in raw["fields"].items():
        out[key] = FieldSpec(
            key=key,
            label=spec["label"],
            statement=spec["statement"],
            period_type=spec["period_type"],
            unit=spec["unit"],
            scaled=spec["scaled"],
            concepts=tuple(spec["concepts"]),
            row_patterns=tuple(spec["row_patterns"]),
            exclude_patterns=tuple(spec.get("exclude_patterns", [])),
            section_exclude_patterns=tuple(spec.get("section_exclude_patterns", [])),
            section_patterns=tuple(spec.get("section_patterns", [])),
            negative_ok=spec.get("negative_ok", True),
        )
    return out


def load_fields_meta() -> dict:
    return json.loads((CONFIG_DIR / "fields.json").read_text(encoding="utf-8"))


def load_universe() -> list[dict]:
    raw = json.loads((CONFIG_DIR / "universe.json").read_text(encoding="utf-8"))
    return raw["companies"]


def load_universe_meta() -> dict:
    return json.loads((CONFIG_DIR / "universe.json").read_text(encoding="utf-8"))


def ensure_dirs() -> None:
    for d in (CACHE_DIR, SAMPLE_DIR, RESULTS_DIR, FIGURES_DIR):
        d.mkdir(parents=True, exist_ok=True)
