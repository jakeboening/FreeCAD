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

"""Task panel for the design point table.

Read only: these are results.  Editing an input here would suggest the row
could be recomputed in place, which it cannot - changing a design point means
running it, which is the sweep's job.
"""

__title__ = "FreeCAD FEM exploration design points task panel"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import os

import FreeCAD
import FreeCADGui

from PySide import QtCore
from PySide import QtGui

from femexploration import model as expl_model

from . import base_femtaskpanel

#: colour cues for the per-row state, so a failed run reads at a glance
_STATE_COLOUR = {
    expl_model.COMPLETED: None,
    expl_model.CANCELLED: QtGui.QColor(200, 200, 200),
}


class _TaskPanel(base_femtaskpanel._BaseTaskPanel):
    """Show the results of a study run."""

    def __init__(self, obj):
        super().__init__(obj)

        self.form = QtGui.QWidget()
        self.form.setWindowTitle("Design Points")
        layout = QtGui.QVBoxLayout(self.form)

        self.summary = QtGui.QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.table = QtGui.QTableWidget()
        self.table.setEditTriggers(QtGui.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtGui.QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)

        row = QtGui.QHBoxLayout()
        export = QtGui.QPushButton("Export CSV...")
        export.clicked.connect(self.export)
        row.addWidget(export)
        apply_point = QtGui.QPushButton("Apply selected design")
        apply_point.setToolTip(
            "Write the selected point's inputs back into the model, so the "
            "document becomes that design"
        )
        apply_point.clicked.connect(self.apply_point)
        row.addWidget(apply_point)
        row.addStretch(1)
        layout.addLayout(row)

        self.refresh()

    # ------------------------------------------------------------------- view

    def refresh(self):
        obj = self.obj
        rows = obj.Proxy.rows(obj)

        keys = list(getattr(obj, "ColumnKeys", []) or [])
        headings = list(obj.ColumnNames) or keys
        units = list(obj.ColumnUnits)
        if not keys:
            keys = headings

        columns = ["Point", "State"] + [
            f"{h} [{u}]" if u else h for h, u in zip(headings, units + [""] * len(headings))
        ]
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels(columns)
        self.table.setRowCount(len(rows))

        failed = 0
        for r, point in enumerate(rows):
            state = point.get("state", "")
            if state in expl_model.FAILED_STATES:
                failed += 1

            self.table.setItem(r, 0, self._cell(point.get("point_id", "")))
            state_cell = self._cell(state)
            if state in expl_model.FAILED_STATES:
                state_cell.setForeground(QtGui.QColor(200, 60, 60))
            self.table.setItem(r, 1, state_cell)

            inputs = point.get("inputs", {})
            outputs = point.get("outputs", {})
            for c, key in enumerate(keys):
                if key in inputs:
                    value = inputs[key]
                    value = value[0] if isinstance(value, (list, tuple)) else value
                elif key in outputs:
                    value = outputs[key]
                else:
                    value = None
                text = "" if value is None else (f"{value:g}" if isinstance(value, (int, float)) else str(value))
                self.table.setItem(r, c + 2, self._cell(text))

        self.table.resizeColumnsToContents()

        best = getattr(obj, "BestPoint", "")
        self.summary.setText(
            f"<b>{len(rows)} design points</b>"
            + (f", {failed} failed" if failed else "")
            + (f". Best: {best}" if best else "")
            + (f"<br>Run directory: {obj.RunDirectory}" if obj.RunDirectory else "")
        )

    @staticmethod
    def _cell(text):
        item = QtGui.QTableWidgetItem(str(text))
        item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
        return item

    # ---------------------------------------------------------------- actions

    def export(self):
        path, _filter = QtGui.QFileDialog.getSaveFileName(
            self.form, "Export design points", "design_points.csv", "CSV files (*.csv)"
        )
        if not path:
            return
        from femexploration import api

        study = self.obj.getParentGroup()
        try:
            api.export_csv(path, study=study, run_dir=self.obj.RunDirectory or None)
            FreeCAD.Console.PrintMessage(f"Design points written to {path}\n")
        except Exception as exc:
            QtGui.QMessageBox.warning(self.form, "Export failed", str(exc))

    def apply_point(self):
        """Make the model become the selected design."""
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        point = self.obj.Proxy.rows(self.obj)[rows[0].row()]
        inputs = point.get("inputs", {})

        study = self.obj.getParentGroup()
        analysis = study.getParentGroup() if study is not None else None
        if analysis is None:
            return

        from femexploration import spec as spec_module
        from femexploration.evaluate import writer_for
        from femtools import explorationutils

        param_set = explorationutils.get_parameter_set(analysis)
        doc = self.obj.Document
        doc.openTransaction("Apply design point")
        try:
            for var_obj in explorationutils.get_inputs(param_set):
                entry = inputs.get(var_obj.Name)
                if entry is None:
                    continue
                value = entry[0] if isinstance(entry, (list, tuple)) else entry
                target = var_obj.Target
                if target is None:
                    continue
                writer_for(var_obj.TargetTypeId)(
                    target, var_obj.TargetProperty, value, var_obj.Unit
                )
            doc.commitTransaction()
        except Exception as exc:
            doc.abortTransaction()
            QtGui.QMessageBox.warning(self.form, "Could not apply the design point", str(exc))
            return
        doc.recompute()
        FreeCAD.Console.PrintMessage(
            f"Model set to design point {point.get('point_id', '')}\n"
        )

    def getStandardButtons(self):
        return int(QtGui.QDialogButtonBox.Close)

    def accept(self):
        gui_doc = self.obj.ViewObject.Document
        gui_doc.resetEdit()
        return True

    def reject(self):
        gui_doc = self.obj.ViewObject.Document
        gui_doc.resetEdit()
        return True


##  @}
