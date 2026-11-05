"""Dispatch MCP requests from the HTTP thread to the Qt GUI thread."""

import logging
import threading
import time
from dataclasses import dataclass, field

import PySide6.QtCore as QtC


@dataclass(eq=False)
class EditorRequest:
    method: str
    arguments: dict
    deadline: float
    generation: int
    done: threading.Event = field(default_factory=threading.Event)
    result: object = None
    error: str = ""


class EditorBridge(QtC.QObject):
    """Keep all editor access on its owning thread, including cancellation."""

    requested = QtC.Signal(object)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._lock = threading.Lock()
        self._pending = set()
        self._enabled = False
        self._generation = 0
        self.requested.connect(
            self._dispatch, QtC.Qt.ConnectionType.QueuedConnection,
        )

    def start(self):
        with self._lock:
            self._enabled = True

    def stop(self):
        with self._lock:
            self._enabled = False
            self._generation += 1
            for request in self._pending:
                request.error = "AI control was disabled."
                request.done.set()

    def call(self, method, arguments, *, timeout=10):
        """Wait in a worker thread; never retry a possibly committed edit."""
        with self._lock:
            if not self._enabled:
                raise ValueError("AI control is disabled.")
            request = EditorRequest(
                method, arguments, time.monotonic() + timeout,
                self._generation,
            )
            self._pending.add(request)
        try:
            self.requested.emit(request)
            if not request.done.wait(timeout):
                with self._lock:
                    request.error = (
                        "The editor did not respond. Read get_pipeline before "
                        "retrying any edit; it may already have been applied."
                    )
                    request.done.set()
            if request.error:
                raise ValueError(request.error)
            return request.result
        finally:
            with self._lock:
                self._pending.discard(request)

    @QtC.Slot(object)
    def _dispatch(self, request):
        with self._lock:
            if request.done.is_set():
                return
            if (not self._enabled
                    or request.generation != self._generation):
                request.error = "AI control is disabled."
                request.done.set()
                return
            if time.monotonic() >= request.deadline:
                request.error = "The editor request expired."
                request.done.set()
                return
        try:
            request.result = self.controller.dispatch(
                request.method, request.arguments,
            )
        except (ValueError, TypeError, KeyError) as error:
            request.error = str(error)
        except Exception:
            logging.exception("Local AI control request failed")
            request.error = "Internal editor error; inspect the app log."
        finally:
            request.done.set()
