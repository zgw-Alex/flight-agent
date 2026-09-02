"""Opt-in bounded live FLIGGY Level-2 evidence validation entry point."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from flight_agent.adapters.flight_providers.fliggy.browser_probe import (
    Level2ExpansionBounds,
    Level2ExpansionOutcome,
    ProbeInput,
    run_fliggy_level2_live_validation,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Opt-in FLIGGY Level-2 bounded live validation")
    parser.add_argument("--origin", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--departure-date", required=True)
    parser.add_argument("--experiment-run-id")
    parser.add_argument("--search-plan-id")
    parser.add_argument("--execution-id")
    parser.add_argument("--deadline-seconds", type=float, default=35.0)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--max-offer-rows", type=int, default=5)
    parser.add_argument("--max-wait-ms", type=int, default=3000)
    parser.add_argument("--max-retries", type=int, default=0)
    parser.add_argument("--max-level1-targets", type=int, default=1)
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()

    result = asyncio.run(
        run_fliggy_level2_live_validation(
            ProbeInput.from_args(args),
            bounds=Level2ExpansionBounds(
                max_offer_rows=args.max_offer_rows,
                max_wait_ms=args.max_wait_ms,
                max_retries=args.max_retries,
            ),
            max_level1_targets=args.max_level1_targets,
        )
    )
    rendered = result.to_json()
    print(rendered)
    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(rendered + "\n", encoding="utf-8")
    return 0 if result.outcome in {Level2ExpansionOutcome.SUCCESS_EXPANDED, Level2ExpansionOutcome.SUCCESS_EMPTY} else 2


if __name__ == "__main__":
    raise SystemExit(main())
