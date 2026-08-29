# ***************************************************************************
# *   Copyright (c) 2026 FreeCAD Project Association                        *
# *                                                                         *
# *   This file is part of the FreeCAD CAx development system.              *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENCE text file.                                 *
# *                                                                         *
# *   This program is distributed in the hope that it will be useful,       *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# *   You should have received a copy of the GNU Library General Public     *
# *   License along with this program; if not, write to the Free Software   *
# *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
# *   USA                                                                   *
# ***************************************************************************

"""Reading the progress stream a study run writes.

The command line front end emits one JSON object per line on stdout, and the
mesher and solver it drives write whatever they like to the same stream.  Every
consumer - the task panel, the agent bridge, a script tailing a log - has to
tell the two apart, and "not JSON" is the wrong test: FreeCAD prints bare
numbers on stdout, and ``0`` parses perfectly well, just to an int.  Doing the
classification once here, with no Qt and no FreeCAD imports, means it is
tested once and every consumer agrees.
"""

__title__ = "FreeCAD FEM design exploration progress events"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import json
import re

EVENT = "event"
TEXT = "text"
NOISE = "noise"

#: Lines FreeCAD itself writes on stdout that carry no information: bare
#: numbers, its banner, and blank lines.  Repeating them in a log the user is
#: reading to find a solver error is pure obstruction.
_NOISE = re.compile(
    r"^\s*$"
    r"|^-?\d+(\.\d+)?$"
    r"|^FreeCAD \d"
    r"|^\(C\) \d{4}"
    r"|^FreeCAD is free and open-source"
)


def parse_line(line):
    """Classify one line of a run's output.

    Returns ``(EVENT, dict)`` for a progress record, ``(TEXT, str)`` for
    anything else worth showing, and ``(NOISE, str)`` for FreeCAD's own
    chatter.  Never raises: this is called from signal handlers and reader
    threads, where an exception has nowhere useful to go.
    """
    line = (line or "").rstrip("\r\n")
    stripped = line.strip()
    if not stripped:
        return NOISE, line
    if stripped[0] == "{":
        try:
            payload = json.loads(stripped)
        except ValueError:
            payload = None
        if isinstance(payload, dict) and "event" in payload:
            return EVENT, payload
    if _NOISE.match(stripped):
        return NOISE, line
    return TEXT, line


class LineBuffer:
    """Accumulates partial reads and yields whole lines.

    A pipe delivers whatever happens to be ready, so an event can arrive in
    two halves; parsing a half would either raise or, worse, classify the
    fragment as text and lose the event.
    """

    def __init__(self):
        self._pending = ""

    def feed(self, data):
        """Add *data* and return the complete lines it finished."""
        self._pending += data
        lines = []
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            lines.append(line)
        return lines

    def flush(self):
        """Whatever is left, as a final line, once the stream has ended."""
        rest, self._pending = self._pending, ""
        return [rest] if rest.strip() else []


def format_value(value):
    """Render a value for a table cell or a status line."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return f"{value:g}"
    if isinstance(value, (list, tuple)) and value and isinstance(value[0], (int, float)):
        # inputs are stored as [value, unit]
        return f"{value[0]:g}"
    return str(value)


def describe_point(point, completed=None, total=None, labels=None):
    """One readable line about a point that just finished."""
    labels = labels or {}

    def label(name):
        return labels.get(name, name)

    inputs = ", ".join(
        f"{label(name)} {format_value(value)}" for name, value in point.get("inputs", {}).items()
    )
    prefix = f"{completed} of {total}: " if completed is not None and total else ""
    state = point.get("state", "")
    if state != "completed":
        return f"{prefix}{inputs} - {state} ({point.get('error', '')})"
    outputs = ", ".join(
        f"{label(name)} {format_value(value)}"
        for name, value in point.get("outputs", {}).items()
        if not name.startswith("_")
    )
    suffix = "  (reused)" if point.get("reused") else ""
    return f"{prefix}{inputs} -> {outputs}{suffix}"


##  @}
