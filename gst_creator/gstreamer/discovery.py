"""Read installed element metadata through the GStreamer inspection tool."""

import json
from pathlib import Path
import shutil

import PySide6.QtCore as QtC

from gst_creator.gstreamer.parser import parse_elements


DISCOVERY_TIMEOUT_MS = 120_000


class ElementDiscovery(QtC.QObject):
    """Inspect elements without blocking the GUI's event loop."""

    ready = QtC.Signal(object)
    failed = QtC.Signal(str)

    def __init__(
        self, parent: QtC.QObject | None = None,
        *, cache_path: Path | None = None,
    ) -> None:
        super().__init__(parent)

        if cache_path is None:
            cache_path = Path(QtC.QStandardPaths.writableLocation(
                QtC.QStandardPaths.StandardLocation.CacheLocation
            )) / "gst-creator" / "elements-v1.json"

        self._cache_path = cache_path
        self._from_cache = False

        self._process = QtC.QProcess(self)
        self._process.finished.connect(self._finished)
        self._process.errorOccurred.connect(self._process_error)
        
        self._timer = QtC.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._timed_out)
        
        self._active = False

    @property
    def from_cache(self) -> bool:
        """Whether the latest discovery result was loaded from cache."""
        return self._from_cache

    def start(self, *, force_discovery: bool = False) -> None:
        """Use saved metadata unless a fresh inspection is requested."""
        if self._active:
            return

        self._from_cache = False
        if not force_discovery:
            elements = self._read_cache()
            if elements:
                self._from_cache = True
                self.ready.emit(elements)
                return

        executable = shutil.which("gst-inspect-1.0")
        if executable is None:
            self.failed.emit(
                "GStreamer tools were not found. Install GStreamer and make "
                "sure gst-inspect-1.0 is available on PATH, then choose "
                "Tools > Rediscover Elements."
            )
            return

        self._active = True
        self._timer.start(DISCOVERY_TIMEOUT_MS)
        environment = QtC.QProcessEnvironment.systemEnvironment()
        environment.insert("LC_ALL", "C")
        self._process.setProcessEnvironment(environment)
        self._process.start(executable, ["--print-all"])

    def _read_cache(self) -> list:
        try:
            cached = json.loads(self._cache_path.read_text(encoding="utf-8"))
            if not isinstance(cached, dict) or cached.get("version") != 1:
                return []
            
            output = cached.get("output")
            if not isinstance(output, str):
                return []
            
            return parse_elements(output)
        except (OSError, ValueError):
            return []

    def _write_cache(self, output: str) -> None:
        # Keep the last successful cache if saving is interrupted or fails.
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        
        data = json.dumps({"version": 1, "output": output}).encode("utf-8")
        cache = QtC.QSaveFile(str(self._cache_path))
        if not cache.open(QtC.QIODevice.OpenModeFlag.WriteOnly):
            return
        
        if cache.write(data) != len(data):
            cache.cancelWriting()
            return
        
        cache.commit()

    def stop(self) -> None:
        """Release the inspection process when the window closes."""
        self._active = False
        self._timer.stop()
        if self._process.state() != QtC.QProcess.ProcessState.NotRunning:
            self._process.kill()
            self._process.waitForFinished(1000)

    def _fail(self, message: str) -> None:
        if self._active:
            self._active = False
            self._timer.stop()
            self.failed.emit(message)

    def _timed_out(self) -> None:
        self._fail(
            "GStreamer discovery timed out. Check your plugin installation."
        )
        self._process.kill()

    def _process_error(self, error: QtC.QProcess.ProcessError) -> None:
        # A crashed process also emits finished, where stderr is available.
        if error == QtC.QProcess.ProcessError.Crashed:
            return

        self._fail(
            "Could not inspect GStreamer elements: "
            f"{self._process.errorString()}"
        )

    def _finished(
        self, exit_code: int, status: QtC.QProcess.ExitStatus
    ) -> None:
        if not self._active:
            return
        
        self._timer.stop()

        if exit_code != 0 or status != QtC.QProcess.ExitStatus.NormalExit:
            details = (
                bytes(self._process.readAllStandardError())
                .decode("utf-8", errors="replace")
                .strip()
            )
            message = f"GStreamer inspection failed (exit code {exit_code})."
            if details:
                message += f"\n\n{details[-4000:]}"
            self._fail(message)
            return

        output = bytes(self._process.readAllStandardOutput())
        output = output.decode("utf-8", errors="replace")

        elements = parse_elements(output)
        if not elements:
            self._fail(
                "No usable GStreamer elements were found. Check your plugins."
            )
            return

        self._active = False
        self._write_cache(output)
        self.ready.emit(elements)
