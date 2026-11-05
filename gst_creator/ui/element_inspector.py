"""Property controls for a single selected pipeline instance."""

import json
from dataclasses import replace

import PySide6.QtCore as QtC
import PySide6.QtWidgets as QtW

from gst_creator.gstreamer.models import PropertyType
from gst_creator.editor.models import PipelineDocument
from gst_creator.ui.numeric_controls import numeric_editor


def plain_label(text: str) -> QtW.QLabel:
    label = QtW.QLabel(text)
    label.setTextFormat(QtC.Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class ElementInspector(QtW.QWidget):
    instance_renamed = QtC.Signal(str)
    properties_changed = QtC.Signal()

    def __init__(self, document: PipelineDocument, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(300)
        self.document = document
        self.node = None
        self.editors = {}
        self.reset_buttons = {}
        self.layout = QtW.QVBoxLayout(self)
        self.scroll = QtW.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.layout.addWidget(self.scroll)
        self.set_node(None)

    def set_node(self, node) -> None:
        self.node = node
        self.editors = {}
        self.reset_buttons = {}

        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()

        content = QtW.QWidget()
        form = QtW.QFormLayout(content)
        form.setSizeConstraint(QtW.QLayout.SizeConstraint.SetMinAndMaxSize)
        form.setFieldGrowthPolicy(
            QtW.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        form.setRowWrapPolicy(QtW.QFormLayout.RowWrapPolicy.WrapAllRows)
        self.scroll.setWidget(content)

        if node is None:
            form.addRow(plain_label(
                "Select an element to edit properties or a connection "
                "to edit caps."
            ))
            return
        
        instance = node.instance
        definition = node.definition
        self.name_edit = QtW.QLineEdit(instance.name)
        self.name_edit.setAccessibleName("Instance name")
        self.name_error = plain_label("")
        self.name_error.hide()
        self.name_edit.editingFinished.connect(self._rename)
        form.addRow("Instance name", self.name_edit)
        form.addRow(self.name_error)
        form.addRow(plain_label("Properties"))
        properties = QtW.QWidget()
        property_form = QtW.QFormLayout(properties)
        property_form.setContentsMargins(0, 0, 0, 0)
        property_form.setFieldGrowthPolicy(
            QtW.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        property_form.setRowWrapPolicy(
            QtW.QFormLayout.RowWrapPolicy.DontWrapRows
        )
        form.addRow(properties)

        for prop in definition.properties:
            # Name is edited above; parent is a runtime object reference.
            if prop.name == "name":
                continue

            self._add_property(property_form, instance, prop)

        if not definition.properties:
            form.addRow(plain_label("No property metadata available."))

    def set_connection(self, item):
        if item is None:
            return
        
        self.set_node(None)
        form = self.scroll.widget().layout()
        form.removeRow(0)
        form.addRow(plain_label("Connection caps"))
        self.caps_edit = QtW.QLineEdit(item.connection.caps)
        self.caps_edit.setAccessibleName("Connection caps")
        self.caps_edit.setPlaceholderText(
            "video/x-raw,width=1280,height=720,framerate=30/1"
        )
        self.caps_edit.setToolTip(
            "GStreamer caps syntax. Leave empty for unrestricted negotiation."
        )
        form.addRow(self.caps_edit)
        reset = QtW.QPushButton("Reset")
        form.addRow(reset)
        form.addRow(plain_label(
            "Caps constrain negotiation; they do not convert media. "
            "Syntax and compatibility are checked by GStreamer when run."
        ))
        editor = self.caps_edit

        def commit():
            key = item.connection.id
            if key not in self.document.connections:
                return
            
            caps = editor.text().strip()
            if caps == item.connection.caps:
                return
            
            item.connection = replace(item.connection, caps=caps)
            self.document.connections[key] = item.connection
            item.refresh_caps()
            self.properties_changed.emit()

        editor.textEdited.connect(commit)
        reset.clicked.connect(lambda: (editor.clear(), commit()))

    def _rename(self) -> None:
        if self.node is None:
            return
        
        instance = self.node.instance
        if instance.id not in self.document.elements:
            return
        
        try:
            self.document.rename_element(instance.id, self.name_edit.text())
        except ValueError as error:
            self.name_error.setText(str(error))
            self.name_error.show()
            return
        
        self.name_edit.setText(instance.name)
        self.name_error.hide()
        self.instance_renamed.emit(instance.id)

    def _add_property(self, form, instance, prop) -> None:
        default_text = self._default_editor_text(prop)
        value = instance.property_overrides.get(prop.name, default_text)
        kind = prop.type
        caps = kind == PropertyType.CAPS
        numeric = kind.is_numeric
        supported = (
            numeric or caps
            or kind in {PropertyType.BOOLEAN, PropertyType.STRING}
            or (kind in {PropertyType.ENUM, PropertyType.FLAGS}
                and bool(prop.choices))
        )
        writable = prop.flags.writable and supported
        row = QtW.QWidget()
        controls = QtW.QHBoxLayout(row)
        controls.setContentsMargins(0, 0, 0, 0)

        if numeric:
            editor = numeric_editor(prop, value)
        elif kind == PropertyType.BOOLEAN and writable:
            editor = QtW.QCheckBox()
            editor.setChecked(value == "true")
        elif kind == PropertyType.ENUM and writable:
            editor = QtW.QComboBox()
            for choice in prop.choices:
                editor.addItem(f"{choice.nick} — {choice.description}",
                               choice.value)
            selected = (value or "").partition(",")[0].strip()
            editor.setCurrentIndex(editor.findData(selected))
        else:
            editor = QtW.QLineEdit()
            editor.setReadOnly(not writable)
            editor.setText(self._display_value(kind, value))
            if kind == PropertyType.STRING and value is None:
                editor.setPlaceholderText("null (no string)")
            if numeric:
                editor.setPlaceholderText("Enter a number")
        
        editor.setAccessibleName(prop.name)
        self.editors[prop.name] = editor
        controls.addWidget(editor, 1)
        reset = QtW.QPushButton("Reset")
        reset.setAccessibleName(f"Reset {prop.name}")
        reset.setToolTip("Remove the override and use the factory default")
        reset.setEnabled(prop.name in instance.property_overrides)
        self.reset_buttons[prop.name] = reset
        controls.addWidget(reset)
        note = prop.description
        note += f"\nType: {kind}"
        shown_default = default_text if default_text is not None else "None"
        note += f"\nDefault: {shown_default}"
        if prop.value_range is not None:
            note += (
                f"\nRange: {prop.value_range.minimum}"
                f" - {prop.value_range.maximum}"
            )

        if caps:
            note += "\nGStreamer checks caps syntax when the pipeline runs."

        if kind == PropertyType.FLAGS:
            note += "\nUse flag names joined by +, or a numeric bitmask."
            note += "\nChoices: " + ", ".join(p.nick for p in prop.choices)

        if not prop.flags.writable:
            note += "\nRead-only; shown values are factory metadata."
        elif not supported:
            note += "\nEditing this property type is not supported yet."

        label = plain_label(prop.name)
        label.setBuddy(editor)
        label.setToolTip(note)
        editor.setToolTip(note)
        row.setToolTip(note)
        form.addRow(label, row)

        def commit():
            if instance.id not in self.document.elements:
                return
            
            if isinstance(editor, QtW.QCheckBox):
                text = "true" if editor.isChecked() else "false"
            elif isinstance(editor, QtW.QComboBox):
                text = editor.currentData()
                if text is None:
                    return
            elif kind == PropertyType.STRING:
                text = json.dumps(editor.text(), ensure_ascii=False)
            elif numeric:
                text = str(editor.value())
            elif caps or kind == PropertyType.FLAGS:
                text = editor.text().strip()
            else:
                return
            
            instance.property_overrides[prop.name] = text
            reset.setEnabled(True)
            self.properties_changed.emit()

        def restore():
            instance.property_overrides.pop(prop.name, None)
            blocker = QtC.QSignalBlocker(editor)

            if numeric:
                convert = int if kind.is_integer else float
                editor.setValue(convert(prop.default_value)
                                if prop.default_value is not None
                                else max(editor.minimum(), 0))
            elif isinstance(editor, QtW.QCheckBox):
                editor.setChecked(prop.default_value is True)
            elif isinstance(editor, QtW.QComboBox):
                default = default_text or ""
                editor.setCurrentIndex(editor.findData(default))
            else:
                editor.setText(self._display_value(kind, default_text))

            del blocker
            reset.setEnabled(False)
            self.properties_changed.emit()

        if writable:
            if numeric:
                editor.valueChanged.connect(commit)
            elif isinstance(editor, QtW.QCheckBox):
                editor.toggled.connect(commit)
            elif isinstance(editor, QtW.QComboBox):
                editor.currentIndexChanged.connect(commit)
            else:
                # Only commit edits, not focus changes on untouched defaults.
                editor.textEdited.connect(lambda: editor.setModified(True))
                editor.editingFinished.connect(
                    lambda: commit() if editor.isModified() else None
                )

        reset.clicked.connect(restore)

    @staticmethod
    def _default_editor_text(prop) -> str | None:
        value = prop.default_value
        if value is None:
            return None
        if prop.type == PropertyType.STRING:
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, bool):
            return "true" if value else "false"
        if prop.type == PropertyType.FLAGS and isinstance(value, int):
            return f"0x{value:08x}"
        
        return str(value)

    @staticmethod
    def _display_value(kind, value) -> str:
        if value is None:
            return ""
        if kind == PropertyType.FLAGS:
            return value.partition(",")[0].strip()
        if kind == PropertyType.STRING and value == "null":
            return ""
        if kind == PropertyType.STRING and value.startswith('"'):
            try:
                decoded = json.loads(value)
                if isinstance(decoded, str):
                    return decoded
            except ValueError:
                pass
            
        return value
