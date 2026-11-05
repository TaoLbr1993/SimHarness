"""Compatibility entry for existing Scene Studio shortcuts; opens TrafficForge."""

from scripts.dev.open_trafficforge import main

if __name__ == "__main__":
    raise SystemExit(main())
