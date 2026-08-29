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

"""Dialog for choosing which properties become design variables.

Deliberately a plain list with checkboxes rather than a property editor: the
question being asked is only "which of these should vary?", and the answer is
usually two or three of them.  Bounds are filled in automatically and edited
afterwards in the input object's own panel, so nothing here needs a spin box.
"""

__title__ = "FreeCAD FEM design variable promotion dialog"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import FreeCAD
import FreeCADGui

from PySide import QtCore
from PySide import QtGui

from femtools import explorationpromote


class PromoteDialog(QtGui.QDialog):
    """Pick promotable properties of one object."""

    def __init__(self, obj, parent=None):
        super().__init__(parent or FreeCADGui.getMainWindow())
        self.obj = obj
        self.setWindowTitle(f"Add design variables from {obj.Label}")
        self.resize(460, 340)

        layout = QtGui.QVBoxLayout(self)
        layout.addWidget(
            QtGui.QLabel(
                "Select the properties to sweep. Each becomes a variable in the "
                "analysis variable set, and the property is bound to it."
            )
        )

        self.table = QtGui.QTableWidget(self)
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Property", "Type", "Current value"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QtGui.QAbstractItemView.NoSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

        self.status = QtGui.QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QtGui.QDialogButtonBox(
            QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._populate()

    def _populate(self):
        rows = explorationpromote.promotable_properties(self.obj)
        self.table.setRowCount(len(rows))
        for row, (name, type_id, value) in enumerate(rows):
            item = QtGui.QTableWidgetItem(name)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Unchecked)
            self.table.setItem(row, 0, item)
            self.table.setItem(row, 1, self._readonly(type_id.replace("App::Property", "")))
            self.table.setItem(row, 2, self._readonly(value))
        self.table.resizeColumnsToContents()

        if not rows:
            # explain rather than present an empty table: "nothing happened" is
            # the worst possible outcome for this action
            self.status.setText(
                f"{self.obj.Label} has no numeric property that a sweep could drive. "
                "Properties that are read only, computed outputs, or already driven "
                "by an expression cannot be promoted."
            )

    @staticmethod
    def _readonly(text):
        item = QtGui.QTableWidgetItem(text)
        item.setFlags(QtCore.Qt.ItemIsEnabled)
        return item

    def selected_properties(self):
        return [
            self.table.item(row, 0).text()
            for row in range(self.table.rowCount())
            if self.table.item(row, 0).checkState() == QtCore.Qt.Checked
        ]


def run(obj, analysis, manage_transaction=True):
    """Show the dialog and promote whatever the user picked.

    Returns the list of created input objects.

    :param manage_transaction:
        Open and close a transaction around the promotions.  Pass ``False``
        when the caller already owns one - a task panel, typically.  FreeCAD
        transactions do not nest usefully: committing an inner one closes the
        outer, after which the outer abort reverts nothing.  So ownership has
        to be explicit rather than detected, especially as
        ``Document.HasPendingTransaction`` reads False immediately after
        ``openTransaction``.
    """
    dialog = PromoteDialog(obj)
    if dialog.exec_() != QtGui.QDialog.Accepted:
        return []

    props = dialog.selected_properties()
    if not props:
        return []

    doc = obj.Document
    if manage_transaction:
        doc.openTransaction("Add design variables")

    created = []
    try:
        for prop in props:
            created.append(explorationpromote.promote(obj, prop, analysis))
        if manage_transaction:
            doc.commitTransaction()
    except explorationpromote.PromotionError as exc:
        if manage_transaction:
            doc.abortTransaction()
        FreeCAD.Console.PrintError(f"{exc}\n")
        QtGui.QMessageBox.warning(FreeCADGui.getMainWindow(), "Cannot add design variable", str(exc))
        return []
    except Exception as exc:
        if manage_transaction:
            doc.abortTransaction()
        FreeCAD.Console.PrintError(f"Adding design variables failed: {exc}\n")
        raise

    doc.recompute()
    return created


##  @}
