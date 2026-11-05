"""Document and canvas interaction checks (use QT_QPA_PLATFORM=offscreen)."""

import unittest

import PySide6.QtCore as QtC
import PySide6.QtTest as QtT
import PySide6.QtWidgets as QtW

from gst_creator.editor.models import PipelineDocument
from gst_creator.gstreamer.models import (
    ElementDefinition, FactoryInfo, PluginInfo,
)
from gst_creator.ui.pipeline_canvas import PipelineCanvas


class DocumentTests(unittest.TestCase):
    def test_instances_have_unique_identity_and_independent_overrides(self):
        document = PipelineDocument()
        first = document.add_element("tee")
        second = document.add_element("tee")
        self.assertNotEqual(first.id, second.id)
        self.assertEqual((first.name, second.name), ("tee0", "tee1"))
        first.property_overrides["silent"] = "false"
        self.assertEqual(second.property_overrides, {})
        del document.elements[first.id]
        third = document.add_element("tee")
        self.assertNotEqual(third.name, second.name)
        self.assertNotEqual(third.id, first.id)


class CanvasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtW.QApplication.instance() or QtW.QApplication([])

    def setUp(self):
        self.canvas = PipelineCanvas()
        self.canvas.resize(800, 600)
        self.canvas.show()
        self.app.processEvents()
        self.definition = ElementDefinition(
            FactoryInfo("tee", "Tee", "Generic", "Split"), PluginInfo(),
        )

    def tearDown(self):
        self.canvas.close()

    def test_drag_updates_document_position(self):
        instance = self.canvas.add_element(self.definition)
        node = self.canvas.nodes[instance.id]
        start = self.canvas.mapFromScene(node.sceneBoundingRect().center())
        initial = QtC.QPointF(node.pos())
        end = start + QtC.QPoint(80, 50)
        QtT.QTest.mousePress(
            self.canvas.viewport(), QtC.Qt.MouseButton.LeftButton, pos=start
        )
        QtT.QTest.mouseMove(self.canvas.viewport(), end)
        QtT.QTest.mouseRelease(
            self.canvas.viewport(), QtC.Qt.MouseButton.LeftButton, pos=end
        )
        self.assertNotEqual(node.pos(), initial)
        self.assertEqual((instance.x, instance.y),
                         (node.pos().x(), node.pos().y()))

    def test_delete_key_removes_only_selected_instances(self):
        first = self.canvas.add_element(self.definition)
        second = self.canvas.add_element(self.definition)
        self.app.processEvents()
        QtT.QTest.keyClick(self.canvas, QtC.Qt.Key.Key_Delete)
        self.assertIn(first.id, self.canvas.document.elements)
        self.assertNotIn(second.id, self.canvas.document.elements)
        self.assertNotIn(second.id, self.canvas.nodes)
        self.assertFalse(self.canvas.delete_action.isEnabled())
        self.canvas.nodes[first.id].setSelected(True)
        self.canvas.delete_action.trigger()
        self.assertFalse(self.canvas.document.elements)
        self.assertFalse(self.canvas.fit_action.isEnabled())

    def test_middle_drag_pans_without_moving_nodes(self):
        instance = self.canvas.add_element(self.definition)
        original = (instance.x, instance.y)
        center = self.canvas.mapToScene(self.canvas.viewport().rect().center())
        QtT.QTest.mousePress(
            self.canvas.viewport(), QtC.Qt.MouseButton.MiddleButton,
            pos=QtC.QPoint(100, 100),
        )
        QtT.QTest.mouseMove(self.canvas.viewport(), QtC.QPoint(160, 140))
        QtT.QTest.mouseRelease(
            self.canvas.viewport(), QtC.Qt.MouseButton.MiddleButton,
            pos=QtC.QPoint(160, 140),
        )
        self.assertNotEqual(
            center,
            self.canvas.mapToScene(self.canvas.viewport().rect().center()),
        )
        self.assertEqual((instance.x, instance.y), original)

    def test_zoom_bounds_and_fit_include_distant_nodes(self):
        self.canvas.zoom(100)
        self.assertAlmostEqual(self.canvas.transform().m11(), 3.0)
        self.canvas.zoom(0.001)
        self.assertAlmostEqual(self.canvas.transform().m11(), 0.2)
        first = self.canvas.add_element(self.definition)
        second = self.canvas.add_element(self.definition)
        self.canvas.nodes[first.id].setPos(-5000, 0)
        self.canvas.nodes[second.id].setPos(5000, 0)
        self.canvas.fit_elements()
        visible = self.canvas.mapToScene(self.canvas.viewport().rect())
        for node in self.canvas.nodes.values():
            self.assertTrue(visible.boundingRect().contains(
                node.sceneBoundingRect()
            ))
        self.canvas.reset_zoom_action.trigger()
        self.assertAlmostEqual(self.canvas.transform().m11(), 1.0)

    def test_loaded_nodes_expand_scene_before_first_fit(self):
        document = PipelineDocument()
        definitions = {}
        for index in range(30):
            element = document.add_element('tee', index * 340, index % 2 * 300)
            definitions[element.id] = self.definition
        self.canvas.set_document(document, definitions)
        self.app.processEvents()
        self.canvas.fit_action.trigger()
        visible = self.canvas.mapToScene(self.canvas.viewport().rect())
        for node in self.canvas.nodes.values():
            self.assertTrue(self.canvas.sceneRect().contains(
                node.sceneBoundingRect()
            ))
            self.assertTrue(visible.boundingRect().contains(
                node.sceneBoundingRect()
            ))


if __name__ == "__main__":
    unittest.main()
