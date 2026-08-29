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

"""Task panel for a design of experiments.

The sweep panel with a plan that takes a point count: the same three boxes,
the same preview - which matters even more here, since a Latin hypercube
at thirty points and a central composite at fifteen look nothing alike and
the table is the quickest way to see which one the study will run.
"""

__title__ = "FreeCAD FEM exploration DOE task panel"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

from PySide import QtGui

from femexploration.algorithms import sampling

from . import task_exploration_sweep


class _TaskPanel(task_exploration_sweep._TaskPanel):
    """Configure, preview and run a design of experiments."""

    settings_title = "Design of Experiments"

    def plan_names(self):
        from femobjects.exploration_doe import PLANS

        return list(PLANS)

    def _extra_settings(self, form, obj):
        from femobjects.exploration_doe import ALPHAS

        self.count = QtGui.QSpinBox()
        self.count.setRange(1, 100000)
        self.count.setValue(int(obj.PointCount))
        self.count.setToolTip("Design points to sample, for the plans that take a count")
        form.addRow("Points", self.count)

        self.alpha = QtGui.QComboBox()
        self.alpha.addItems(list(ALPHAS))
        self.alpha.setCurrentText(obj.Alpha)
        self.alpha.setToolTip("Axial distance of a central composite design")
        form.addRow("Axial points", self.alpha)

        self.seed = QtGui.QSpinBox()
        self.seed.setRange(0, 2**31 - 1)
        self.seed.setValue(int(obj.Seed))
        self.seed.setToolTip("Random seed; the same seed gives the same points")
        form.addRow("Seed", self.seed)

    def setup_connections(self):
        super().setup_connections()
        self.count.valueChanged.connect(self.refresh)
        self.alpha.currentTextChanged.connect(self.refresh)
        self.seed.valueChanged.connect(self.refresh)
        self.plan.currentTextChanged.connect(self._enable_settings)
        self._enable_settings()

    def _enable_settings(self, *args):
        name = self.plan.currentText()
        self.count.setEnabled(bool(sampling.DOE_PLANS.get(name, False)))
        self.alpha.setEnabled(name == "CentralComposite")
        self.seed.setEnabled(name in ("LatinHypercube", "Sobol", "RandomUniform"))

    def plan_points(self, spec):
        return sampling.build_plan(
            self.plan.currentText(),
            spec.active_variables,
            count=self.count.value(),
            seed=self.seed.value(),
            alpha=self.alpha.currentText(),
        )

    def get_object_params(self):
        super().get_object_params()
        self.count.setValue(int(self.obj.PointCount))
        self.alpha.setCurrentText(self.obj.Alpha)
        self.seed.setValue(int(self.obj.Seed))

    def set_object_params(self):
        super().set_object_params()
        self.obj.PointCount = self.count.value()
        self.obj.Alpha = self.alpha.currentText()
        self.obj.Seed = self.seed.value()


##  @}
