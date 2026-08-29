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

"""The design point table.

A ``QAbstractTableModel`` over the rows a study produced, and a view with the
actions a table of designs needs.  The model *is* the state: there is no
"currently selected point" kept anywhere else, no signal blocking and no
re-entrancy flags, because every widget reads the same model and Qt keeps
them consistent.

Columns are the design point's state, whether its full output is kept, its
identifier, then the inputs, the responses, and - once a study has them -
objectives and constraint violations.  Editing an input marks the row out of
date rather than re-running it: running is the study's job, and the user asks
for it explicitly with Update.
"""

__title__ = "FreeCAD FEM exploration design point table"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import csv
import os

from PySide import QtCore
from PySide import QtGui

from femexploration import model as expl_model
from femexploration.events import format_value

COL_STATE = 0
COL_KEEP = 1
COL_ID = 2
FIXED_COLUMNS = ("State", "Keep", "Point")

ROLE_INPUT = "input"
ROLE_RESPONSE = "response"
ROLE_OBJECTIVE = "objective"
ROLE_VIOLATION = "violation"

#: what each state looks like, as an RGB triple for the row icon
STATE_COLOURS = {
    expl_model.COMPLETED: (76, 175, 80),
    expl_model.PENDING: (158, 158, 158),
    expl_model.RUNNING: (33, 150, 243),
    expl_model.OUT_OF_DATE: (255, 193, 7),
    expl_model.CANCELLED: (158, 158, 158),
}
for _state in expl_model.FAILED_STATES:
    STATE_COLOURS[_state] = (211, 47, 47)

#: filter modes the view offers, in display order
FILTERS = ("All", "Completed", "Failed", "Feasible", "Pareto", "Out of date")

_icons = {}


def state_icon(state):
    """A coloured dot for a state, or None when there is no GUI to paint it."""
    if QtGui.QGuiApplication.instance() is None:
        return None
    if state not in _icons:
        rgb = STATE_COLOURS.get(state, (120, 120, 120))
        pixmap = QtGui.QPixmap(12, 12)
        pixmap.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setBrush(QtGui.QColor(*rgb))
        painter.setPen(QtCore.Qt.NoPen)
        painter.drawEllipse(1, 1, 10, 10)
        painter.end()
        _icons[state] = QtGui.QIcon(pixmap)
    return _icons[state]


class Column:
    """One data column: where its value lives in a row, and how it is shown."""

    __slots__ = ("key", "label", "unit", "role")

    def __init__(self, key, label="", unit="", role=ROLE_INPUT):
        self.key = key
        self.label = label or key
        self.unit = unit
        self.role = role

    @property
    def heading(self):
        return f"{self.label} [{self.unit}]" if self.unit else self.label


def columns_from_spec(spec):
    """The columns a study specification implies."""
    cols = [Column(v.name, v.label, v.unit, ROLE_INPUT) for v in spec.active_variables]
    cols += [Column(r.name, r.label, r.unit, ROLE_RESPONSE) for r in spec.responses]
    cols += [Column(o.name, o.name, "", ROLE_OBJECTIVE) for o in spec.active_objectives]
    cols += [Column(c.name, c.name, "", ROLE_VIOLATION) for c in spec.active_constraints]
    return cols


def columns_from_table(obj):
    """The columns a design point document object recorded."""
    keys = list(getattr(obj, "ColumnKeys", []) or [])
    names = list(getattr(obj, "ColumnNames", []) or [])
    units = list(getattr(obj, "ColumnUnits", []) or [])
    roles = list(getattr(obj, "ColumnRoles", []) or [])
    if not keys:
        keys = list(names)
    cols = []
    for i, key in enumerate(keys):
        cols.append(
            Column(
                key,
                names[i] if i < len(names) else key,
                units[i] if i < len(units) else "",
                roles[i] if i < len(roles) else ROLE_INPUT,
            )
        )
    return cols


def _blank_row():
    return {
        "point_id": "",
        "state": expl_model.PENDING,
        "inputs": {},
        "outputs": {},
        "objectives": {},
        "violations": {},
        "error": "",
        "retained": False,
        "duration_s": 0.0,
        "reused": False,
    }


def _normalise(row):
    """A row dict with every key the model reads, copied so edits stay local."""
    out = _blank_row()
    for key in out:
        if key in row and row[key] is not None:
            value = row[key]
            out[key] = dict(value) if isinstance(value, dict) else value
    # progress events use "id" for what the journal calls "point_id"
    if not out["point_id"] and row.get("id"):
        out["point_id"] = row["id"]
    return out


def _scalar(value):
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def _unit_of(value):
    if isinstance(value, (list, tuple)) and len(value) > 1:
        return value[1]
    return ""


class DesignPointModel(QtCore.QAbstractTableModel):
    """Rows of design points; the single source of truth for every view."""

    #: raw numeric value for sorting and charts
    ValueRole = QtCore.Qt.UserRole + 1
    #: the row's state string
    StateRole = QtCore.Qt.UserRole + 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []
        self._columns = []
        self._index = {}
        self._new_count = 0

    # ---------------------------------------------------------------- content

    def set_columns(self, columns):
        self.beginResetModel()
        self._columns = list(columns)
        self.endResetModel()

    def columns(self):
        return list(self._columns)

    def set_rows(self, rows):
        self.beginResetModel()
        self._rows = [_normalise(r) for r in rows]
        self._reindex()
        self.endResetModel()

    def rows(self):
        return list(self._rows)

    def row(self, r):
        return self._rows[r]

    def _reindex(self):
        self._index = {}
        for i, row in enumerate(self._rows):
            if row["point_id"]:
                self._index[row["point_id"]] = i

    def find(self, point_id):
        return self._index.get(point_id)

    def upsert(self, summary):
        """Add or update the row for a progress event's point.  Returns its row."""
        row = _normalise(summary)
        if not row["point_id"]:
            return None
        existing = self._index.get(row["point_id"])
        if existing is None:
            # a point typed into the table and now being run: the engine's
            # id arrives with its first event, so match on inputs instead
            existing = self._match_unrun(row["inputs"])
        if existing is None:
            self.beginInsertRows(QtCore.QModelIndex(), len(self._rows), len(self._rows))
            self._rows.append(row)
            self._index[row["point_id"]] = len(self._rows) - 1
            self.endInsertRows()
            return len(self._rows) - 1
        kept = self._rows[existing]["retained"]
        row["retained"] = kept or row["retained"]
        self._rows[existing] = row
        self._index[row["point_id"]] = existing
        self.dataChanged.emit(self.index(existing, 0), self.index(existing, self.columnCount() - 1))
        return existing

    def _match_unrun(self, inputs):
        wanted = {k: _scalar(v) for k, v in inputs.items()}
        for i, row in enumerate(self._rows):
            if row["point_id"] and row["point_id"] in self._index and row["state"] not in (
                expl_model.PENDING,
                expl_model.OUT_OF_DATE,
            ):
                continue
            if row["point_id"].startswith("new-") or row["state"] in (
                expl_model.PENDING,
                expl_model.OUT_OF_DATE,
            ):
                have = {k: _scalar(v) for k, v in row["inputs"].items()}
                if all(abs(float(have.get(k, 0.0)) - float(v)) <= 1e-9 * max(1.0, abs(float(v)))
                       for k, v in wanted.items()):
                    return i
        return None

    def add_row(self, inputs, after=None):
        """Append a pending row with *inputs* ``{key: [value, unit]}``."""
        self._new_count += 1
        row = _blank_row()
        row["point_id"] = f"new-{self._new_count}"
        row["inputs"] = {k: list(v) if isinstance(v, (list, tuple)) else [v, ""] for k, v in inputs.items()}
        position = len(self._rows) if after is None else after + 1
        self.beginInsertRows(QtCore.QModelIndex(), position, position)
        self._rows.insert(position, row)
        self._reindex()
        self.endInsertRows()
        return position

    def duplicate_row(self, r):
        source = self._rows[r]
        return self.add_row({k: list(v) if isinstance(v, (list, tuple)) else [v, ""]
                             for k, v in source["inputs"].items()}, after=r)

    def remove_rows(self, rows):
        for r in sorted(set(rows), reverse=True):
            self.beginRemoveRows(QtCore.QModelIndex(), r, r)
            del self._rows[r]
            self.endRemoveRows()
        self._reindex()

    def clear(self):
        self.set_rows([])

    # --------------------------------------------------------------- queries

    def input_columns(self):
        return [c for c in self._columns if c.role == ROLE_INPUT]

    def inputs_of(self, r):
        """Plain ``{key: value}`` inputs of a row, for running it."""
        return {k: _scalar(v) for k, v in self._rows[r]["inputs"].items()}

    def is_feasible(self, r):
        row = self._rows[r]
        if row["state"] != expl_model.COMPLETED:
            return False
        return all(float(v or 0.0) <= 0.0 for v in row["violations"].values())

    def is_failed(self, r):
        return self._rows[r]["state"] in expl_model.FAILED_STATES

    def is_stale(self, r):
        return self._rows[r]["state"] in (expl_model.PENDING, expl_model.OUT_OF_DATE)

    def pareto_rows(self):
        """Rows that are feasible and not dominated on their objectives."""
        scored = [
            (i, [float(v) for _k, v in sorted(self._rows[i]["objectives"].items())])
            for i in range(len(self._rows))
            if self.is_feasible(i) and self._rows[i]["objectives"]
        ]
        front = set()
        for i, fi in scored:
            dominated = False
            for j, fj in scored:
                if i == j or len(fi) != len(fj):
                    continue
                if all(b <= a for a, b in zip(fi, fj)) and any(b < a for a, b in zip(fi, fj)):
                    dominated = True
                    break
            if not dominated:
                front.add(i)
        return front

    def stale_points(self):
        """Inputs of every pending or out-of-date row, ready to run."""
        return [self.inputs_of(i) for i in range(len(self._rows)) if self.is_stale(i)]

    # ------------------------------------------------------- Qt model methods

    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(FIXED_COLUMNS) + len(self._columns)

    def headerData(self, section, orientation, role=QtCore.Qt.DisplayRole):
        if orientation != QtCore.Qt.Horizontal:
            return None
        if role == QtCore.Qt.DisplayRole:
            if section < len(FIXED_COLUMNS):
                return FIXED_COLUMNS[section]
            return self._columns[section - len(FIXED_COLUMNS)].heading
        if role == QtCore.Qt.ToolTipRole and section >= len(FIXED_COLUMNS):
            column = self._columns[section - len(FIXED_COLUMNS)]
            return f"{column.role}: {column.key}"
        return None

    def _column(self, section):
        if section < len(FIXED_COLUMNS):
            return None
        return self._columns[section - len(FIXED_COLUMNS)]

    def _raw(self, row, column):
        if column.role == ROLE_INPUT:
            return _scalar(row["inputs"].get(column.key))
        if column.role == ROLE_RESPONSE:
            return row["outputs"].get(column.key)
        if column.role == ROLE_OBJECTIVE:
            return row["objectives"].get(column.key)
        if column.role == ROLE_VIOLATION:
            return row["violations"].get(column.key)
        return None

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        section = index.column()

        if role == self.StateRole:
            return row["state"]

        if section == COL_STATE:
            if role == QtCore.Qt.DisplayRole:
                return row["state"] + (" (reused)" if row.get("reused") else "")
            if role == QtCore.Qt.DecorationRole:
                return state_icon(row["state"])
            if role == QtCore.Qt.ToolTipRole:
                return row["error"] or row["state"]
            if role == QtCore.Qt.ForegroundRole and row["state"] in expl_model.FAILED_STATES:
                return QtGui.QColor(200, 60, 60)
            return None

        if section == COL_KEEP:
            if role == QtCore.Qt.CheckStateRole:
                return QtCore.Qt.Checked if row["retained"] else QtCore.Qt.Unchecked
            if role == QtCore.Qt.ToolTipRole:
                return "Keep this point's model and solver output on disk"
            return None

        if section == COL_ID:
            if role == QtCore.Qt.DisplayRole:
                return row["point_id"]
            if role == QtCore.Qt.FontRole:
                font = QtGui.QFont()
                font.setFamily("monospace")
                return font
            return None

        column = self._column(section)
        value = self._raw(row, column)
        if role == self.ValueRole:
            return value
        if role in (QtCore.Qt.DisplayRole, QtCore.Qt.EditRole):
            return format_value(value) if role == QtCore.Qt.DisplayRole else value
        if role == QtCore.Qt.TextAlignmentRole:
            return int(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        if role == QtCore.Qt.BackgroundRole:
            if column.role == ROLE_VIOLATION and value is not None and float(value) > 0.0:
                return QtGui.QColor(255, 205, 210)
            if column.role == ROLE_INPUT and row["state"] == expl_model.OUT_OF_DATE:
                return QtGui.QColor(255, 243, 205)
        if role == QtCore.Qt.ToolTipRole and column.role == ROLE_VIOLATION and value:
            return f"constraint violated by {format_value(value)}"
        return None

    def flags(self, index):
        base = QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
        if not index.isValid():
            return base
        section = index.column()
        if section == COL_KEEP:
            return base | QtCore.Qt.ItemIsUserCheckable
        column = self._column(section)
        if column is not None and column.role == ROLE_INPUT:
            return base | QtCore.Qt.ItemIsEditable
        return base

    def setData(self, index, value, role=QtCore.Qt.EditRole):
        if not index.isValid():
            return False
        row = self._rows[index.row()]
        section = index.column()

        if section == COL_KEEP and role == QtCore.Qt.CheckStateRole:
            # PySide6 hands over a Qt.CheckState enum, PySide2 an int, and a
            # script may pass a bool; accept all three
            numeric = getattr(value, "value", value)
            row["retained"] = value is True or value == QtCore.Qt.Checked or numeric == 2
            self.dataChanged.emit(index, index)
            return True

        column = self._column(section)
        if column is None or column.role != ROLE_INPUT or role != QtCore.Qt.EditRole:
            return False
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        current = row["inputs"].get(column.key)
        if _scalar(current) is not None and abs(number - float(_scalar(current))) < 1e-15:
            return False
        row["inputs"][column.key] = [number, _unit_of(current)]
        # an edited row is no longer the point that was solved; say so
        # rather than showing outputs that belong to different inputs
        if row["state"] != expl_model.PENDING:
            row["state"] = expl_model.OUT_OF_DATE
        self.dataChanged.emit(self.index(index.row(), 0), self.index(index.row(), self.columnCount() - 1))
        return True

    # ------------------------------------------------------------------- csv

    def export_csv(self, path):
        with open(path, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["point_id", "state"] + [c.heading for c in self._columns])
            for row in self._rows:
                writer.writerow(
                    [row["point_id"], row["state"]]
                    + ["" if self._raw(row, c) is None else self._raw(row, c) for c in self._columns]
                )

    def import_csv(self, path):
        """Add a pending row per CSV line.  Returns how many were added.

        Columns are matched by heading, label or key so a file exported from
        here, or hand written with the variable names, both work.
        """
        inputs = self.input_columns()
        lookup = {}
        for column in inputs:
            for name in (column.heading, column.label, column.key):
                lookup[name.strip().lower()] = column
        added = 0
        with open(path, newline="") as fh:
            reader = csv.DictReader(fh)
            for record in reader:
                values = {}
                for heading, text in record.items():
                    column = lookup.get((heading or "").strip().lower())
                    if column is None or text in (None, ""):
                        continue
                    try:
                        values[column.key] = [float(text), column.unit]
                    except ValueError:
                        continue
                if values:
                    self.add_row(values)
                    added += 1
        return added


class DesignPointFilter(QtCore.QSortFilterProxyModel):
    """Which rows to show, and numeric sorting."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode = "All"
        self._pareto = set()

    def set_mode(self, mode):
        self._mode = mode
        self.invalidateFilter()

    def mode(self):
        return self._mode

    def invalidateFilter(self):
        source = self.sourceModel()
        if self._mode == "Pareto" and source is not None:
            self._pareto = source.pareto_rows()
        super().invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        source = self.sourceModel()
        if source is None or self._mode == "All":
            return True
        if self._mode == "Completed":
            return source.row(row)["state"] == expl_model.COMPLETED
        if self._mode == "Failed":
            return source.is_failed(row)
        if self._mode == "Feasible":
            return source.is_feasible(row)
        if self._mode == "Pareto":
            return row in self._pareto
        if self._mode == "Out of date":
            return source.is_stale(row)
        return True

    def lessThan(self, left, right):
        source = self.sourceModel()
        a = source.data(left, DesignPointModel.ValueRole)
        b = source.data(right, DesignPointModel.ValueRole)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return a < b
        return str(source.data(left) or "") < str(source.data(right) or "")


class DesignPointView(QtGui.QWidget):
    """The table plus its toolbar.

    The widget owns the model and proxy; the panel using it connects the two
    signals that need the document: ``runRequested`` with the inputs to
    evaluate, and ``currentRequested`` with a row to write into the model.
    """

    runRequested = QtCore.Signal(list)
    currentRequested = QtCore.Signal(dict)
    clearRequested = QtCore.Signal()

    def __init__(self, parent=None, run_dir=None, show_run_actions=True):
        super().__init__(parent)
        self.setWindowTitle("Design Points")
        self.model = DesignPointModel(self)
        self.proxy = DesignPointFilter(self)
        self.proxy.setSourceModel(self.model)
        self.run_dir = run_dir
        self._defaults = {}

        layout = QtGui.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        bar = QtGui.QHBoxLayout()
        self.summary = QtGui.QLabel()
        bar.addWidget(self.summary, 1)
        bar.addWidget(QtGui.QLabel("Show"))
        self.filter = QtGui.QComboBox()
        self.filter.addItems(list(FILTERS))
        self.filter.currentTextChanged.connect(self.proxy.set_mode)
        bar.addWidget(self.filter)
        layout.addLayout(bar)

        self.table = QtGui.QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QtGui.QAbstractItemView.SelectRows)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(
            QtGui.QAbstractItemView.DoubleClicked | QtGui.QAbstractItemView.EditKeyPressed
        )
        self.table.setMinimumHeight(140)
        layout.addWidget(self.table)

        actions = QtGui.QHBoxLayout()
        self.buttons = {}

        def button(name, text, slot, tip):
            b = QtGui.QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            actions.addWidget(b)
            self.buttons[name] = b
            return b

        button("export", "Export CSV", self.export_csv, "Write the table to a CSV file")
        button("import", "Import CSV", self.import_csv, "Add design points from a CSV file, to be run")
        if show_run_actions:
            button("add", "Add", self.add_point, "Add a design point at the nominal values, to edit and run")
            button("duplicate", "Duplicate", self.duplicate_point, "Copy the selected design point")
            button("delete", "Delete", self.delete_points, "Remove the selected rows from the table")
            button("update", "Update", self.update_points,
                   "Run every pending or out-of-date design point, or the selected ones")
        button("current", "Set as current", self.set_current,
               "Write the selected point's inputs into the model, so the document becomes that design")
        button("open", "Open retained", self.open_retained,
               "Open the saved model of the selected point, if its output was kept")
        actions.addStretch(1)
        layout.addLayout(actions)

        self.model.modelReset.connect(self._refresh_summary)
        self.model.rowsInserted.connect(self._refresh_summary)
        self.model.rowsRemoved.connect(self._refresh_summary)
        self.model.dataChanged.connect(self._refresh_summary)
        self._refresh_summary()

    # ----------------------------------------------------------------- data

    def set_columns(self, columns):
        self.model.set_columns(columns)
        self.table.resizeColumnsToContents()

    def set_rows(self, rows):
        self.model.set_rows(rows)
        self.table.resizeColumnsToContents()

    def set_defaults(self, defaults):
        """Inputs a new row starts from: ``{key: [nominal, unit]}``."""
        self._defaults = dict(defaults)

    def upsert(self, summary):
        r = self.model.upsert(summary)
        self.table.resizeColumnsToContents()
        self.table.scrollToBottom()
        return r

    def selected_rows(self):
        """Source row indices of the selection, in table order."""
        selection = self.table.selectionModel()
        if selection is None:
            return []
        rows = []
        for index in selection.selectedRows():
            rows.append(self.proxy.mapToSource(index).row())
        return rows

    def _refresh_summary(self, *args):
        rows = self.model.rows()
        failed = sum(1 for i in range(len(rows)) if self.model.is_failed(i))
        stale = sum(1 for i in range(len(rows)) if self.model.is_stale(i))
        text = f"<b>{len(rows)} design points</b>"
        if failed:
            text += f", {failed} failed"
        if stale:
            text += f", {stale} to run"
        self.summary.setText(text)

    # -------------------------------------------------------------- actions

    def export_csv(self):
        path, _filter = QtGui.QFileDialog.getSaveFileName(
            self, "Export design points", "design_points.csv", "CSV files (*.csv)"
        )
        if path:
            try:
                self.model.export_csv(path)
            except OSError as exc:
                QtGui.QMessageBox.warning(self, "Export failed", str(exc))

    def import_csv(self):
        path, _filter = QtGui.QFileDialog.getOpenFileName(
            self, "Import design points", "", "CSV files (*.csv)"
        )
        if path:
            try:
                added = self.model.import_csv(path)
            except OSError as exc:
                QtGui.QMessageBox.warning(self, "Import failed", str(exc))
                return
            self.table.resizeColumnsToContents()
            if not added:
                QtGui.QMessageBox.information(
                    self,
                    "Nothing imported",
                    "No column of the file matched a design variable of this study.",
                )

    def add_point(self):
        r = self.model.add_row(self._defaults)
        index = self.proxy.mapFromSource(self.model.index(r, len(FIXED_COLUMNS)))
        self.table.setCurrentIndex(index)
        self.table.edit(index)

    def duplicate_point(self):
        for r in sorted(self.selected_rows()):
            self.model.duplicate_row(r)

    def delete_points(self):
        rows = self.selected_rows()
        if rows:
            self.model.remove_rows(rows)

    def update_points(self):
        selected = [r for r in self.selected_rows()]
        if selected:
            points = [self.model.inputs_of(r) for r in selected]
        else:
            points = self.model.stale_points()
        if not points:
            QtGui.QMessageBox.information(
                self, "Nothing to run", "Select rows to re-run, or add or edit a design point first."
            )
            return
        self.runRequested.emit(points)

    def set_current(self):
        rows = self.selected_rows()
        if rows:
            self.currentRequested.emit(self.model.row(rows[0]))

    def open_retained(self):
        rows = self.selected_rows()
        if not rows or not self.run_dir:
            return
        from femexploration.store import PointStore

        row = self.model.row(rows[0])
        model_path = os.path.join(PointStore(self.run_dir).point_dir(row["point_id"]), "model.FCStd")
        if not os.path.exists(model_path):
            QtGui.QMessageBox.information(
                self,
                "Not retained",
                "This point's model was not kept.  Tick Keep, or set the study's "
                "Retain property, and run it again.",
            )
            return
        import FreeCAD

        FreeCAD.openDocument(model_path)


##  @}
