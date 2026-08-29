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

"""A response surface study: fit a surrogate, optimise it, verify for real.

Needs a design point table to learn from - a DOE, a sweep, a finished
optimization - and produces a handful of verified design points, which is
the shape of every commercial tool's "response surface optimization": the
surrogate proposes, the solver decides.
"""

__title__ = "FreeCAD FEM exploration response surface object"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

from femexploration.algorithms import surrogate
from femtools import explorationutils

from . import base_fempythonobject
from . import exploration_base

_PropHelper = base_fempythonobject._PropHelper

MODELS = list(surrogate.SURROGATES)


class ExplorationResponseSurface(base_fempythonobject.BaseFemPythonObject):
    """A surrogate-driven study over an existing design point table."""

    Type = explorationutils.RESPONSE_SURFACE_TYPE

    def __init__(self, obj):
        super().__init__(obj)
        obj.addExtension("App::GroupExtensionPython")
        for prop in self._get_properties():
            prop.add_to_object(obj)

    def _get_properties(self):
        prop = list(exploration_base.common_study_properties())
        prop.append(
            _PropHelper(
                type="App::PropertyLink",
                name="TrainingStudy",
                group="Response Surface",
                doc="Study whose design points the surrogate learns from; empty uses every study in the analysis",
                value=None,
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyEnumeration",
                name="Model",
                group="Response Surface",
                doc=(
                    "Kriging interpolates and estimates its own uncertainty; Polynomial is "
                    "the robust choice for few points; RBF interpolates without a variance"
                ),
                value=list(MODELS),
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyInteger",
                name="Candidates",
                group="Response Surface",
                doc="Surrogate optima verified with real solves per round",
                value=5,
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyInteger",
                name="Rounds",
                group="Response Surface",
                doc="Fit, optimise and verify this many times, refitting on the verified points",
                value=1,
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyInteger",
                name="SurrogateEvaluations",
                group="Response Surface",
                doc="Optimizer evaluations against the surrogate per round; these are free",
                value=2000,
            )
        )
        return prop

    def onDocumentRestored(self, obj):
        exploration_base.restore_properties(self, obj)

    def execute(self, obj):
        return True


##  @}
