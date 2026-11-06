"""Instantiate configured Agent wrappers without importing a CLI entry point."""

import importlib
from typing import cast

from .config import Binding
from .models.base import VehicleAgent


def load_agent(binding: Binding) -> VehicleAgent:
    module_name, separator, class_name = binding.wrapper.partition(":")
    if not separator or not module_name or not class_name:
        raise ValueError(f"wrapper for {binding.name} must have the form module:Class")
    wrapper = getattr(importlib.import_module(module_name), class_name)
    instance = wrapper(binding.model) if binding.model is not None else wrapper()
    if not callable(getattr(instance, "decide", None)):
        raise TypeError(f"wrapper for {binding.name} must implement decide")
    return cast(VehicleAgent, instance)
