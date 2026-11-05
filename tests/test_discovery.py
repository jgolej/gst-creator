"""Checks for inspection parsing and unavailable GStreamer tools."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import PySide6.QtCore as QtC

from gst_creator.gstreamer.discovery import ElementDiscovery, parse_elements


class DiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtC.QCoreApplication.instance() or QtC.QCoreApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.cache_path = Path(directory.name) / "cache" / "elements.json"
        fixture = (Path(__file__).parent / "fixtures/fakesink.txt").read_text()
        self.output = "\n".join(
            f"fakesink: {line}" for line in fixture.splitlines()
        )

    def discovery(self):
        discovery = ElementDiscovery(cache_path=self.cache_path)
        self.addCleanup(discovery.stop)
        return discovery

    def test_successful_discovery_is_reused_without_starting_process(self):
        discovery = self.discovery()
        process = Mock()
        process.readAllStandardOutput.return_value = self.output.encode()
        discovery._active = True
        with patch.object(discovery, "_process", process):
            discovery._finished(0, QtC.QProcess.ExitStatus.NormalExit)

        restarted = self.discovery()
        results = []
        restarted.ready.connect(results.append)
        with patch("gst_creator.gstreamer.discovery.shutil.which") as which:
            with patch.object(restarted, "_process") as process:
                restarted.start()
                process.start.assert_not_called()
            which.assert_not_called()
        self.assertTrue(restarted.from_cache)
        self.assertEqual(results, [parse_elements(self.output)])

    def test_forced_discovery_bypasses_cache_and_replaces_it(self):
        discovery = self.discovery()
        discovery._write_cache(self.output)
        updated = self.output.replace("fakesink:", "newsink:")
        process = Mock()
        process.readAllStandardOutput.return_value = updated.encode()
        with patch("gst_creator.gstreamer.discovery.shutil.which",
                   return_value="gst-inspect-1.0"):
            with patch.object(discovery, "_process", process):
                discovery.start(force_discovery=True)
                process.start.assert_called_once_with(
                    "gst-inspect-1.0", ["--print-all"]
                )
                discovery._finished(0, QtC.QProcess.ExitStatus.NormalExit)
        self.assertFalse(discovery.from_cache)
        self.assertEqual(discovery._read_cache(), parse_elements(updated))

    def test_invalid_cache_falls_back_to_inspection(self):
        self.cache_path.parent.mkdir()
        for contents in (
            "broken json", "[]", json.dumps({"version": 99}),
            json.dumps({"version": 1, "output": 123}),
            json.dumps({"version": 1, "output": "no elements"}),
        ):
            with self.subTest(contents=contents):
                self.cache_path.write_text(contents, encoding="utf-8")
                discovery = self.discovery()
                with patch("gst_creator.gstreamer.discovery.shutil.which",
                           return_value="gst-inspect-1.0"):
                    with patch.object(discovery, "_process") as process:
                        discovery.start()
                        process.start.assert_called_once()
                discovery.stop()

    def test_failed_refresh_keeps_previous_cache(self):
        discovery = self.discovery()
        discovery._write_cache(self.output)
        original = self.cache_path.read_bytes()
        discovery._active = True
        process = Mock()
        process.readAllStandardError.return_value = b"failed"
        with patch.object(discovery, "_process", process):
            discovery._finished(1, QtC.QProcess.ExitStatus.NormalExit)
        self.assertEqual(self.cache_path.read_bytes(), original)

    def test_unwritable_cache_does_not_prevent_discovery(self):
        discovery = self.discovery()
        results = []
        discovery.ready.connect(results.append)
        discovery._active = True
        process = Mock()
        process.readAllStandardOutput.return_value = self.output.encode()
        with patch.object(discovery, "_process", process):
            with patch.object(Path, "mkdir", side_effect=PermissionError):
                discovery._finished(0, QtC.QProcess.ExitStatus.NormalExit)
        self.assertEqual(results, [parse_elements(self.output)])

    def test_factory_description_is_not_replaced_by_plugin_description(self):
        elements = parse_elements(
            "fakesink: Factory Details:\n"
            "fakesink:   Long-name                Fake Sink\n"
            "fakesink:   Klass                    Sink\n"
            "fakesink:   Description              Black hole for data\n"
            "fakesink: \n"
            "fakesink: Plugin Details:\n"
            "fakesink:   Name                     coreelements\n"
            "fakesink:   Description              Core plugin description\n"
        )
        self.assertEqual(len(elements), 1)
        self.assertEqual(elements[0].factory.name, "fakesink")
        self.assertEqual(elements[0].factory.long_name, "Fake Sink")
        self.assertEqual(elements[0].factory.category, "Sink")
        self.assertEqual(elements[0].factory.description, "Black hole for data")
        self.assertEqual(elements[0].plugin.name, "coreelements")

    def test_non_elements_and_incomplete_factories_are_ignored(self):
        self.assertEqual(parse_elements(
            "typefindfunctions: audio/aac: aac, adts\n"
            "broken: Factory Details:\n"
            "broken:   Long-name                Broken\n"
        ), [])

    def test_missing_tool_produces_actionable_error(self):
        discovery = self.discovery()
        errors = []
        discovery.failed.connect(errors.append)
        with patch("gst_creator.gstreamer.discovery.shutil.which", return_value=None):
            discovery.start()
        self.assertEqual(len(errors), 1)
        self.assertIn("gst-inspect-1.0", errors[0])
        self.assertIn("PATH", errors[0])

    def test_failed_inspection_reports_exit_code_and_stderr(self):
        discovery = self.discovery()
        errors = []
        discovery.failed.connect(errors.append)
        discovery._active = True
        process = Mock()
        process.readAllStandardError.return_value = b"symbol lookup error: libpthread.so.0"
        with patch.object(discovery, "_process", process):
            discovery._finished(127, QtC.QProcess.ExitStatus.NormalExit)
        self.assertEqual(len(errors), 1)
        self.assertIn("exit code 127", errors[0])
        self.assertIn("symbol lookup error: libpthread.so.0", errors[0])


if __name__ == "__main__":
    unittest.main()
