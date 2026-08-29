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

The table is the study's results, with the actions a table of designs wants:
filter, sort, export, set the model to a row, open a retained point, and add
or edit rows and have the study run them.  Running goes through the parent
study's tools in a child process; this panel only watches.
"""

__title__ = "FreeCAD FEM exploration design points task panel"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import FreeCAD

from PySide import QtGui

from femexploration import api
from femexploration import events
from femguiutils import exploration_designpoints as dp
from femtools import explorationtools

from . import base_femtaskpanel


class _TaskPanel(base_femtaskpanel._BaseTaskPanel):
    """Show, and act on, the results of a study run."""

    def __init__(self, obj):
        super().__init__(obj)
        self.study = obj.getParentGroup()

        self.form = QtGui.QWidget()
        self.form.setWindowTitle("Design Points")
        layout = QtGui.QVBoxLayout(self.form)

        self.view = dp.DesignPointView(
            run_dir=obj.RunDirectory or None, show_run_actions=self.study is not None
        )
        layout.addWidget(self.view)

        self.status = QtGui.QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.view.runRequested.connect(self.run_points)
        self.view.currentRequested.connect(self.apply_point)
        self.tool = None
        self.refresh()

    # ------------------------------------------------------------------- view

    def refresh(self):
        obj = self.obj
        self.view.set_columns(dp.columns_from_table(obj))
        self.view.set_rows(obj.Proxy.rows(obj))
        self.view.run_dir = obj.RunDirectory or self.view.run_dir
        if self.study is not None:
            try:
                spec = api.study_spec(self.study)
                self.view.set_defaults(
                    {v.name: [float(v.nominal), v.unit] for v in spec.active_variables}
                )
            except Exception:
                pass
        best = getattr(obj, "BestPoint", "")
        text = f"Best: {best}. " if best else ""
        if obj.RunDirectory:
            text += f"Run directory: {obj.RunDirectory}"
        self.status.setText(text)

    # ---------------------------------------------------------------- actions

    def apply_point(self, row):
        if self.study is None:
            return
        try:
            api.apply_point(self.study, row.get("inputs", {}))
        except Exception as exc:
            QtGui.QMessageBox.warning(self.form, "Could not apply the design point", str(exc))
            return
        FreeCAD.Console.PrintMessage(f"Model set to design point {row.get('point_id', '')}\n")

    def run_points(self, points):
        """Evaluate rows the user added or edited, in the study's child process."""
        if self.study is None:
            return
        if self.tool is not None and self.tool.running():
            self.status.setText("A run is already in progress.")
            return
        self.tool = explorationtools.ExplorationTools(self.study)
        self.tool.points = list(points)
        try:
            self.tool.prepare()
        except Exception as exc:
            self.status.setText(f"Cannot run: {exc}")
            return
        self.tool.attach_console()
        self.tool.process.readyReadStandardOutput.connect(self._on_output)
        self.tool.process.finished.connect(self._on_finished)
        self.status.setText(f"Running {len(points)} design points; see the report view for progress.")
        self.tool.compute()

    def _on_output(self):
        # attach_console already prints; here only the table is updated
        pass

    def _on_finished(self, code, status):
        # the tool imported the journal into the document before this fires
        self.refresh()
        self.status.setText(
            self.status.text() + f"  Run finished with exit code {code}."
        )

    # ------------------------------------------------------------ task dialog

    def getStandardButtons(self):
        return int(QtGui.QDialogButtonBox.Close)

    def accept(self):
        gui_doc = self.obj.ViewObject.Document
        gui_doc.resetEdit()
        return True

    def reject(self):
        if self.tool is not None and self.tool.running():
            self.tool.cancel()
            self.status.setText("Cancellation requested...")
            return False
        gui_doc = self.obj.ViewObject.Document
        gui_doc.resetEdit()
        return True


##  @}
