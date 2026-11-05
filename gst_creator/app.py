"""Application startup and component wiring."""

import sys
from pathlib import Path

import PySide6.QtCore as QtC
import PySide6.QtGui as QtG
import PySide6.QtWidgets as QtW

from gst_creator import __version__
from gst_creator.gstreamer.discovery import ElementDiscovery
from gst_creator.editor.models import PipelineDocument
from gst_creator.editor.project import deserialize, save_project, serialize
from gst_creator.gstreamer.models import ElementDefinition
from gst_creator.ui.element_browser import ElementBrowser
from gst_creator.ui.pipeline_canvas import PipelineCanvas
from gst_creator.ui.element_inspector import ElementInspector
from gst_creator.ui.pipeline_output import PipelineOutputPanel
from gst_creator.ui.icons import tool_icon
from gst_creator.ui.history import EditHistory
from gst_creator.http_control import HttpControlServer
from gst_creator.ui.automation import EditorController


class MainWindow(QtW.QMainWindow):
    """Application shell, element browser, and pipeline canvas."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowIcon(QtG.QIcon(str(
            Path(__file__).parent / "assets" / "app-icon.svg"
        )))
        self.setWindowTitle("GStreamer Pipeline Creator")
        self.resize(1100, 700)

        self.element_browser = ElementBrowser()
        self.canvas = PipelineCanvas()
        self.setCentralWidget(self.canvas)
        self.element_browser.element_activated.connect(self.canvas.add_element)
        self.canvas.message.connect(
            lambda message: self.statusBar().showMessage(message, 5000)
        )
        self.browser_dock = QtW.QDockWidget("Element Browser", self)
        self.browser_dock.setObjectName("elementBrowserDock")
        self.browser_dock.setWidget(self.element_browser)
        self.browser_dock.setAllowedAreas(
            QtC.Qt.DockWidgetArea.LeftDockWidgetArea
            | QtC.Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(
            QtC.Qt.DockWidgetArea.LeftDockWidgetArea, self.browser_dock
        )
        self.resizeDocks(
            [self.browser_dock], [360], QtC.Qt.Orientation.Horizontal
        )
        self.inspector = ElementInspector(self.canvas.document)
        self.inspector_dock = QtW.QDockWidget("Selection Properties", self)
        self.inspector_dock.setObjectName("elementInspectorDock")
        self.inspector_dock.setWidget(self.inspector)
        self.addDockWidget(
            QtC.Qt.DockWidgetArea.RightDockWidgetArea, self.inspector_dock
        )
        self.canvas.selected_node_changed.connect(self.inspector.set_node)
        self.canvas.selected_node_changed.connect(
            lambda node: self.element_browser.show_element_details(
                node.definition if node is not None else None
            )
        )
        self.canvas.selected_connection_changed.connect(
            self.inspector.set_connection
        )
        self.inspector.instance_renamed.connect(
            lambda instance_id: self.canvas.nodes[instance_id].refresh_name()
        )
        self.output_panel = PipelineOutputPanel(self.canvas)
        self.output_dock = QtW.QDockWidget("Pipeline Output", self)
        self.output_dock.setObjectName("pipelineOutputDock")
        self.output_dock.setWidget(self.output_panel)
        self.addDockWidget(
            QtC.Qt.DockWidgetArea.BottomDockWidgetArea, self.output_dock
        )
        self.canvas.document_changed.connect(self.output_panel.refresh)
        self.inspector.properties_changed.connect(self.output_panel.refresh)
        self.inspector.instance_renamed.connect(self.output_panel.refresh)
        self.history = EditHistory(self.canvas, self.inspector, self)
        self.ai_control = HttpControlServer(EditorController(self), self)
        self._create_actions()
        self._create_menus_and_toolbar()
        self.project_path = None
        self._saved_project = self._project_text()
        self.canvas.document_changed.connect(self._update_project_title)
        self.inspector.properties_changed.connect(self._update_project_title)
        self.inspector.instance_renamed.connect(self._update_project_title)
        self._update_project_title()

        self.ai_control_status = QtW.QToolButton()
        self.ai_control_status.setAutoRaise(True)
        self.ai_control_status.setText("AI control off")
        self.ai_control_status.setEnabled(False)
        self.ai_control_status.setToolTip(
            "Enable Tools > Enable AI Control to connect an assistant."
        )
        self.ai_control_status.setAccessibleName("Copy MCP server URL")
        self.ai_control_status.setCursor(
            QtC.Qt.CursorShape.PointingHandCursor
        )
        self.ai_control_status.clicked.connect(self._copy_mcp_url)
        self.statusBar().addPermanentWidget(self.ai_control_status)

        self.discovery_status = QtW.QLabel("Discovering installed elements…")
        self.discovery_status.setTextFormat(QtC.Qt.TextFormat.PlainText)
        self.discovery_status.setAccessibleName("Discovery status")
        self.statusBar().addPermanentWidget(self.discovery_status)
        self.statusBar().showMessage("Ready")

        self.discovery = ElementDiscovery(self)
        self._discovered = {}
        self._has_elements = False
        self.discovery.ready.connect(self._elements_ready)
        self.discovery.failed.connect(self._discovery_failed)
        self._start_discovery()

    def _create_actions(self) -> None:
        self.ai_control_action = QtG.QAction("Enable AI Control", self)
        self.ai_control_action.setCheckable(True)
        self.ai_control_action.setStatusTip(
            "Allow local MCP assistants to inspect and edit this pipeline"
        )
        self.ai_control_action.toggled.connect(self._toggle_ai_control)
        self.ai_control.failed.connect(self._ai_control_failed)
        self.undo_action = self.history.stack.createUndoAction(self, "&Undo")
        self.redo_action = self.history.stack.createRedoAction(self, "&Redo")
        self.undo_action.setIcon(tool_icon("undo"))
        self.redo_action.setIcon(tool_icon("redo"))
        self.undo_action.setShortcuts(QtG.QKeySequence.StandardKey.Undo)
        self.redo_action.setShortcuts(QtG.QKeySequence.StandardKey.Redo)
        self.rediscover_action = QtG.QAction("&Rediscover Elements", self)
        self.rediscover_action.setIcon(tool_icon("refresh"))
        self.rediscover_action.setStatusTip(
            "Inspect installed GStreamer plugins and refresh the cache"
        )
        self.rediscover_action.triggered.connect(
            lambda: self._start_discovery(force=True)
        )
        self.new_action = QtG.QAction("&New", self)
        self.open_action = QtG.QAction("&Open…", self)
        self.save_action = QtG.QAction("&Save", self)
        self.save_as_action = QtG.QAction("Save &As…", self)
        project_actions = (
            (self.new_action, QtG.QKeySequence.StandardKey.New,
             "new"),
            (self.open_action, QtG.QKeySequence.StandardKey.Open,
             "open"),
            (self.save_action, QtG.QKeySequence.StandardKey.Save,
             "save"),
            (self.save_as_action, QtG.QKeySequence.StandardKey.SaveAs,
             "save-as"),
        )
        for action, shortcut, icon in project_actions:
            action.setShortcut(shortcut)
            action.setIcon(tool_icon(icon))
        self.new_action.triggered.connect(self._new_project)
        self.open_action.triggered.connect(self._open_project)
        self.save_action.triggered.connect(lambda: self._save_project())
        self.save_as_action.triggered.connect(
            lambda: self._save_project(save_as=True)
        )

        self.quit_action = QtG.QAction("&Quit", self)
        self.quit_action.setShortcut(QtG.QKeySequence.StandardKey.Quit)
        self.quit_action.setMenuRole(QtG.QAction.MenuRole.QuitRole)
        self.quit_action.setStatusTip("Close the application")
        self.quit_action.triggered.connect(self.close)
        self.about_action = QtG.QAction("&About", self)
        self.about_action.setMenuRole(QtG.QAction.MenuRole.AboutRole)
        self.about_action.setStatusTip("About GStreamer Pipeline Creator")
        self.about_action.triggered.connect(self._show_about)

    def _create_menus_and_toolbar(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addActions([
            self.new_action, self.open_action,
            self.save_action, self.save_as_action,
        ])
        file_menu.addSeparator()
        file_menu.addAction(self.quit_action)

        edit_menu = self.menuBar().addMenu("&Edit")
        edit_menu.addActions([self.undo_action, self.redo_action])
        edit_menu.addSeparator()
        edit_menu.addAction(self.canvas.duplicate_action)
        edit_menu.addAction(self.canvas.delete_action)

        self.toolbar = self.addToolBar("Main Toolbar")
        self.toolbar.setObjectName("mainToolbar")
        self.toolbar.setToolButtonStyle(
            QtC.Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        self.toolbar.addActions([
            self.new_action, self.open_action, self.save_action,
        ])
        self.toolbar.addSeparator()
        self.toolbar.addActions([self.undo_action, self.redo_action])
        self.toolbar.addSeparator()
        self.toolbar.addActions([
            self.canvas.zoom_in_action, self.canvas.zoom_out_action,
            self.canvas.fit_action,
        ])

        view_menu = self.menuBar().addMenu("&View")
        view_menu.addAction(self.browser_dock.toggleViewAction())
        view_menu.addAction(self.inspector_dock.toggleViewAction())
        view_menu.addAction(self.toolbar.toggleViewAction())
        view_menu.addAction(self.output_dock.toggleViewAction())
        view_menu.addSeparator()
        view_menu.addActions([
            self.canvas.zoom_in_action, self.canvas.zoom_out_action,
            self.canvas.reset_zoom_action, self.canvas.fit_action,
        ])
        tools_menu = self.menuBar().addMenu("&Tools")
        tools_menu.addAction(self.rediscover_action)
        tools_menu.addSeparator()
        tools_menu.addAction(self.ai_control_action)
        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(self.about_action)

    def _toggle_ai_control(self, enabled):
        if enabled:
            try:
                self.ai_control.start()
            except ValueError as error:
                self.ai_control_action.setChecked(False)
                self.statusBar().showMessage(str(error), 10000)
                return
            self.ai_control_action.setToolTip(self.ai_control.url)
            self.ai_control_status.setText(f"MCP: {self.ai_control.url}")
            self.ai_control_status.setToolTip(
                "Click to copy the MCP URL for your assistant's settings."
            )
            self.ai_control_status.setEnabled(True)
            self.statusBar().showMessage(
                f"AI control: {self.ai_control.url}", 10000,
            )
        else:
            self.ai_control.stop()
            self.ai_control_status.setText("AI control off")
            self.ai_control_status.setEnabled(False)
            self.ai_control_status.setToolTip(
                "Enable Tools > Enable AI Control to connect an assistant."
            )
            self.statusBar().showMessage("Local AI control disabled", 5000)

    def _copy_mcp_url(self):
        QtW.QApplication.clipboard().setText(self.ai_control.url)
        self.statusBar().showMessage("MCP URL copied to clipboard", 5000)

    def _ai_control_failed(self, message):
        self.ai_control_action.setChecked(False)
        self.statusBar().showMessage(message, 10000)

    def _project_text(self):
        return serialize(self.canvas.document)

    def _update_project_title(self):
        modified = self._project_text() != self._saved_project
        name = self.project_path.name if self.project_path else "Untitled"
        self.setWindowTitle(f"{name}[*] - GStreamer Pipeline Creator")
        self.setWindowModified(modified)

    def _confirm_project_change(self):
        if self._project_text() == self._saved_project:
            return True
        buttons = QtW.QMessageBox.StandardButton
        answer = QtW.QMessageBox.warning(
            self, "Unsaved changes", "Save changes to the current project?",
            buttons.Save | buttons.Discard | buttons.Cancel, buttons.Save,
        )
        if answer == buttons.Save:
            return self._save_project()
        return answer == buttons.Discard

    def _replace_project(self, document, path=None):
        self.output_panel.shutdown()
        self.inspector.set_node(None)
        self.inspector.document = document
        self.canvas.set_document(document, {
            element.id: self._discovered[element.factory_name]
            for element in document.elements.values()
        })
        self.history.reset()
        self.output_panel.run_output.clear()
        self.project_path = path
        self._saved_project = self._project_text()
        self._update_project_title()

    def _new_project(self):
        if self._confirm_project_change():
            self._replace_project(PipelineDocument())

    def _open_project(self):
        if not self.open_action.isEnabled():
            return
        filename, _ = QtW.QFileDialog.getOpenFileName(
            self, "Open pipeline project", "",
            "GStreamer projects (*.gstproj);;All files (*)",
        )
        if not filename:
            return
        path = Path(filename)
        try:
            document = deserialize(
                path.read_text(encoding="utf-8"), self._discovered,
            )
        except (OSError, ValueError) as error:
            QtW.QMessageBox.critical(self, "Cannot open project", str(error))
            return
        if self._confirm_project_change():
            self._replace_project(document, path)
            self.statusBar().showMessage(f"Opened {path.name}", 5000)

    def _save_project(self, *, save_as=False):
        path = self.project_path
        if save_as or path is None:
            dialog = QtW.QFileDialog(self, "Save pipeline project")
            dialog.setAcceptMode(QtW.QFileDialog.AcceptMode.AcceptSave)
            dialog.setNameFilter("GStreamer projects (*.gstproj)")
            dialog.setDefaultSuffix("gstproj")
            dialog.selectFile(str(path) if path else "pipeline.gstproj")
            if not dialog.exec():
                return False
            path = Path(dialog.selectedFiles()[0])

        try:
            text = self._project_text()
            save_project(path, text)
        except (OSError, ValueError) as error:
            QtW.QMessageBox.critical(self, "Cannot save project", str(error))
            return False
        
        self.project_path = path
        self._saved_project = text
        self.history.break_group()
        self._update_project_title()
        self.statusBar().showMessage(f"Saved {path.name}", 5000)
        
        return True

    def _start_discovery(self, *, force: bool = False) -> None:
        self.open_action.setEnabled(False)
        self.rediscover_action.setEnabled(False)
        self.discovery_status.setText("Discovering installed elements...")
        self.discovery_status.setToolTip("")
        self.discovery.start(force_discovery=force)

    def _elements_ready(self, elements: list[ElementDefinition]) -> None:
        self._discovered = {element.factory.name: element for element in elements}
        self.open_action.setEnabled(True)
        self._has_elements = True
        self.rediscover_action.setEnabled(True)
        self.element_browser.set_elements(elements)
        self.discovery_status.setText(f"{len(elements)} installed elements")
        self.discovery_status.setToolTip("")
        message = (
            "Elements loaded from cache" if self.discovery.from_cache
            else "Element discovery complete"
        )
        self.statusBar().showMessage(message, 5000)

    def _discovery_failed(self, message: str) -> None:
        self.open_action.setEnabled(self._has_elements)
        self.rediscover_action.setEnabled(True)
        if self._has_elements:
            self.element_browser.status.setText(
                f"Refresh failed; keeping previous elements.\n{message}"
            )
        else:
            self.element_browser.show_error(message)
        self.discovery_status.setText("Element discovery failed")
        self.discovery_status.setToolTip(message)
        self.statusBar().showMessage(
            "See Element Browser for discovery error details."
        )

    def _show_about(self) -> None:
        QtW.QMessageBox.about(
            self,
            "About GStreamer Pipeline Creator",
            "<h3>GStreamer Pipeline Creator</h3>"
            f"<p>Version {__version__}</p>"
            "<p>A graphical editor for building GStreamer pipelines.</p>"
            "<p>Browse installed elements and arrange them on the canvas. "
            "Connect their pads to plan a pipeline. "
            "Save and reopen pipelines as project files.</p>",
        )

    def closeEvent(self, event: QtG.QCloseEvent) -> None:
        if not self._confirm_project_change():
            event.ignore()
            return
        self.output_panel.shutdown()
        self.discovery.stop()
        self.ai_control.stop()
        super().closeEvent(event)


def main() -> int:
    """Run the Qt application and return its exit status."""
    app = QtW.QApplication(sys.argv)
    app.setApplicationName("GStreamer Pipeline Creator")
    app.setApplicationVersion(__version__)

    window = MainWindow()
    window.ai_control_action.setChecked(True)
    window.showMaximized()

    return app.exec()
