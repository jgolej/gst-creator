"""Compact JSON projects resolved against discovered element metadata."""

from dataclasses import asdict
import json
import os
from os import PathLike
from pathlib import Path
import tempfile
from typing import Any

from gst_creator.gstreamer.models import PadAvailability
from gst_creator.gstreamer.models import PadDirection
from gst_creator.gstreamer.models import ElementDefinition
from gst_creator.editor.models import Connection
from gst_creator.editor.models import ElementInstance
from gst_creator.editor.models import PadReference
from gst_creator.editor.models import PipelineDocument


def serialize(document: PipelineDocument) -> str:
    pipeline = asdict(document)
    pipeline["elements"] = list(pipeline["elements"].values())
    pipeline["connections"] = list(pipeline["connections"].values())

    for element in pipeline["elements"]:
        del element["pad_templates"]
        
    return json.dumps({
        "format": "gst-creator", "version": 1,
        "document": pipeline,
    }, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def _decode_pad(data: dict[str, Any]) -> PadReference:
    return PadReference(
        element_id=data["element_id"],
        template_name=data["template_name"],
        direction=PadDirection(data["direction"]),
        slot=data["slot"],
    )


def _decode_element(data: dict[str, Any]) -> ElementInstance:
    return ElementInstance(
        factory_name=data["factory_name"],
        name=data["name"],
        x=data["x"],
        y=data["y"],
        id=data["id"],
        property_overrides=data["property_overrides"],
    )


def _decode_connection(data: dict[str, Any]) -> Connection:
    return Connection(
        source=_decode_pad(data["source"]),
        sink=_decode_pad(data["sink"]),
        id=data["id"],
        caps=data["caps"],
    )


def _decode_document(data: dict[str, Any]) -> PipelineDocument:
    stored_elements = data["elements"]
    stored_connections = data["connections"]
    document = PipelineDocument()

    for element_data in stored_elements:
        element = _decode_element(element_data)
        if element.id in document.elements:
            raise ValueError("Duplicate element ID.")
        document.elements[element.id] = element

    for connection_data in stored_connections:
        connection = _decode_connection(connection_data)
        if connection.id in document.connections:
            raise ValueError("Duplicate connection ID.")
        document.connections[connection.id] = connection

    return document


def deserialize(
    text: str, discovered: dict[str, ElementDefinition],
) -> PipelineDocument:
    """Restore a document and resolve its pads from discovered factories."""
    try:
        data = json.loads(text)
        if (not isinstance(data, dict)
                or data.get("format") != "gst-creator"
                or type(data.get("version")) is not int
                or data["version"] != 1):
            raise ValueError("Unsupported project format or version.")
        
        document = _decode_document(data["document"])

        names = set()
        for key, element in document.elements.items():
            if not key or key != element.id or not element.name.strip():
                raise ValueError("Invalid element identity.")
            
            if element.name in names:
                raise ValueError("Duplicate instance name.")
            
            names.add(element.name)
            definition = discovered.get(element.factory_name)
            if definition is None:
                raise ValueError(
                    f"Factory '{element.factory_name}' was not discovered. "
                    "Install the plugin and use Tools > Rediscover Elements."
                )
            element.pad_templates = definition.pads

        used = set()
        for key, link in document.connections.items():
            if not key or key != link.id:
                raise ValueError("Invalid connection identity.")
            
            if link.source.element_id == link.sink.element_id:
                raise ValueError("Cannot connect an element to itself.")
            
            for pad, direction in ((link.source, "src"), (link.sink, "sink")):
                element = document.elements[pad.element_id]
                template = None
                for candidate in element.pad_templates:
                    if candidate.name_template != pad.template_name:
                        continue
                    if candidate.direction != direction:
                        continue
                    template = candidate
                    break

                if template is None:
                    raise ValueError(
                        f"Pad '{pad.template_name}' ({direction}) is no longer "
                        f"available on '{element.factory_name}'."
                    )
                
                if pad.direction != direction or pad.slot < 0 or pad in used:
                    raise ValueError("Invalid or occupied connection pad.")
                
                if pad.slot and (template.availability == PadAvailability.ALWAYS
                                 or "%" not in template.name_template):
                    raise ValueError("Invalid fixed pad slot.")
                
                used.add(pad)
                
        return document
    except (KeyError, TypeError, AttributeError, OverflowError,
            RecursionError) as error:
        raise ValueError("Invalid project structure.") from error


def save_project(path: str | PathLike[str], text: str) -> None:
    """Atomically replace the destination only after a complete write."""
    path = Path(path)
    data = text.encode("utf-8")
    temporary_path = None
    
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=".gst-creator-", delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(data)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
