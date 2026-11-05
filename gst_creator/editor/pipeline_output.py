"""Static pipeline checks and POSIX-shell gst-launch command generation.

Validation does not instantiate elements or check runtime negotiation.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import re
import shlex
import sys

from gst_creator.gstreamer.models import PropertyDefinition, PropertyType
from gst_creator.editor.models import PipelineDocument
from gst_creator.gstreamer.models import CapsKind
from gst_creator.gstreamer.models import ElementDefinition
from gst_creator.gstreamer.models import PadAvailability
from gst_creator.gstreamer.models import PadTemplate


IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*\Z")


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    message: str
    element_id: str | None = None
    connection_id: str | None = None


@dataclass(frozen=True)
class PipelineOutput:
    issues: tuple[ValidationIssue, ...]
    command: str | None
    arguments: tuple[str, ...] = ()


def _property_value(prop: PropertyDefinition, value: str) -> str:
    if prop.name == "name":
        raise ValueError("Use the instance name field instead.")
    
    if not prop.flags.writable:
        raise ValueError("Property is read-only.")
    
    kind = prop.type

    if kind == PropertyType.CAPS:
        if not value.strip() or "\x00" in value:
            raise ValueError("Expected non-empty caps without NUL characters.")
        # GStreamer validates the caps grammar at runtime, as for link caps.

        return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'
    
    if kind == PropertyType.STRING:
        try:
            decoded = json.loads(value)
        except ValueError:
            raise ValueError("Invalid stored string value.") from None
        if not isinstance(decoded, str) or "\x00" in decoded:
            raise ValueError("Expected a string without NUL characters.")
        
        return '"' + decoded.replace('\\', '\\\\').replace('"', '\\"') + '"'
    
    if kind == PropertyType.BOOLEAN:
        if value not in ("true", "false"):
            raise ValueError("Expected true or false.")
        
        return value
    
    if kind == PropertyType.ENUM:
        for choice in prop.choices:
            if value == choice.value or value == choice.nick:
                return choice.value
        raise ValueError("Unknown enumeration value.")
    
    if kind == PropertyType.FLAGS:
        # Resolve nicknames and combinations to a validated unsigned bitmask.
        choices = {entry.nick: int(entry.value, 0) for entry in prop.choices}
        allowed = 0
        for mask in choices.values():
            allowed |= mask

        result = 0
        for token in re.split(r"[+|]", value):
            token = token.strip()
            if token in choices:
                mask = choices[token]
            elif re.fullmatch(r"0[xX][0-9a-fA-F]+|[0-9]+", token):
                mask = int(token, 16 if token.lower().startswith("0x") else 10)
            else:
                raise ValueError("Unknown flags value.")
            if mask > 0xffffffff or mask & ~allowed:
                raise ValueError("Flags contain unknown bits.")
            result |= mask

        return str(result)
    
    if not kind.is_numeric:
        raise ValueError(f"Command generation does not support {kind} yet.")
    
    try:
        number = Decimal(value)
        if not number.is_finite():
            raise ValueError("Expected a finite number.")
        
        if kind.is_integer:
            if not re.fullmatch(r"[+-]?\d+", value):
                raise ValueError("Expected a whole number.")
            
            if kind == PropertyType.INTEGER:
                low = -(2 ** 31)
                high = 2 ** 31 - 1
            elif kind == PropertyType.UNSIGNED_INTEGER:
                low = 0
                high = 2 ** 32 - 1
            elif kind == PropertyType.INTEGER64:
                low = -(2 ** 63)
                high = 2 ** 63 - 1
            elif kind == PropertyType.UNSIGNED_INTEGER64:
                low = 0
                high = 2 ** 64 - 1
            if not low <= number <= high:
                raise ValueError("Value exceeds the integer type's range.")
        else:
            if kind == PropertyType.FLOAT:
                limit = Decimal("3.4028234663852886e38")
            else:
                limit = Decimal(str(sys.float_info.max))

            if abs(number) > limit:
                raise ValueError("Value exceeds the floating-point range.")
            
        if prop.value_range is not None:
            low = Decimal(str(prop.value_range.minimum))
            high = Decimal(str(prop.value_range.maximum))
            if not low <= number <= high:
                raise ValueError("Value is outside the property range.")
    except InvalidOperation:
        raise ValueError("Invalid numeric value or range.") from None
    
    return str(int(number)) if kind.is_integer else str(number)


def _pad_name(template: PadTemplate, slot: int) -> str:
    if slot < 0:
        raise ValueError("Invalid negative pad slot.")
    
    name = template.name_template
    if "%" in name:
        if template.availability != PadAvailability.ON_REQUEST:
            raise ValueError(
                "Dynamic pad names require runtime stream selection; "
                "command generation is not supported for this link yet."
            )
        
        if len(re.findall(r"%[ud]", name)) != 1:
            raise ValueError("Unsupported request-pad name pattern.")
        
        name = re.sub(r"%[ud]", str(slot), name)
    elif slot != 0:
        raise ValueError("A fixed-name pad cannot have multiple slots.")
    
    if not IDENTIFIER.fullmatch(name):
        raise ValueError("Pad name cannot be represented in a command.")
    
    return name


def _media_types(pad: PadTemplate) -> set[str]:
    # Only reject disjoint media types. Fields/features need GstCaps analysis.
    if pad.capabilities != CapsKind.SPECIFIC:
        return set()
    
    return {structure.media_type.split("(", 1)[0] for structure in pad.structures}


def _can_link_automatically(
    definition: ElementDefinition, template: PadTemplate,
) -> bool:
    """Only omit pad names when there is one fixed pad in that direction."""
    if template.availability != PadAvailability.ALWAYS:
        return False
    
    for candidate in definition.pads:
        if candidate.direction == template.direction and candidate != template:
            return False
        
    return True


def _command_tokens(
    document: PipelineDocument,
    declarations: dict[str, list[str]],
    links: dict[str, list[str]],
    automatic_pads: set[str],
) -> list[str]:
    """Emit downstream chains, declaring each element exactly once."""
    outgoing = {}
    incoming = set()
    for key, connection in document.connections.items():
        outgoing.setdefault(connection.source.element_id, []).append(key)
        incoming.add(connection.sink.element_id)

    roots = []
    for key in document.elements:
        if key not in incoming:
            roots.append(key)
    # Include disconnected elements and components containing cycles.
    roots.extend(document.elements)

    tokens = ["gst-launch-1.0", "-e"]
    emitted = set()
    for root in roots:
        if root in emitted:
            continue
        tokens.extend(declarations[root])
        emitted.add(root)
        tail = root
        pending = list(reversed(outgoing.get(root, [])))
        while pending:
            key = pending.pop()
            connection = document.connections[key]
            source, *middle, sink = links[key]
            if (tail != connection.source.element_id
                    or source not in automatic_pads):
                tokens.append(source)
            tokens.extend(middle)

            sink_id = connection.sink.element_id
            if sink_id in emitted:
                tokens.append(sink)
                tail = None
                continue

            if sink not in automatic_pads:
                pad_name = sink.split(".", 1)[1]
                tokens.append(f".{pad_name}")
            tokens.extend(declarations[sink_id])
            emitted.add(sink_id)
            tail = sink_id
            pending.extend(reversed(outgoing.get(sink_id, [])))

    return tokens


def build_output(
    document: PipelineDocument,
    definitions: dict[str, ElementDefinition],
) -> PipelineOutput:
    issues = []
    declarations = {}
    links = {}
    automatic_pads = set()
    names = set()
    used_pads = set()
    linked_elements = set()

    if not document.elements:
        issues.append(ValidationIssue(
            "error", "Add elements to the pipeline.",
        ))

    for key, element in document.elements.items():
        def error(message: str) -> None:
            issues.append(ValidationIssue("error", message, key))

        if not IDENTIFIER.fullmatch(element.name):
            error(f"{element.name}: use a name starting with a letter or "
                  "underscore; use letters, digits, underscores or hyphens.")
            
        if element.name in names:
            error(f"Duplicate instance name: {element.name}.")

        names.add(element.name)
        definition = definitions.get(element.factory_name)
        if definition is None:
            error(f"Factory {element.factory_name} is unavailable.")
            continue

        if not re.fullmatch(r"[A-Za-z0-9_-]+", element.factory_name):
            error("Unsupported factory name.")

        declaration = [element.factory_name, f"name={element.name}"]
        declarations[key] = declaration
        properties = {prop.name: prop for prop in definition.properties}
        for name, value in sorted(element.property_overrides.items()):
            prop = properties.get(name)
            if prop is None or not IDENTIFIER.fullmatch(name):
                error(f"{element.name}: unknown property {name}.")
                continue
            try:
                declaration.append(f"{name}={_property_value(prop, value)}")
            except ValueError as problem:
                error(f"{element.name}.{name}: {problem}")

    for key, connection in document.connections.items():
        endpoints = []
        templates = []
        for reference, direction in ((connection.source, "src"),
                                     (connection.sink, "sink")):
            element = document.elements.get(reference.element_id)
            definition = None
            template = None
            if element is not None:
                definition = definitions.get(element.factory_name)
            if definition is not None:
                for candidate in definition.pads:
                    if candidate.direction != direction:
                        continue
                    if candidate.name_template != reference.template_name:
                        continue
                    template = candidate
                    break
            try:
                if element is None or template is None:
                    raise ValueError("Connection references a missing element/pad.")
                
                if reference.direction != direction:
                    raise ValueError("Connect output pads to input pads.")
                
                name = _pad_name(template, reference.slot)
                endpoint = (reference.element_id, direction, name)
                if endpoint in used_pads:
                    raise ValueError(
                        f"{element.name}.{name} is connected twice."
                    )
                
                used_pads.add(endpoint)
                linked_elements.add(reference.element_id)
                endpoint_name = f"{element.name}.{name}"
                endpoints.append(endpoint_name)

                if _can_link_automatically(definition, template):
                    automatic_pads.add(endpoint_name)
                templates.append(template)

                if template.availability == PadAvailability.SOMETIMES:
                    issues.append(ValidationIssue(
                        "warning", "This link needs a pad to appear at runtime.",
                        reference.element_id, key,
                    ))
            except ValueError as problem:
                issues.append(ValidationIssue(
                    "error", str(problem), reference.element_id, key,
                ))

        if connection.source.element_id == connection.sink.element_id:
            issues.append(ValidationIssue(
                "error", "An element cannot link to itself.", connection_id=key,
            ))

        if len(endpoints) == 2:
            if any(pad.capabilities == CapsKind.EMPTY for pad in templates):
                issues.append(ValidationIssue(
                    "error", "Connected pad has empty capabilities.",
                    connection_id=key,
                ))

            source_types, sink_types = map(_media_types, templates)
            if source_types and sink_types and not source_types & sink_types:
                issues.append(ValidationIssue(
                    "error", "Connected pads have incompatible media types.",
                    connection_id=key,
                ))

            link_tokens = [endpoints[0], "!"]
            links[key] = link_tokens
            if connection.caps.strip():
                if "\x00" in connection.caps:
                    issues.append(ValidationIssue(
                        "error", "Caps cannot contain NUL characters.",
                        connection_id=key,
                    ))
                caps = connection.caps.strip().replace('\\', '\\\\')
                caps = caps.replace('"', '\\"')
                link_tokens.extend(["capsfilter", f'caps="{caps}"', "!"])
            link_tokens.append(endpoints[1])

    for key, element in document.elements.items():
        definition = definitions.get(element.factory_name)
        if definition is None:
            continue

        if len(document.elements) > 1 and key not in linked_elements:
            issues.append(ValidationIssue(
                "warning", f"{element.name} is disconnected.", key,
            ))

        for pad in definition.pads:
            if pad.availability == PadAvailability.ALWAYS and (
                key, pad.direction, pad.name_template
            ) not in used_pads:
                issues.append(ValidationIssue(
                    "warning", f"{element.name}.{pad.name_template} is unlinked.",
                    key,
                ))

    if document.elements:
        issues.append(ValidationIssue(
            "info", "Static checks only: full caps negotiation, runtime pads, "
            "devices, files and execution have not been verified.",
        ))

    command = None
    tokens = []
    if not any(issue.severity == "error" for issue in issues):
        tokens = _command_tokens(document, declarations, links, automatic_pads)
        command = shlex.join(tokens)
        
    return PipelineOutput(
        tuple(issues), command, tuple(tokens[1:]) if command else (),
    )
