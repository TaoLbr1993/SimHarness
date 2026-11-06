"""SimHarness desktop application shell."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ui.api_client import RestApiClient
from ui.models import (
    TRAFFIC_SCENARIO_PRESETS,
    ControlAvailability,
    ExperimentStatus,
    MapSummary,
    ReplayRecord,
    ReplaySummary,
    ReplayWindow,
    SimulationConfigurationDraft,
    TrafficScenarioPreset,
    WorkspaceOverview,
    WorkspaceSummary,
)
from ui.scene_language import Language
from ui.viewmodels import ReplayPlaybackViewModel, RunViewModel
from ui.viewmodels.agent_connection import AgentConnectionViewModel
from ui.viewmodels.agent_decisions import AgentDecisionsViewModel
from ui.views.agent_asset_page import AgentAssetPage
from ui.views.agent_connection_page import AgentConnectionPage
from ui.views.data_replay_page import DataReplayPage
from ui.views.experiment_management_page import ExperimentManagementPage
from ui.views.live_monitor_page import LiveMonitorPage
from ui.views.map_asset_page import MapAssetPage
from ui.views.navigation import NavigationRail, WorkspaceNavigationRail
from ui.views.project_detail_page import ProjectDetailPage
from ui.views.scene_configuration_page import SceneConfigurationPage
from ui.views.scene_generation_page import SceneGenerationPage
from ui.views.system_settings_page import SystemSettingsPage
from ui.views.theme import DEFAULT_THEME, ThemeMode, configure_application_font, load_stylesheet
from ui.views.traffic_scene_page import TrafficScenePage
from ui.views.workspace_page import (
    WorkspaceDeleteDialog,
    WorkspaceEditDialog,
    WorkspaceOverviewPage,
    run_workspace_dialog,
)
from ui.widgets import MapLibreDeckMapWidget

_WINDOW_ICON_PATH = Path(__file__).resolve().parents[1] / "assets/icons/logo.svg"
_SUCCESS_NOTICE_TIMEOUT_MS = 3_000
_NAVIGATION_STRETCH = 13
_CONTENT_STRETCH = 67


class MainWindow(QMainWindow):
    """Compose the navigation shell and route view-model state into pages."""

    def __init__(
        self,
        viewmodel: RunViewModel,
        *,
        load_web_map: bool = True,
        traffic_mode: bool = False,
        scene_language: Language = "en",
    ) -> None:
        configure_application_font()
        super().__init__()
        self._viewmodel = viewmodel
        self._replay_viewmodel = ReplayPlaybackViewModel(self)
        self._replay_networks: dict[str, object] = {}
        self.setWindowIcon(QIcon(str(_WINDOW_ICON_PATH)))
        self.setWindowTitle("SimHarness · 交互式交通仿真系统")
        self.resize(1600, 960)
        self.setMinimumSize(1180, 720)

        self.navigation = NavigationRail()
        self.workspace_navigation = WorkspaceNavigationRail()
        self.navigation_stack = QStackedWidget()
        self.navigation_stack.setObjectName("navigationStack")
        self.navigation_stack.addWidget(self.workspace_navigation)
        self.navigation_stack.addWidget(self.navigation)
        self.page_stack = QStackedWidget()
        self.page_stack.setObjectName("pageStack")
        self.notice = QLabel()
        self.notice.setObjectName("notice")
        self.notice.setWordWrap(True)
        self.notice.hide()
        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.setInterval(_SUCCESS_NOTICE_TIMEOUT_MS)
        self._notice_timer.timeout.connect(self._hide_notice)

        self.live_page = LiveMonitorPage(load_web_map=load_web_map, traffic_mode=traffic_mode)
        self.scene_page = SceneConfigurationPage(
            load_web_map=load_web_map, traffic_mode=traffic_mode
        )
        self.generation_page = SceneGenerationPage(
            traffic_mode=traffic_mode, language=scene_language
        )
        self._generation_mode = "traffic" if traffic_mode else "autonomy"
        self.experiments_page = ExperimentManagementPage()
        self.replay_page = DataReplayPage()
        self.traffic_scenes_page = TrafficScenePage(load_web_map=load_web_map)
        self.maps_page = MapAssetPage(load_web_map=load_web_map)
        self.agents_page = AgentAssetPage(traffic_mode=traffic_mode)
        self.agent_connection_page = AgentConnectionPage()
        self.agent_connection_viewmodel = AgentConnectionViewModel(
            RestApiClient(viewmodel.api_url, self), self
        )
        self.agent_decisions_viewmodel = AgentDecisionsViewModel(
            RestApiClient(viewmodel.api_url, self), self
        )
        self.settings_page = SystemSettingsPage()
        self.workspace_page = WorkspaceOverviewPage(
            load_web_map=load_web_map, traffic_mode=traffic_mode
        )
        self.project_detail_page = ProjectDetailPage()
        self._pages = {
            "workspace": self.workspace_page,
            "project": self.project_detail_page,
            "live": self.live_page,
            "scene": self.scene_page,
            "generate": self.generation_page,
            "experiments": self.experiments_page,
            "replay": self.replay_page,
            "traffic_scenes": self.traffic_scenes_page,
            "maps": self.maps_page,
            "agents": self.agents_page,
            "agent_connection": self.agent_connection_page,
            "settings": self.settings_page,
        }
        for page in self._pages.values():
            self.page_stack.addWidget(page)

        if traffic_mode:
            self.setWindowTitle("SimHarness · 普通交通仿真")
            scripted_navigation = self.navigation.findChild(QWidget, "navContainer_traffic_scenes")
            if scripted_navigation is not None:
                scripted_navigation.hide()
            for page in self._pages.values():
                for map_widget in page.findChildren(MapLibreDeckMapWidget):
                    map_widget.set_traffic_presentation(True)

        self._normal_window_title = self.windowTitle()
        self._viewmodel.set_scene_language(scene_language)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self.notice)
        content_layout.addWidget(self.page_stack, 1)

        shell = QWidget()
        shell.setObjectName("appShell")
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        shell_layout.addWidget(self.navigation_stack, _NAVIGATION_STRETCH)
        shell_layout.addWidget(content, _CONTENT_STRETCH)
        self.setCentralWidget(shell)

        self._connect_pages()
        self._connect_viewmodel()
        self._apply_theme(DEFAULT_THEME.value)
        self.navigation_stack.setCurrentWidget(self.workspace_navigation)
        self.page_stack.setCurrentWidget(self.workspace_page)

    def _connect_pages(self) -> None:
        vm = self._viewmodel
        connection = self.agent_connection_viewmodel
        page = self.agent_connection_page
        page.command_requested.connect(connection.command)
        page.visibility_changed.connect(connection.set_visible)
        page.monitor_requested.connect(lambda: self._show_page("live"))
        connection.view_changed.connect(page.set_view)
        connection.config_loaded.connect(page.set_configuration)
        connection.message_changed.connect(page.message.setText)
        connection.busy_changed.connect(page.set_busy)
        decisions = self.agent_decisions_viewmodel
        connection.view_changed.connect(decisions.set_session)
        decisions.page_changed.connect(page.decisions.set_page)
        decisions.detail_changed.connect(page.decisions.set_detail)
        decisions.error.connect(page.decisions.detail_note.setText)
        page.decisions.page_requested.connect(decisions.load_page)
        page.decisions.detail_requested.connect(decisions.load_detail)
        page.visibility_changed.connect(
            lambda visible: decisions.set_visible(visible and page.sections.currentIndex() == 1)
        )
        page.sections.currentChanged.connect(
            lambda index: decisions.set_visible(page.isVisible() and index == 1)
        )
        self.live_page.agent_connection_requested.connect(
            lambda: self._show_page("agent_connection")
        )
        self.navigation.page_selected.connect(self._show_page)
        self.navigation.replay_requested.connect(self._show_replay)
        self.navigation.traffic_scene_requested.connect(self._show_traffic_scene)
        self.navigation.project_detail_requested.connect(lambda: self._show_page("project"))
        self.navigation.workspace_exit_requested.connect(vm.leave_workspace)
        self.workspace_navigation.workspace_selected.connect(vm.select_workspace)
        self.workspace_navigation.workspace_enter_requested.connect(vm.enter_selected_workspace)
        self.workspace_navigation.search_changed.connect(vm.search_workspaces)
        self.workspace_navigation.create_requested.connect(self._create_workspace)
        self.workspace_navigation.delete_requested.connect(self._delete_workspace_entry)
        self.workspace_navigation.settings_requested.connect(lambda: self._show_page("settings"))
        self.workspace_page.enter_requested.connect(vm.enter_selected_workspace)
        self.workspace_page.rename_requested.connect(self._rename_workspace)
        self.workspace_page.delete_requested.connect(self._delete_workspace)
        self.project_detail_page.edit_requested.connect(self._edit_project)
        self.project_detail_page.create_simulation_requested.connect(
            lambda: self._show_page("scene")
        )
        self.project_detail_page.simulation_action_requested.connect(
            self._handle_project_simulation_action
        )
        self.replay_page.back_requested.connect(lambda: self._show_page("project"))
        self.replay_page.playback_requested.connect(self._open_replay)
        self.replay_page.export_requested.connect(self._export_replay)
        self.live_page.start_requested.connect(self._start_or_play)
        self.live_page.pause_requested.connect(self._pause_live_or_replay)
        self.live_page.resume_requested.connect(self._resume_live_or_replay)
        self.live_page.stop_requested.connect(self._stop_live_or_replay)
        self.live_page.restart_requested.connect(self._restart_live_or_replay)
        self.live_page.speed_changed.connect(self._set_live_or_replay_speed)
        self.scene_page.map_selected.connect(vm.select_map)
        self.scene_page.configuration_save_requested.connect(vm.save_configuration)
        self.scene_page.generation_page_requested.connect(self._open_scene_generator)
        self.generation_page.generation_requested.connect(
            lambda description: vm.generate_scene(
                description, simulation_mode=self._generation_mode
            )
        )
        self.generation_page.publish_requested.connect(vm.publish_scene)
        self.generation_page.revision_requested.connect(vm.revise_scene)
        self.generation_page.preview_retry_requested.connect(vm.retry_scene_preview)
        self.generation_page.configure_requested.connect(self._configure_generated_scene)
        self.generation_page.language_changed.connect(self._apply_scene_language)
        self.scene_page.refresh_requested.connect(vm.refresh_maps)
        self.scene_page.launch_requested.connect(self._launch_scene_configuration)
        self.scene_page.test_requested.connect(self._test_scene_configuration)
        self.traffic_scenes_page.scene_selected.connect(self._launch_traffic_scenario)
        self.traffic_scenes_page.configuration_requested.connect(self._configure_traffic_scenario)
        self.traffic_scenes_page.preview_requested.connect(vm.preview_map_asset)
        self.maps_page.import_requested.connect(self._choose_map)
        self.maps_page.preview_requested.connect(vm.preview_map_asset)
        self.agents_page.configure_requested.connect(vm.configure_agent_api)
        self.agents_page.delete_requested.connect(vm.delete_agent_api)
        self.settings_page.theme_changed.connect(self._apply_theme)

    def _connect_viewmodel(self) -> None:
        vm = self._viewmodel
        vm.workspace_catalog_changed.connect(self._set_workspaces)
        vm.workspace_selected_changed.connect(self._set_selected_workspace)
        vm.workspace_overview_changed.connect(self._set_workspace_overview)
        vm.workspace_context_changed.connect(self._set_workspace_context)
        vm.agent_catalog_changed.connect(self.agents_page.set_agents)
        vm.agent_catalog_changed.connect(self.agent_connection_page.set_assets)
        vm.experiment_id_changed.connect(self.agent_connection_viewmodel.bind_experiment)
        vm.map_catalog_changed.connect(self._set_maps)
        vm.generation_state_changed.connect(self.generation_page.set_generation_state)
        vm.scene_generation_failed.connect(self.generation_page.set_generation_error)
        vm.scene_draft_ready.connect(self.generation_page.set_draft)
        vm.scene_preview_ready.connect(self.generation_page.set_preview_image)
        vm.scene_preview_failed.connect(self.generation_page.set_preview_error)
        vm.publication_state_changed.connect(self.generation_page.set_publication_state)
        vm.revision_state_changed.connect(self.generation_page.set_revision_state)
        vm.scene_published.connect(self.generation_page.set_published)
        vm.generated_scene_selected.connect(self.scene_page.select_generated_scene)
        vm.map_manifest_changed.connect(self.maps_page.set_manifest)
        vm.asset_network_changed.connect(self.maps_page.set_preview_network)
        vm.asset_network_changed.connect(self.traffic_scenes_page.set_preview_network)
        vm.network_changed.connect(self._set_live_network)
        vm.network_changed.connect(self.scene_page.set_preview_network)
        vm.network_changed.connect(self.workspace_page.set_preview_network)
        vm.vehicles_changed.connect(self._set_vehicles)
        vm.collisions_changed.connect(self._set_collision_vehicle_ids)
        vm.traffic_lights_changed.connect(self._set_traffic_lights)
        vm.live_metrics_changed.connect(self._set_live_metrics)
        vm.experiment_status_changed.connect(self._set_status)
        vm.experiment_id_changed.connect(self.live_page.set_experiment_id)
        vm.simulation_time_changed.connect(self._set_time)
        vm.control_availability_changed.connect(self._set_controls)
        vm.connection_changed.connect(self.live_page.set_connection)
        vm.monitor_requested.connect(lambda: self._show_page("live"))
        vm.history_catalog_changed.connect(self._set_history)
        vm.replay_record_changed.connect(self._set_replay_record)
        vm.replay_network_changed.connect(self._set_replay_network)
        vm.replay_window_changed.connect(self._set_replay_window)
        vm.notification.connect(self._show_notice)
        playback = self._replay_viewmodel
        playback.frame_changed.connect(self.live_page.set_replay_frame)
        playback.state_changed.connect(self.live_page.set_replay_state)
        playback.more_requested.connect(vm.load_replay_window)
        playback.exited.connect(self._replay_exited)

    @Slot(str)
    def _show_page(self, key: str) -> None:
        if key not in {"workspace", "settings"} and self._viewmodel.active_workspace is None:
            self._show_notice("warning", "请先进入工作区，再使用仿真功能。")
            return
        page = self._pages.get(key)
        if page is None:
            return
        self.page_stack.setCurrentWidget(page)
        self.setWindowTitle(
            "SimHarness · TrafficForge"
            if page is self.generation_page
            else self._normal_window_title
        )
        self.navigation.set_active(key)
        if key != "replay":
            self.navigation.set_history_selection(None)
        if key == "traffic_scenes":
            self.navigation.set_traffic_scene_selection(
                self.traffic_scenes_page.selected_scenario.scenario_id
            )
        else:
            self.navigation.set_traffic_scene_selection(None)

    def _apply_scene_language(self, language: str) -> None:
        """Keep TrafficForge language local; page navigation must not reset the shell."""
        self._viewmodel.set_scene_language(language)

    def _open_scene_generator(self) -> None:
        if not self.generation_page.description.text().strip():
            self.generation_page.description.setText(self.scene_page.description.toPlainText())
        self._show_page("generate")

    def _configure_generated_scene(self, map_id: str) -> None:
        if self.scene_page.map_combo.findData(map_id) < 0:
            self._viewmodel.refresh_maps()
            self._show_notice("warning", "场景列表尚未加载，请稍后再次进入仿真配置。")
            return
        self.scene_page.select_generated_scene(map_id)
        self._show_page("scene")

    @Slot(str)
    def _show_replay(self, record_id: str) -> None:
        self.page_stack.setCurrentWidget(self.replay_page)
        self.navigation.set_active("replay")
        self.navigation.set_history_selection(record_id)
        self._viewmodel.load_simulation_result(record_id)

    @Slot(object)
    def _set_history(self, value: object) -> None:
        records = (
            tuple(item for item in value if isinstance(item, ReplaySummary))
            if isinstance(value, tuple)
            else ()
        )
        self.navigation.set_history(records)

    @Slot(object)
    def _set_replay_record(self, value: object) -> None:
        if not isinstance(value, ReplayRecord):
            return
        self.replay_page.set_record(value)
        self.page_stack.setCurrentWidget(self.replay_page)
        self.navigation.set_active("replay")
        self.navigation.set_history_selection(value.run_id)
        network = self._replay_networks.get(value.run_id)
        if network is not None:
            self.replay_page.set_network(network)

    @Slot(str, object)
    def _set_replay_network(self, run_id: str, network: object) -> None:
        self._replay_networks[run_id] = network
        if self.replay_page.record_id == run_id:
            self.replay_page.set_network(network)
        if self._replay_viewmodel.run_id == run_id:
            self.live_page.map_widget.set_network(network)

    @Slot(object)
    def _set_replay_window(self, value: object) -> None:
        if not isinstance(value, ReplayWindow):
            return
        first_window = self._replay_viewmodel.run_id != value.run_id
        self._replay_viewmodel.load_window(value)
        if not first_window:
            return
        self.live_page.set_replay_mode(True)
        network = self._replay_networks.get(value.run_id)
        if network is not None:
            self.live_page.map_widget.set_network(network)
        self.page_stack.setCurrentWidget(self.live_page)
        self.navigation.set_active("replay")
        self.navigation.set_history_selection(value.run_id)

    @Slot(str)
    def _open_replay(self, run_id: str) -> None:
        self._viewmodel.load_replay_window(run_id)

    @Slot(str)
    def _replay_exited(self, run_id: str) -> None:
        self.live_page.set_replay_mode(False)
        self.page_stack.setCurrentWidget(self.replay_page)
        self.navigation.set_active("replay")
        self.navigation.set_history_selection(run_id)

    @Slot(str)
    def _export_replay(self, run_id: str) -> None:
        default_name = str(Path.home() / f"simharness-simulation-{run_id}.zip")
        target, _ = QFileDialog.getSaveFileName(
            self,
            "导出仿真结果",
            default_name,
            "ZIP 压缩包 (*.zip)",
        )
        if not target:
            return
        path = Path(target)
        if path.suffix.lower() != ".zip":
            path = path.with_suffix(".zip")
        self._viewmodel.export_simulation_result(run_id, path)

    @Slot()
    def _start_or_play(self) -> None:
        if self._replay_viewmodel.active:
            self._replay_viewmodel.start()
        else:
            self._viewmodel.start()

    @Slot()
    def _pause_live_or_replay(self) -> None:
        if self._replay_viewmodel.active:
            self._replay_viewmodel.pause()
        else:
            self._viewmodel.pause()

    @Slot()
    def _resume_live_or_replay(self) -> None:
        self._start_or_play()

    @Slot()
    def _stop_live_or_replay(self) -> None:
        if self._replay_viewmodel.active:
            self._replay_viewmodel.exit()
        else:
            self._viewmodel.stop()

    @Slot()
    def _restart_live_or_replay(self) -> None:
        if self._replay_viewmodel.active:
            self._replay_viewmodel.restart()
        else:
            self._viewmodel.restart()

    @Slot(float)
    def _set_live_or_replay_speed(self, multiplier: float) -> None:
        if self._replay_viewmodel.active:
            self._replay_viewmodel.set_speed(multiplier)
        else:
            self._viewmodel.set_speed(multiplier)

    @Slot(object)
    def _set_live_network(self, network: object) -> None:
        if not self._replay_viewmodel.active:
            self.live_page.map_widget.set_network(network)

    @Slot(str)
    def _show_traffic_scene(self, scenario_id: str) -> None:
        preset = next(
            (item for item in TRAFFIC_SCENARIO_PRESETS if item.scenario_id == scenario_id),
            None,
        )
        if preset is None:
            return
        self.traffic_scenes_page.set_scenario(preset)
        self.page_stack.setCurrentWidget(self.traffic_scenes_page)
        self.navigation.set_active("traffic_scenes")
        self.navigation.set_history_selection(None)
        self.navigation.set_traffic_scene_selection(scenario_id)

    @Slot(object)
    def _set_workspaces(self, workspaces: object) -> None:
        values = (
            tuple(item for item in workspaces if isinstance(item, WorkspaceSummary))
            if isinstance(workspaces, tuple)
            else ()
        )
        selected = self.workspace_page.workspace
        selected_id = str(selected.workspace_id) if selected is not None else None
        self.workspace_navigation.set_workspaces(values, selected_id)

    @Slot(object)
    def _set_selected_workspace(self, workspace: object) -> None:
        selected = workspace if isinstance(workspace, WorkspaceSummary) else None
        self.workspace_page.set_workspace(selected)
        self.project_detail_page.set_workspace(selected)
        self.workspace_navigation.set_selected(
            str(selected.workspace_id) if selected is not None else None
        )
        active = self._viewmodel.active_workspace
        if (
            selected is not None
            and active is not None
            and selected.workspace_id == active.workspace_id
        ):
            self.navigation.set_workspace(selected.name)
        if self._viewmodel.active_workspace is None:
            self.page_stack.setCurrentWidget(self.workspace_page)

    @Slot(object)
    def _set_workspace_overview(self, overview: object) -> None:
        value = overview if isinstance(overview, WorkspaceOverview) else None
        self.workspace_page.set_overview(value)
        self.project_detail_page.set_overview(value)

    @Slot(object)
    def _set_workspace_context(self, workspace: object) -> None:
        selected = workspace if isinstance(workspace, WorkspaceSummary) else None
        if selected is None:
            self.navigation_stack.setCurrentWidget(self.workspace_navigation)
            self.page_stack.setCurrentWidget(self.workspace_page)
            return
        self.navigation.set_workspace(selected.name)
        self.navigation_stack.setCurrentWidget(self.navigation)
        self.project_detail_page.set_workspace(selected)
        self._show_page("project")

    @Slot(object)
    def _set_maps(self, maps: object) -> None:
        values = (
            tuple(item for item in maps if isinstance(item, MapSummary))
            if isinstance(maps, tuple)
            else ()
        )
        self.scene_page.set_maps(tuple(item for item in values if item.kind == "sumo"))
        self.workspace_page.set_maps(values)
        self.traffic_scenes_page.set_maps(values)
        self.maps_page.set_maps(values)

    @Slot(object)
    def _launch_traffic_scenario(self, value: object) -> None:
        if not isinstance(value, TrafficScenarioPreset):
            return
        if not self.scene_page.apply_traffic_scenario(value):
            self._show_notice("error", "场景对应的 SUMO 资源不可用，请检查场景包。")
            return
        active_statuses = {
            ExperimentStatus.CREATED,
            ExperimentStatus.PREPARING,
            ExperimentStatus.READY,
            ExperimentStatus.RUNNING,
            ExperimentStatus.PAUSED,
            ExperimentStatus.STOPPING,
        }
        message = (
            f"正在停止当前仿真，随后启动“{value.name}”……"
            if self._viewmodel.status in active_statuses
            else f"正在准备“{value.name}”并进入仿真运行……"
        )
        self._show_notice("info", message)
        self._viewmodel.set_external_stepping(False)
        self._viewmodel.launch_configuration(self.scene_page.current_configuration())

    @Slot(object)
    def _launch_scene_configuration(self, value: object) -> None:
        if not isinstance(value, SimulationConfigurationDraft):
            return
        self._viewmodel.set_external_stepping(self.scene_page.external_stepping.isChecked())
        self._viewmodel.launch_configuration(value)

    @Slot(object)
    def _test_scene_configuration(self, value: object) -> None:
        if not isinstance(value, SimulationConfigurationDraft):
            return
        self._viewmodel.set_external_stepping(self.scene_page.external_stepping.isChecked())
        self._viewmodel.test_configuration(value)

    @Slot(object)
    def _configure_traffic_scenario(self, value: object) -> None:
        if not isinstance(value, TrafficScenarioPreset):
            return
        if not self.scene_page.apply_traffic_scenario(value):
            self._show_notice("error", "场景对应的 SUMO 资源不可用，请检查场景包。")
            return
        self._show_page("scene")
        self._show_notice("info", f"已加载“{value.name}”的仿真配置。")

    @Slot(object)
    def _set_vehicles(self, vehicles: object) -> None:
        if not self._replay_viewmodel.active:
            self.live_page.map_widget.set_vehicles(vehicles)

    @Slot(object)
    def _set_collision_vehicle_ids(self, vehicle_ids: object) -> None:
        if not self._replay_viewmodel.active:
            self.live_page.map_widget.set_collision_vehicle_ids(vehicle_ids)

    @Slot(object)
    def _set_traffic_lights(self, lights: object) -> None:
        if not self._replay_viewmodel.active:
            self.live_page.map_widget.set_traffic_lights(lights)

    @Slot(object)
    def _set_live_metrics(self, metrics: object) -> None:
        if not self._replay_viewmodel.active:
            self.live_page.set_metrics(metrics)

    @Slot(str)
    def _set_status(self, status: str) -> None:
        labels = {
            "NOT_CREATED": "未创建",
            "CREATED": "已创建",
            "PREPARING": "准备中",
            "READY": "已就绪",
            "RUNNING": "运行中",
            "PAUSED": "已暂停",
            "STOPPING": "停止中",
            "COMPLETED": "已完成",
            "FAILED": "失败",
        }
        display_status = labels.get(status, status)
        if not self._replay_viewmodel.active:
            self.live_page.set_status(display_status)
        self.experiments_page.set_status(display_status)
        if status == "RUNNING" and self.notice.property("level") == "info":
            self.notice.clear()
            self.notice.hide()

    @Slot(int)
    def _set_time(self, simulation_time_ms: int) -> None:
        if not self._replay_viewmodel.active:
            self.live_page.set_time(simulation_time_ms)
        self.experiments_page.set_time(simulation_time_ms)

    @Slot(object)
    def _set_controls(self, availability: object) -> None:
        if not isinstance(availability, ControlAvailability):
            return
        if not self._replay_viewmodel.active:
            self.live_page.set_controls(availability)
        self.scene_page.set_create_enabled(availability.can_create)

    @Slot(str, str)
    def _show_notice(self, level: str, message: str) -> None:
        self._notice_timer.stop()
        self.notice.setProperty("level", level)
        self.notice.setText(message)
        self.notice.show()
        self.notice.style().unpolish(self.notice)
        self.notice.style().polish(self.notice)
        if level == "success":
            self._notice_timer.start()

    @Slot()
    def _hide_notice(self) -> None:
        self.notice.clear()
        self.notice.hide()

    def _choose_map(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入 OpenDRIVE 地图",
            str(Path.home()),
            "OpenDRIVE (*.xodr)",
        )
        if path:
            self._viewmodel.import_map(Path(path))

    def _create_workspace(self) -> None:
        run_workspace_dialog(
            WorkspaceEditDialog(title="新增工作区", parent=self),
            self._viewmodel.create_workspace,
        )

    def _rename_workspace(self) -> None:
        workspace = self.workspace_page.workspace
        if workspace is None:
            return
        run_workspace_dialog(
            WorkspaceEditDialog(title="修改工作区", workspace=workspace, parent=self),
            lambda name, description: self._viewmodel.update_workspace(
                workspace.workspace_id,
                name,
                description,
            ),
        )

    @Slot(str)
    def _edit_project(self, field: str) -> None:
        workspace = self.project_detail_page.workspace
        if workspace is None:
            return
        dialog = WorkspaceEditDialog(
            title="编辑项目信息",
            workspace=workspace,
            entity_label="项目",
            parent=self,
        )
        if field == "description":
            dialog.description_input.setFocus()
        else:
            dialog.name_input.setFocus()
            dialog.name_input.selectAll()
        run_workspace_dialog(
            dialog,
            lambda name, description: self._viewmodel.update_workspace(
                workspace.workspace_id,
                name,
                description,
            ),
        )

    @Slot(str, str, str)
    def _handle_project_simulation_action(
        self,
        simulation_name: str,
        action: str,
        parameter_summary: str,
    ) -> None:
        if action == "copy":
            self.scene_page.apply_simulation_copy(simulation_name, parameter_summary)
            self._show_page("scene")
            self._show_notice("success", f"已复制“{simulation_name}”，可继续调整参数。")
            return
        if action in {"view", "logs", "replay"}:
            self._show_page("experiments")
            label = "回放" if action == "replay" else "查看"
            self._show_notice("warning", f"“{simulation_name}”的{label}接口尚未接入。")
            return
        label = "暂停" if action == "pause" else "删除"
        self._show_notice("warning", f"“{simulation_name}”的{label}接口尚未接入。")

    def _delete_workspace(self) -> None:
        workspace = self.workspace_page.workspace
        if workspace is None:
            return
        self._confirm_delete_workspace(workspace)

    @Slot(object)
    def _delete_workspace_entry(self, workspace: object) -> None:
        if isinstance(workspace, WorkspaceSummary):
            self._confirm_delete_workspace(workspace)

    def _confirm_delete_workspace(self, workspace: WorkspaceSummary) -> None:
        dialog = WorkspaceDeleteDialog(workspace, self)
        if dialog.exec() == dialog.DialogCode.Accepted:
            self._viewmodel.delete_workspace(workspace.workspace_id)

    @Slot(str)
    def _apply_theme(self, theme_name: str) -> None:
        try:
            theme = ThemeMode(theme_name)
        except ValueError:
            return
        self.setProperty("theme", theme.value)
        self.setStyleSheet(load_stylesheet(theme))
        self.navigation.refresh_icons(theme)
        self.project_detail_page.refresh_action_icons(theme)
        self.live_page.map_widget.set_theme(theme.value)
        self.scene_page.map_widget.set_theme(theme.value)
        self.generation_page.set_theme(theme.value)
        self.workspace_page.preview_map.set_theme(theme.value)
        self.maps_page.map_widget.set_theme(theme.value)
        self.traffic_scenes_page.set_theme(theme.value)
