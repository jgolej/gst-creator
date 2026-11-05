"""Pipeline launch arguments and asynchronous process lifecycle."""

import json
import sys
import unittest
from unittest.mock import patch

import PySide6.QtCore as QtC
import PySide6.QtTest as QtT
import PySide6.QtWidgets as QtW

from gst_creator.editor.pipeline_output import PipelineOutput
from gst_creator.ui.pipeline_canvas import PipelineCanvas
from gst_creator.ui.pipeline_output import PipelineOutputPanel
from test_pipeline_output import DEFINITIONS


class PipelineExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtW.QApplication.instance() or QtW.QApplication([])

    def setUp(self):
        self.canvas = PipelineCanvas()
        self.panel = PipelineOutputPanel(self.canvas)
        self.addCleanup(self.canvas.close)
        self.addCleanup(self.panel.close)
        self.addCleanup(self.panel.shutdown)

    def test_invalid_pipeline_cannot_run(self):
        self.assertFalse(self.panel.run_button.isEnabled())
        with patch.object(self.panel.process, "start") as start:
            self.panel._run_or_stop()
            start.assert_not_called()

    def test_launch_preserves_property_arguments_without_shell(self):
        source = self.canvas.add_element(DEFINITIONS['filesrc'])
        value = 'C:/some folder/a "quote" $HOME `x` ! ; file.txt'
        source.property_overrides['location'] = json.dumps(value)
        with patch('gst_creator.ui.pipeline_output.shutil.which',
                   return_value='C:/GStreamer/bin/gst-launch-1.0.exe'):
            with patch.object(self.panel.process, 'start') as start:
                self.panel._run_or_stop()
        executable, arguments = start.call_args.args
        self.assertEqual(executable, 'C:/GStreamer/bin/gst-launch-1.0.exe')
        self.assertIn('location="' + value.replace('"', '\\"') + '"',
                      arguments)
        self.assertEqual(self.panel.run_button.text(), 'Stop pipeline')

    def test_missing_tool_and_failed_start_allow_retry(self):
        self.canvas.add_element(DEFINITIONS['fakesrc'])
        with patch('gst_creator.ui.pipeline_output.shutil.which',
                   return_value=None):
            self.panel._run_or_stop()
        self.assertIn('not found', self.panel.run_output.toPlainText())
        with patch('gst_creator.ui.pipeline_output.shutil.which',
                   return_value='missing-gst-executable-for-test'):
            self.panel._run_or_stop()
            self.panel.process.waitForStarted(2000)
        self.assertFalse(self.panel._running)
        self.assertTrue(self.panel.run_button.isEnabled())
        self.assertIn('Pipeline error:', self.panel.run_output.toPlainText())

    def launch_helper(self, script):
        output = PipelineOutput((), 'helper', ('-c', script))
        with patch('gst_creator.ui.pipeline_output.build_output',
                   return_value=output):
            with patch('gst_creator.ui.pipeline_output.shutil.which',
                       return_value=sys.executable):
                self.panel._run_or_stop()
        self.assertTrue(self.panel.process.waitForStarted(2000))

    def test_completion_shows_output_and_exit_code(self):
        self.launch_helper("import sys; print('pipeline output'); sys.exit(3)")
        self.assertTrue(self.panel.process.waitForFinished(5000))
        self.assertIn('pipeline output', self.panel.run_output.toPlainText())
        self.assertIn('exit code 3', self.panel.run_output.toPlainText())
        self.assertEqual(self.panel.run_button.text(), 'Run pipeline')

    def test_stop_terminates_process_and_allows_another_run(self):
        self.launch_helper('import time; time.sleep(30)')
        self.panel.run_button.click()
        for _ in range(60):
            if not self.panel._running:
                break
            QtT.QTest.qWait(50)
        self.assertFalse(self.panel._running)
        self.assertEqual(self.panel.process.state(),
                         QtC.QProcess.ProcessState.NotRunning)
        self.assertTrue(self.panel.run_button.isEnabled())
        self.assertIn('Pipeline stopped.', self.panel.run_output.toPlainText())

    def test_shutdown_terminates_running_child(self):
        self.launch_helper('import time; time.sleep(30)')
        self.panel.shutdown()
        self.assertEqual(self.panel.process.state(),
                         QtC.QProcess.ProcessState.NotRunning)


if __name__ == '__main__':
    unittest.main()
