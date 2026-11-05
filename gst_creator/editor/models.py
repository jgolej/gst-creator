"""Mutable pipeline instances, separate from installed factory definitions."""

from dataclasses import dataclass, field
from uuid import uuid4

from gst_creator.gstreamer.models import PadAvailability
from gst_creator.gstreamer.models import PadDirection
from gst_creator.gstreamer.models import PadTemplate


@dataclass
class ElementInstance:
    factory_name: str
    name: str
    x: float = 0.0
    y: float = 0.0
    id: str = field(default_factory=lambda: uuid4().hex)
    property_overrides: dict[str, str] = field(default_factory=dict)
    pad_templates: tuple[PadTemplate, ...] = ()


@dataclass(frozen=True)
class PadReference:
    """A planned pad; slot distinguishes uses of a variable pad template.

    Request and dynamic references are plans, not existing runtime pads.
    """

    element_id: str     # Unique ID of the element instance that owns the pad.
    template_name: str  # Pad template name, such as "src" or "src_%u".
    direction: PadDirection  # SRC for output or SINK for input.
    slot: int = 0       # Distinguishes pads using the same template; 0 for fixed pads.


@dataclass(frozen=True)
class Connection:
    source: PadReference
    sink: PadReference
    id: str = field(default_factory=lambda: uuid4().hex)
    caps: str = ""


@dataclass
class PipelineDocument:
    elements: dict[str, ElementInstance] = field(default_factory=dict)
    connections: dict[str, Connection] = field(default_factory=dict)

    def add_element(
        self, factory_name: str, x: float = 0.0, y: float = 0.0,
        pad_templates: tuple[PadTemplate, ...] = (),
    ) -> ElementInstance:
        used_names = {element.name for element in self.elements.values()}
        index = 0
        while f"{factory_name}{index}" in used_names:
            index += 1

        instance = ElementInstance(
            factory_name, f"{factory_name}{index}", x, y,
            pad_templates=pad_templates,
        )
        
        self.elements[instance.id] = instance
        return instance

    def remove_element(self, element_id: str) -> None:
        attached: list[str] = []
        for connection_id, connection in self.connections.items():
            is_source = connection.source.element_id == element_id
            is_sink = connection.sink.element_id == element_id
            if is_source or is_sink:
                attached.append(connection_id)

        for connection_id in attached:
            del self.connections[connection_id]

        del self.elements[element_id]

    def rename_element(self, element_id: str, name: str) -> None:
        name = name.strip()
        if not name:
            raise ValueError("Enter a non-empty instance name.")
        
        for element in self.elements.values():
            if element.id == element_id:
                continue
            if element.name == name:
                raise ValueError("Another element already uses this name.")
        
        self.elements[element_id].name = name

    def connect(
        self, source_id: str, source_template_name: str,
        sink_id: str, sink_template_name: str,
    ) -> Connection:
        if source_id == sink_id:
            raise ValueError("Connect pads on different elements.")
        
        source = self._available_pad(
            source_id, source_template_name, PadDirection.SRC,
        )
        sink = self._available_pad(
            sink_id, sink_template_name, PadDirection.SINK,
        )

        connection = Connection(source, sink)
        self.connections[connection.id] = connection
        
        return connection

    def _available_pad(
        self, element_id: str, template_name: str, direction: PadDirection,
    ) -> PadReference:
        element = self.elements.get(element_id)
        if element is None:
            raise ValueError("The element no longer exists.")
        
        template = None
        for pad in element.pad_templates:
            if pad.name_template != template_name:
                continue
            if pad.direction != direction:
                continue
            template = pad
            break

        if template is None:
            raise ValueError(f"Pad '{template_name}' ({direction}) not found.")
        
        used: set[int] = set()
        for connection in self.connections.values():
            for pad in (connection.source, connection.sink):
                if pad.element_id != element_id:
                    continue
                if pad.template_name != template_name:
                    continue
                if pad.direction != direction:
                    continue
                used.add(pad.slot)

        is_request_or_dynamic = template.availability != PadAvailability.ALWAYS
        has_name_placeholder = "%" in template.name_template
        repeatable = is_request_or_dynamic and has_name_placeholder
        
        if used and not repeatable:
            raise ValueError(f"Pad {element.name}:{template_name} "
                             "is already connected.")
        
        slot = 0
        while slot in used:
            slot += 1

        return PadReference(element_id, template_name, direction, slot)
