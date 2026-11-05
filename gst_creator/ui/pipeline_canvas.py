"""Interactive canvas for placing and arranging pipeline elements."""

import math
from dataclasses import replace
from uuid import uuid4

import PySide6.QtCore as QtC
import PySide6.QtGui as QtG
import PySide6.QtWidgets as QtW

from gst_creator.editor.models import ElementInstance, PipelineDocument
from gst_creator.gstreamer.models import ElementDefinition, PadDirection
from gst_creator.ui.icons import tool_icon
from gst_creator.ui.pipeline_connections import (
    ConnectionItem, PadItem, connection_path,
)


class ElementNode(QtW.QGraphicsRectItem):
    """A selectable node whose position is stored in the document."""

    def __init__(
        self, instance: ElementInstance, definition: ElementDefinition
    ) -> None:
        super().__init__(0, 0, 220, 76)
        self.instance = instance
        self.definition = definition
        self.setFlags(
            QtW.QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QtW.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QtW.QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setBrush(QtG.QColor("#263445"))
        self.setPen(QtG.QPen(QtG.QColor("#73869c"), 1.5))
        self.setToolTip(f"{definition.factory.long_name}\n{definition.factory.description}")
        header_path = QtG.QPainterPath()
        header_path.addRoundedRect(QtC.QRectF(2, 2, 216, 34), 8, 8)
        self.header = QtW.QGraphicsPathItem(header_path, self)
        categories = set(definition.factory.category.casefold().split("/"))
        directions = {pad.direction for pad in definition.pads}
        if "source" in categories and PadDirection.SINK not in categories:
            header_color = "#286456"
        elif PadDirection.SINK in categories and "source" not in categories:
            header_color = "#795036"
        elif directions == {PadDirection.SRC}:
            header_color = "#286456"
        elif directions == {PadDirection.SINK}:
            header_color = "#795036"
        else:
            header_color = "#3b5573"
        self.header.setBrush(QtG.QColor(header_color))
        self.header.setPen(QtG.QPen(QtC.Qt.PenStyle.NoPen))
        self.header.setAcceptedMouseButtons(QtC.Qt.MouseButton.NoButton)
        for text, y, bold in (
            (instance.name, 0, True), (definition.factory.name, 44, False)
        ):
            label = QtW.QGraphicsSimpleTextItem(self)
            font = label.font()
            font.setBold(bold)
            if not bold:
                font.setPointSizeF(max(6, font.pointSizeF() - 1))
            label.setFont(font)
            label.setText(QtG.QFontMetrics(font).elidedText(
                text, QtC.Qt.TextElideMode.ElideRight, 196
            ))
            label.setBrush(QtG.QColor("#f0f4f8" if bold else "#bdcbdc"))
            label.setPos((220 - label.boundingRect().width()) / 2, y)
            label.setAcceptedMouseButtons(QtC.Qt.MouseButton.NoButton)
            if bold:
                self.name_label = label
        self.refresh_name()
        self.pads = {}
        counts = {PadDirection.SRC: 0, PadDirection.SINK: 0}
        for template in definition.pads:
            if template.direction not in counts:
                continue
            y = 92 + counts[template.direction] * 28
            counts[template.direction] += 1
            self.pads[(template.direction, template.name_template)] = PadItem(
                self, template, y
            )
        self.setRect(0, 0, 220, max(76, 80 + max(counts.values()) * 28))
        self.setPos(instance.x, instance.y)

    def shape(self):
        path = QtG.QPainterPath()
        path.addRoundedRect(self.rect(), 10, 10)
        return path

    def paint(self, painter, option, widget=None):
        painter.setPen(self.pen())
        painter.setBrush(self.brush())
        painter.drawRoundedRect(self.rect(), 10, 10)

    def refresh_name(self) -> None:
        self.name_label.setText(QtG.QFontMetrics(
            self.name_label.font()
        ).elidedText(self.instance.name, QtC.Qt.TextElideMode.ElideRight, 196))
        bounds = self.name_label.boundingRect()
        self.name_label.setPos(
            (self.rect().width() - bounds.width()) / 2,
            2 + (34 - bounds.height()) / 2,
        )
        self.name_label.setToolTip(self.instance.name)

    def itemChange(self, change, value):
        if change == QtW.QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.instance.x = value.x()
            self.instance.y = value.y()
            for pad in self.pads.values():
                for link in pad.links:
                    link.update_path()
            if self.scene() is not None:
                # Keep moved nodes reachable beyond the initial workspace.
                self.scene().setSceneRect(self.scene().sceneRect().united(
                    self.sceneBoundingRect().adjusted(-400, -400, 400, 400)
                ))
        elif change == QtW.QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            color = "#65baff" if value else "#73869c"
            self.setPen(QtG.QPen(QtG.QColor(color), 2.5 if value else 1.5))
        return super().itemChange(change, value)


class PipelineCanvas(QtW.QGraphicsView):
    """Drag nodes, rubber-band select, wheel zoom, and middle-button pan."""

    message = QtC.Signal(str)
    selected_node_changed = QtC.Signal(object)
    selected_connection_changed = QtC.Signal(object)
    document_changed = QtC.Signal()

    def __init__(self, parent: QtW.QWidget | None = None) -> None:
        super().__init__(parent)
        self.document = PipelineDocument()
        self.nodes: dict[str, ElementNode] = {}
        self.connections: dict[str, ConnectionItem] = {}
        self._link_start = None
        self._link_preview = None
        self.setScene(QtW.QGraphicsScene(self))
        self.scene().setSceneRect(-2000, -2000, 4000, 4000)
        self.setAccessibleName("Pipeline canvas")
        self.setRenderHint(QtG.QPainter.RenderHint.Antialiasing)
        self.setBackgroundBrush(QtG.QColor("#18212c"))
        self.setDragMode(QtW.QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(
            QtW.QGraphicsView.ViewportAnchor.AnchorUnderMouse
        )
        self._pan_position: QtC.QPoint | None = None

        self.delete_action = QtG.QAction("&Delete selection", self)
        self.delete_action.setIcon(tool_icon("delete"))
        self.delete_action.setShortcut(QtG.QKeySequence.StandardKey.Delete)
        self.delete_action.setShortcutContext(
            QtC.Qt.ShortcutContext.WidgetWithChildrenShortcut
        )
        self.delete_action.setEnabled(False)
        self.delete_action.triggered.connect(self.delete_selected)
        self.addAction(self.delete_action)
        self.duplicate_action = QtG.QAction("&Duplicate selection", self)
        self.duplicate_action.setIcon(tool_icon("copy"))
        self.duplicate_action.setShortcut(QtG.QKeySequence("Ctrl+D"))
        self.duplicate_action.setShortcutContext(
            QtC.Qt.ShortcutContext.WidgetWithChildrenShortcut
        )
        self.duplicate_action.setEnabled(False)
        self.duplicate_action.triggered.connect(self.duplicate_selected)
        self.addAction(self.duplicate_action)
        self.scene().selectionChanged.connect(self._selection_changed)

        self.zoom_in_action = QtG.QAction("Zoom &In", self)
        self.zoom_in_action.setIcon(tool_icon("zoom-in"))
        self.zoom_in_action.triggered.connect(lambda: self.zoom(1.2))
        self.zoom_out_action = QtG.QAction("Zoom &Out", self)
        self.zoom_out_action.setIcon(tool_icon("zoom-out"))
        self.zoom_out_action.triggered.connect(lambda: self.zoom(1 / 1.2))
        self.reset_zoom_action = QtG.QAction("&Actual Size", self)
        self.reset_zoom_action.setIcon(tool_icon("actual-size"))
        self.reset_zoom_action.triggered.connect(self.resetTransform)
        self.fit_action = QtG.QAction("&Fit Elements", self)
        self.fit_action.setIcon(tool_icon("fit"))
        self.fit_action.triggered.connect(self.fit_elements)
        self.fit_action.setEnabled(False)
        self.centerOn(0, 0)

    def add_element(self, definition: ElementDefinition) -> ElementInstance:
        center = self.mapToScene(self.viewport().rect().center())
        position = center - QtC.QPointF(110, 38)
        while any(
            node.sceneBoundingRect().intersects(
                QtC.QRectF(position, QtC.QSizeF(
                    220, max(76, 80 + 28 * max(
                        sum(p.direction == PadDirection.SRC for p in definition.pads),
                        sum(p.direction == PadDirection.SINK for p in definition.pads),
                    ))
                ))
            ) for node in self.nodes.values()
        ):
            position += QtC.QPointF(250, 0)
        instance = self.document.add_element(
            definition.factory.name, position.x(), position.y(), definition.pads
        )
        node = ElementNode(instance, definition)
        self.nodes[instance.id] = node
        self.scene().addItem(node)
        self.scene().setSceneRect(self.scene().sceneRect().united(
            node.sceneBoundingRect().adjusted(-400, -400, 400, 400)
        ))
        self.scene().clearSelection()
        node.setSelected(True)
        self.ensureVisible(node)
        self.fit_action.setEnabled(True)
        self.setFocus()
        self.viewport().update()
        self.document_changed.emit()
        self.message.emit(f"Added {instance.name}")
        return instance

    def _selection_changed(self) -> None:
        selected = self.scene().selectedItems()
        self.delete_action.setEnabled(bool(selected))
        self.duplicate_action.setEnabled(
            any(isinstance(item, ElementNode) for item in selected)
        )
        self.selected_node_changed.emit(
            selected[0] if len(selected) == 1
            and isinstance(selected[0], ElementNode) else None
        )
        self.selected_connection_changed.emit(
            selected[0] if len(selected) == 1
            and isinstance(selected[0], ConnectionItem) else None
        )

    def set_document(self, document, definitions, *, preserve_view=False):
        view_center = self.mapToScene(self.viewport().rect().center())
        self._cancel_link()
        self.scene().clearSelection()
        self.nodes.clear()
        self.connections.clear()
        self.scene().clear()
        self.document = document
        self.scene().setSceneRect(-2000, -2000, 4000, 4000)
        for element in document.elements.values():
            node = ElementNode(element, definitions[element.id])
            self.nodes[element.id] = node
            self.scene().addItem(node)
        for link in document.connections.values():
            source = self.nodes[link.source.element_id].pads[
                (PadDirection.SRC, link.source.template_name)
            ]
            sink = self.nodes[link.sink.element_id].pads[
                (PadDirection.SINK, link.sink.template_name)
            ]
            item = ConnectionItem(link, source, sink)
            self.connections[link.id] = item
            self.scene().addItem(item)
        self.scene().setSceneRect(self.scene().sceneRect().united(
            self.scene().itemsBoundingRect().adjusted(-400, -400, 400, 400)
        ))
        self.fit_action.setEnabled(bool(self.nodes))
        if preserve_view:
            self.centerOn(view_center)
        else:
            self.resetTransform()
            self.fit_elements()
        self.viewport().update()
        self.document_changed.emit()

    def connect_pads(self, source: PadItem, sink: PadItem):
        if source.template.direction != PadDirection.SRC or sink.template.direction != PadDirection.SINK:
            raise ValueError("Drag from an output pad to an input pad.")
        connection = self.document.connect(
            source.node.instance.id, source.template.name_template,
            sink.node.instance.id, sink.template.name_template,
        )
        item = ConnectionItem(connection, source, sink)
        self.connections[connection.id] = item
        self.scene().addItem(item)
        self.document_changed.emit()
        self.message.emit("Connected pads")
        return connection

    def duplicate_selected(self) -> None:
        originals = [node for node in self.nodes.values() if node.isSelected()]
        if not originals:
            return
        self._cancel_link()
        # Place the copied group below the original, preserving its layout.
        top = min(node.sceneBoundingRect().top() for node in originals)
        bottom = max(node.sceneBoundingRect().bottom() for node in originals)
        offset = QtC.QPointF(40, bottom - top + 40)
        while any(node.sceneBoundingRect().translated(offset).intersects(
                existing.sceneBoundingRect())
                for node in originals for existing in self.nodes.values()):
            offset += QtC.QPointF(40, bottom - top + 40)
        used_names = {element.name for element in self.document.elements.values()}
        copies = {}
        for node in originals:
            original = node.instance
            base = f"{original.name}_copy"
            name = base
            index = 2
            while name in used_names:
                name = f"{base}{index}"
                index += 1
            used_names.add(name)
            instance = replace(
                original, id=uuid4().hex, name=name,
                x=original.x + offset.x(), y=original.y + offset.y(),
                property_overrides=dict(original.property_overrides),
            )
            self.document.elements[instance.id] = instance
            copy = ElementNode(instance, node.definition)
            copies[original.id] = copy
            self.nodes[instance.id] = copy
            self.scene().addItem(copy)
            self.scene().setSceneRect(self.scene().sceneRect().united(
                copy.sceneBoundingRect().adjusted(-400, -400, 400, 400)
            ))
        for link in tuple(self.document.connections.values()):
            if (link.source.element_id not in copies
                    or link.sink.element_id not in copies):
                continue
            source = copies[link.source.element_id]
            sink = copies[link.sink.element_id]
            connection = replace(
                link, id=uuid4().hex,
                source=replace(link.source, element_id=source.instance.id),
                sink=replace(link.sink, element_id=sink.instance.id),
            )
            self.document.connections[connection.id] = connection
            item = ConnectionItem(
                connection, source.pads[(PadDirection.SRC, link.source.template_name)],
                sink.pads[(PadDirection.SINK, link.sink.template_name)],
            )
            self.connections[connection.id] = item
            self.scene().addItem(item)
        self.scene().clearSelection()
        for node in copies.values():
            node.setSelected(True)
        self.ensureVisible(next(iter(copies.values())))
        self.setFocus()
        self.document_changed.emit()
        self.message.emit(f"Duplicated {len(copies)} element(s)")

    def delete_selected(self) -> None:
        self._cancel_link()
        selected = self.scene().selectedItems()
        nodes = [item for item in selected if isinstance(item, ElementNode)]
        links = {item for item in selected if isinstance(item, ConnectionItem)}
        for node in nodes:
            for pad in node.pads.values():
                links.update(pad.links)
        for link in links:
            link.detach()
            self.scene().removeItem(link)
            del self.connections[link.connection.id]
            del self.document.connections[link.connection.id]
        for node in nodes:
            self.scene().removeItem(node)
            del self.nodes[node.instance.id]
            self.document.remove_element(node.instance.id)
        self.fit_action.setEnabled(bool(self.nodes))
        self.viewport().update()
        if selected:
            self.document_changed.emit()
            self.message.emit(
                f"Deleted {len(nodes)} element(s) and {len(links)} connection(s)"
            )

    def _pad_at(self, position):
        # Labels share their anchor's hit target; preview curves never mask it.
        for item in self.items(position):
            while item is not None:
                if isinstance(item, PadItem):
                    return item
                item = item.parentItem()
        return None

    def _cancel_link(self):
        if self._link_preview is not None:
            self.scene().removeItem(self._link_preview)
        self._link_start = None
        self._link_preview = None

    def keyPressEvent(self, event):
        if event.key() == QtC.Qt.Key.Key_Escape and self._link_start is not None:
            self._cancel_link()
            event.accept()
            return
        super().keyPressEvent(event)

    def zoom(self, factor: float) -> None:
        current = self.transform().m11()
        target = max(0.2, min(3.0, current * factor))
        self.scale(target / current, target / current)

    def fit_elements(self) -> None:
        if self.nodes:
            bounds = self.scene().itemsBoundingRect().adjusted(-40, -40, 40, 40)
            # fitInView cannot center content outside an explicit scene rect.
            # Imported nodes are positioned before they join the scene, so
            # their position-change handler has not expanded it for them.
            self.scene().setSceneRect(self.scene().sceneRect().united(bounds))
            self.fitInView(bounds, QtC.Qt.AspectRatioMode.KeepAspectRatio)
            # Avoid magnifying a single node excessively.
            if self.transform().m11() > 3:
                self.zoom(1)

    def wheelEvent(self, event: QtG.QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta:
            self.zoom(1.2 if delta > 0 else 1 / 1.2)
        event.accept()

    def mousePressEvent(self, event: QtG.QMouseEvent) -> None:
        if event.button() == QtC.Qt.MouseButton.LeftButton:
            pad = self._pad_at(event.position().toPoint())
            if pad is not None:
                self.setFocus()
                if pad.template.direction != PadDirection.SRC:
                    self.message.emit("Start a connection at an output pad.")
                else:
                    self._cancel_link()
                    self._link_start = pad
                    self._link_preview = QtW.QGraphicsPathItem()
                    self._link_preview.setPen(QtG.QPen(
                        QtG.QColor("#ffca68"), 2, QtC.Qt.PenStyle.DashLine
                    ))
                    self._link_preview.setZValue(1)
                    self.scene().addItem(self._link_preview)
                event.accept()
                return
        if event.button() == QtC.Qt.MouseButton.MiddleButton:
            self._pan_position = event.position().toPoint()
            self.viewport().setCursor(QtC.Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtG.QMouseEvent) -> None:
        if self._link_start is not None:
            self._link_preview.setPath(connection_path(
                self._link_start.scenePos(),
                self.mapToScene(event.position().toPoint()),
            ))
            event.accept()
            return
        if self._pan_position is not None:
            position = event.position().toPoint()
            delta = position - self._pan_position
            self._pan_position = position
            for bar, amount in (
                (self.horizontalScrollBar(), delta.x()),
                (self.verticalScrollBar(), delta.y()),
            ):
                bar.setValue(bar.value() - amount)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtG.QMouseEvent) -> None:
        if (event.button() == QtC.Qt.MouseButton.LeftButton
                and self._link_start is not None):
            source = self._link_start
            target = self._pad_at(event.position().toPoint())
            self._cancel_link()
            if target is not None:
                try:
                    self.connect_pads(source, target)
                except ValueError as error:
                    self.message.emit(str(error))
            else:
                self.message.emit("Connection cancelled; drop on an input pad.")
            event.accept()
            return
        if event.button() == QtC.Qt.MouseButton.MiddleButton:
            self._pan_position = None
            self.viewport().unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)
        self.document_changed.emit()

    def drawBackground(self, painter, rect) -> None:
        super().drawBackground(painter, rect)
        spacing = 40
        left = math.floor(rect.left() / spacing) * spacing
        top = math.floor(rect.top() / spacing) * spacing
        painter.setPen(QtG.QPen(QtG.QColor("#2d3948"), 0))
        for x in range(left, math.ceil(rect.right()), spacing):
            painter.drawLine(QtC.QLineF(x, rect.top(), x, rect.bottom()))
        for y in range(top, math.ceil(rect.bottom()), spacing):
            painter.drawLine(QtC.QLineF(rect.left(), y, rect.right(), y))

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self.nodes:
            painter = QtG.QPainter(self.viewport())
            painter.setPen(QtG.QColor("#c5d0dd"))
            painter.drawText(
                self.viewport().rect().adjusted(24, 24, -24, -24),
                QtC.Qt.AlignmentFlag.AlignCenter
                | QtC.Qt.TextFlag.TextWordWrap,
                "Double-click an element in the browser to add it.\n\n"
                "Drag output pads to input pads to connect.\n"
                "Drag to move or select • Delete to remove\n"
                "Scroll to zoom • Middle-drag to pan",
            )
            painter.end()
