"""GUI-thread command boundary for local AI control."""

from dataclasses import asdict
from fractions import Fraction
import math

import PySide6.QtWidgets as QtW

from gst_creator.control_schema import EDIT_ADAPTER
from gst_creator.editor.automation import (
    apply_edits, pipeline_state, revision, validation_result,
)


def metadata(value):
    """Keep installed metadata compact and JSON-compatible."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key != "raw_text":
                result[key] = metadata(item)
        return result
    if isinstance(value, (tuple, list)):
        return [metadata(item) for item in value]
    if isinstance(value, Fraction):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


class EditorController:
    def __init__(self, window):
        self.window = window

    def definitions(self):
        definitions = dict(self.window._discovered)
        for node in self.window.canvas.nodes.values():
            definitions[node.instance.factory_name] = node.definition
        return definitions

    def dispatch(self, method, arguments):
        if not isinstance(arguments, dict):
            raise ValueError("Arguments must be an object.")
        methods = {
            "get_pipeline": self.get_pipeline,
            "search_elements": self.search_elements,
            "inspect_element": self.inspect_element,
            "apply_pipeline_edits": self.apply_pipeline_edits,
            "validate_pipeline": self.validate_pipeline,
            "undo_pipeline_edit": self.undo_pipeline_edit,
            "redo_pipeline_edit": self.redo_pipeline_edit,
        }
        handler = methods.get(method)
        if handler is None:
            raise ValueError(f"Unknown method: {method}.")
        return handler(**arguments)

    def _require_ready(self):
        if not self.window._has_elements:
            raise ValueError(
                "Element discovery is not ready. Wait, or check the "
                "Element Browser for discovery errors."
            )

    def get_pipeline(self):
        state = pipeline_state(self.window.canvas.document, self.definitions())
        state["discovery_ready"] = self.window._has_elements
        state["discovery_status"] = self.window.discovery_status.text()
        state["project_path"] = (
            str(self.window.project_path) if self.window.project_path else None
        )
        state["modified"] = self.window.isWindowModified()
        state["can_undo"] = self.window.history.stack.canUndo()
        state["can_redo"] = self.window.history.stack.canRedo()
        return state

    def search_elements(self, query="", offset=0, limit=30):
        self._require_ready()
        if (type(offset) is not int or offset < 0
                or type(limit) is not int or not 1 <= limit <= 100):
            raise ValueError("Use offset >= 0 and limit between 1 and 100.")
        terms = query.casefold().split()
        matches = []
        for name, definition in sorted(self.window._discovered.items()):
            factory = definition.factory
            text = " ".join((
                name, factory.long_name, factory.description,
                factory.category, definition.plugin.name,
            )).casefold()
            if all(term in text for term in terms):
                matches.append({
                    "factory": name, "description": factory.description,
                    "category": factory.category,
                    "plugin": definition.plugin.name,
                })
        return {
            "elements": matches[offset:offset + limit],
            "total": len(matches), "offset": offset,
        }

    def inspect_element(self, factory):
        self._require_ready()
        definition = self.window._discovered.get(factory)
        if definition is None:
            raise ValueError(f"Factory '{factory}' is not installed.")
        return metadata(asdict(definition))

    def validate_pipeline(self):
        return validation_result(
            self.window.canvas.document, self.definitions(),
        )

    def _check_edit(self, expected_revision):
        if QtW.QApplication.activeModalWidget() is not None:
            raise ValueError("Close the app's open dialog before editing.")
        if QtW.QApplication.mouseButtons():
            raise ValueError(
                "An editor gesture is active. Retry after it ends."
            )
        if revision(self.window.canvas.document) != expected_revision:
            raise ValueError(
                "The pipeline changed. Read get_pipeline and retry "
                "with its revision."
            )

    def apply_pipeline_edits(self, operations, expected_revision):
        self._require_ready()
        self._check_edit(expected_revision)
        parsed = EDIT_ADAPTER.validate_python(operations)
        operations = [operation.model_dump() for operation in parsed]
        definitions = self.definitions()
        document = apply_edits(
            self.window.canvas.document, definitions,
            operations, expected_revision,
        )
        instances = {}
        for element in document.elements.values():
            instances[element.id] = definitions[element.factory_name]
        self.window.history.commit_document(
            document, instances, "AI pipeline edit",
        )
        self.window.canvas.fit_elements()
        self.window.statusBar().showMessage("AI pipeline edit applied", 5000)
        return {"pipeline": self.get_pipeline(),
                "validation": self.validate_pipeline()}

    def undo_pipeline_edit(self, expected_revision):
        self._check_edit(expected_revision)
        if not self.window.history.stack.canUndo():
            raise ValueError("There is no edit to undo.")
        self.window.history.stack.undo()
        return self.get_pipeline()

    def redo_pipeline_edit(self, expected_revision):
        self._check_edit(expected_revision)
        if not self.window.history.stack.canRedo():
            raise ValueError("There is no edit to redo.")
        self.window.history.stack.redo()
        return self.get_pipeline()
