"""Opt-in live CTRIP browser acquisition probe entry point."""

from flight_agent.adapters.flight_providers.ctrip.browser_probe import main

if __name__ == "__main__":
    raise SystemExit(main())
