"""Project persistence and file-action behavior."""

import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import PySide6.QtCore as QtC
import PySide6.QtWidgets as QtW

from gst_creator.app import MainWindow
from gst_creator.editor.models import PipelineDocument
from gst_creator.editor.project import deserialize, save_project, serialize
from test_pipeline_output import DEFINITIONS


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.document = PipelineDocument()
        for factory in ('fakesrc', 'tee', 'fakesink', 'fakesink'):
            definition = DEFINITIONS[factory]
            self.document.add_element(
                factory, 123.5, -44, definition.pads,
            )
        self.source, tee, sink, other = self.document.elements.values()
        self.source.property_overrides['num-buffers'] = '3'
        self.document.connect(self.source.id, 'src', tee.id, 'sink')
        self.document.connect(tee.id, 'src_%u', sink.id, 'sink')
        self.document.connect(tee.id, 'src_%u', other.id, 'sink')

    def test_round_trip_preserves_positions_and_request_slots(self):
        text = serialize(self.document)
        self.assertNotIn('definitions', json.loads(text))
        self.assertNotIn('pad_templates', text)
        saved_document = json.loads(text)['document']
        self.assertIsInstance(saved_document['elements'], list)
        self.assertIsInstance(saved_document['connections'], list)
        self.assertEqual(
            saved_document['elements'][0]['id'], self.source.id,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pipeline.gstproj'
            save_project(path, text)
            document = deserialize(
                path.read_text(encoding='utf-8'), DEFINITIONS,
            )
        self.assertEqual(document, self.document)

    def test_save_replaces_existing_file_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pipeline.gstproj'
            path.write_text('old project', encoding='utf-8')
            text = 'Unicode: \u017e\n'
            save_project(path, text)
            self.assertEqual(path.read_bytes(), text.encode('utf-8'))
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_failed_save_preserves_existing_file_and_cleans_up(self):
        for operation in ('fsync', 'replace'):
            with self.subTest(operation=operation):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / 'pipeline.gstproj'
                    path.write_text('old project', encoding='utf-8')
                    with patch(
                        f'gst_creator.editor.project.os.{operation}',
                        side_effect=OSError('save failed'),
                    ):
                        with self.assertRaises(OSError):
                            save_project(path, 'new project')
                    self.assertEqual(path.read_bytes(), b'old project')
                    self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_invalid_structure_version_and_connections_are_rejected(self):
        original = serialize(self.document)
        for change in ('version', 'reference', 'duplicate'):
            data = json.loads(original)
            if change == 'version':
                data['version'] = 99
            elif change == 'reference':
                link = data['document']['connections'][0]
                link['source']['element_id'] = 'missing'
            else:
                links = data['document']['connections']
                duplicate = dict(links[0], id='duplicate')
                links.append(duplicate)
            with self.subTest(change=change), self.assertRaises(ValueError):
                deserialize(json.dumps(data), DEFINITIONS)

    def test_invalid_saved_structure_is_rejected(self):
        cases = (
            ('element', 'name', 123),
            ('pad', 'slot', 'invalid'),
            ('pad', 'direction', 'invalid'),
            ('connection', 'source', []),
        )
        for target, field_name, value in cases:
            with self.subTest(target=target, field=field_name, value=value):
                data = json.loads(serialize(self.document))
                if target == 'element':
                    fields = data['document']['elements'][0]
                else:
                    fields = data['document']['connections'][0]
                    if target == 'pad':
                        fields = fields['source']
                fields[field_name] = value
                with self.assertRaises(ValueError):
                    deserialize(json.dumps(data), DEFINITIONS)

    def test_extra_fields_are_ignored(self):
        data = json.loads(serialize(self.document))
        data['document']['extra'] = 'ignored'
        element = data['document']['elements'][0]
        element['extra'] = 'ignored'
        connection = data['document']['connections'][0]
        connection['extra'] = 'ignored'
        connection['source']['extra'] = 'ignored'
        document = deserialize(json.dumps(data), DEFINITIONS)
        self.assertEqual(document, self.document)

    def test_duplicate_saved_ids_are_rejected(self):
        for collection in ('elements', 'connections'):
            with self.subTest(collection=collection):
                data = json.loads(serialize(self.document))
                items = data['document'][collection]
                items.append(dict(items[0]))
                with self.assertRaisesRegex(ValueError, 'Duplicate .* ID'):
                    deserialize(json.dumps(data), DEFINITIONS)

    def test_missing_required_field_is_rejected(self):
        data = json.loads(serialize(self.document))
        del data['document']['elements'][0]['name']
        with self.assertRaises(ValueError):
            deserialize(json.dumps(data), DEFINITIONS)

    def test_stored_pad_metadata_is_replaced_by_discovery(self):
        data = json.loads(serialize(self.document))
        data['document']['elements'][0]['pad_templates'] = 'ignored'
        document = deserialize(json.dumps(data), DEFINITIONS)
        self.assertEqual(document, self.document)

    def test_missing_factory_and_changed_pads_report_errors(self):
        text = serialize(self.document)
        with self.assertRaisesRegex(ValueError, "fakesrc.*not discovered"):
            deserialize(text, {})
        changed = dict(DEFINITIONS)
        changed['fakesrc'] = replace(changed['fakesrc'], pads=())
        with self.assertRaisesRegex(ValueError, "Pad 'src'.*fakesrc"):
            deserialize(text, changed)

    def test_project_uses_current_discovery(self):
        text = serialize(self.document)
        changed = dict(DEFINITIONS)
        changed['fakesrc'] = replace(
            changed['fakesrc'],
            factory=replace(changed['fakesrc'].factory, description='Updated'),
        )
        document = deserialize(text, changed)
        self.assertEqual(document, self.document)
        self.assertIs(document.elements[self.source.id].pad_templates,
                      changed['fakesrc'].pads)
        self.assertEqual(json.loads(serialize(document))['version'], 1)


class ProjectActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtW.QApplication.instance() or QtW.QApplication([])

    def setUp(self):
        with patch('gst_creator.app.ElementDiscovery.start'):
            self.window = MainWindow()
        self.window.discovery.ready.emit(list(DEFINITIONS.values()))
        self.addCleanup(self.window.close)
        self.addCleanup(lambda: setattr(
            self.window, '_saved_project', self.window._project_text()
        ))
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'test.gstproj'

    def test_save_new_open_restores_canvas_and_inspector_document(self):
        window = self.window
        source = window.canvas.add_element(DEFINITIONS['fakesrc'])
        sink = window.canvas.add_element(DEFINITIONS['fakesink'])
        window.canvas.connect_pads(
            window.canvas.nodes[source.id].pads[('src', 'src')],
            window.canvas.nodes[sink.id].pads[('sink', 'sink')],
        )
        window.canvas.nodes[source.id].setPos(345, -123)
        window.project_path = self.path
        self.assertTrue(window._save_project())
        window.new_action.trigger()
        self.assertFalse(window.canvas.nodes)
        updated = replace(
            DEFINITIONS['fakesrc'],
            factory=replace(DEFINITIONS['fakesrc'].factory,
                            description='Updated'),
        )
        window.discovery.ready.emit([
            updated if definition.factory.name == 'fakesrc' else definition
            for definition in DEFINITIONS.values()
        ])
        with patch.object(QtW.QFileDialog, 'getOpenFileName',
                          return_value=(str(self.path), '')):
            window.open_action.trigger()
        self.assertEqual(len(window.canvas.connections), 1)
        self.assertIs(window.canvas.nodes[source.id].definition,
                      updated)
        self.assertEqual(window.canvas.nodes[source.id].pos(),
                         QtC.QPointF(345, -123))
        self.assertIs(window.inspector.document, window.canvas.document)
        self.assertFalse(window.isWindowModified())

    def test_cancel_and_failed_save_preserve_current_project(self):
        window = self.window
        element = window.canvas.add_element(DEFINITIONS['fakesrc'])
        with patch.object(QtW.QMessageBox, 'warning',
                          return_value=QtW.QMessageBox.StandardButton.Cancel):
            window.new_action.trigger()
        self.assertIn(element.id, window.canvas.nodes)
        window.project_path = self.path
        with patch('gst_creator.app.save_project', side_effect=OSError('full')):
            with patch.object(QtW.QMessageBox, 'critical'):
                self.assertFalse(window._save_project())
        self.assertTrue(window.isWindowModified())

    def test_invalid_open_preserves_current_project(self):
        element = self.window.canvas.add_element(DEFINITIONS['fakesrc'])
        self.path.write_text('{}')
        with patch.object(QtW.QFileDialog, 'getOpenFileName',
                          return_value=(str(self.path), '')):
            with patch.object(QtW.QMessageBox, 'critical') as error:
                self.window._open_project()
        error.assert_called_once()
        self.assertIn(element.id, self.window.canvas.nodes)

    def test_open_waits_for_discovery(self):
        with patch.object(self.window.discovery, 'start'):
            self.window._start_discovery()
        self.assertFalse(self.window.open_action.isEnabled())
        self.window.discovery.ready.emit(list(DEFINITIONS.values()))
        self.assertTrue(self.window.open_action.isEnabled())

    def test_save_as_and_cancelled_save_dialog(self):
        window = self.window
        window.canvas.add_element(DEFINITIONS['fakesrc'])
        with patch('gst_creator.app.QtW.QFileDialog') as dialog_type:
            dialog = dialog_type.return_value
            dialog.exec.return_value = 0
            self.assertFalse(window._save_project())
            self.assertIsNone(window.project_path)
            dialog.exec.return_value = 1
            dialog.selectedFiles.return_value = [str(self.path)]
            self.assertTrue(window._save_project(save_as=True))
        self.assertEqual(window.project_path, self.path)
        self.assertTrue(self.path.exists())
        self.assertFalse(window.isWindowModified())

    def test_save_before_new_and_discard_before_close(self):
        window = self.window
        window.canvas.add_element(DEFINITIONS['fakesrc'])
        window.project_path = self.path
        with patch.object(QtW.QMessageBox, 'warning',
                          return_value=QtW.QMessageBox.StandardButton.Save):
            window._new_project()
        self.assertTrue(self.path.exists())
        self.assertFalse(window.canvas.nodes)
        window.canvas.add_element(DEFINITIONS['fakesink'])
        with patch.object(QtW.QMessageBox, 'warning',
                          return_value=QtW.QMessageBox.StandardButton.Discard):
            self.assertTrue(window.close())
