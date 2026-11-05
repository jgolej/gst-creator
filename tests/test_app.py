"""Discovery controls in the application window."""

import unittest
from unittest.mock import patch

import PySide6.QtWidgets as QtW

from gst_creator.app import MainWindow
from gst_creator.gstreamer.models import (
    ElementDefinition, FactoryInfo, PluginInfo,
)


class DiscoveryMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtW.QApplication.instance() or QtW.QApplication([])

    def setUp(self):
        patcher = patch("gst_creator.app.ElementDiscovery.start")
        self.start = patcher.start()
        self.addCleanup(patcher.stop)
        self.window = MainWindow()
        self.addCleanup(self.window.close)
        self.addCleanup(lambda: setattr(
            self.window, "_saved_project", self.window._project_text()
        ))
        self.elements = [ElementDefinition(
            FactoryInfo("tee", "Tee", "Generic", "Split"), PluginInfo(),
        )]

    def test_refresh_bypasses_cache_and_disables_action_until_ready(self):
        self.start.assert_called_once_with(force=False)
        action = self.window.rediscover_action
        self.assertFalse(action.isEnabled())
        self.window.discovery.ready.emit(self.elements)
        self.assertTrue(action.isEnabled())
        action.trigger()
        self.start.assert_called_with(force=True)
        self.assertFalse(action.isEnabled())
        self.window.discovery.ready.emit(self.elements)
        self.assertTrue(action.isEnabled())

    def test_failed_refresh_preserves_browser_and_canvas(self):
        self.window.discovery.ready.emit(self.elements)
        instance = self.window.canvas.add_element(self.elements[0])
        self.window.rediscover_action.trigger()
        self.window.discovery.failed.emit("Inspection failed")
        self.assertTrue(self.window.rediscover_action.isEnabled())
        browser = self.window.element_browser
        self.assertTrue(browser.search.isEnabled())
        self.assertEqual(browser.element_list.topLevelItemCount(), 1)
        self.assertIn("Inspection failed", browser.status.text())
        self.assertIn(instance.id, self.window.canvas.document.elements)

    def test_startup_failure_allows_retry(self):
        self.window.discovery.failed.emit("Missing tool")
        self.assertTrue(self.window.rediscover_action.isEnabled())
        self.assertFalse(self.window.element_browser.search.isEnabled())
        self.window.rediscover_action.trigger()
        self.start.assert_called_with(force=True)


if __name__ == "__main__":
    unittest.main()
