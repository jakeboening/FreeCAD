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

"""Task panel for the design exploration parameter set.

The inputs and outputs are editable grids, not a read-only summary: this is
where an engineer expects to set bounds, so this is where they are set.  Each
cell writes straight through to the underlying document object.  The panel
opens its own transaction on entry, because opening the task dialog does not
start one, so OK keeps the edits and Cancel reverts them.
"""

__title__ = "FreeCAD FEM exploration parameter set task panel"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import FreeCAD
import FreeCADGui

from PySide import QtGui

from femguiutils import exploration_table_model as tablemodel
from femtools import explorationutils

from . import base_femtaskpanel


class _TaskPanel(base_femtaskpanel._BaseTaskPanel):
    """Edit the design variables and responses of one analysis."""

    def __init__(self, obj):
        super().__init__(obj)

        # setEdit does not open a transaction, so without this nothing would
        # undo the edits made here.  The base class commits it on OK and
        # aborts it on Cancel.
        obj.Document.openTransaction("Edit parameter set")

        self.form = QtGui.QWidget()
        self.form.setWindowTitle("Parameter Set")
        layout = QtGui.QVBoxLayout(self.form)

        # ---------------------------------------------------------- inputs
        layout.addWidget(QtGui.QLabel("<b>Input parameters</b> (design variables)"))
        self.input_model = tablemodel.InputTableModel()
        self.input_view = tablemodel.make_view(self.input_model)
        self.input_view.setMinimumHeight(120)
        layout.addWidget(self.input_view)

        row = QtGui.QHBoxLayout()
        add = QtGui.QPushButton("Add design variable...")
        add.setToolTip("Promote a property of the selected object to a design variable")
        add.clicked.connect(self.add_variable)
        row.addWidget(add)
        self.remove_button = QtGui.QPushButton("Remove")
        self.remove_button.setToolTip("Delete the selected variable and unbind its property")
        self.remove_button.clicked.connect(self.remove_variable)
        row.addWidget(self.remove_button)
        row.addStretch(1)
        layout.addLayout(row)

        # --------------------------------------------------------- outputs
        layout.addWidget(QtGui.QLabel("<b>Output parameters</b> (responses)"))
        self.output_model = tablemodel.OutputTableModel()
        self.output_view = tablemodel.make_view(self.output_model)
        self.output_view.setMinimumHeight(90)
        layout.addWidget(self.output_view)

        row = QtGui.QHBoxLayout()
        add_out = QtGui.QPushButton("Add output")
        add_out.setToolTip("Add a response measured after each solve")
        add_out.clicked.connect(self.add_output)
        row.addWidget(add_out)
        self.remove_out_button = QtGui.QPushButton("Remove")
        self.remove_out_button.clicked.connect(self.remove_output)
        row.addWidget(self.remove_out_button)
        row.addStretch(1)
        layout.addLayout(row)

        self.hint = QtGui.QLabel()
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        self.input_model.dataChanged.connect(self.update_hint)
        self.refresh()

    # ------------------------------------------------------------------ view

    def refresh(self):
        self.input_model.setObjects(explorationutils.get_inputs(self.obj))
        self.output_model.setObjects(explorationutils.get_outputs(self.obj))
        self.input_view.resizeColumnsToContents()
        self.output_view.resizeColumnsToContents()
        self.update_hint()

    def update_hint(self, *args):
        inputs = explorationutils.get_inputs(self.obj)
        active = [o for o in inputs if o.Active]

        if not inputs:
            self.hint.setText(
                "No design variables yet. Select an object in the tree, then press "
                "'Add design variable' to choose which of its properties to sweep."
            )
            return

        points = 1
        for obj in active:
            points *= max(1, len(self._grid(obj)))

        remesh = any(o.GeometryAffecting for o in active)
        self.hint.setText(
            f"{len(active)} of {len(inputs)} variables active. "
            f"A full factorial sweep would be {points} design point"
            f"{'' if points == 1 else 's'}. "
            + (
                "At least one active variable changes the geometry, so every point "
                "is re-meshed."
                if remesh
                else "No active variable changes the geometry, so the mesh is built "
                "once and reused."
            )
        )

    @staticmethod
    def _grid(obj):
        """The values one variable would take, via the shared schema logic."""
        from femexploration.model import SchemaError
        from femexploration.model import Variable

        probe = Variable(
            name=obj.Name,
            unit=obj.Unit,
            var_type=obj.VarType,
            lower=obj.Lower,
            upper=obj.Upper,
            scale=obj.Scale,
            level_count=int(obj.LevelCount),
            levels=list(obj.Levels),
            enum_levels=list(obj.EnumLevels),
        )
        try:
            probe.validate()
            return probe.grid()
        except SchemaError:
            return []

    # ---------------------------------------------------------------- actions

    def add_variable(self):
        sel = FreeCADGui.Selection.getSelection()
        if len(sel) != 1:
            QtGui.QMessageBox.information(
                self.form,
                "Select an object",
                "Select exactly one object in the tree or the 3D view, then press "
                "this button to choose which of its properties to sweep.",
            )
            return

        from femguiutils import exploration_promote

        # this panel owns the transaction; run() must not commit it early
        created = exploration_promote.run(
            sel[0], self.obj.getParentGroup(), manage_transaction=False
        )
        if created:
            FreeCAD.Console.PrintMessage(f"Added {len(created)} design variable(s)\n")
        self.refresh()

    def remove_variable(self):
        obj = self._selected(self.input_view, self.input_model)
        if obj is None:
            return
        # unbind the driven property first, or it stays frozen at its last value
        target, prop = obj.Target, obj.TargetProperty
        doc = obj.Document
        if target is not None:
            for owner in doc.Objects:
                for path, _expr in owner.ExpressionEngine:
                    if f"{target.Name}.{prop}" in (_expr or ""):
                        owner.setExpression(path, None)
        doc.removeObject(obj.Name)
        doc.recompute()
        self.refresh()

    def add_output(self):
        import ObjectsFem

        doc = self.obj.Document
        out = ObjectsFem.makeExplorationOutput(doc)
        self.obj.addObject(out)
        doc.recompute()
        self.refresh()

    def remove_output(self):
        obj = self._selected(self.output_view, self.output_model)
        if obj is None:
            return
        doc = obj.Document
        doc.removeObject(obj.Name)
        doc.recompute()
        self.refresh()

    @staticmethod
    def _selected(view, table_model):
        rows = view.selectionModel().selectedRows()
        if not rows:
            return None
        return table_model.objectAt(rows[0].row())


##  @}
