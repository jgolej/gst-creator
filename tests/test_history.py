"""Undo and redo across canvas, inspector, and project operations."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import PySide6.QtCore as QtC
import PySide6.QtWidgets as QtW

from gst_creator.app import MainWindow
from gst_creator.editor.models import PipelineDocument
from test_pipeline_output import DEFINITIONS


class HistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtW.QApplication.instance() or QtW.QApplication([])

    def setUp(self):
        with patch('gst_creator.app.ElementDiscovery.start'):
            self.window = MainWindow()
        self.canvas = self.window.canvas
        self.stack = self.window.history.stack
        self.addCleanup(self.window.close)
        self.addCleanup(lambda: setattr(
            self.window, '_saved_project', self.window._project_text()
        ))

    def add_pair(self):
        source = self.canvas.add_element(DEFINITIONS['fakesrc'])
        sink = self.canvas.add_element(DEFINITIONS['fakesink'])
        link = self.canvas.connect_pads(
            self.canvas.nodes[source.id].pads[('src', 'src')],
            self.canvas.nodes[sink.id].pads[('sink', 'sink')],
        )
        return source.id, sink.id, link.id

    def test_add_undo_redo_and_branch(self):
        self.assertFalse(self.window.undo_action.isEnabled())
        source = self.canvas.add_element(DEFINITIONS['fakesrc'])
        self.window.undo_action.trigger()
        self.assertFalse(self.canvas.nodes)
        self.assertFalse(self.window.isWindowModified())
        self.window.redo_action.trigger()
        self.assertIn(source.id, self.canvas.nodes)
        self.stack.undo()
        self.canvas.add_element(DEFINITIONS['fakesink'])
        self.assertFalse(self.stack.canRedo())

    def test_duplicate_group_preserves_caps_and_undoes_in_one_step(self):
        source, sink, link = self.add_pair()
        self.canvas.scene().clearSelection()
        self.canvas.connections[link].setSelected(True)
        editor = self.window.inspector.caps_edit
        editor.setText('video/x-raw,width=640')
        editor.textEdited.emit(editor.text())
        self.canvas.scene().clearSelection()
        self.canvas.nodes[source].setSelected(True)
        self.canvas.nodes[sink].setSelected(True)
        count = self.stack.count()
        self.canvas.duplicate_action.trigger()
        copies = [node for node in self.canvas.nodes.values()
                  if node.isSelected()]
        self.assertEqual(len(copies), 2)
        self.assertEqual(len(self.canvas.connections), 2)
        copied_link = next(item.connection for key, item
                           in self.canvas.connections.items() if key != link)
        self.assertEqual(copied_link.caps, 'video/x-raw,width=640')
        self.assertEqual({copied_link.source.element_id,
                          copied_link.sink.element_id},
                         {node.instance.id for node in copies})
        self.assertEqual(self.stack.count(), count + 1)
        self.stack.undo()
        self.assertEqual(len(self.canvas.nodes), 2)
        self.assertEqual(len(self.canvas.connections), 1)
        self.stack.redo()
        self.assertEqual(len(self.canvas.nodes), 4)
        self.assertEqual(len(self.canvas.connections), 2)

    def test_duplicate_is_independent_and_does_not_copy_external_links(self):
        source, sink, _ = self.add_pair()
        self.canvas.document.elements[source].property_overrides = {
            'num-buffers': '3',
        }
        self.canvas.scene().clearSelection()
        self.canvas.nodes[source].setSelected(True)
        self.canvas.duplicate_selected()
        copy = next(node.instance for node in self.canvas.nodes.values()
                    if node.isSelected())
        self.assertEqual(copy.property_overrides, {'num-buffers': '3'})
        copy.property_overrides['num-buffers'] = '7'
        self.assertEqual(self.canvas.document.elements[source]
                         .property_overrides['num-buffers'], '3')
        self.assertEqual(len(self.canvas.connections), 1)
        self.canvas.scene().clearSelection()
        self.canvas.nodes[source].setSelected(True)
        self.canvas.duplicate_selected()
        names = [element.name for element in self.canvas.document.elements.values()]
        self.assertEqual(len(names), len(set(names)))
        self.canvas.scene().clearSelection()
        self.assertFalse(self.canvas.duplicate_action.isEnabled())

    def test_delete_restores_connections_and_caps(self):
        source, sink, link = self.add_pair()
        self.canvas.scene().clearSelection()
        self.canvas.connections[link].setSelected(True)
        editor = self.window.inspector.caps_edit
        editor.setText('video/x-raw,width=640')
        editor.textEdited.emit(editor.text())
        self.canvas.scene().clearSelection()
        self.canvas.nodes[source].setSelected(True)
        self.canvas.delete_selected()
        self.assertNotIn(link, self.canvas.connections)
        self.stack.undo()
        self.assertIn(source, self.canvas.nodes)
        self.assertEqual(self.canvas.connections[link].connection.caps,
                         'video/x-raw,width=640')
        self.stack.redo()
        self.assertNotIn(source, self.canvas.nodes)

    def test_move_multiple_nodes_preserves_view(self):
        source, sink, _ = self.add_pair()
        before = {key: node.pos() for key, node in self.canvas.nodes.items()}
        count = self.stack.count()
        self.canvas.zoom(1.2)
        transform = self.canvas.transform()
        for node in self.canvas.nodes.values():
            node.setPos(node.pos() + QtC.QPointF(50, 100))
        self.canvas.document_changed.emit()
        self.assertEqual(self.stack.count(), count + 1)
        self.stack.undo()
        for key, position in before.items():
            self.assertEqual(self.canvas.nodes[key].pos(), position)
        self.assertEqual(self.canvas.transform(), transform)
        self.stack.redo()
        self.assertEqual(self.canvas.nodes[source].pos(),
                         before[source] + QtC.QPointF(50, 100))

    def test_property_edits_merge_and_restore_inspector(self):
        source = self.canvas.add_element(DEFINITIONS['fakesrc'])
        editor = self.window.inspector.editors['num-buffers']
        count = self.stack.count()
        editor.setValue(3)
        editor.setValue(4)
        self.assertEqual(self.stack.count(), count + 1)
        self.stack.undo()
        self.assertEqual(self.canvas.document.elements[source.id]
                         .property_overrides, {})
        self.assertEqual(self.window.inspector.editors['num-buffers'].value(),
                         -1)
        self.stack.redo()
        self.assertEqual(self.window.inspector.editors['num-buffers'].value(), 4)
        self.assertIs(self.window.inspector.document, self.canvas.document)

    def test_rename_and_caps_undo(self):
        source, _, link = self.add_pair()
        self.canvas.scene().clearSelection()
        self.canvas.nodes[source].setSelected(True)
        editor = self.window.inspector.name_edit
        editor.setText('input')
        editor.editingFinished.emit()
        self.stack.undo()
        self.assertEqual(self.canvas.nodes[source].name_label.text(), 'fakesrc0')
        self.stack.redo()
        self.assertEqual(self.canvas.nodes[source].name_label.text(), 'input')
        self.window.history.break_group()
        self.canvas.scene().clearSelection()
        self.canvas.connections[link].setSelected(True)
        editor = self.window.inspector.caps_edit
        editor.setText('video/x-raw')
        editor.textEdited.emit(editor.text())
        self.stack.undo()
        self.assertEqual(self.canvas.connections[link].connection.caps, '')
        self.stack.redo()
        self.assertEqual(self.canvas.connections[link].connection.caps,
                         'video/x-raw')

    def test_save_boundary_and_new_project_reset(self):
        self.canvas.add_element(DEFINITIONS['fakesrc'])
        self.window.inspector.editors['num-buffers'].setValue(3)
        with tempfile.TemporaryDirectory() as directory:
            self.window.project_path = Path(directory) / 'test.gstproj'
            self.assertTrue(self.window._save_project())
        self.window.inspector.editors['num-buffers'].setValue(4)
        self.stack.undo()
        self.assertFalse(self.window.isWindowModified())
        self.window._replace_project(PipelineDocument())
        self.assertFalse(self.stack.canUndo())
        self.assertFalse(self.stack.canRedo())
