"""Numeric property controls, including exact integers beyond Qt's int range."""

import re
import sys

import PySide6.QtCore as QtC
import PySide6.QtGui as QtG
import PySide6.QtWidgets as QtW

from gst_creator.gstreamer.models import PropertyType


class WideIntegerSpinBox(QtW.QAbstractSpinBox):
    """Spin box using Python integers for unsigned and 64-bit properties."""

    valueChanged = QtC.Signal(object)

    def __init__(self, minimum: int, maximum: int) -> None:
        super().__init__()
        self._minimum = minimum
        self._maximum = maximum
        self._value = minimum
        self.lineEdit().setText(str(minimum))
        self.editingFinished.connect(self._interpret_text)

    def minimum(self) -> int:
        return self._minimum

    def maximum(self) -> int:
        return self._maximum

    def value(self) -> int:
        return self._value

    def setValue(self, value: int) -> None:
        value = max(self._minimum, min(self._maximum, value))
        changed = value != self._value
        self._value = value
        self.lineEdit().setText(str(value))
        if changed:
            self.valueChanged.emit(value)

    def validate(self, text, position):
        state = QtG.QValidator.State

        if text in ("", "+", "-"):
            return state.Intermediate, text, position
        
        if not re.fullmatch(r"[+-]?\d+", text):
            return state.Invalid, text, position
        
        number = int(text)
        if self._minimum <= number <= self._maximum:
            return state.Acceptable, text, position
        
        if 0 <= number < self._minimum:
            return state.Intermediate, text, position
        
        return state.Invalid, text, position

    def fixup(self, text):
        return str(self._value)

    def _interpret_text(self) -> None:
        text = self.lineEdit().text()
        if self.validate(text, 0)[0] == QtG.QValidator.State.Acceptable:
            self.setValue(int(text))
        else:
            self.lineEdit().setText(str(self._value))

    def stepBy(self, steps: int) -> None:
        self._interpret_text()
        self.setValue(self._value + steps)

    def stepEnabled(self):
        flags = QtW.QAbstractSpinBox.StepEnabledFlag
        result = flags.StepNone
        if not self.isReadOnly():
            if self._value < self._maximum:
                result |= flags.StepUpEnabled
            if self._value > self._minimum:
                result |= flags.StepDownEnabled

        return result


class RealSpinBox(QtW.QDoubleSpinBox):
    """Retain small floating-point values without displaying trailing zeros."""

    def textFromValue(self, value):
        return self.locale().toString(value, 'g', 17)


def numeric_editor(prop, value):
    """Create a spin box with the type's range narrowed by factory metadata."""
    kind = prop.type
    integer = kind.is_integer
    if integer:
        bits = 64 if kind in {
            PropertyType.INTEGER64, PropertyType.UNSIGNED_INTEGER64,
        } else 32
        unsigned = kind in {
            PropertyType.UNSIGNED_INTEGER, PropertyType.UNSIGNED_INTEGER64,
        }
        low = 0 if unsigned else -(2 ** (bits - 1))
        high = 2 ** (bits if unsigned else bits - 1) - 1
        convert = int
    else:
        high = (3.4028234663852886e38 if kind == PropertyType.FLOAT
                else sys.float_info.max)
        low = -high
        convert = float

    if prop.value_range is not None:
        low = max(low, convert(prop.value_range.minimum))
        high = min(high, convert(prop.value_range.maximum))

    if integer:
        if -(2 ** 31) <= low <= high <= 2 ** 31 - 1:
            editor = QtW.QSpinBox()
            editor.setRange(low, high)
        else:
            editor = WideIntegerSpinBox(low, high)
    else:
        editor = RealSpinBox()
        editor.setDecimals(323)
        editor.setRange(low, high)
        editor.setSingleStep(min(0.1, (high - low) / 100))

    editor.setLocale(QtC.QLocale.c())
    editor.setKeyboardTracking(False)
    editor.setValue(convert(value) if value is not None else max(low, 0))
    editor.setReadOnly(not prop.flags.writable)
    
    return editor
