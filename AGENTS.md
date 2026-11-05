# Repository guidelines

This application lets users create GStreamer pipelines in a GUI by adding
elements and connecting their pads. The application validates the resulting
pipeline and can generate a GStreamer command-line pipeline.

GStreamer must be installed on the system. When the application starts, it
checks which elements are installed and lists them.

## Hard rules

- Ask me if you want to install new Python package into .venv
- Do not edit anything in the .venv directory
- For QT imports, always use shortcuts like import PySide6.QtWidgets as QtW,
  do not import individual objects.

## Formatting rules

- Keep Markdown lines to 80 characters. Exceptions are allowed when a line
  cannot be split, such as a long URL or file path.
- Use PEP8 for Python code.
- Do not use complex list, set, or dictionary comprehensions or generator
  expressions. Use explicit loops for multiple filters, nested iteration,
  or searches such as `next(...)` over a filtered generator. Simple
  comprehensions with one loop and at most one simple filter are allowed.

## Commit messages

- Use plain Git messages, short imperative subject, optionally followed by 
  a blank line and a body explaining the change.
- See changes from the staged changes in the git.
- Comment always the main changes, skip small minor changes.
