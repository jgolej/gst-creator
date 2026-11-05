"""Pad-template anchors and selectable connection curves."""

import PySide6.QtCore as QtC
import PySide6.QtGui as QtG
import PySide6.QtWidgets as QtW


from gst_creator.gstreamer.models import CapsKind, PadAvailability, PadDirection


def connection_path(source, sink):
    path = QtG.QPainterPath(source)
    distance = max(60, abs(sink.x() - source.x()) / 2)
    path.cubicTo(source + QtC.QPointF(distance, 0),
                 sink - QtC.QPointF(distance, 0), sink)
    return path


def caps_display_text(caps):
    """Put fields on separate lines without splitting lists or strings."""
    parts = []
    start = 0
    depth = 0
    quoted = False
    escaped = False
    for index, char in enumerate(caps):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif not quoted:
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth = max(0, depth - 1)
            elif char in ",;" and depth == 0:
                parts.append(caps[start:index + 1].strip())
                start = index + 1

    parts.append(caps[start:].strip())
    return "\n".join(parts)


class CapsLabel(QtW.QGraphicsTextItem):
    """Wrapped text on an opaque card, readable over any curve angle."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setDefaultTextColor(QtG.QColor("#ffca68"))
        self.document().setDocumentMargin(7)
        self.setTextInteractionFlags(QtC.Qt.TextInteractionFlag.NoTextInteraction)
        self.setAcceptedMouseButtons(QtC.Qt.MouseButton.LeftButton)

    def setText(self, text):
        self.setPlainText(text)
        self.setTextWidth(-1)
        self.setTextWidth(min(260, self.document().idealWidth()))
        self.setVisible(bool(text))

    def text(self):
        return self.toPlainText()

    def paint(self, painter, option, widget=None):
        painter.setPen(QtG.QPen(QtG.QColor("#73869c"), 1))
        painter.setBrush(QtG.QColor("#18212c"))
        painter.drawRoundedRect(self.boundingRect().adjusted(.5, .5, -.5, -.5),
                                5, 5)
        super().paint(painter, option, widget)

    def mousePressEvent(self, event):
        self.scene().clearSelection()
        self.parentItem().setSelected(True)
        event.accept()


class PadItem(QtW.QGraphicsEllipseItem):
    """Anchor representing a static pad or a planned request/dynamic pad."""

    def __init__(self, node, template, y):
        super().__init__(-7, -7, 14, 14, node)
        self.node = node
        self.template = template
        self.links = set()
        self.setPos(0 if template.direction == PadDirection.SINK else 220, y)
        colors = {
            PadAvailability.ALWAYS: "#76d7a0",
            PadAvailability.ON_REQUEST: "#ffca68",
            PadAvailability.SOMETIMES: "#b9a0ff",
        }
        self.setBrush(QtG.QColor(colors.get(template.availability, "#aab4c0")))
        pen = QtG.QPen(QtG.QColor("#e7edf5"), 1.5)
        if template.availability != PadAvailability.ALWAYS:
            pen.setStyle(QtC.Qt.PenStyle.DashLine)
        self.setPen(pen)
        self.setCursor(QtC.Qt.CursorShape.CrossCursor)
        suffix = {
            PadAvailability.ALWAYS: "",
            PadAvailability.ON_REQUEST: " [request]",
            PadAvailability.SOMETIMES: " [dynamic]",
        }.get(template.availability, "")
        text = template.name_template + suffix
        label = QtW.QGraphicsSimpleTextItem(self)
        label.setText(QtG.QFontMetrics(label.font()).elidedText(
            text, QtC.Qt.TextElideMode.ElideRight, 94
        ))
        label.setBrush(QtG.QColor("#e7edf5"))
        label.setPos(12 if template.direction == PadDirection.SINK
                     else -12 - label.boundingRect().width(), -9)
        label.setAcceptedMouseButtons(QtC.Qt.MouseButton.NoButton)
        if template.capabilities == CapsKind.SPECIFIC:
            caps_text = "\n".join(
                structure.raw_text or structure.media_type
                for structure in template.structures
            )
        else:
            caps_text = template.capabilities.value.upper()
        tip = (f"{template.name_template} ({template.direction})\n"
               f"Availability: {template.availability}\n\n"
               f"{caps_text}")
        if template.availability != PadAvailability.ALWAYS:
            tip += "\n\nPlanned pad; resolved when the pipeline runs."
        self.setToolTip(tip)
        label.setToolTip(tip)


class ConnectionItem(QtW.QGraphicsPathItem):
    def __init__(self, connection, source, sink):
        super().__init__()
        self.connection = connection
        self.source = source
        self.sink = sink
        self.setFlag(QtW.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setZValue(-1)
        self.setPen(QtG.QPen(QtG.QColor("#8ec9ff"), 2.5))
        source.links.add(self)
        sink.links.add(self)
        self.caps_label = CapsLabel(self)
        self.refresh_caps()

    def refresh_caps(self):
        self.caps_label.setText(caps_display_text(self.connection.caps))
        self.caps_label.setToolTip(self.connection.caps)
        self.setToolTip(
            f"{self.source.template.name_template} → "
            f"{self.sink.template.name_template}\n"
            f"{self.connection.caps or 'No caps filter'}\n"
            "Select to edit caps; press Delete to remove."
        )
        self.update_path()

    def update_path(self):
        self.setPath(connection_path(self.source.scenePos(),
                                     self.sink.scenePos()))
        midpoint = self.path().pointAtPercent(0.5)
        bounds = self.caps_label.boundingRect()
        self.caps_label.setPos(midpoint.x() - bounds.width() / 2,
                               midpoint.y() - bounds.height() - 5)

    def shape(self):
        stroke = QtG.QPainterPathStroker()
        stroke.setWidth(12)
        return stroke.createStroke(self.path())

    def boundingRect(self):
        return self.path().boundingRect().adjusted(-6, -6, 6, 6)

    def itemChange(self, change, value):
        if change == QtW.QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            color = "#ffca68" if value else "#8ec9ff"
            self.setPen(QtG.QPen(QtG.QColor(color), 3 if value else 2.5))
        return super().itemChange(change, value)

    def detach(self):
        self.source.links.discard(self)
        self.sink.links.discard(self)
