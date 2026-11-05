"""Validation results, command copying, and managed pipeline execution."""

import codecs
import shutil

import PySide6.QtCore as QtC
import PySide6.QtGui as QtG
import PySide6.QtWidgets as QtW

from gst_creator.editor.pipeline_output import build_output
from gst_creator.ui.icons import tool_icon


class PipelineOutputPanel(QtW.QWidget):
    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self._arguments = ()
        self._running = False
        self._stopping = False

        self.process = QtC.QProcess(self)
        self.process.setProcessChannelMode(
            QtC.QProcess.ProcessChannelMode.MergedChannels
        )
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)

        self.stop_timer = QtC.QTimer(self)
        self.stop_timer.setSingleShot(True)
        self.stop_timer.timeout.connect(self.process.kill)

        self.summary = QtW.QLabel()
        self.summary.setTextFormat(QtC.Qt.TextFormat.PlainText)

        self.issues = QtW.QListWidget()
        self.issues.setAccessibleName("Pipeline validation issues")
        self.issues.itemActivated.connect(self._activate_issue)

        self.command = QtW.QPlainTextEdit()
        self.command.setReadOnly(True)
        self.command.setAccessibleName("Generated POSIX shell command")
        self.command.setFont(QtG.QFontDatabase.systemFont(
            QtG.QFontDatabase.SystemFont.FixedFont
        ))

        self.copy_button = QtW.QPushButton("Copy command")
        self.copy_button.setIcon(tool_icon("copy"))
        self.copy_button.clicked.connect(self._copy)

        self.run_button = QtW.QPushButton("Run pipeline")
        self.run_button.setIcon(tool_icon("play"))
        self.run_button.clicked.connect(self._run_or_stop)

        header = QtW.QHBoxLayout()
        header.addWidget(self.summary, 1)
        header.addWidget(self.copy_button)
        header.addWidget(self.run_button)

        self.tabs = QtW.QTabWidget()
        self.tabs.addTab(self.issues, "Validation")
        self.tabs.addTab(self.command, "Command")

        self.run_output = QtW.QPlainTextEdit()
        self.run_output.setReadOnly(True)
        self.run_output.setAccessibleName("Pipeline execution output")
        self.run_output.setMaximumBlockCount(5000)

        self.tabs.addTab(self.run_output, "Run output")

        layout = QtW.QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(self.tabs)

        self.refresh()

    def refresh(self):
        definitions = {node.definition.factory.name: node.definition
                       for node in self.canvas.nodes.values()}
        output = build_output(self.canvas.document, definitions)
        errors = sum(issue.severity == "error" for issue in output.issues)
        warnings = sum(issue.severity == "warning" for issue in output.issues)
        self.summary.setText(f"Static validation: {errors} errors, "
                             f"{warnings} warnings")
        self.issues.clear()

        for issue in output.issues:
            item = QtW.QListWidgetItem(
                f"{issue.severity.upper()}: {issue.message}"
            )
            item.setToolTip(issue.message)
            item.setData(QtC.Qt.ItemDataRole.UserRole, issue)
            self.issues.addItem(item)

        self.command.setPlainText(output.command or "")
        self.command.setPlaceholderText(
            "Resolve validation errors to generate a command."
        )
        self.copy_button.setEnabled(output.command is not None)
        self._arguments = output.arguments
        self.run_button.setEnabled(
            not self._stopping and (self._running or bool(self._arguments))
        )

    def _run_or_stop(self):
        if self._running:
            self._stopping = True
            self.run_button.setEnabled(False)
            self.run_output.appendPlainText("\nStopping pipeline...")
            self.process.terminate()
            self.stop_timer.start(2000)
            return
        
        self.refresh()
        if not self._arguments:
            return
        
        self.tabs.setCurrentWidget(self.run_output)
        self.run_output.clear()
        executable = shutil.which("gst-launch-1.0")
        if executable is None:
            self.run_output.setPlainText(
                "gst-launch-1.0 was not found. Install the GStreamer tools "
                "and make sure they are available on PATH."
            )
            return
        
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._running = True
        self._stopping = False
        self.run_button.setText("Stop pipeline")
        self.run_button.setIcon(tool_icon("stop"))
        self.run_output.appendPlainText("Starting pipeline...\n")
        self.process.start(executable, list(self._arguments))

    def _read_output(self):
        text = self._decoder.decode(bytes(self.process.readAllStandardOutput()))
        self.run_output.moveCursor(QtG.QTextCursor.MoveOperation.End)
        self.run_output.insertPlainText(text)

    def _execution_done(self):
        self.stop_timer.stop()
        self._running = False
        self._stopping = False
        self.run_button.setText("Run pipeline")
        self.run_button.setIcon(tool_icon("play"))
        self.run_button.setEnabled(bool(self._arguments))

    def _finished(self, exit_code, status):
        self._read_output()
        if self._stopping:
            message = "Pipeline stopped."
        elif status == QtC.QProcess.ExitStatus.CrashExit:
            message = "Pipeline process crashed."
        else:
            message = f"Pipeline finished (exit code {exit_code})."
        self.run_output.appendPlainText(f"\n{message}")
        self._execution_done()

    def _process_error(self, error):
        self.run_output.appendPlainText(
            f"\nPipeline error: {self.process.errorString()}"
        )
        if error == QtC.QProcess.ProcessError.FailedToStart:
            self._execution_done()

    def shutdown(self):
        """Stop the child process before the application closes."""
        self.stop_timer.stop()
        if self.process.state() != QtC.QProcess.ProcessState.NotRunning:
            self.process.kill()
            self.process.waitForFinished(1000)

    def _copy(self):
        if self.copy_button.isEnabled():
            QtW.QApplication.clipboard().setText(self.command.toPlainText())

    def _activate_issue(self, item):
        issue = item.data(QtC.Qt.ItemDataRole.UserRole)
        target = self.canvas.connections.get(issue.connection_id)
        if target is None:
            target = self.canvas.nodes.get(issue.element_id)
        if target is not None:
            self.canvas.scene().clearSelection()
            target.setSelected(True)
            self.canvas.ensureVisible(target)
