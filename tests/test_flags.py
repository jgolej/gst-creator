"""Flags output, editing, and execution with real GStreamer metadata."""

from dataclasses import replace
import shutil
import subprocess
import unittest

import PySide6.QtWidgets as QtW

from gst_creator.gstreamer.models import PropertyType
from gst_creator.editor.models import PipelineDocument
from gst_creator.editor.pipeline_output import _property_value, build_output
from gst_creator.editor.project import deserialize, serialize
from gst_creator.gstreamer.parser import parse_elements
from gst_creator.gstreamer.models import (
    PropertyChoice, PropertyDefinition, PropertyFlags,
)
from gst_creator.ui.element_inspector import ElementInspector
from gst_creator.ui.pipeline_canvas import PipelineCanvas
from test_pipeline_output import DEFINITIONS


TUNE = PropertyDefinition(
    'tune', '', PropertyType.FLAGS,
    PropertyFlags(readable=True, writable=True),
    0, choices=(
        PropertyChoice('0x00000001', 'stillimage', 'Still image'),
        PropertyChoice('0x00000002', 'fastdecode', 'Fast decode'),
        PropertyChoice('0x00000004', 'zerolatency', 'Zero latency'),
    ),
)


class FlagsTests(unittest.TestCase):
    def test_names_combinations_and_numeric_masks(self):
        for value, expected in (
            ('zerolatency', '4'), ('fastdecode+zerolatency', '6'),
            ('fastdecode | zerolatency', '6'), ('0x00000004', '4'),
            ('6', '6'), ('0', '0'), ('04', '4'),
        ):
            with self.subTest(value=value):
                self.assertEqual(_property_value(TUNE, value), expected)

    def test_invalid_flags_and_read_only_values(self):
        for value in ('', 'unknown', '-1', '8', '0x10', '4294967296',
                      'zerolatency+', 'zerolatency ! fakesink'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _property_value(TUNE, value)
        with self.assertRaises(ValueError):
            _property_value(
                replace(TUNE, flags=PropertyFlags(readable=True)),
                'zerolatency',
            )

    def test_command_output_and_project_round_trip(self):
        discovered = dict(DEFINITIONS)
        discovered['fakesrc'] = replace(
            discovered['fakesrc'], properties=(TUNE,),
        )
        document = PipelineDocument()
        source = document.add_element(
            'fakesrc', pad_templates=discovered['fakesrc'].pads,
        )
        source.property_overrides['tune'] = 'fastdecode+zerolatency'
        sink = document.add_element(
            'fakesink', pad_templates=discovered['fakesink'].pads,
        )
        document.connect(source.id, 'src', sink.id, 'sink')
        output = build_output(document, discovered)
        self.assertIn('tune=6', output.command)
        restored = deserialize(serialize(document), discovered)
        self.assertEqual(build_output(restored, discovered).command,
                         output.command)

    @unittest.skipUnless(shutil.which('gst-inspect-1.0')
                         and shutil.which('gst-launch-1.0'),
                         'Requires GStreamer')
    def test_real_x264_pipeline_runs_to_eos(self):
        discovered = {}
        for name in ('videotestsrc', 'x264enc', 'fakesink'):
            inspection = subprocess.run(
                [shutil.which('gst-inspect-1.0'), name], capture_output=True,
                text=True, timeout=10,
            )
            if inspection.returncode:
                self.skipTest(f'Requires {name}')
            discovered[name] = parse_elements('\n'.join(
                f'{name}: {line}' for line in inspection.stdout.splitlines()
            ))[0]
        document = PipelineDocument()
        source = document.add_element(
            'videotestsrc', pad_templates=discovered['videotestsrc'].pads,
        )
        source.property_overrides['num-buffers'] = '3'
        encoder = document.add_element(
            'x264enc', pad_templates=discovered['x264enc'].pads,
        )
        encoder.property_overrides.update({
            'tune': 'zerolatency', 'speed-preset': 'ultrafast',
        })
        sink = document.add_element(
            'fakesink', pad_templates=discovered['fakesink'].pads,
        )
        link = document.connect(source.id, 'src', encoder.id, 'sink')
        document.connections[link.id] = replace(
            link, caps='video/x-raw,format=I420,width=64,height=64',
        )
        document.connect(encoder.id, 'src', sink.id, 'sink')
        output = build_output(document, discovered)
        self.assertIsNotNone(output.command, output.issues)
        arguments = [shutil.which('gst-launch-1.0'), *output.arguments[1:]]
        result = subprocess.run(arguments, capture_output=True,
                                text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('EOS', result.stdout)


class FlagsInspectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtW.QApplication.instance() or QtW.QApplication([])

    def test_edit_and_reset_flags(self):
        canvas = PipelineCanvas()
        inspector = ElementInspector(canvas.document)
        self.addCleanup(canvas.close)
        self.addCleanup(inspector.close)
        canvas.selected_node_changed.connect(inspector.set_node)
        instance = canvas.add_element(replace(
            DEFINITIONS['fakesrc'], properties=(TUNE,),
        ))
        editor = inspector.editors['tune']
        self.assertFalse(editor.isReadOnly())
        self.assertEqual(editor.text(), '0x00000000')
        editor.setText('fastdecode+zerolatency')
        editor.setModified(True)
        editor.editingFinished.emit()
        self.assertEqual(instance.property_overrides['tune'],
                         'fastdecode+zerolatency')
        inspector.reset_buttons['tune'].click()
        self.assertNotIn('tune', instance.property_overrides)
        self.assertEqual(editor.text(), '0x00000000')
