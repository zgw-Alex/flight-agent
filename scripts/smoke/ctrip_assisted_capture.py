"""Local-only CTRIP assisted manual evidence import entry point."""

from flight_agent.adapters.flight_providers.ctrip.assisted_capture import main

if __name__ == "__main__":
    raise SystemExit(main())
