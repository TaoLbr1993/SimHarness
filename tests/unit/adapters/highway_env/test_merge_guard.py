"""Merge reservations must not block traffic after their owner changes route/lane."""

from types import SimpleNamespace
from typing import Any

from trafficverse.adapters.highway_env.merge_guard import SharedMergeGuard


def test_owner_leaving_approach_releases_waiting_vehicle() -> None:
    main = ("main-in", "main-out", 0)
    ramp = ("ramp-in", "ramp-out", 0)
    target = ("exit-in", "exit-out", 0)
    main_connector = ("main-out", "exit-in", 0)
    ramp_connector = ("ramp-out", "exit-in", 0)
    # Minimal SDK-shaped objects; no third-party runtime is imported in this unit test.
    lane = SimpleNamespace(length=100, local_coordinates=lambda position: position)
    network = SimpleNamespace(
        get_lane=lambda key: lane,
        connector_targets={main_connector: target, ramp_connector: target},
        transitions={(main, main_connector[:2]): [0], (ramp, ramp_connector[:2]): [0]},
    )
    road = SimpleNamespace(neighbour_vehicles=lambda vehicle, index: (None, None))

    def vehicle(index: tuple[str, str, int], x_m: float, connector: tuple[str, str, int]) -> Any:
        return SimpleNamespace(
            lane_index=index,
            target_lane_index=index,
            position=(x_m, 0),
            route=[index, connector, target],
            speed=10,
            road=road,
            COMFORT_ACC_MIN=-4,
            TIME_WANTED=1,
            LENGTH=5,
            DISTANCE_WANTED=7,
            unsafe_control=False,
            crashed=False,
        )

    leader = vehicle(main, 91, main_connector)
    waiting = vehicle(ramp, 90, ramp_connector)
    guard = SharedMergeGuard(network)
    stopped: list[Any] = []
    guard.apply([leader, waiting], lambda car, position, dt: stopped.append(car), 0.05)
    assert stopped == [waiting]
    leader.lane_index = ("escape-in", "escape-out", 0)
    leader.route = []
    stopped.clear()
    guard.apply([leader, waiting], lambda car, position, dt: stopped.append(car), 0.05)
    assert stopped == []
