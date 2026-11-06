"""Run the external agent harness from the command line."""

import argparse
import logging
from pathlib import Path

from .client import HttpSimulationClient
from .config import load_config
from .loader import load_agent as load_agent
from .manager import AgentManager


def main() -> int:
    parser = argparse.ArgumentParser(description="Run agents against a SUMO experiment")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = load_config(args.config)
    agents = {binding.name: load_agent(binding) for binding in config.bindings}
    AgentManager(
        config, HttpSimulationClient(config.api_url, config.request_timeout_s), agents
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
