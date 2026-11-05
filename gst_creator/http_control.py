"""Lifecycle of the local HTTP MCP server embedded in the editor."""

import logging
import os
import socket
import threading
import time

import PySide6.QtCore as QtC

import uvicorn

from gst_creator.local_control import EditorBridge
from gst_creator.mcp_server import create_mcp


DEFAULT_PORT = 8765
HOST = "127.0.0.1"


class HttpControlServer(QtC.QObject):
    """Serve MCP in a background thread; only the bridge touches the GUI."""

    failed = QtC.Signal(str)

    def __init__(self, controller, parent=None, *, port=None):
        super().__init__(parent)
        self.bridge = EditorBridge(controller, self)
        self._configured_port = port
        self.port = DEFAULT_PORT
        self._server = None
        self._thread = None
        self._socket = None
        self._error = ""

    @property
    def url(self):
        return f"http://{HOST}:{self.port}/mcp"

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            if self._server.started and not self._server.should_exit:
                return
            raise ValueError(
                "AI control is still stopping. Try again shortly."
            )
        configured = self._configured_port
        if configured is None:
            configured = os.environ.get("GST_CREATOR_MCP_PORT", DEFAULT_PORT)
        try:
            port = int(configured)
            if not 0 <= port <= 65535:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError(
                "GST_CREATOR_MCP_PORT must be a port number from 0 to 65535."
            ) from None

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if os.name == "nt":
                listener.setsockopt(
                    socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1,
                )
            else:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((HOST, port))
            listener.listen(128)
            listener.setblocking(False)
        except OSError as error:
            listener.close()
            raise ValueError(
                f"Cannot enable AI control on {HOST}:{port}. Another app "
                f"may be using this port. {error}"
            ) from error

        self.port = listener.getsockname()[1]
        self._socket = listener
        self._error = ""
        try:
            mcp = create_mcp(self.bridge.call)
            config = uvicorn.Config(
                mcp.streamable_http_app(), host=HOST, port=self.port,
                log_config=None, log_level="warning", access_log=False,
                loop="asyncio", http="h11", ws="none", lifespan="on",
                proxy_headers=False, timeout_graceful_shutdown=1,
            )
            self._server = uvicorn.Server(config)
            self.bridge.start()
            self._thread = threading.Thread(
                target=self._serve, name="gst-creator-mcp", daemon=True,
            )
            self._thread.start()
            deadline = time.monotonic() + 5
            while self._thread.is_alive() and not self._server.started:
                if time.monotonic() >= deadline:
                    raise ValueError("The local MCP server did not start.")
                time.sleep(0.01)
            if not self._server.started:
                raise ValueError(self._error or "The local MCP server failed.")
        except Exception as error:
            self.stop()
            raise ValueError(f"Cannot enable AI control. {error}") from error

    def _serve(self):
        try:
            self._server.run(sockets=[self._socket])
        except BaseException as error:
            self._error = str(error)
            logging.exception("Local MCP server failed")
            self.failed.emit(f"Local MCP server failed: {error}")

    def stop(self):
        self.bridge.stop()
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=3)
            if self._thread.is_alive():
                return
        if self._socket is not None:
            self._socket.close()
        self._socket = None
        self._thread = None
        self._server = None
