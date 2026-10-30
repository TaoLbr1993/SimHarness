"""Run the external agent harness from the command line."""

import argparse
import importlib
import logging
from pathlib import Path
from typing import cast

from .client import HttpSimulationClient
from .config import Binding, load_config
from .manager import AgentManager
from .models.base import VehicleAgent


def load_agent(binding: Binding) -> VehicleAgent:
    module_name, separator, class_name = binding.wrapper.partition(":")
    if not separator or not module_name or not class_name:
        raise ValueError(f"wrapper for {binding.name} must have the form module:Class")
    wrapper = getattr(importlib.import_module(module_name), class_name)
    instance = wrapper()
    if not callable(getattr(instance, "decide", None)):
        raise TypeError(f"wrapper for {binding.name} must implement decide")
    return cast(VehicleAgent, instance)


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
