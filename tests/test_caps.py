"""Connection caps editing, persistence, and command generation."""

from dataclasses import replace
import json
import shlex
import shutil
import subprocess
import unittest

import PySide6.QtWidgets as QtW

from gst_creator.gstreamer.models import PropertyType
from gst_creator.gstreamer.models import PropertyFlags
from gst_creator.editor.models import PipelineDocument
from gst_creator.editor.pipeline_output import _property_value, build_output
from gst_creator.editor.project import deserialize, serialize
from gst_creator.gstreamer.parser import parse_elements
from gst_creator.gstreamer.models import PropertyDefinition
from gst_creator.ui.element_inspector import ElementInspector
from gst_creator.ui.pipeline_canvas import PipelineCanvas
from test_pipeline_output import DEFINITIONS


class CapsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtW.QApplication.instance() or QtW.QApplication([])

    def setUp(self):
        self.canvas = PipelineCanvas()
        self.inspector = ElementInspector(self.canvas.document)
        self.addCleanup(self.canvas.close)
        self.addCleanup(self.inspector.close)
        self.canvas.selected_node_changed.connect(self.inspector.set_node)
        self.canvas.selected_connection_changed.connect(
            self.inspector.set_connection
        )
        source = self.canvas.add_element(DEFINITIONS['fakesrc'])
        sink = self.canvas.add_element(DEFINITIONS['fakesink'])
        self.link = self.canvas.connect_pads(
            self.canvas.nodes[source.id].pads[('src', 'src')],
            self.canvas.nodes[sink.id].pads[('sink', 'sink')],
        )
        self.item = self.canvas.connections[self.link.id]

    def test_edit_updates_label_output_and_saved_project(self):
        self.canvas.scene().clearSelection()
        self.item.setSelected(True)
        caps = 'video/x-raw,width=1280,height=720,framerate=30/1'
        editor = self.inspector.caps_edit
        editor.setText(caps)
        editor.textEdited.emit(caps)
        self.assertEqual(self.item.connection.caps, caps)
        self.assertTrue(self.item.caps_label.text())
        output = build_output(self.canvas.document, DEFINITIONS)
        self.assertIn(f'caps="{caps}"', output.arguments)
        self.assertEqual(shlex.split(output.command)[1:],
                         list(output.arguments))
        restored = deserialize(serialize(self.canvas.document), DEFINITIONS)
        self.assertEqual(restored.connections[self.link.id].caps, caps)
        self.inspector.findChild(QtW.QPushButton).click()
        self.assertEqual(self.item.connection.caps, '')
        self.assertEqual(self.item.caps_label.text(), '')
        self.assertNotIn('capsfilter', build_output(
            self.canvas.document, DEFINITIONS,
        ).arguments)

    def test_unsupported_project_version_is_rejected(self):
        data = json.loads(serialize(self.canvas.document))
        data['version'] = 2
        with self.assertRaisesRegex(ValueError, 'Unsupported project'):
            deserialize(json.dumps(data), DEFINITIONS)

    def test_caps_property_edit_reset_and_round_trip(self):
        caps = 'application/x-rtp,media=video,encoding-name=H264,payload=96'
        for kind in (PropertyType.CAPS,):
            with self.subTest(kind=kind):
                prop = PropertyDefinition(
                    'caps', '', kind, PropertyFlags(writable=True),
                )
                definitions = dict(DEFINITIONS)
                definitions['fakesrc'] = replace(
                    definitions['fakesrc'], properties=(prop,),
                )
                document = PipelineDocument()
                source = document.add_element(
                    'fakesrc', pad_templates=definitions['fakesrc'].pads,
                )
                source.property_overrides['caps'] = caps
                sink = document.add_element(
                    'fakesink', pad_templates=definitions['fakesink'].pads,
                )
                document.connect(source.id, 'src', sink.id, 'sink')
                output = build_output(document, definitions)
                self.assertIn(f'caps="{caps}"', output.arguments)
                restored = deserialize(serialize(document), definitions)
                self.assertEqual(build_output(restored, definitions).command,
                                 output.command)
                self.canvas.scene().clearSelection()
                instance = self.canvas.add_element(definitions['fakesrc'])
                editor = self.inspector.editors['caps']
                self.assertFalse(editor.isReadOnly())
                editor.setText(caps)
                editor.setModified(True)
                editor.editingFinished.emit()
                self.assertEqual(instance.property_overrides['caps'], caps)
                self.inspector.reset_buttons['caps'].click()
                self.assertNotIn('caps', instance.property_overrides)

    def test_caps_property_quotes_and_rejects_empty_or_nul(self):
        prop = PropertyDefinition(
            'caps', '', PropertyType.CAPS, PropertyFlags(writable=True),
        )
        value = 'application/x-test,label=(string)"a ! b"'
        self.assertEqual(_property_value(prop, value),
                         '"application/x-test,label=(string)\\"a ! b\\""')
        for value in ('', '  ', 'video/x-raw\x00'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _property_value(prop, value)

    @unittest.skipUnless(shutil.which('gst-inspect-1.0')
                         and shutil.which('gst-launch-1.0'),
                         'Requires GStreamer')
    def test_real_udpsrc_caps_execution(self):
        definitions = {}
        for name in ('udpsrc', 'fakesink'):
            result = subprocess.run(
                [shutil.which('gst-inspect-1.0'), name],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            definitions[name] = parse_elements('\n'.join(
                f'{name}: {line}' for line in result.stdout.splitlines()
            ))[0]
        prop = next(p for p in definitions['udpsrc'].properties
                    if p.name == 'caps')
        self.assertEqual(prop.type, PropertyType.CAPS)
        self.assertIsNone(prop.default_value)
        document = PipelineDocument()
        source = document.add_element(
            'udpsrc', pad_templates=definitions['udpsrc'].pads,
        )
        source.property_overrides.update({
            'port': '0',
            'num-buffers': '0',
            'caps': ('application/x-rtp,media=video,encoding-name=H264,'
                     'payload=96,clock-rate=90000'),
        })
        sink = document.add_element(
            'fakesink', pad_templates=definitions['fakesink'].pads,
        )
        document.connect(source.id, 'src', sink.id, 'sink')
        output = build_output(document, definitions)
        self.assertIsNotNone(output.command, output.issues)
        result = subprocess.run(
            [shutil.which('gst-launch-1.0'), *output.arguments],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('EOS', result.stdout)

    def test_quoted_caps_remain_a_single_property_argument(self):
        caps = 'application/x-test,label=(string)"a ! b"'
        self.canvas.document.connections[self.link.id] = replace(
            self.link, caps=caps,
        )
        output = build_output(self.canvas.document, DEFINITIONS)
        self.assertIn('caps="application/x-test,label=(string)\\"a ! b\\""',
                      output.arguments)
        self.canvas.document.connections[self.link.id] = replace(
            self.link, caps='video/x-raw\x00',
        )
        self.assertIsNone(build_output(self.canvas.document, DEFINITIONS).command)
