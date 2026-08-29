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

"""Task panel for an optimization study.

The sweep panel's three boxes with the optimizer's settings in the first.
The preview shows the first batch the algorithm will ask for - a simplex,
a population, a screening design - which is the part of an optimization a
user can sanity-check before spending a solver on it.
"""

__title__ = "FreeCAD FEM exploration optimization task panel"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

from PySide import QtGui

from femexploration import planner as planner_module
from femexploration.algorithms import optimize

from . import task_exploration_sweep


class _TaskPanel(task_exploration_sweep._TaskPanel):
    """Configure, preview and run an optimization."""

    settings_title = "Optimization"
    plan_label = "Algorithm"

    def current_plan(self, obj):
        return obj.Algorithm

    def plan_names(self):
        return list(optimize.ALGORITHMS)

    def _extra_settings(self, form, obj):
        self.evaluations = QtGui.QSpinBox()
        self.evaluations.setRange(1, 1000000)
        self.evaluations.setValue(int(obj.MaxEvaluations))
        self.evaluations.setToolTip("Design points the study may evaluate before stopping")
        form.addRow("Max evaluations", self.evaluations)

        self.population_size = QtGui.QSpinBox()
        self.population_size.setRange(0, 10000)
        self.population_size.setSpecialValueText("auto")
        self.population_size.setValue(int(obj.PopulationSize))
        self.population_size.setToolTip("Points per generation or screening batch; auto uses the variable count")
        form.addRow("Population", self.population_size)

        self.handling = QtGui.QComboBox()
        self.handling.addItems(list(optimize.CONSTRAINT_HANDLERS))
        self.handling.setCurrentText(obj.ConstraintHandling)
        form.addRow("Constraints", self.handling)

        self.note = QtGui.QLabel()
        self.note.setWordWrap(True)
        form.addRow("", self.note)

    def setup_connections(self):
        super().setup_connections()
        self.evaluations.valueChanged.connect(self.refresh)
        self.population_size.valueChanged.connect(self.refresh)
        self.plan.currentTextChanged.connect(self._describe)
        self.backend.currentTextChanged.connect(self._describe)
        self._describe()

    def _describe(self, *args):
        name = self.plan.currentText()
        if name == optimize.NELDER_MEAD and self.backend.currentText() != "Serial":
            self.note.setText(
                "Nelder-Mead proposes one point at a time, so a parallel backend "
                "gains nothing here; Differential Evolution or NSGA2 use every slot."
            )
        elif name == optimize.NSGA2:
            self.note.setText("NSGA2 produces a Pareto front over several objectives; read it with the Pareto chart.")
        elif name == optimize.SCREENING:
            self.note.setText("Screening samples the space once, fits a linear trend and tries the predicted best corner.")
        else:
            self.note.setText("")

    def plan_points(self, spec):
        # the spec was read from the object; apply the panel's live settings
        spec.method.update(
            {
                "algorithm": self.plan.currentText(),
                "max_evaluations": self.evaluations.value(),
                "population": self.population_size.value(),
                "constraints": self.handling.currentText(),
            }
        )
        made = planner_module.make_planner(spec)
        return [dict(v.values) for v in made.initial_batch()]

    def refresh(self, *args):
        super().refresh(*args)
        try:
            self.preview.setText(
                self.preview.text().replace(
                    "design points</b>", "design points in the first batch</b>"
                )
                + f" Up to {self.evaluations.value()} evaluations in all."
            )
        except Exception:
            pass

    def get_object_params(self):
        super().get_object_params()
        self.plan.setCurrentText(self.obj.Algorithm)
        self.evaluations.setValue(int(self.obj.MaxEvaluations))
        self.population_size.setValue(int(self.obj.PopulationSize))
        self.handling.setCurrentText(self.obj.ConstraintHandling)

    def set_object_params(self):
        self.obj.Backend = self.backend.currentText()
        self.obj.ResumeFromCache = self.resume.isChecked()
        self.obj.Algorithm = self.plan.currentText()
        self.obj.MaxEvaluations = self.evaluations.value()
        self.obj.PopulationSize = self.population_size.value()
        self.obj.ConstraintHandling = self.handling.currentText()


##  @}
