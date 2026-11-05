"""Opt-in checks of the distributed, console-free Windows application."""

import asyncio
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import time
import unittest

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


@unittest.skipUnless(
    os.name == "nt" and os.environ.get("GST_CREATOR_TEST_EXECUTABLE"),
    "Set GST_CREATOR_TEST_EXECUTABLE to test the built Windows application",
)
class FrozenHttpTests(unittest.TestCase):
    def test_windowed_executable_hosts_mcp_without_extra_arguments(self):
        executable = Path(os.environ["GST_CREATOR_TEST_EXECUTABLE"]).resolve()
        with executable.open("rb") as binary:
            header = binary.read(4096)
        pe_offset = struct.unpack_from("<I", header, 0x3c)[0]
        subsystem = struct.unpack_from("<H", header, pe_offset + 24 + 68)[0]
        self.assertEqual(subsystem, 2, "The executable must use Windows GUI")

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        environment = dict(os.environ)
        environment["GST_CREATOR_MCP_PORT"] = str(port)
        environment["QT_QPA_PLATFORM"] = "offscreen"
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        url = f"http://127.0.0.1:{port}/mcp"
        with tempfile.TemporaryFile() as log:
            process = subprocess.Popen(
                [str(executable)], env=environment, cwd=tempfile.gettempdir(),
                stdout=log, stderr=log,
            )
            try:
                deadline = time.monotonic() + 25
                with httpx.Client(trust_env=False, timeout=1) as client:
                    while time.monotonic() < deadline:
                        self.assertIsNone(process.poll(), "The GUI exited")
                        try:
                            client.get(url)
                            break
                        except (httpx.ConnectError, httpx.TimeoutException):
                            time.sleep(0.1)
                    else:
                        log.seek(0)
                        self.fail(f"MCP did not start: {log.read()!r}")
                asyncio.run(self.check_tools(url))
            finally:
                # One-file PyInstaller has a bootloader parent. Stop its whole
                # test process tree so no hidden editor is left running.
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=10, check=False,
                )
                process.wait(timeout=10)

    async def check_tools(self, url):
        async with streamable_http_client(url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                self.assertEqual(len(tools.tools), 7)

                async def call(name, arguments=None):
                    result = await session.call_tool(name, arguments or {})
                    self.assertFalse(result.isError, str(result.content))
                    return json.loads(result.content[0].text)

                deadline = time.monotonic() + 20
                state = await call("get_pipeline")
                while not state["discovery_ready"]:
                    self.assertLess(time.monotonic(), deadline)
                    await asyncio.sleep(0.1)
                    state = await call("get_pipeline")
                search = await call("search_elements", {"query": "fakesrc"})
                self.assertGreater(search["total"], 0)
                await call("inspect_element", {"factory": "fakesrc"})
                result = await call("apply_pipeline_edits", {
                    "expected_revision": state["revision"],
                    "operations": [{"op": "add_element", "factory": "fakesrc",
                                    "name": "packaged_source"}],
                })
                self.assertEqual(len(result["pipeline"]["elements"]), 1)
                await call("validate_pipeline")
                state = await call("undo_pipeline_edit", {
                    "expected_revision": result["pipeline"]["revision"],
                })
                self.assertEqual(state["elements"], [])
                state = await call("redo_pipeline_edit", {
                    "expected_revision": state["revision"],
                })
                self.assertEqual(len(state["elements"]), 1)


if __name__ == "__main__":
    unittest.main()
