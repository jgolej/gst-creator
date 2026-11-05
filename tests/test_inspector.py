"""Selected-instance property editing and validation."""

from pathlib import Path
import unittest

import PySide6.QtWidgets as QtW
import PySide6.QtGui as QtG

from gst_creator.gstreamer.models import PropertyType
from gst_creator.gstreamer.models import PropertyFlags
from gst_creator.gstreamer.parser import parse_elements
from gst_creator.editor.pipeline_output import build_output
from gst_creator.ui.element_inspector import ElementInspector
from gst_creator.ui.pipeline_canvas import PipelineCanvas
from gst_creator.gstreamer.models import FloatRange, IntRange, PropertyDefinition
from gst_creator.ui.numeric_controls import numeric_editor, WideIntegerSpinBox


class InspectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtW.QApplication.instance() or QtW.QApplication([])
        text = (Path(__file__).parent / 'fixtures/fakesink.txt').read_text()
        cls.definition = parse_elements('\n'.join(
            f'fakesink: {line}' for line in text.splitlines()
        ))[0]

    def setUp(self):
        self.canvas = PipelineCanvas()
        self.inspector = ElementInspector(self.canvas.document)
        self.canvas.selected_node_changed.connect(self.inspector.set_node)
        self.inspector.instance_renamed.connect(
            lambda key: self.canvas.nodes[key].refresh_name()
        )
        self.first = self.canvas.add_element(self.definition)
        self.second = self.canvas.add_element(self.definition)

    def tearDown(self):
        self.inspector.close()
        self.canvas.close()

    def edit(self, name, value):
        editor = self.inspector.editors[name]
        editor.setText(value)
        editor.setModified(True)
        editor.editingFinished.emit()

    def test_boolean_edit_and_reset_are_instance_local(self):
        editor = self.inspector.editors['sync']
        editor.setChecked(True)
        self.assertEqual(self.second.property_overrides['sync'], 'true')
        self.assertEqual(self.first.property_overrides, {})
        self.inspector.reset_buttons['sync'].click()
        self.assertNotIn('sync', self.second.property_overrides)
        self.assertFalse(editor.isChecked())
        self.assertEqual(next(p for p in self.definition.properties
                              if p.name == 'sync').default_value, False)

    def test_decimal_comma_metadata_can_be_edited_reset_and_generated(self):
        text = (
            'Factory Details:\n'
            '  Long-name                Localized scaler\n'
            '  Klass                    Filter/Video\n'
            '  Description              Test\n'
            '\nElement Properties:\n'
            '  sharpness : Sharpness\n'
            '    flags: readable, writable\n'
            '    Double. Range: 0,5 - 1,5 Default: 1,25\n'
            '  label : Label\n'
            '    flags: readable, writable\n'
            '    String. Default: "0,5"\n'
        )
        definition = parse_elements('\n'.join(
            f'scaler: {line}' for line in text.splitlines()
        ))[0]
        self.canvas.scene().clearSelection()
        instance = self.canvas.add_element(definition)
        editor = self.inspector.editors['sharpness']
        self.assertEqual((editor.minimum(), editor.maximum()), (0.5, 1.5))
        self.assertEqual(editor.value(), 1.25)
        self.assertEqual(self.inspector.editors['label'].text(), '0,5')
        editor.setValue(0.75)
        definitions = {self.definition.factory.name: self.definition,
                       definition.factory.name: definition}
        self.assertIn('sharpness=0.75', build_output(
            self.canvas.document, definitions,
        ).arguments)
        self.inspector.reset_buttons['sharpness'].click()
        self.assertEqual(editor.value(), 1.25)
        self.assertNotIn('sharpness', instance.property_overrides)

    def test_numeric_ranges_and_64_bit_precision(self):
        editor = self.inspector.editors['max-bitrate']
        self.assertIsInstance(editor, WideIntegerSpinBox)
        self.assertEqual(editor.minimum(), 0)
        self.assertEqual(editor.maximum(), 18446744073709551615)
        editor.setValue(18446744073709551615)
        self.assertEqual(self.second.property_overrides['max-bitrate'],
                         '18446744073709551615')
        for invalid in ('18446744073709551616', '-1', '1.5', 'nan', 'bad'):
            self.assertEqual(editor.validate(invalid, 0)[0],
                             QtG.QValidator.State.Invalid)
        editor.stepDown()
        self.assertEqual(editor.value(), 18446744073709551614)
        self.assertEqual(self.second.property_overrides['max-bitrate'],
                         '18446744073709551614')
        editor.stepUp()
        editor.stepUp()
        self.assertEqual(editor.value(), editor.maximum())
        self.inspector.reset_buttons['max-bitrate'].click()
        self.assertEqual(editor.value(), 0)
        self.assertNotIn('max-bitrate', self.second.property_overrides)

    def test_native_integer_control_uses_reported_range(self):
        editor = self.inspector.editors['num-buffers']
        self.assertIsInstance(editor, QtW.QSpinBox)
        self.assertEqual((editor.minimum(), editor.maximum()),
                         (-1, 2147483647))
        editor.setValue(42)
        self.assertEqual(self.second.property_overrides['num-buffers'], '42')
        self.assertEqual(self.first.property_overrides, {})
        editor.setValue(-100)
        self.assertEqual(editor.value(), -1)

    def test_float_control_limits_precision_and_read_only(self):
        prop = PropertyDefinition(
            'ratio', 'Ratio', PropertyType.DOUBLE,
            PropertyFlags(readable=True, writable=True),
            1e-20, FloatRange(0.0, 1.0),
        )
        editor = numeric_editor(prop, prop.default_value)
        self.assertIsInstance(editor, QtW.QDoubleSpinBox)
        self.assertEqual((editor.minimum(), editor.maximum()), (0, 1))
        self.assertEqual(editor.value(), 1e-20)
        editor.setValue(2)
        self.assertEqual(editor.value(), 1)
        editor.setValue(-1)
        self.assertEqual(editor.value(), 0)
        editor.setValue(0.12345678901234567)
        self.assertEqual(editor.value(), 0.12345678901234567)
        readonly = numeric_editor(PropertyDefinition(
            'count', 'Count', PropertyType.INTEGER,
            PropertyFlags(readable=True),
            2, IntRange(0, 10),
        ), '2')
        self.assertTrue(readonly.isReadOnly())
        self.assertEqual(readonly.maximum(), 10)

    def test_signed_64_bit_input_and_steps_are_exact(self):
        editor = self.inspector.editors['ts-offset']
        self.assertEqual(editor.minimum(), -(2 ** 63))
        self.assertEqual(editor.maximum(), 2 ** 63 - 1)
        editor.lineEdit().setText('-9223372036854775808')
        editor.editingFinished.emit()
        self.assertEqual(editor.value(), -(2 ** 63))
        editor.stepUp()
        self.assertEqual(editor.value(), -(2 ** 63) + 1)
        self.assertEqual(self.second.property_overrides['ts-offset'],
                         '-9223372036854775807')

    def test_enum_read_only_and_selection_restoration(self):
        enum = self.inspector.editors['state-error']
        enum.setCurrentIndex(2)
        self.assertEqual(self.second.property_overrides['state-error'], '2')
        self.assertTrue(self.inspector.editors['last-message'].isReadOnly())
        self.assertTrue(self.inspector.editors['parent'].isReadOnly())
        self.canvas.scene().clearSelection()
        self.assertIsNone(self.inspector.node)
        self.canvas.nodes[self.second.id].setSelected(True)
        self.assertEqual(self.inspector.editors['state-error'].currentData(),
                         '2')
        self.canvas.nodes[self.first.id].setSelected(True)
        self.assertIsNone(self.inspector.node)
        self.canvas.delete_selected()
        self.assertIsNone(self.inspector.node)

    def test_unique_names_update_canvas(self):
        self.inspector.name_edit.setText(self.first.name)
        self.inspector.name_edit.editingFinished.emit()
        self.assertEqual(self.second.name, 'fakesink1')
        self.assertFalse(self.inspector.name_error.isHidden())
        self.inspector.name_edit.setText('')
        self.inspector.name_edit.editingFinished.emit()
        self.assertEqual(self.second.name, 'fakesink1')
        self.inspector.name_edit.setText('output')
        self.inspector.name_edit.editingFinished.emit()
        self.assertEqual(self.second.name, 'output')
        self.assertEqual(self.canvas.nodes[self.second.id].name_label.text(),
                         'output')
        self.assertNotIn('name', self.second.property_overrides)


if __name__ == '__main__':
    unittest.main()
