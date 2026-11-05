"""Typed edit operations exposed to MCP clients."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


PropertyValue = str | bool | int | float | None


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class AddElement(Operation):
    op: Literal["add_element"]
    factory: str
    name: str
    properties: dict[str, PropertyValue] = Field(default_factory=dict)
    x: float | None = None
    y: float | None = None


class Connect(Operation):
    op: Literal["connect"]
    source: str
    source_pad: str
    sink: str
    sink_pad: str
    caps: str = ""


class Disconnect(Operation):
    op: Literal["disconnect"]
    connection_id: str


class SetCaps(Operation):
    op: Literal["set_caps"]
    connection_id: str
    caps: str


class SetProperties(Operation):
    op: Literal["set_properties"]
    element: str
    properties: dict[str, PropertyValue]


class RenameElement(Operation):
    op: Literal["rename_element"]
    element: str
    name: str


class RemoveElement(Operation):
    op: Literal["remove_element"]
    element: str


class MoveElement(Operation):
    op: Literal["move_element"]
    element: str
    x: float
    y: float


class Clear(Operation):
    op: Literal["clear"]


class AutoLayout(Operation):
    op: Literal["auto_layout"]


Edit = Annotated[
    AddElement | Connect | Disconnect | SetCaps | SetProperties
    | RenameElement | RemoveElement | MoveElement | Clear | AutoLayout,
    Field(discriminator="op"),
]
EDIT_ADAPTER = TypeAdapter(list[Edit])
