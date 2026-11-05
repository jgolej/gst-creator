"""Local vector icons shared by toolbar actions and buttons."""

from pathlib import Path

import PySide6.QtGui as QtG


def tool_icon(name: str) -> QtG.QIcon:
    path = Path(__file__).resolve().parents[1] / "assets" / "tools"
    return QtG.QIcon(str(path / f"{name}.svg"))
