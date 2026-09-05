"""Single entry point. `make demo` runs this end to end."""

from __future__ import annotations

import argparse
import json
import logging
import time

import pandas as pd

from . import evaluate as ev
from . import failures, fetch, groundtruth, hybrid, prepared, report, split
from .config import RESULTS_DIR, ensure_dirs
from .extractors import run_rules
from .sec import SecClient

log = logging.getLogger(__name__)


def stage_fetch() -> pd.DataFrame:
    client = SecClient()
    index = fetch.run(client)
    prepared.build_all(index, client)
    return index


def stage_groundtruth(index: pd.DataFrame) -> None:
    client = SecClient()
    groundtruth.run(index, client)
    groundtruth.all_period_values(index, client).to_csv(
        RESULTS_DIR / "truth_all_periods.csv", index=False)
    split.write()


def stage_rules(index: pd.DataFrame) -> pd.DataFrame:
    predictions = run_rules(index)
    predictions.to_csv(RESULTS_DIR / "predictions_rules.csv", index=False)
    return predictions


def stage_llm(index: pd.DataFrame, runs: int) -> pd.DataFrame | None:
    from .llm import LlmSettings, NoApiKey, run_llm

    frames = []
    try:
        for i in range(runs):
            frames.append(run_llm(index, LlmSettings(), run_label=f"run{i + 1}"))
    except NoApiKey as exc:
        log.warning("skipping the LLM stage: %s", exc)
        print("[llm] skipped, no API key present. The rules half is unaffected.")
        return None
    predictions = pd.concat(frames, ignore_index=True)
    predictions.to_csv(RESULTS_DIR / "predictions_llm.csv", index=False)
    return predictions


def stage_score(rules_predictions, llm_predictions) -> None:
    index, truth, all_periods = ev.load_inputs()
    frames = [rules_predictions]
    if llm_predictions is not None and not llm_predictions.empty:
        frames.append(llm_predictions)
        frames.append(hybrid.combine(rules_predictions, llm_predictions))
    scored = pd.concat(
        [ev.evaluate(frame, truth, all_periods) for frame in frames], ignore_index=True)
    ev.write_tables(scored)
    failures.run(scored, index)
    print(ev.summarise(scored))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="filing extraction benchmark")
    parser.add_argument("--stage", default="all",
                        choices=["all", "fetch", "groundtruth", "rules", "llm", "report"])
    parser.add_argument("--llm-runs", type=int, default=3,
                        help="repeat the LLM evaluation to measure run to run variation")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s")
    ensure_dirs()
    started = time.perf_counter()
    timings: dict[str, float] = {}

    def timed(name, fn, *fn_args):
        mark = time.perf_counter()
        result = fn(*fn_args)
        timings[name] = round(time.perf_counter() - mark, 2)
        print(f"[{name}] {timings[name]:.1f}s")
        return result

    if args.stage in ("all", "fetch"):
        index = timed("fetch", stage_fetch)
    else:
        index = pd.read_csv(RESULTS_DIR / "filing_index.csv", dtype={"accession": str})

    if args.stage in ("all", "groundtruth"):
        timed("groundtruth", stage_groundtruth, index)

    rules_predictions = llm_predictions = None
    if args.stage in ("all", "rules"):
        rules_predictions = timed("rules", stage_rules, index)
    if args.stage in ("all", "llm"):
        llm_predictions = timed("llm", stage_llm, index, args.llm_runs)

    if args.stage == "all":
        timed("score", stage_score, rules_predictions, llm_predictions)
    if args.stage in ("all", "report"):
        timed("report", report.build_all)

    timings["total"] = round(time.perf_counter() - started, 2)
    (RESULTS_DIR / "run_timings.json").write_text(
        json.dumps(timings, indent=2), encoding="utf-8")
    print(f"[total] {timings['total']:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
