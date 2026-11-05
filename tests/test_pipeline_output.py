"""Static validation, command quoting, and real gst-launch smoke checks."""

from dataclasses import replace
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

import PySide6.QtWidgets as QtW

from gst_creator.gstreamer.models import PropertyType
from gst_creator.gstreamer.models import PropertyFlags
from gst_creator.editor.models import PipelineDocument
from gst_creator.editor.pipeline_output import build_output
from gst_creator.gstreamer.models import (
    CapsKind, CapsStructure, ElementDefinition, FactoryInfo, IntRange,
    PadAvailability, PadDirection, PadTemplate, PluginInfo,
    PropertyDefinition,
)
from gst_creator.ui.pipeline_canvas import PipelineCanvas
from gst_creator.ui.pipeline_output import PipelineOutputPanel


SRC = PadTemplate(
    'src', PadDirection.SRC, PadAvailability.ALWAYS, CapsKind.ANY,
)
SINK = PadTemplate(
    'sink', PadDirection.SINK, PadAvailability.ALWAYS, CapsKind.ANY,
)
COUNT = PropertyDefinition(
    'num-buffers', '', PropertyType.INTEGER, PropertyFlags(writable=True),
    -1, IntRange(-1, 2147483647),
)
DEFINITIONS = {
    'fakesrc': ElementDefinition(
        FactoryInfo('fakesrc'), PluginInfo(), pads=(SRC,), properties=(COUNT,),
    ),
    'fakesink': ElementDefinition(
        FactoryInfo('fakesink'), PluginInfo(), pads=(SINK,),
    ),
    'tee': ElementDefinition(FactoryInfo('tee'), PluginInfo(), pads=(
        SINK, PadTemplate(
            'src_%u', PadDirection.SRC, PadAvailability.ON_REQUEST, CapsKind.ANY,
        ),
    )),
    'queue': ElementDefinition(
        FactoryInfo('queue'), PluginInfo(), pads=(SINK, SRC),
    ),
    'filesrc': ElementDefinition(
        FactoryInfo('filesrc'), PluginInfo(), pads=(SRC,),
        properties=(PropertyDefinition(
            'location', '', PropertyType.STRING, PropertyFlags(writable=True),
        ),),
    ),
}


def add(document, factory):
    return document.add_element(
        factory, pad_templates=DEFINITIONS[factory].pads
    )


class PipelineOutputTests(unittest.TestCase):
    def setUp(self):
        self.document = PipelineDocument()
        self.source = add(self.document, 'fakesrc')
        self.source.property_overrides['num-buffers'] = '3'
        self.sink = add(self.document, 'fakesink')
        self.document.connect(self.source.id, 'src', self.sink.id, 'sink')

    def test_command_follows_the_linked_chain(self):
        result = build_output(self.document, DEFINITIONS)
        self.assertEqual(shlex.split(result.command), [
            'gst-launch-1.0', '-e', 'fakesrc', 'name=fakesrc0',
            'num-buffers=3', '!', 'fakesink', 'name=fakesink0',
        ])
        self.assertFalse(any(i.severity == 'error' for i in result.issues))

    def test_chain_order_does_not_depend_on_element_creation_order(self):
        document = PipelineDocument()
        sink = add(document, 'fakesink')
        queue = add(document, 'queue')
        source = add(document, 'fakesrc')
        document.connect(queue.id, 'src', sink.id, 'sink')
        document.connect(source.id, 'src', queue.id, 'sink')
        output = build_output(document, DEFINITIONS)
        self.assertEqual(list(output.arguments), [
            '-e', 'fakesrc', 'name=fakesrc0', '!',
            'queue', 'name=queue0', '!', 'fakesink', 'name=fakesink0',
        ])

    def test_join_keeps_explicit_request_pad_slots(self):
        definitions = dict(DEFINITIONS)
        definitions['funnel'] = ElementDefinition(
            FactoryInfo('funnel'), PluginInfo(), pads=(
                SRC, replace(SINK, name_template='sink_%u',
                             availability=PadAvailability.ON_REQUEST),
            ),
        )
        document = PipelineDocument()
        funnel = document.add_element(
            'funnel', pad_templates=definitions['funnel'].pads,
        )
        sink = add(document, 'fakesink')
        document.connect(funnel.id, 'src', sink.id, 'sink')
        for slot in (7, 12):
            source = add(document, 'fakesrc')
            source.property_overrides['num-buffers'] = '3'
            link = document.connect(source.id, 'src', funnel.id, 'sink_%u')
            document.connections[link.id] = replace(
                link, sink=replace(link.sink, slot=slot),
            )
        output = build_output(document, definitions)
        self.assertEqual(list(output.arguments), [
            '-e', 'fakesrc', 'name=fakesrc0', 'num-buffers=3', '!',
            '.sink_7', 'funnel', 'name=funnel0', '!',
            'fakesink', 'name=fakesink0',
            'fakesrc', 'name=fakesrc1', 'num-buffers=3', '!',
            'funnel0.sink_12',
        ])
        executable = shutil.which('gst-launch-1.0')
        if executable:
            result = subprocess.run(
                [executable, *output.arguments], capture_output=True,
                text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_cycle_emits_each_element_once_and_closes_with_a_reference(self):
        document = PipelineDocument()
        first = add(document, 'queue')
        second = add(document, 'queue')
        document.connect(first.id, 'src', second.id, 'sink')
        document.connect(second.id, 'src', first.id, 'sink')
        output = build_output(document, DEFINITIONS)
        self.assertEqual(list(output.arguments), [
            '-e', 'queue', 'name=queue0', '!', 'queue', 'name=queue1',
            '!', 'queue0.sink',
        ])

    def test_empty_missing_factory_duplicate_and_invalid_names(self):
        empty = build_output(PipelineDocument(), DEFINITIONS)
        self.assertIsNone(empty.command)
        self.assertIsNone(build_output(self.document, {}).command)
        for name in ('has spaces', 'sink; echo bad', self.source.name):
            self.sink.name = name
            self.assertIsNone(build_output(self.document, DEFINITIONS).command)

    def test_invalid_properties_block_output(self):
        for properties in ({'num-buffers': '-2'}, {'num-buffers': 'nan'},
                           {'num-buffers': '1 ! fakesink'}, {'missing': '1'}):
            self.source.property_overrides = properties
            self.assertIsNone(build_output(self.document, DEFINITIONS).command)

    def test_disjoint_caps_and_dynamic_pad_patterns_block_output(self):
        definitions = dict(DEFINITIONS)
        definitions['fakesrc'] = replace(
            definitions['fakesrc'],
            pads=(replace(
                SRC, capabilities=CapsKind.SPECIFIC,
                structures=(CapsStructure('audio/x-raw'),),
            ),),
        )
        definitions['fakesink'] = replace(
            definitions['fakesink'],
            pads=(replace(
                SINK, capabilities=CapsKind.SPECIFIC,
                structures=(CapsStructure('video/x-raw'),),
            ),),
        )
        result = build_output(self.document, definitions)
        self.assertIsNone(result.command)
        self.assertTrue(any('incompatible' in issue.message
                            for issue in result.issues))
        link = next(iter(self.document.connections.values()))
        dynamic = PadTemplate(
            'src_%u', PadDirection.SRC, PadAvailability.SOMETIMES, CapsKind.ANY,
        )
        definitions['fakesrc'] = replace(
            definitions['fakesrc'], pads=(dynamic,)
        )
        self.document.connections[link.id] = replace(
            link, source=replace(link.source, template_name='src_%u')
        )
        result = build_output(self.document, definitions)
        self.assertTrue(any('runtime stream selection' in i.message
                            for i in result.issues))

    def test_missing_and_duplicate_endpoints(self):
        link = next(iter(self.document.connections.values()))
        self.document.connections['duplicate'] = replace(link, id='duplicate')
        result = build_output(self.document, DEFINITIONS)
        self.assertIsNone(result.command)
        self.assertTrue(any('twice' in i.message for i in result.issues))
        del self.document.elements[self.sink.id]
        self.assertIsNone(build_output(self.document, DEFINITIONS).command)

    def test_unlinked_pads_are_warnings_not_silent_success(self):
        self.document.connections.clear()
        result = build_output(self.document, DEFINITIONS)
        self.assertIsNotNone(result.command)
        self.assertEqual(list(result.arguments), [
            '-e', 'fakesrc', 'name=fakesrc0', 'num-buffers=3',
            'fakesink', 'name=fakesink0',
        ])
        self.assertTrue(any(i.severity == 'warning' and 'unlinked' in i.message
                            for i in result.issues))

    @unittest.skipUnless(shutil.which('gst-launch-1.0'), 'Requires GStreamer')
    def test_generated_tee_branch_command_runs_to_eos(self):
        document = PipelineDocument()
        source = add(document, 'fakesrc')
        source.property_overrides['num-buffers'] = '3'
        tee = add(document, 'tee')
        document.connect(source.id, 'src', tee.id, 'sink')
        for slot in (7, 12):
            queue = add(document, 'queue')
            sink = add(document, 'fakesink')
            link = document.connect(tee.id, 'src_%u', queue.id, 'sink')
            document.connections[link.id] = replace(
                link, source=replace(link.source, slot=slot),
            )
            document.connect(queue.id, 'src', sink.id, 'sink')
        output = build_output(document, DEFINITIONS)
        self.assertEqual(list(output.arguments), [
            '-e', 'fakesrc', 'name=fakesrc0', 'num-buffers=3', '!',
            'tee', 'name=tee0', 'tee0.src_7', '!',
            'queue', 'name=queue0', '!', 'fakesink', 'name=fakesink0',
            'tee0.src_12', '!',
            'queue', 'name=queue1', '!', 'fakesink', 'name=fakesink1',
        ])
        result = subprocess.run(
            [shutil.which('gst-launch-1.0'), *output.arguments],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('EOS', result.stdout)

    @unittest.skipUnless(shutil.which('gst-launch-1.0'), 'Requires GStreamer')
    def test_string_quoting_preserves_special_characters(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'a $HOME `x` ! ; file.txt'
            path.write_text('test')
            document = PipelineDocument()
            source = add(document, 'filesrc')
            source.property_overrides['location'] = json.dumps(str(path))
            sink = add(document, 'fakesink')
            document.connect(source.id, 'src', sink.id, 'sink')
            output = build_output(document, DEFINITIONS)
            self.assertEqual(shlex.split(output.command)[1:],
                             list(output.arguments))
            result = subprocess.run([shutil.which('gst-launch-1.0'),
                                     *output.arguments],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)


class OutputPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtW.QApplication.instance() or QtW.QApplication([])

    def test_refresh_copy_and_issue_navigation(self):
        canvas = PipelineCanvas()
        panel = PipelineOutputPanel(canvas)
        canvas.document_changed.connect(panel.refresh)
        self.assertFalse(panel.copy_button.isEnabled())
        source = canvas.add_element(DEFINITIONS['fakesrc'])
        self.assertTrue(panel.copy_button.isEnabled())
        canvas.scene().clearSelection()
        panel._activate_issue(panel.issues.item(0))
        self.assertTrue(canvas.nodes[source.id].isSelected())
        panel.copy_button.click()
        self.assertEqual(QtW.QApplication.clipboard().text(),
                         panel.command.toPlainText())
        canvas.delete_selected()
        self.assertFalse(panel.copy_button.isEnabled())
        self.assertEqual(panel.command.toPlainText(), '')
        panel.close()
        canvas.close()


if __name__ == '__main__':
    unittest.main()
