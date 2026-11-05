"""Searchable tree of GStreamer elements grouped by their owning plugin."""

import PySide6.QtCore as QtC
import PySide6.QtGui as QtG
import PySide6.QtWidgets as QtW

from gst_creator.gstreamer.models import ElementDefinition


class ElementBrowser(QtW.QWidget):
    element_activated = QtC.Signal(object)

    def __init__(self, parent: QtW.QWidget | None = None) -> None:
        super().__init__(parent)
        self._elements: list[ElementDefinition] = []

        self.search = QtW.QLineEdit()
        self.search.setPlaceholderText("Search elements…")
        self.search.setAccessibleName("Search GStreamer elements")
        self.search.setClearButtonEnabled(True)
        self.search.setEnabled(False)

        self.status = QtW.QLabel("Discovering installed elements…")
        self.status.setWordWrap(True)
        self.status.setTextFormat(QtC.Qt.TextFormat.PlainText)

        self.element_list = QtW.QTreeWidget()
        self.element_list.setHeaderLabels(["Plugin / Element", "Description"])
        self.element_list.setRootIsDecorated(True)
        self.element_list.setColumnWidth(0, 170)
        self.element_list.setAccessibleName("Installed GStreamer elements")

        self.details = QtW.QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName("Element details")
        self.details.setPlaceholderText("Select an element to inspect it.")
        self.details.setLineWrapMode(QtW.QPlainTextEdit.LineWrapMode.NoWrap)
        self.details.setFont(
            QtG.QFontDatabase.systemFont(QtG.QFontDatabase.SystemFont.FixedFont)
        )

        self.splitter = QtW.QSplitter(QtC.Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.element_list)
        self.splitter.addWidget(self.details)
        self.splitter.setStretchFactor(0, 2)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([400, 200])

        layout = QtW.QVBoxLayout(self)
        layout.addWidget(QtW.QLabel("Elements"))
        layout.addWidget(self.search)
        layout.addWidget(self.status)
        layout.addWidget(self.splitter, 1)

        self.search.textChanged.connect(self._filter)
        self.element_list.currentItemChanged.connect(self._show_details)
        self.element_list.itemClicked.connect(
            lambda item, column: self._show_details(item, None)
        )
        self.element_list.itemActivated.connect(self._activate_element)

    def _activate_element(self, item, column) -> None:
        element = item.data(0, QtC.Qt.ItemDataRole.UserRole)
        if element is not None:
            self.element_activated.emit(element)

    def set_elements(self, elements: list[ElementDefinition]) -> None:
        self._elements = elements
        self.element_list.clear()
        self.details.clear()
        groups: dict[str, QtW.QTreeWidgetItem] = {}
        ordered = sorted(
            elements, key=lambda entry: (entry.plugin.name, entry.factory.name)
        )

        for element in ordered:
            if element.plugin.name not in groups:
                group = QtW.QTreeWidgetItem([element.plugin.name])
                self.element_list.addTopLevelItem(group)
                groups[element.plugin.name] = group
            item = QtW.QTreeWidgetItem([element.factory.name, element.factory.long_name])
            item.setData(0, QtC.Qt.ItemDataRole.UserRole, element)
            item.setToolTip(
                0, f"{element.factory.long_name}\n{element.factory.description}\n\n"
                "Double-click or press Enter to add to the canvas."
            )
            groups[element.plugin.name].addChild(item)

        self.search.setEnabled(True)
        self._filter(self.search.text())

    def show_error(self, message: str) -> None:
        self._elements = []
        self.element_list.clear()
        self.details.clear()
        self.search.setEnabled(False)
        self.status.setText(message)

    def _filter(self, text: str) -> None:
        terms = text.casefold().split()
        visible = 0

        for index in range(self.element_list.topLevelItemCount()):
            group = self.element_list.topLevelItem(index)
            group_visible = 0

            for child_index in range(group.childCount()):
                item = group.child(child_index)
                element = item.data(0, QtC.Qt.ItemDataRole.UserRole)
                searchable = " ".join((
                    element.plugin.name, element.factory.name, element.factory.long_name,
                    element.factory.category, element.factory.description,
                )).casefold()
                matches = all(term in searchable for term in terms)
                item.setHidden(not matches)

                if not matches and item is self.element_list.currentItem():
                    self.element_list.setCurrentItem(None)

                group_visible += matches

            group.setHidden(group_visible == 0)

            if not group_visible and group is self.element_list.currentItem():
                self.element_list.setCurrentItem(None)

            if terms and group_visible:
                group.setExpanded(True)

            visible += group_visible

        if not visible:
            self.status.setText("No matching elements.")
        else:
            self.status.setText(f"{visible} of {len(self._elements)} elements")

    def _show_details(self, current, previous) -> None:
        element = (current.data(0, QtC.Qt.ItemDataRole.UserRole)
                   if current is not None else None)
        self.show_element_details(element)

    def show_element_details(self, element: ElementDefinition | None) -> None:
        if element is None:
            self.details.clear()
            return
        
        summary = (
            f"{element.factory.long_name}\nPlugin: {element.plugin.name}\n"
            f"{element.factory.category}\n\n{element.factory.description}"
        )
        
        self.details.setPlainText(
            f"{element.factory.name}\n\n{element.raw_text or summary}"
        )
