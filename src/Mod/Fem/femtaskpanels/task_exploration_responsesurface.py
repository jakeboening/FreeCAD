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

"""Task panel for a response surface study.

The preview is the candidates the surrogate proposes right now - fitted on
the training table when the panel opens - so the user sees what a run would
verify before spending a solver on it.
"""

__title__ = "FreeCAD FEM exploration response surface task panel"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

from PySide import QtGui

from femexploration import api
from femexploration import planner as planner_module

from . import task_exploration_sweep


class _TaskPanel(task_exploration_sweep._TaskPanel):
    """Configure, preview and run a response surface study."""

    settings_title = "Response Surface"
    plan_label = "Model"

    def current_plan(self, obj):
        return obj.Model

    def plan_names(self):
        from femobjects.exploration_responsesurface import MODELS

        return list(MODELS)

    def _extra_settings(self, form, obj):
        from femtools import explorationutils

        self.training = QtGui.QComboBox()
        self.training.addItem("(every study in the analysis)", "")
        analysis = obj.getParentGroup()
        for study in explorationutils.get_studies(analysis) if analysis else []:
            if study is not obj:
                self.training.addItem(study.Label, study.Name)
        current = obj.TrainingStudy.Name if obj.TrainingStudy else ""
        index = self.training.findData(current)
        self.training.setCurrentIndex(max(0, index))
        form.addRow("Learn from", self.training)

        self.candidates = QtGui.QSpinBox()
        self.candidates.setRange(1, 100)
        self.candidates.setValue(int(obj.Candidates))
        form.addRow("Candidates", self.candidates)

        self.rounds = QtGui.QSpinBox()
        self.rounds.setRange(1, 50)
        self.rounds.setValue(int(obj.Rounds))
        form.addRow("Rounds", self.rounds)

        self.fit = QtGui.QLabel()
        self.fit.setWordWrap(True)
        form.addRow("", self.fit)

    def setup_connections(self):
        super().setup_connections()
        self.training.currentIndexChanged.connect(self.refresh)
        self.candidates.valueChanged.connect(self.refresh)
        self.rounds.valueChanged.connect(self.refresh)

    def plan_points(self, spec):
        spec.method.update({
            "model": self.plan.currentText(),
            "candidates": self.candidates.value(),
            "rounds": self.rounds.value(),
            "training_study": self.training.currentData() or "",
        })
        rows = api.training_rows_for(self.obj, spec) or []
        made = planner_module.make_planner(spec, training_rows=rows)
        points = [dict(v.values) for v in made.initial_batch()]
        self.fit.setText(
            "; ".join(
                f"{key}: {s['model']} on {s['points']} points, LOO RMSE {s['loocv_rmse']:.3g}"
                for key, s in made.summaries.items()
            )
        )
        return points

    def get_object_params(self):
        super().get_object_params()
        self.candidates.setValue(int(self.obj.Candidates))
        self.rounds.setValue(int(self.obj.Rounds))

    def set_object_params(self):
        self.obj.Backend = self.backend.currentText()
        self.obj.ResumeFromCache = self.resume.isChecked()
        self.obj.Model = self.plan.currentText()
        self.obj.Candidates = self.candidates.value()
        self.obj.Rounds = self.rounds.value()
        name = self.training.currentData() or ""
        self.obj.TrainingStudy = self.obj.Document.getObject(name) if name else None


##  @}
