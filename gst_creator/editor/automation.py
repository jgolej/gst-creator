"""Transactional pipeline edits shared by automation and the editor."""

from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import math

from gst_creator.editor.pipeline_output import (
    IDENTIFIER, _property_value, build_output,
)
from gst_creator.editor.project import serialize
from gst_creator.gstreamer.models import PropertyType


def revision(document):
    return hashlib.sha256(serialize(document).encode("utf-8")).hexdigest()


def pipeline_state(document, definitions):
    state = json.loads(serialize(document))["document"]
    # Expose natural values instead of the editor's property storage encoding.
    for element in state["elements"]:
        properties = element.pop("property_overrides")
        definition = definitions.get(element["factory_name"])
        types = {}
        if definition is not None:
            types = {prop.name: prop.type for prop in definition.properties}
        for name, value in properties.items():
            if types.get(name) == PropertyType.STRING:
                try:
                    properties[name] = json.loads(value)
                except ValueError:
                    pass
        element["properties"] = properties
    state["revision"] = revision(document)
    return state


def validation_result(document, definitions):
    output = build_output(document, definitions)
    return {
        "revision": revision(document),
        "issues": [asdict(issue) for issue in output.issues],
        "command": output.command,
        "arguments": list(output.arguments),
    }


def find_element(document, reference):
    if reference in document.elements:
        return document.elements[reference]
    for element in document.elements.values():
        if element.name == reference:
            return element
    raise ValueError(
        f"Element '{reference}' does not exist. Read get_pipeline."
    )


def set_properties(element, properties, definition):
    available = {prop.name: prop for prop in definition.properties}
    for name, value in properties.items():
        prop = available.get(name)
        if prop is None:
            raise ValueError(f"Unknown property: {element.name}.{name}.")
        if name == "name":
            raise ValueError("Use rename_element to change the instance name.")
        if value is None:
            element.property_overrides.pop(name, None)
            continue
        if prop.type == PropertyType.STRING:
            if not isinstance(value, str):
                raise ValueError(f"{name} requires a string.")
            stored = json.dumps(value, ensure_ascii=False)
        elif isinstance(value, bool):
            stored = "true" if value else "false"
        elif isinstance(value, (str, int, float)):
            stored = str(value)
        else:
            raise ValueError(f"Unsupported value for {name}.")
        _property_value(prop, stored)
        element.property_overrides[name] = stored


def auto_layout(document):
    """Place sources on the left and branches on separate rows."""
    incoming = {key: 0 for key in document.elements}
    children = {key: [] for key in document.elements}
    levels = {key: 0 for key in document.elements}
    for link in document.connections.values():
        incoming[link.sink.element_id] += 1
        children[link.source.element_id].append(link.sink.element_id)
    pending = [key for key in incoming if incoming[key] == 0]
    for key in pending:
        for child in children[key]:
            levels[child] = max(levels[child], levels[key] + 1)
            incoming[child] -= 1
            if incoming[child] == 0:
                pending.append(child)
    rows = {}
    for key, element in document.elements.items():
        level = levels[key]
        element.x = level * 340.0
        element.y = rows.get(level, 0.0)
        height = max(240, 160 + 28 * len(element.pad_templates))
        rows[level] = element.y + height


def apply_edits(document, definitions, operations, expected_revision):
    """Return a validated copy. Never mutate the live document on failure."""
    if expected_revision != revision(document):
        raise ValueError(
            "The pipeline changed. Read get_pipeline and retry "
            "with its revision."
        )
    if not isinstance(operations, list) or not 1 <= len(operations) <= 500:
        raise ValueError("Provide between 1 and 500 edit operations.")
    candidate = deepcopy(document)
    for index, operation in enumerate(operations):
        try:
            _apply_operation(candidate, definitions, operation)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"Operation {index + 1}: {error}") from error
    if candidate.elements:
        output = build_output(candidate, definitions)
        errors = []
        for issue in output.issues:
            if issue.severity == "error":
                errors.append(issue.message)
        if errors:
            raise ValueError("No edits applied: " + "; ".join(errors))
    return candidate


def _position(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Coordinates must be numbers.")
    if not math.isfinite(value) or abs(value) > 1_000_000:
        raise ValueError("Coordinates must be finite and within +/-1000000.")
    return float(value)


def _rename(document, element, name):
    if not isinstance(name, str) or not IDENTIFIER.fullmatch(name):
        raise ValueError("Use a name starting with a letter or underscore; "
                         "then letters, digits, underscores or hyphens.")
    if name in document.elements and name != element.id:
        raise ValueError("The name conflicts with another element ID.")
    document.rename_element(element.id, name)


def _apply_operation(document, definitions, operation):
    kind = operation["op"]
    if kind == "add_element":
        factory = operation["factory"]
        if factory not in definitions:
            raise ValueError(f"Factory '{factory}' is not installed.")
        definition = definitions[factory]
        right = -340.0
        for existing in document.elements.values():
            right = max(right, existing.x)
        x = operation.get("x")
        y = operation.get("y")
        element = document.add_element(
            factory, _position(right + 340 if x is None else x),
            _position(0 if y is None else y), definition.pads,
        )
        _rename(document, element, operation["name"])
        set_properties(element, operation.get("properties", {}), definition)
    elif kind == "connect":
        source = find_element(document, operation["source"])
        sink = find_element(document, operation["sink"])
        link = document.connect(
            source.id, operation["source_pad"], sink.id, operation["sink_pad"],
        )
        document.connections[link.id] = replace(
            link, caps=operation.get("caps", ""),
        )
    elif kind == "disconnect":
        del document.connections[operation["connection_id"]]
    elif kind == "set_caps":
        key = operation["connection_id"]
        document.connections[key] = replace(
            document.connections[key], caps=operation["caps"],
        )
    elif kind in {"set_properties", "rename_element", "remove_element",
                  "move_element"}:
        element = find_element(document, operation["element"])
        if kind == "set_properties":
            set_properties(element, operation["properties"],
                           definitions[element.factory_name])
        elif kind == "rename_element":
            _rename(document, element, operation["name"])
        elif kind == "remove_element":
            document.remove_element(element.id)
        else:
            element.x = _position(operation["x"])
            element.y = _position(operation["y"])
    elif kind == "clear":
        document.elements.clear()
        document.connections.clear()
    elif kind == "auto_layout":
        auto_layout(document)
    else:
        raise ValueError(f"Unknown edit operation: {kind}.")
