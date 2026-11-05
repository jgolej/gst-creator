"""Transactional AI edits and real HTTP MCP-to-GUI round trips."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
import socket
import time
import unittest
from unittest.mock import patch

import PySide6.QtWidgets as QtW

import httpx

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from gst_creator.app import MainWindow
from gst_creator.http_control import HttpControlServer
from gst_creator.ui.automation import EditorController
from test_pipeline_output import DEFINITIONS


def pair():
    return [
        {"op": "add_element", "factory": "fakesrc", "name": "source",
         "properties": {"num-buffers": 3}},
        {"op": "add_element", "factory": "fakesink", "name": "output"},
        {"op": "connect", "source": "source", "source_pad": "src",
         "sink": "output", "sink_pad": "sink"},
        {"op": "auto_layout"},
    ]


class AutomationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtW.QApplication.instance() or QtW.QApplication([])

    def setUp(self):
        with patch("gst_creator.app.ElementDiscovery.start"):
            self.window = MainWindow()
        self.window.discovery.ready.emit(list(DEFINITIONS.values()))
        self.controller = EditorController(self.window)
        self.addCleanup(self.window.close)
        self.addCleanup(lambda: setattr(
            self.window, "_saved_project", self.window._project_text(),
        ))

    def edit(self, operations):
        return self.controller.apply_pipeline_edits(
            operations, self.controller.get_pipeline()["revision"],
        )

    def pump(self, function):
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(function)
            deadline = time.monotonic() + 25
            while not future.done() and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.005)
            self.assertTrue(future.done(), "IPC request did not finish")
            return future.result()

    def test_create_updates_canvas_output_dirty_state_and_single_undo(self):
        result = self.edit(pair())
        self.assertEqual(len(self.window.canvas.nodes), 2)
        self.assertEqual(len(self.window.canvas.connections), 1)
        self.assertTrue(self.window.isWindowModified())
        self.assertIn("num-buffers=3", result["validation"]["command"])
        self.assertEqual(self.window.history.stack.count(), 1)
        self.assertEqual(
            self.window.history.stack.undoText(), "AI pipeline edit",
        )
        self.window.history.stack.undo()
        self.assertFalse(self.window.canvas.nodes)
        self.assertFalse(self.window.isWindowModified())
        self.window.history.stack.redo()
        self.assertEqual(len(self.window.canvas.connections), 1)
        self.assertIs(
            self.window.inspector.document, self.window.canvas.document,
        )

    def test_property_update_refreshes_selected_inspector_and_undo(self):
        self.edit(pair())
        source = None
        for node in self.window.canvas.nodes.values():
            if node.instance.name == "source":
                source = node
                break
        source.setSelected(True)
        self.edit([{"op": "set_properties", "element": source.instance.id,
                    "properties": {"num-buffers": 7}}])
        self.assertEqual(
            self.window.inspector.editors["num-buffers"].value(), 7,
        )
        self.window.history.stack.undo()
        self.assertEqual(
            self.window.inspector.editors["num-buffers"].value(), 3,
        )
        self.window.history.stack.redo()
        self.edit([{"op": "set_properties", "element": "source",
                    "properties": {"num-buffers": None}}])
        self.assertEqual(
            self.window.inspector.editors["num-buffers"].value(), -1,
        )

    def test_insert_element_and_edit_caps(self):
        result = self.edit(pair())
        link = result["pipeline"]["connections"][0]["id"]
        result = self.edit([
            {"op": "disconnect", "connection_id": link},
            {"op": "add_element", "factory": "queue", "name": "buffer"},
            {"op": "connect", "source": "source", "source_pad": "src",
             "sink": "buffer", "sink_pad": "sink"},
            {"op": "connect", "source": "buffer", "source_pad": "src",
             "sink": "output", "sink_pad": "sink", "caps": "video/x-raw"},
            {"op": "auto_layout"},
        ])
        self.assertEqual(len(self.window.canvas.nodes), 3)
        self.assertEqual(len(self.window.canvas.connections), 2)
        self.assertIn("capsfilter", result["validation"]["command"])
        self.window.history.stack.undo()
        self.assertEqual(list(self.window.canvas.connections), [link])

    def test_failure_rolls_back_entire_batch(self):
        self.edit(pair())
        before = self.controller.get_pipeline()
        count = self.window.history.stack.count()
        with self.assertRaisesRegex(ValueError, "not installed"):
            self.edit([
                {"op": "clear"},
                {"op": "add_element", "factory": "missing", "name": "bad"},
            ])
        self.assertEqual(self.controller.get_pipeline(), before)
        self.assertEqual(self.window.history.stack.count(), count)

    def test_stale_revision_does_not_overwrite_manual_edits(self):
        revision = self.controller.get_pipeline()["revision"]
        self.window.canvas.add_element(DEFINITIONS["queue"])
        with self.assertRaisesRegex(ValueError, "pipeline changed"):
            self.controller.apply_pipeline_edits(pair(), revision)
        self.assertEqual(len(self.window.canvas.nodes), 1)

    def test_invalid_properties_names_and_occupied_pads_are_rejected(self):
        self.edit(pair())
        cases = [
            [{"op": "set_properties", "element": "source",
              "properties": {"num-buffers": -2}}],
            [{"op": "set_properties", "element": "source",
              "properties": {"unknown": 1}}],
            [{"op": "rename_element", "element": "source", "name": "output"}],
            [{"op": "rename_element", "element": "source",
              "name": "bad name"}],
            [{"op": "connect", "source": "source", "source_pad": "src",
              "sink": "output", "sink_pad": "sink"}],
            [{"op": "move_element", "element": "source", "x": float("nan"),
              "y": 0}],
            [{"op": "clear", "unexpected": True}],
        ]
        before = self.controller.get_pipeline()
        for operations in cases:
            with self.subTest(operations=operations):
                with self.assertRaises(ValueError):
                    self.edit(operations)
                self.assertEqual(self.controller.get_pipeline(), before)

    def test_strings_use_natural_values_and_safe_storage(self):
        location = 'C:/media/it\'s a "test" ž.mp4'
        result = self.edit([
            {"op": "add_element", "factory": "filesrc", "name": "input",
             "properties": {"location": location}},
        ])
        element = result["pipeline"]["elements"][0]
        self.assertEqual(element["properties"]["location"], location)
        instance = self.window.canvas.document.elements[element["id"]]
        self.assertEqual(json.loads(instance.property_overrides["location"]),
                         location)
        self.assertIsNotNone(result["validation"]["command"])

    def test_request_pad_branches_get_distinct_slots(self):
        self.edit([
            {"op": "add_element", "factory": "fakesrc", "name": "source"},
            {"op": "add_element", "factory": "tee", "name": "split"},
            {"op": "add_element", "factory": "fakesink", "name": "out1"},
            {"op": "add_element", "factory": "fakesink", "name": "out2"},
            {"op": "connect", "source": "source", "source_pad": "src",
             "sink": "split", "sink_pad": "sink"},
            {"op": "connect", "source": "split", "source_pad": "src_%u",
             "sink": "out1", "sink_pad": "sink"},
            {"op": "connect", "source": "split", "source_pad": "src_%u",
             "sink": "out2", "sink_pad": "sink"},
            {"op": "auto_layout"},
        ])
        slots = []
        for link in self.window.canvas.document.connections.values():
            if link.source.template_name == "src_%u":
                slots.append(link.source.slot)
        self.assertEqual(slots, [0, 1])

    def test_remove_clear_rename_move_and_caps(self):
        result = self.edit(pair())
        link = result["pipeline"]["connections"][0]["id"]
        self.edit([
            {"op": "set_caps", "connection_id": link, "caps": "video/x-raw"},
            {"op": "rename_element", "element": "source", "name": "input"},
            {"op": "move_element", "element": "input", "x": 100, "y": 200},
        ])
        self.assertEqual(self.window.canvas.connections[link].connection.caps,
                         "video/x-raw")
        self.edit([{"op": "remove_element", "element": "input"}])
        self.assertFalse(self.window.canvas.connections)
        self.edit([{"op": "clear"}])
        self.assertFalse(self.window.canvas.nodes)
        self.window.history.stack.undo()
        self.assertEqual(len(self.window.canvas.nodes), 1)

    def test_inspection_pagination_and_discovery_errors(self):
        result = self.controller.search_elements("fake", limit=1)
        self.assertEqual(result["total"], 2)
        self.assertEqual(len(result["elements"]), 1)
        definition = self.controller.inspect_element("tee")
        self.assertEqual(definition["pads"][1]["name_template"], "src_%u")
        self.assertNotIn("raw_text", definition)
        self.window._has_elements = False
        self.assertFalse(self.controller.get_pipeline()["discovery_ready"])
        with self.assertRaisesRegex(ValueError, "discovery is not ready"):
            self.controller.search_elements()
        with self.assertRaises(ValueError):
            self.edit(pair())

    def test_modal_dialog_blocks_mutation(self):
        with patch.object(QtW.QApplication, "activeModalWidget",
                          return_value=object()):
            with self.assertRaisesRegex(ValueError, "open dialog"):
                self.edit(pair())

    def start_http(self):
        self.window.ai_control._configured_port = 0
        self.window.ai_control_action.setChecked(True)
        self.assertTrue(self.window.ai_control_action.isChecked())
        return self.window.ai_control

    def test_http_lifecycle_and_duplicate_endpoint(self):
        server = self.start_http()
        port = server.port
        server._configured_port = port
        duplicate = HttpControlServer(self.controller, self.window, port=port)
        self.addCleanup(duplicate.stop)
        with self.assertRaisesRegex(ValueError, "Another app"):
            duplicate.start()
        result = self.pump(lambda: server.bridge.call("get_pipeline", {}))
        self.assertEqual(result["elements"], [])
        self.window.ai_control_action.setChecked(False)
        self.assertIsNone(server._thread)
        with socket.socket() as probe:
            self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0)
        self.window.ai_control_action.setChecked(True)
        self.assertEqual(server.port, port)
        result = self.pump(lambda: server.bridge.call("get_pipeline", {}))
        self.assertEqual(result["elements"], [])
        self.window.close()
        self.assertIsNone(server._thread)

    def test_http_rejects_untrusted_origin_and_host(self):
        server = self.start_http()
        headers = {"Accept": "application/json, text/event-stream"}
        message = {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "1"}},
        }
        with httpx.Client(trust_env=False) as client:
            origin = client.post(
                server.url, json=message,
                headers=dict(headers, Origin="https://untrusted.example"),
            )
            self.assertEqual(origin.status_code, 403)
            host = client.post(
                server.url, json=message,
                headers=dict(headers, Host="untrusted.example"),
            )
            self.assertEqual(host.status_code, 421)

    def test_invalid_port_leaves_ai_control_disabled(self):
        with patch.dict(os.environ, {"GST_CREATOR_MCP_PORT": "invalid"}):
            self.window.ai_control_action.setChecked(True)
        self.assertFalse(self.window.ai_control_action.isChecked())
        self.assertIn("port number", self.window.statusBar().currentMessage())
        self.assertIsNone(self.window.ai_control._thread)

    def test_expired_request_cannot_edit_when_gui_resumes(self):
        server = self.start_http()
        revision = self.controller.get_pipeline()["revision"]
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                server.bridge.call, "apply_pipeline_edits",
                {"operations": pair(), "expected_revision": revision},
                timeout=0.02,
            )
            with self.assertRaisesRegex(ValueError, "did not respond"):
                future.result(timeout=2)
        self.app.processEvents()
        self.assertFalse(self.window.canvas.nodes)

    def test_disabling_cancels_queued_edits_even_after_restart(self):
        server = self.start_http()
        revision = self.controller.get_pipeline()["revision"]
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                server.bridge.call, "apply_pipeline_edits",
                {"operations": pair(), "expected_revision": revision},
            )
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with server.bridge._lock:
                    pending = bool(server.bridge._pending)
                if pending:
                    break
                time.sleep(0.005)
            self.assertTrue(pending)
            self.window.ai_control_action.setChecked(False)
            with self.assertRaisesRegex(ValueError, "disabled"):
                future.result(timeout=2)
        self.window.ai_control_action.setChecked(True)
        self.app.processEvents()
        self.assertFalse(self.window.canvas.nodes)

    async def exercise_http(self, url):
        async with streamable_http_client(url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                self.assertEqual(len(tools.tools), 7)

                async def call(tool, arguments=None):
                    result = await session.call_tool(tool, arguments or {})
                    self.assertFalse(result.isError, str(result.content))
                    return json.loads(result.content[0].text)

                state = await call("get_pipeline")
                await call("search_elements", {"query": "fake"})
                await call("inspect_element", {"factory": "fakesrc"})
                result = await call("apply_pipeline_edits", {
                    "operations": pair(),
                    "expected_revision": state["revision"],
                })
                state = result["pipeline"]
                result = await call("apply_pipeline_edits", {
                    "operations": [{
                        "op": "set_properties", "element": "source",
                        "properties": {"num-buffers": 9},
                    }],
                    "expected_revision": state["revision"],
                })
                self.assertIn(
                    "num-buffers=9", result["validation"]["command"],
                )
                failed = await session.call_tool("apply_pipeline_edits", {
                    "operations": [{"op": "clear"}],
                    "expected_revision": state["revision"],
                })
                self.assertTrue(failed.isError)
                validation = await call("validate_pipeline")
                state = await call("undo_pipeline_edit", {
                    "expected_revision": validation["revision"],
                })
                await call("redo_pipeline_edit", {
                    "expected_revision": state["revision"],
                })
                return await call("get_pipeline")

    def test_http_mcp_client_creates_and_refines_live_gui(self):
        server = self.start_http()
        result = self.pump(lambda: asyncio.run(self.exercise_http(server.url)))
        self.assertEqual(len(self.window.canvas.nodes), 2)
        self.assertEqual(len(self.window.canvas.connections), 1)
        self.assertEqual(self.window.history.stack.count(), 2)
        self.assertIn(
            "num-buffers=9", self.controller.validate_pipeline()["command"],
        )
        self.assertEqual(len(result["elements"]), 2)


if __name__ == "__main__":
    unittest.main()
