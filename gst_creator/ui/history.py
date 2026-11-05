"""Undoable document snapshots with coalesced inspector edits."""

from copy import deepcopy

import PySide6.QtCore as QtC
import PySide6.QtGui as QtG
import PySide6.QtWidgets as QtW


class EditCommand(QtG.QUndoCommand):
    def __init__(self, history, before, after, text, group):
        super().__init__(text)
        self.history = history
        self.before = before
        self.after = after
        self.group = group
        self.first_redo = True

    def id(self):
        return 1

    def mergeWith(self, other):
        if self.group is None or self.group != other.group:
            return False
        
        self.after = other.after
        return True

    def undo(self):
        self.history.restore(self.before)

    def redo(self):
        if self.first_redo:
            self.first_redo = False
        else:
            self.history.restore(self.after)


class EditHistory(QtC.QObject):
    def __init__(self, canvas, inspector, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.inspector = inspector
        self.stack = QtG.QUndoStack(self)
        self.stack.setUndoLimit(100)
        self.restoring = False
        self.group = 0
        self.previous = self.snapshot()
        canvas.document_changed.connect(self.record)
        inspector.properties_changed.connect(self.record_property)
        inspector.instance_renamed.connect(self.record_property)
        QtW.QApplication.instance().focusChanged.connect(self.break_group)

    def break_group(self, *args):
        self.group += 1

    def snapshot(self):
        return (
            deepcopy(self.canvas.document),
            {key: node.definition for key, node in self.canvas.nodes.items()},
            tuple(key for key, node in self.canvas.nodes.items()
                  if node.isSelected()),
            tuple(key for key, link in self.canvas.connections.items()
                  if link.isSelected()),
        )

    def reset(self):
        self.stack.clear()
        self.break_group()
        self.previous = self.snapshot()

    def commit_document(self, document, definitions, text):
        """Publish an automation transaction as a single undoable edit."""
        before = self.snapshot()
        if before[0] == document:
            return
        nodes = tuple(key for key in before[2] if key in document.elements)
        links = tuple(key for key in before[3] if key in document.connections)
        self.restore((document, definitions, nodes, links))
        after = self.snapshot()
        self.stack.push(EditCommand(self, before, after, text, None))
        self.previous = after

    def record_property(self, *args):
        self.record(property_edit=True)

    def record(self, *, property_edit=False):
        if self.restoring:
            return
        
        current = self.snapshot()
        before = self.previous[0]
        after = current[0]
        
        if before == after:
            return
        
        if property_edit:
            text = "Edit properties"
            group = self.group
        else:
            self.break_group()
            group = None
            if len(after.elements) > len(before.elements):
                text = "Add element"
            elif len(after.elements) < len(before.elements):
                text = "Delete selection"
            elif len(after.connections) > len(before.connections):
                text = "Connect pads"
            elif len(after.connections) < len(before.connections):
                text = "Delete connection"
            else:
                text = "Move elements"

        self.stack.push(EditCommand(self, self.previous, current, text, group))
        self.previous = current

    def restore(self, snapshot):
        self.restoring = True
        self.break_group()
        try:
            document, definitions, nodes, links = snapshot
            document = deepcopy(document)
            self.inspector.set_node(None)
            self.inspector.document = document
            self.canvas.set_document(document, definitions, preserve_view=True)
            for key in nodes:
                self.canvas.nodes[key].setSelected(True)
            for key in links:
                self.canvas.connections[key].setSelected(True)
            self.previous = self.snapshot()
        finally:
            self.restoring = False
