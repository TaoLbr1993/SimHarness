"""Area selection contract and dialog lifecycle, without network access."""

import math

import pytest
from pydantic import ValidationError
from PySide6.QtWidgets import QApplication, QDialog
from ui.models.map_selection import MapSearchPlace, MapSelectionBounds, SelectionConfig
from ui.widgets.map_area_dialog import MapAreaDialog


def _app() -> QApplication:
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])


def _bounds() -> MapSelectionBounds:
    return MapSelectionBounds(west=116.34, south=39.97, east=116.36, north=39.99)


def _selection(dialog: MapAreaDialog) -> MapSelectionBounds | None:
    return dialog.selected_bounds


@pytest.mark.parametrize(
    "change",
    [
        {"east": 116.34},
        {"north": 39.96},
        {"west": -181},
        {"north": 85},
        {"south": -85},
        {"west": float("nan")},
        {"east": float("inf")},
        {"west": -180, "east": 180},
        {"west": 179, "east": -179},
        {"coordinate_system": "GCJ02"},
    ],
)
def test_invalid_bounds_rejected(change: dict[str, object]) -> None:
    values = _bounds().model_dump()
    values.update(change)
    with pytest.raises(ValidationError):
        MapSelectionBounds.model_validate(values)


def test_dimensions_are_geographic_and_bounds_immutable() -> None:
    bounds = _bounds()
    assert 1700 < bounds.width_m < 1710
    assert 2220 < bounds.height_m < 2230
    assert math.isclose(
        bounds.diagonal_m, math.hypot(bounds.width_m, bounds.height_m), rel_tol=0.001
    )
    with pytest.raises(ValidationError):
        bounds.west = 0  # type: ignore[misc]


def test_accept_requires_service_visible_tiles_and_valid_rectangle() -> None:
    app = _app()
    dialog = MapAreaDialog(selected_bounds=_bounds(), load_page=False)
    assert not dialog.confirm_button.isEnabled()
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    dialog._config_ready(SelectionConfig(max_diagonal_m=6000))
    dialog._map_ready()
    assert not dialog.confirm_button.isEnabled()
    dialog._tile_state(True)
    assert dialog.confirm_button.isEnabled()
    dialog._loading()
    assert not dialog.confirm_button.isEnabled()
    dialog._map_ready()
    assert not dialog.confirm_button.isEnabled()
    dialog._tile_state(True)
    assert dialog.confirm_button.isEnabled()
    dialog.draw_button.setChecked(True)
    assert not dialog.confirm_button.isEnabled()
    dialog._drawing_finished()
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.selected_bounds == _bounds()
    dialog.deleteLater()
    app.processEvents()


def test_clear_and_invalid_bridge_data_cannot_reuse_previous_bounds() -> None:
    app = _app()
    dialog = MapAreaDialog(selected_bounds=_bounds(), load_page=False)
    dialog._bounds_changed('{"west": "NaN"}')
    assert _selection(dialog) is None
    dialog._bounds_changed(_bounds().model_dump_json())
    assert _selection(dialog) == _bounds()
    dialog._clear()
    assert _selection(dialog) is None
    assert not dialog.confirm_button.isEnabled()
    dialog.reject()
    dialog.deleteLater()
    app.processEvents()


def test_area_limits_and_tile_failure_prevent_confirmation() -> None:
    app = _app()
    dialog = MapAreaDialog(language="zh", selected_bounds=_bounds(), load_page=False)
    dialog._config_ready(SelectionConfig(max_diagonal_m=1000))
    dialog._map_ready()
    dialog._tile_state(True)
    assert not dialog.confirm_button.isEnabled()
    assert "范围过大" in dialog.selection_label.text()
    dialog._config_ready(SelectionConfig(max_diagonal_m=6000))
    assert dialog.confirm_button.isEnabled()
    dialog._tile_failure()
    assert not dialog.confirm_button.isEnabled()
    assert "加载失败" in dialog.map_status.text()
    dialog._tile_state(True)
    assert dialog.confirm_button.isEnabled()
    dialog._script_error("Unable to initialize the map")
    assert not dialog.confirm_button.isEnabled()
    assert "Unable to initialize" in dialog.map_status.text()
    dialog._map_ready()
    dialog._tile_state(True)
    dialog._request_failed("config", "Unavailable")
    assert not dialog.confirm_button.isEnabled()
    dialog.reject()
    dialog.deleteLater()
    app.processEvents()


def test_search_select_clears_old_area_and_cancel_keeps_original_immutable() -> None:
    app = _app()
    original = _bounds()
    dialog = MapAreaDialog(selected_bounds=original, load_page=False)
    dialog._places_ready([MapSearchPlace(id="N1", name="London", longitude=-0.12, latitude=51.5)])
    dialog._choose_place(0)
    assert dialog.selected_bounds is None
    dialog.reject()
    assert original == _bounds()
    dialog.deleteLater()
    app.processEvents()
