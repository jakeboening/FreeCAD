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

"""Editable table models over the design exploration parameter objects.

The model *is* the state.  Each cell reads and writes the corresponding
property on the document object directly, so there is no "current item" widget
state to keep in sync, no signal blocking, and no re-entrancy flags - the whole
apparatus that a selector-combo-plus-single-form design needs, and the reason
that design runs to several hundred lines for what is really a grid.

Edits land inside the transaction the task panel is already running in, so
Cancel reverts them and OK keeps them without either path needing special code.
"""

__title__ = "FreeCAD FEM design exploration table models"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

from PySide import QtCore
from PySide import QtGui

from femexploration import model


# a column is (header, attribute, kind, tooltip); kind drives editing behaviour
TEXT = "text"
FLOAT = "float"
INT = "int"
BOOL = "bool"
ENUM = "enum"
READONLY = "readonly"


class _Column:
    def __init__(self, header, attr, kind, tooltip="", choices=None):
        self.header = header
        self.attr = attr
        self.kind = kind
        self.tooltip = tooltip
        self.choices = choices or []


class _ObjectTableModel(QtCore.QAbstractTableModel):
    """Base for a table whose rows are document objects and columns properties."""

    columns = []

    def __init__(self, objects=None, parent=None):
        super().__init__(parent)
        self._objects = list(objects or [])

    # ------------------------------------------------------------------ rows

    def setObjects(self, objects):
        self.beginResetModel()
        self._objects = list(objects)
        self.endResetModel()

    def objectAt(self, row):
        if 0 <= row < len(self._objects):
            return self._objects[row]
        return None

    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self._objects)

    def columnCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    # --------------------------------------------------------------- headers

    def headerData(self, section, orientation, role=QtCore.Qt.DisplayRole):
        if orientation != QtCore.Qt.Horizontal:
            return None
        if role == QtCore.Qt.DisplayRole:
            return self.columns[section].header
        if role == QtCore.Qt.ToolTipRole:
            return self.columns[section].tooltip
        return None

    # ------------------------------------------------------------------ data

    def _raw(self, obj, column):
        if column.attr == "Label":
            return obj.Label
        return getattr(obj, column.attr)

    def _display(self, obj, column):
        """Override for columns that are not a plain property read."""
        value = self._raw(obj, column)
        if column.kind == FLOAT:
            return f"{float(value):g}"
        return str(value)

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid():
            return None
        obj = self._objects[index.row()]
        column = self.columns[index.column()]

        if column.kind == BOOL:
            if role == QtCore.Qt.CheckStateRole:
                return QtCore.Qt.Checked if self._raw(obj, column) else QtCore.Qt.Unchecked
            if role == QtCore.Qt.ToolTipRole:
                return column.tooltip
            return None

        if role in (QtCore.Qt.DisplayRole, QtCore.Qt.EditRole):
            if role == QtCore.Qt.EditRole and column.kind in (FLOAT, INT):
                return self._raw(obj, column)
            return self._display(obj, column)
        if role == QtCore.Qt.ToolTipRole:
            return column.tooltip
        if role == QtCore.Qt.TextAlignmentRole and column.kind in (FLOAT, INT):
            return int(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        return None

    # ---------------------------------------------------------------- editing

    def _editable(self, obj, column):
        """Whether this cell is editable for this row.  Override as needed."""
        return column.kind != READONLY

    def flags(self, index):
        if not index.isValid():
            return QtCore.Qt.NoItemFlags
        obj = self._objects[index.row()]
        column = self.columns[index.column()]

        flags = QtCore.Qt.ItemIsSelectable
        if self._editable(obj, column):
            flags |= QtCore.Qt.ItemIsEnabled
            if column.kind == BOOL:
                flags |= QtCore.Qt.ItemIsUserCheckable
            else:
                flags |= QtCore.Qt.ItemIsEditable
        return flags

    def setData(self, index, value, role=QtCore.Qt.EditRole):
        if not index.isValid():
            return False
        obj = self._objects[index.row()]
        column = self.columns[index.column()]
        if not self._editable(obj, column):
            return False

        try:
            if column.kind == BOOL and role == QtCore.Qt.CheckStateRole:
                setattr(obj, column.attr, QtCore.Qt.CheckState(value) == QtCore.Qt.Checked)
            elif role != QtCore.Qt.EditRole:
                return False
            elif column.kind == FLOAT:
                setattr(obj, column.attr, float(value))
            elif column.kind == INT:
                setattr(obj, column.attr, int(value))
            elif column.attr == "Label":
                obj.Label = str(value)
            else:
                setattr(obj, column.attr, str(value))
        except (ValueError, TypeError):
            # a malformed entry must not corrupt the object; keep the old value
            return False

        obj.Document.recompute()
        self.dataChanged.emit(index, index)
        # a change may enable or disable other cells in the same row
        self.dataChanged.emit(index.sibling(index.row(), 0),
                              index.sibling(index.row(), len(self.columns) - 1))
        return True


class InputTableModel(_ObjectTableModel):
    """Design variables: what varies, over what range."""

    columns = [
        _Column("Name", "Label", TEXT, "Name shown in the tree"),
        _Column("Drives", "", READONLY, "Property this variable writes to"),
        _Column("Unit", "Unit", READONLY, "All bounds below are in this unit"),
        _Column("Type", "VarType", ENUM, "Kind of design variable", list(model.VAR_TYPES)),
        _Column("Lower", "Lower", FLOAT, "Lower bound, in the row's unit"),
        _Column("Upper", "Upper", FLOAT, "Upper bound, in the row's unit"),
        _Column("Nominal", "Nominal", FLOAT, "Baseline value, used when the variable is inactive"),
        _Column("Scale", "Scale", ENUM, "Spacing of the sweep grid", list(model.SCALES)),
        _Column("Levels", "LevelCount", INT, "Number of values a sweep will take"),
        _Column(
            "Re-mesh",
            "GeometryAffecting",
            BOOL,
            "This variable changes the geometry, so every design point must be re-meshed",
        ),
        _Column("Active", "Active", BOOL, "Vary this parameter; when off it is held at Nominal"),
    ]

    def _display(self, obj, column):
        if column.header == "Drives":
            if obj.Target is None:
                return "(unbound)"
            return f"{obj.Target.Label}.{obj.TargetProperty}"
        return super()._display(obj, column)

    def _editable(self, obj, column):
        if column.kind == READONLY:
            return False
        # bounds are meaningless for a variable enumerated by explicit values
        if obj.VarType in (model.DISCRETE, model.ENUMERATION) and column.header in (
            "Lower",
            "Upper",
            "Scale",
            "Levels",
        ):
            return False
        return True


class OutputTableModel(_ObjectTableModel):
    """Responses: what gets measured after each solve."""

    columns = [
        _Column("Name", "Label", TEXT, "Name shown in the tree"),
        _Column("Source", "Source", ENUM, "Where the value comes from",
                list(model.RESPONSE_SOURCES)),
        _Column("Quantity", "Quantity", ENUM, "Result quantity to read",
                list(model.RESULT_QUANTITIES)),
        _Column("Aggregate", "Aggregate", ENUM, "Which end of the quantity's range",
                list(model.AGGREGATES)),
        _Column("Expression", "Expression", TEXT, "Expression evaluated after the solve"),
        _Column("Unit", "Unit", TEXT, "Unit the value is reported in"),
        _Column("Step", "LoadStep", INT, "Which solution stage to read; -1 is the last"),
    ]

    def _editable(self, obj, column):
        # only show as editable the fields the chosen source actually uses
        if column.header in ("Quantity", "Aggregate"):
            return obj.Source == model.RESULT_STAT
        if column.header == "Expression":
            return obj.Source == model.EXPRESSION
        return column.kind != READONLY

    def _display(self, obj, column):
        if not self._editable(obj, column) and column.header in (
            "Quantity",
            "Aggregate",
            "Expression",
        ):
            return ""
        return super()._display(obj, column)


LINK = "link"


class _LinkedTableModel(_ObjectTableModel):
    """Rows that point at an output parameter through a link column."""

    def __init__(self, objects=None, outputs=None, parent=None):
        super().__init__(objects, parent)
        self._outputs = list(outputs or [])

    def setOutputs(self, outputs):
        self._outputs = list(outputs)
        self.layoutChanged.emit()

    def output_labels(self):
        return [o.Label for o in self._outputs]

    def _display(self, obj, column):
        if column.kind == LINK:
            target = getattr(obj, column.attr)
            return target.Label if target is not None else "(choose an output)"
        return super()._display(obj, column)

    def setData(self, index, value, role=QtCore.Qt.EditRole):
        if not index.isValid():
            return False
        column = self.columns[index.column()]
        if column.kind == LINK and role == QtCore.Qt.EditRole:
            obj = self._objects[index.row()]
            for output in self._outputs:
                if output.Label == str(value) or output.Name == str(value):
                    setattr(obj, column.attr, output)
                    obj.Document.recompute()
                    self.dataChanged.emit(index, index)
                    return True
            return False
        return super().setData(index, value, role)


class ObjectiveTableModel(_LinkedTableModel):
    """Objectives: what to drive, which way."""

    columns = [
        _Column("Name", "Label", TEXT, "Name shown in the tree"),
        _Column("Output", "Output", LINK, "Output parameter this objective drives"),
        _Column("Direction", "Direction", ENUM, "Minimize, Maximize, or reach the target",
                list(model.DIRECTIONS)),
        _Column("Target", "TargetValue", FLOAT, "Value to reach when Direction is Target"),
        _Column("Weight", "Weight", FLOAT, "Relative weight when objectives are combined"),
        _Column("Active", "Active", BOOL, "Optimize this objective"),
    ]

    def _editable(self, obj, column):
        if column.header == "Target":
            return obj.Direction == model.TARGET
        return column.kind != READONLY

    def _display(self, obj, column):
        if column.header == "Target" and obj.Direction != model.TARGET:
            return ""
        return super()._display(obj, column)


class ConstraintTableModel(_LinkedTableModel):
    """Constraints: a bound on a response."""

    columns = [
        _Column("Name", "Label", TEXT, "Name shown in the tree"),
        _Column("Output", "Output", LINK, "Output parameter this constraint bounds"),
        _Column("Operator", "Operator", ENUM, "How the output relates to the limit",
                list(model.OPERATORS)),
        _Column("Limit", "Limit", FLOAT, "Bound, in the output's unit"),
        _Column("Tolerance", "Tolerance", FLOAT, "Slack allowed before it counts as violated"),
        _Column("Active", "Active", BOOL, "Enforce this constraint"),
    ]


class EnumDelegate(QtGui.QStyledItemDelegate):
    """Combo box editor for the enumeration columns."""

    def __init__(self, model_, parent=None):
        super().__init__(parent)
        self._model = model_

    def createEditor(self, parent, option, index):
        column = self._model.columns[index.column()]
        if column.kind == LINK and hasattr(self._model, "output_labels"):
            combo = QtGui.QComboBox(parent)
            combo.addItems(self._model.output_labels())
            return combo
        if column.kind != ENUM:
            return super().createEditor(parent, option, index)
        combo = QtGui.QComboBox(parent)
        combo.addItems(column.choices)
        return combo

    def setEditorData(self, editor, index):
        if isinstance(editor, QtGui.QComboBox):
            editor.setCurrentText(str(index.data(QtCore.Qt.DisplayRole)))
        else:
            super().setEditorData(editor, index)

    def setModelData(self, editor, model_, index):
        if isinstance(editor, QtGui.QComboBox):
            model_.setData(index, editor.currentText(), QtCore.Qt.EditRole)
        else:
            super().setModelData(editor, model_, index)


def make_view(table_model, parent=None):
    """A table view wired up for one of the models above."""
    view = QtGui.QTableView(parent)
    view.setModel(table_model)
    view.setItemDelegate(EnumDelegate(table_model, view))
    view.setSelectionBehavior(QtGui.QAbstractItemView.SelectRows)
    view.setEditTriggers(
        QtGui.QAbstractItemView.DoubleClicked
        | QtGui.QAbstractItemView.SelectedClicked
        | QtGui.QAbstractItemView.EditKeyPressed
        | QtGui.QAbstractItemView.AnyKeyPressed
    )
    view.verticalHeader().setVisible(False)
    view.horizontalHeader().setStretchLastSection(False)
    view.resizeColumnsToContents()
    return view


##  @}
