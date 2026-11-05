"""Parse gst-inspect's human-readable output, retaining unmodeled details."""

import ast
from fractions import Fraction
import json
import re
from typing import Any

from gst_creator.gstreamer.models import (
    CapsKind,
    CapsStructure,
    ElementDefinition,
    FactoryInfo,
    FloatRange,
    FractionRange,
    IntRange,
    PadAvailability,
    PadDirection,
    PadTemplate,
    PluginInfo,
    PropertyChoice,
    PropertyDefinition,
    PropertyFlags,
    PropertyType,
    Range,
    Value,
)


def _sections(lines: list[str]) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: list[str] = []
    for line in lines:
        if line and not line[0].isspace():
            current = sections.setdefault(line.rstrip(":"), [])
        else:
            current.append(line)

    return sections


def _metadata(lines: list[str]) -> dict[str, str]:
    result = {}
    for line in lines:
        parts = re.split(r"\s{2,}", line.strip(), maxsplit=1)
        if len(parts) == 2:
            result[parts[0]] = parts[1]

    return result


def _blocks(lines: list[str], pattern: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    for line in lines:
        if re.match(pattern, line):
            blocks.append([line])
        elif blocks:
            blocks[-1].append(line)

    return blocks


###############################################################################
# Parse pads
###############################################################################

def _split_values(text: str) -> list[str]:
    """Split commas outside nested containers and quoted strings."""
    parts = []
    start = 0
    depth = 0
    quoted = False
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif not quoted:
            if char in "{[<(":
                depth += 1
            elif char in "}]>)":
                depth -= 1
            elif char == "," and depth == 0:
                parts.append(text[start:index].strip())
                start = index + 1
    if quoted or depth != 0:
        raise ValueError("Unbalanced caps value")
    if text[start:].strip():
        parts.append(text[start:].strip())
    return parts


def _caps_value(text: str, type_name: str = "") -> Any:
    text = text.strip()

    match = re.match(r"^\(([^)]+)\)\s*(.*)$", text, re.DOTALL)
    if match:
        type_name, text = match.groups()

    if text.startswith("{") and text.endswith("}"):
        return [_caps_value(part, type_name) 
                for part in _split_values(text[1:-1])]
    
    if text.startswith("<") and text.endswith(">"):
        return tuple(_caps_value(part, type_name)
                     for part in _split_values(text[1:-1]))
    
    if text.startswith("[") and text.endswith("]"):
        bounds = [_caps_value(part, type_name) for part in _split_values(text[1:-1])]
        if len(bounds) in {2, 3} and all(type(v) is int for v in bounds):
            return IntRange(*bounds)
        if len(bounds) == 2:
            if all(isinstance(v, Fraction) for v in bounds):
                return FractionRange(*bounds)
            if all(type(v) in {int, float} for v in bounds):
                return FloatRange(*(float(v) for v in bounds))
        raise ValueError("Unsupported caps range")
    
    if (not type_name or type_name in {"string", "str", "s"}) and (
        text.startswith('"') and text.endswith('"')
    ):
        return ast.literal_eval(text)
    
    if type_name in {"string", "str", "s"}:
        return text
    
    if type_name in {"boolean", "bool", "b"} or (
        not type_name and text.lower() in {"true", "false"}
    ):
        if text.lower() not in {"true", "false"}:
            raise ValueError("Invalid boolean")
        return text.lower() == "true"
    
    if type_name in {"fraction", "f"} or (
        not type_name and re.fullmatch(r"[+-]?\d+/[+-]?\d+", text)
    ):
        numerator, denominator = text.split("/", maxsplit=1)
        return Fraction(int(numerator), int(denominator))
    
    if type_name in {"int", "uint", "int64", "uint64", "gint64", "i"}:
        return int(text, 16 if text.lower().startswith("0x") else 10)
    
    if type_name in {"float", "double", "d"}:
        return float(text)
    
    if not type_name:
        if re.fullmatch(r"[+-]?\d+", text):
            return int(text)
        if re.fullmatch(r"[+-]?0[xX][0-9a-fA-F]+", text):
            return int(text, 16)
        if re.fullmatch(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?",
                        text):
            return float(text)
        
    # Preserve values whose GStreamer type is not supported yet.
    return f"({type_name}){text}" if type_name else text


def _parse_caps_value(value_text: str) -> Any:
    """Convert a field value, preserving unsupported or malformed text."""
    try:
        return _caps_value(value_text)
    except (ValueError, SyntaxError, ZeroDivisionError):
        return value_text


def _parse_capabilities(lines: list[str]) -> tuple[CapsKind, tuple[CapsStructure, ...]]:
    """Group caps fields, then convert their values into structures."""
    stripped_lines = [line for text in lines if (line := text.strip())]
    if len(stripped_lines) == 1 and stripped_lines[0].upper() in {"ANY", "EMPTY"}:
        return CapsKind(stripped_lines[0].lower()), ()

    groups: list[tuple[str, list[tuple[str, str]], list[str]]] = []
    for line in stripped_lines:
        # Match a media type, e.g. video/x-raw(memory:DMABuf).
        if re.fullmatch(r"[\w.+-]+/[\w.+-]+(?:\([^)]*\))?", line):
            groups.append((line, [], [line]))
        elif groups:
            _, fields, source_lines = groups[-1]
            source_lines.append(line)
            # Match a caps field name and value, e.g. width: [ 1, 1920 ].
            match = re.match(r"^([\w-]+)\s*:\s*(.*)$", line)
            if match:
                fields.append((match[1], match[2]))
            elif fields:
                name, value = fields[-1]
                fields[-1] = (name, value + " " + line)

    structures = tuple(
        CapsStructure(
            media_type=media_type,
            fields={name: _parse_caps_value(value) for name, value in fields},
            raw_text="\n".join(source_lines),
        )
        for media_type, fields, source_lines in groups
    )
    kind = CapsKind.SPECIFIC if structures else CapsKind.UNKNOWN

    return kind, structures


def _parse_pads(lines: list[str]) -> tuple[PadTemplate, ...]:
    pads = []
    for block in _blocks(lines, r"^  (?:SRC|SINK) template:"):
        match = re.match(r"  (SRC|SINK) template: '(.+)'", block[0])
        if match is None:
            continue

        availability = ""
        capabilities = []
        in_caps = False
        for line in block[1:]:
            if line.startswith("    Availability:"):
                availability = line.partition(":")[2].strip()
            elif line.startswith("    Capabilities:"):
                in_caps = True
            elif line.strip() and in_caps:
                if line.startswith("      "):
                    capabilities.append(line)
                else:
                    in_caps = False

        caps_kind, structures = _parse_capabilities(capabilities)

        pads.append(PadTemplate(
            name_template=match[2],
            direction=PadDirection(match[1].lower()),
            availability=PadAvailability(availability),
            capabilities=caps_kind,
            structures=structures,
            raw_text="\n".join(block).rstrip(),
        ))

    return tuple(pads)


###############################################################################
# Parse properties
###############################################################################

def _property_flags(text: str) -> PropertyFlags:
    parts = {part.strip() for part in text.split(",")}
    return PropertyFlags(
        readable="readable" in parts,
        writable="writable" in parts,
        deprecated="deprecated" in parts,
    )


def _property_type(text: str) -> PropertyType:
    for kind in PropertyType:
        if text == kind.value or text.startswith(kind.value + " "):
            return kind
    
    return PropertyType.UNKNOWN


def _default_value(type_name: PropertyType, text: str) -> Value | None:
    """Convert factory defaults while retaining unsupported values as text."""
    text = text.strip()
    if text.lower() in {"null", "(null)"}:
        return None
    
    try:
        if type_name == PropertyType.BOOLEAN:
            if text not in {"true", "false"}:
                raise ValueError("Invalid boolean default")
            return text == "true"
        if type_name.is_integer:
            return int(text)
        if type_name in {PropertyType.FLOAT, PropertyType.DOUBLE}:
            return float(text)
        if type_name in {PropertyType.ENUM, PropertyType.FLAGS}:
            number = text.partition(",")[0].strip()
            return int(number, 16 if number.lower().startswith("0x") else 10)
        if type_name == PropertyType.FRACTION:
            return Fraction(text)
        if type_name == PropertyType.STRING and text.startswith('"'):
            value = json.loads(text)
            if isinstance(value, str):
                return value
    except (ValueError, ZeroDivisionError):
        pass

    return text


def _property_range(type_name: PropertyType, text: str) -> Range | None:
    bounds = re.split(r"\s+-\s+", text.strip())
    if len(bounds) != 2:
        return None
    
    try:
        if type_name.is_integer:
            low, high = map(int, bounds)
            return IntRange(low, high) if low <= high else None
        if type_name in {PropertyType.FLOAT, PropertyType.DOUBLE}:
            low, high = map(float, bounds)
            return FloatRange(low, high) if low <= high else None
        if type_name == PropertyType.FRACTION:
            low, high = map(Fraction, bounds)
            return FractionRange(low, high) if low <= high else None
    except (ValueError, ZeroDivisionError):
        pass

    # Unsupported or malformed ranges remain in the element's raw text.
    return None


def _parse_properties(lines: list[str]) -> tuple[PropertyDefinition, ...]:
    """Parse the Element Properties section of gst-inspect output."""
    properties = []
    for block in _blocks(lines, r"^  [\w-]+\s*:"):
        name, _, description = block[0].strip().partition(":")
        details = [line.strip() for line in block[1:] if line.strip()]

        flags = PropertyFlags()
        type_line = ""
        choices = []
        for line in details:
            if line.startswith("flags:"):
                flags = _property_flags(line.partition(":")[2].strip())
            elif line.startswith("("):
                match = re.match(r"\(([^)]+)\):\s*(\S+)\s+-\s*(.*)", line)
                if match:
                    choices.append(PropertyChoice(*match.groups()))
            elif not type_line:
                type_line = line

        before_default, separator, default = type_line.partition("Default:")
        type_name, range_separator, value_range = before_default.partition("Range:")

        type_name = type_name.strip().removesuffix(".")
        if type_name.startswith("Caps "):
            default = type_name[len("Caps "):].strip()
            type_name = PropertyType.CAPS
            separator = True
        type_name = _property_type(type_name)

        # Some Windows builds use the system decimal separator even with
        # LC_ALL=C. Normalize only floating-point metadata, never strings,
        # enum descriptions, or other comma-delimited values.
        if type_name in {PropertyType.FLOAT, PropertyType.DOUBLE}:
            default = default.replace(",", ".")
            value_range = value_range.replace(",", ".")

        properties.append(PropertyDefinition(
            name=name.strip(),
            description=description.strip(),
            type=type_name,
            flags=flags,
            default_value=_default_value(type_name, default) if separator else None,
            value_range=_property_range(type_name, value_range) if range_separator else None,
            choices=tuple(choices),
            raw_text="\n".join(block).rstrip(),
        ))
    
    return tuple(properties)


###############################################################################
# Parse elements
###############################################################################

def parse_elements(output: str) -> list[ElementDefinition]:
    """Read prefixed --print-all output into reusable factory definitions.

    Raw element text preserves sections and values not yet modeled.
    """
    factories: dict[str, list[str]] = {}
    for line in output.splitlines():
        name, separator, content = line.partition(": ")
        if not separator:
            continue
        if content == "Factory Details:":
            factories[name] = [content]
        elif name in factories:
            factories[name].append(content)

    elements = []
    for name, lines in sorted(factories.items()):
        sections = _sections(lines)

        factory = _metadata(sections.get("Factory Details", []))
        plugin = _metadata(sections.get("Plugin Details", []))

        elements.append(ElementDefinition(
            factory=FactoryInfo(
                name=name,
                long_name=factory.get("Long-name", name),
                category=factory.get("Klass", ""),
                description=factory.get("Description", ""),
                raw_text="\n".join(
                    sections.get("Factory Details", [])
                ).rstrip(),
            ),
            plugin=PluginInfo(
                name=plugin.get("Name", "Unknown"),
                description=plugin.get("Description", ""),
                version=plugin.get("Version", ""),
                raw_text="\n".join(
                    sections.get("Plugin Details", [])
                ).rstrip(),
            ),
            pads=_parse_pads(sections.get("Pad Templates", [])),
            properties=_parse_properties(sections.get("Element Properties", [])),
            raw_text="\n".join(lines),
        ))
        
    return elements
