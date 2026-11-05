"""Immutable installed-element definitions, independent of editor instances.

Property defaults use Python values converted from inspection metadata.
Caps structures contain dictionaries and lists of converted field values.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from fractions import Fraction
from typing import Any, TypeAlias


@dataclass(frozen=True)
class IntRange:
    minimum: int
    maximum: int
    step: int = 1


@dataclass(frozen=True)
class FloatRange:
    minimum: float
    maximum: float


@dataclass(frozen=True)
class FractionRange:
    minimum: Fraction
    maximum: Fraction


Range: TypeAlias = IntRange | FloatRange | FractionRange
Value: TypeAlias = bool | int | float | str | Fraction


@dataclass(frozen=True)
class FactoryInfo:
    name: str
    long_name: str = ""
    category: str = ""
    description: str = ""
    raw_text: str = ""


@dataclass(frozen=True)
class PluginInfo:
    name: str = "Unknown"
    description: str = ""
    version: str = ""
    raw_text: str = ""


class PadDirection(StrEnum):
    SRC = "src"
    SINK = "sink"


class PadAvailability(StrEnum):
    ALWAYS = "Always"
    ON_REQUEST = "On request"
    SOMETIMES = "Sometimes"


class CapsKind(StrEnum):
    SPECIFIC = "specific"
    ANY = "any"
    EMPTY = "empty"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CapsStructure:
    media_type: str
    fields: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""


@dataclass(frozen=True)
class PadTemplate:
    name_template: str
    direction: PadDirection
    availability: PadAvailability
    capabilities: CapsKind = CapsKind.UNKNOWN
    structures: tuple[CapsStructure, ...] = ()
    raw_text: str = ""


class PropertyType(StrEnum):
    BOOLEAN = "Boolean"
    INTEGER = "Integer"
    UNSIGNED_INTEGER = "Unsigned Integer"
    INTEGER64 = "Integer64"
    UNSIGNED_INTEGER64 = "Unsigned Integer64"
    FLOAT = "Float"
    DOUBLE = "Double"
    STRING = "String"
    ENUM = "Enum"
    FLAGS = "Flags"
    FRACTION = "Fraction"
    CAPS = "Caps"
    OBJECT = "Object"
    BOXED = "Boxed"
    POINTER = "Pointer"
    UNKNOWN = "Unknown"

    @property
    def is_integer(self) -> bool:
        return self in {
            self.INTEGER, self.UNSIGNED_INTEGER,
            self.INTEGER64, self.UNSIGNED_INTEGER64,
        }

    @property
    def is_numeric(self) -> bool:
        return self.is_integer or self in {self.FLOAT, self.DOUBLE}


@dataclass(frozen=True)
class PropertyFlags:
    readable: bool = False
    writable: bool = False
    deprecated: bool = False


@dataclass(frozen=True)
class PropertyChoice:
    value: str
    nick: str
    description: str


@dataclass(frozen=True)
class PropertyDefinition:
    name: str
    description: str
    type: PropertyType
    flags: PropertyFlags = PropertyFlags()
    default_value: Value | None = None
    value_range: Range | None = None
    choices: tuple[PropertyChoice, ...] = ()
    raw_text: str = ""


@dataclass(frozen=True)
class ElementDefinition:
    factory: FactoryInfo
    plugin: PluginInfo
    pads: tuple[PadTemplate, ...] = ()
    properties: tuple[PropertyDefinition, ...] = ()
    raw_text: str = ""
