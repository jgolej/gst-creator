"""Pad planning and interactive canvas connections."""

import unittest

import PySide6.QtCore as QtC
import PySide6.QtTest as QtT
import PySide6.QtWidgets as QtW

from gst_creator.editor.models import PipelineDocument
from gst_creator.gstreamer.models import (
    CapsKind, CapsStructure, ElementDefinition, FactoryInfo,
    PadAvailability, PadDirection, PadTemplate, PluginInfo,
)
from gst_creator.ui.pipeline_canvas import PipelineCanvas


SOURCE = PadTemplate(
    'src', PadDirection.SRC, PadAvailability.ALWAYS, CapsKind.ANY,
)
SINK = PadTemplate(
    'sink', PadDirection.SINK, PadAvailability.ALWAYS, CapsKind.ANY,
)
REQUEST = PadTemplate(
    'src_%u', PadDirection.SRC, PadAvailability.ON_REQUEST, CapsKind.ANY,
)
DYNAMIC = PadTemplate(
    'src_%u', PadDirection.SRC, PadAvailability.SOMETIMES,
    CapsKind.SPECIFIC, (CapsStructure('audio/x-raw'),),
)


class ConnectionModelTests(unittest.TestCase):
    def setUp(self):
        self.document = PipelineDocument()
        self.source = self.document.add_element('source', pad_templates=(SOURCE,))
        self.sink = self.document.add_element('sink', pad_templates=(SINK,))

    def test_static_pads_cannot_be_linked_twice(self):
        link = self.document.connect(self.source.id, 'src', self.sink.id, 'sink')
        self.assertEqual(link.source.element_id, self.source.id)
        with self.assertRaises(ValueError):
            self.document.connect(self.source.id, 'src', self.sink.id, 'sink')
        other = self.document.add_element('source', pad_templates=(SOURCE,))
        with self.assertRaises(ValueError):
            self.document.connect(other.id, 'src', self.sink.id, 'sink')
        self.assertEqual(len(self.document.connections), 1)

    def test_wrong_direction_missing_pads_and_self_links_are_rejected(self):
        for args in (
            (self.sink.id, 'sink', self.source.id, 'src'),
            (self.source.id, 'missing', self.sink.id, 'sink'),
            ('missing', 'src', self.sink.id, 'sink'),
            (self.source.id, 'src', self.source.id, 'src'),
        ):
            with self.assertRaises(ValueError):
                self.document.connect(*args)
        self.assertFalse(self.document.connections)

    def test_request_and_dynamic_templates_allocate_distinct_planned_slots(self):
        for template in (REQUEST, DYNAMIC):
            document = PipelineDocument()
            source = document.add_element('source', pad_templates=(template,))
            sinks = [document.add_element('sink', pad_templates=(SINK,))
                     for _ in range(2)]
            links = [document.connect(source.id, 'src_%u', sink.id, 'sink')
                     for sink in sinks]
            self.assertEqual([link.source.slot for link in links], [0, 1])
            document.remove_element(sinks[0].id)
            self.assertEqual(list(document.connections), [links[1].id])
            document.remove_element(source.id)
            self.assertFalse(document.connections)


class ConnectionCanvasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtW.QApplication.instance() or QtW.QApplication([])

    def setUp(self):
        self.canvas = PipelineCanvas()
        self.canvas.resize(900, 600)
        self.canvas.show()
        self.app.processEvents()
        source = self.canvas.add_element(ElementDefinition(
            FactoryInfo('source', 'Source', 'Source'), PluginInfo(),
            pads=(SOURCE,),
        ))
        sink = self.canvas.add_element(ElementDefinition(
            FactoryInfo('sink', 'Sink', 'Sink'), PluginInfo(), pads=(SINK,),
        ))
        self.source = self.canvas.nodes[source.id]
        self.sink = self.canvas.nodes[sink.id]
        self.source.setPos(-300, 0)
        self.sink.setPos(100, 0)
        self.canvas.fit_elements()
        self.canvas.scene().clearSelection()
        self.app.processEvents()
        self.src_pad = self.source.pads[('src', 'src')]
        self.sink_pad = self.sink.pads[('sink', 'sink')]

    def tearDown(self):
        self.canvas.close()

    def drag_link(self):
        start = self.canvas.mapFromScene(self.src_pad.scenePos())
        end = self.canvas.mapFromScene(self.sink_pad.scenePos())
        QtT.QTest.mousePress(self.canvas.viewport(),
                            QtC.Qt.MouseButton.LeftButton, pos=start)
        QtT.QTest.mouseMove(self.canvas.viewport(), end)
        QtT.QTest.mouseRelease(self.canvas.viewport(),
                              QtC.Qt.MouseButton.LeftButton, pos=end)

    def test_mouse_drag_creates_link_and_movement_updates_curve(self):
        self.drag_link()
        self.assertEqual(len(self.canvas.document.connections), 1)
        link = next(iter(self.canvas.connections.values()))
        self.assertEqual(link.path().pointAtPercent(0), self.src_pad.scenePos())
        self.source.setPos(-350, 60)
        self.sink.setPos(150, -60)
        self.assertEqual(link.path().pointAtPercent(0), self.src_pad.scenePos())
        self.assertEqual(link.path().pointAtPercent(1), self.sink_pad.scenePos())
        self.assertIsNone(self.canvas._link_preview)

    def test_selecting_and_deleting_curve_preserves_elements(self):
        self.drag_link()
        link = next(iter(self.canvas.connections.values()))
        emitted = []
        self.canvas.selected_node_changed.connect(emitted.append)
        point = self.canvas.mapFromScene(link.path().pointAtPercent(0.5))
        QtT.QTest.mouseClick(self.canvas.viewport(),
                            QtC.Qt.MouseButton.LeftButton, pos=point)
        self.assertTrue(link.isSelected())
        self.assertIsNone(emitted[-1])
        QtT.QTest.keyClick(self.canvas, QtC.Qt.Key.Key_Delete)
        self.assertFalse(self.canvas.connections)
        self.assertFalse(self.canvas.document.connections)
        self.assertFalse(self.src_pad.links)
        self.assertEqual(len(self.canvas.nodes), 2)
        self.drag_link()
        self.assertEqual(len(self.canvas.connections), 1)

    def test_deleting_node_removes_attached_links(self):
        self.drag_link()
        self.source.setSelected(True)
        self.canvas.delete_selected()
        self.assertFalse(self.canvas.connections)
        self.assertFalse(self.canvas.document.connections)
        self.assertFalse(self.sink_pad.links)
        self.assertEqual(len(self.canvas.document.elements), 1)

    def test_escape_cancels_drag_without_document_changes(self):
        start = self.canvas.mapFromScene(self.src_pad.scenePos())
        QtT.QTest.mousePress(self.canvas.viewport(),
                            QtC.Qt.MouseButton.LeftButton, pos=start)
        QtT.QTest.mouseMove(self.canvas.viewport(), start + QtC.QPoint(40, 40))
        QtT.QTest.keyClick(self.canvas, QtC.Qt.Key.Key_Escape)
        QtT.QTest.mouseRelease(self.canvas.viewport(),
                              QtC.Qt.MouseButton.LeftButton, pos=start)
        self.assertIsNone(self.canvas._link_preview)
        self.assertFalse(self.canvas.document.connections)


if __name__ == '__main__':
    unittest.main()
