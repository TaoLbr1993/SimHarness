"""Small HTTP client for the existing externally stepped experiment API."""

import json
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID

from trafficverse.api.models import ExperimentView, VehicleControlRequest
from trafficverse.domain.models import SimulationFrame

from .config import HarnessConfig


class SimulationClient(Protocol):
    def create(self, config: HarnessConfig) -> UUID: ...
    def prepare(self, experiment_id: UUID) -> None: ...
    def start(self, experiment_id: UUID) -> None: ...
    def step(
        self, experiment_id: UUID, sequence: int, actions: tuple[VehicleControlRequest, ...]
    ) -> SimulationFrame: ...
    def status(self, experiment_id: UUID) -> str: ...
    def experiment(self, experiment_id: UUID) -> ExperimentView: ...
    def stop(self, experiment_id: UUID) -> None: ...


class HttpSimulationClient:
    def __init__(self, base_url: str, timeout_s: float) -> None:
        self._base = base_url.rstrip("/") + "/api/v1"
        self._timeout_s = timeout_s

    def _request(self, path: str, payload: dict[str, object] | None = None) -> object:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            self._base + path, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urlopen(request, timeout=self._timeout_s) as response:
                return json.load(response)
        except (HTTPError, URLError, TimeoutError) as error:
            raise RuntimeError(f"simulation API request failed: {path}: {error}") from error

    def create(self, config: HarnessConfig) -> UUID:
        payload: dict[str, object] = {
            "workspace_id": str(config.workspace_id),
            "scenario_id": str(config.scenario_id),
            "map_id": config.map_id,
            "external_stepping": True,
        }
        if config.configuration_id is not None:
            payload["configuration_id"] = config.configuration_id
        result = self._request("/experiments", payload)
        if not isinstance(result, dict) or not isinstance(result.get("experiment_id"), str):
            raise ValueError("experiment creation returned no experiment_id")
        return UUID(result["experiment_id"])

    def prepare(self, experiment_id: UUID) -> None:
        self._request(f"/experiments/{experiment_id}/prepare", {})

    def start(self, experiment_id: UUID) -> None:
        self._request(f"/experiments/{experiment_id}/start", {})

    def step(
        self, experiment_id: UUID, sequence: int, actions: tuple[VehicleControlRequest, ...]
    ) -> SimulationFrame:
        response = self._request(
            f"/experiments/{experiment_id}/step",
            {
                "expected_sequence": sequence,
                "actions": [a.model_dump(mode="json") for a in actions],
            },
        )
        return SimulationFrame.model_validate(response)

    def status(self, experiment_id: UUID) -> str:
        response = self._request(f"/experiments/{experiment_id}")
        if not isinstance(response, dict) or not isinstance(response.get("status"), str):
            raise ValueError("experiment status response is invalid")
        return str(response["status"])

    def experiment(self, experiment_id: UUID) -> ExperimentView:
        return ExperimentView.model_validate(self._request(f"/experiments/{experiment_id}"))

    def stop(self, experiment_id: UUID) -> None:
        self._request(f"/experiments/{experiment_id}/stop", {"reason": "AGENT_HARNESS_STOP"})
