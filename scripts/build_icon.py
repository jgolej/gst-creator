"""Render the SVG icon to PNG and a multi-resolution Windows ICO."""

from pathlib import Path
import struct

import PySide6.QtCore as QtC
import PySide6.QtGui as QtG
import PySide6.QtSvg as QtS


def main():
    assets = Path(__file__).resolve().parents[1] / "gst_creator" / "assets"
    renderer = QtS.QSvgRenderer(str(assets / "app-icon.svg"))
    if not renderer.isValid():
        raise ValueError("Invalid SVG icon")
    sizes = (16, 24, 32, 48, 64, 128, 256)
    images = []
    for size in (*sizes, 512):
        image = QtG.QImage(size, size, QtG.QImage.Format.Format_ARGB32)
        image.fill(QtC.Qt.GlobalColor.transparent)
        painter = QtG.QPainter(image)
        renderer.render(painter)
        painter.end()
        buffer = QtC.QBuffer()
        buffer.open(QtC.QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):
            raise OSError("Could not encode icon")
        data = bytes(buffer.data())
        if size == 512:
            (assets / "app-icon.png").write_bytes(data)
        else:
            images.append(data)
    offset = 6 + 16 * len(images)
    entries = []
    for size, data in zip(sizes, images):
        entries.append(struct.pack(
            "<BBBBHHII", size % 256, size % 256, 0, 0,
            1, 32, len(data), offset,
        ))
        offset += len(data)
    (assets / "app-icon.ico").write_bytes(
        struct.pack("<HHH", 0, 1, len(images))
        + b"".join(entries) + b"".join(images)
    )


if __name__ == "__main__":
    main()
