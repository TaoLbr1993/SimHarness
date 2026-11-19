"""Generate stable JSON Schema files from authoritative Pydantic models."""

import json
from pathlib import Path

import yaml
from pydantic import BaseModel

from trafficverse.api.contracts import build_openapi_contract
from trafficverse.api.models import ClientCommand
from trafficverse.config.models import RuntimeBaseline, ScenarioConfig
from trafficverse.domain.models import VehicleState, WebSocketEnvelope
from trafficverse.maps.models import RoadNetwork

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS: dict[Path, type[BaseModel]] = {
    Path("contracts/runtime_baseline.schema.json"): RuntimeBaseline,
    Path("contracts/scenario.schema.json"): ScenarioConfig,
    Path("contracts/traffic_network.schema.json"): RoadNetwork,
    Path("contracts/websocket/vehicle_state.schema.json"): VehicleState,
    Path("contracts/websocket/websocket_envelope.schema.json"): WebSocketEnvelope,
    Path("contracts/websocket/client_command.schema.json"): ClientCommand,
}


def main() -> None:
    for relative_path, model in CONTRACTS.items():
        output_path = REPOSITORY_ROOT / relative_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            model.model_json_schema(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        output_path.write_text(f"{payload}\n", encoding="utf-8", newline="\n")

    openapi_path = REPOSITORY_ROOT / "contracts/openapi.yaml"
    openapi_path.write_text(
        yaml.safe_dump(
            build_openapi_contract(),
            allow_unicode=True,
            sort_keys=True,
        ),
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    main()
