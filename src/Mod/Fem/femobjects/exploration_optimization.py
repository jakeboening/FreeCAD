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

"""An optimization: drive the objectives while respecting the constraints.

Screening is the default because it is the honest choice for expensive
finite element models: a space-filling batch, a linear picture of the
design space, one predicted improvement.  The iterative methods spend more
evaluations and are worth it when a solve is cheap or a cluster is waiting.
"""

__title__ = "FreeCAD FEM exploration optimization object"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

from femexploration.algorithms import optimize
from femtools import explorationutils

from . import base_fempythonobject
from . import exploration_base

_PropHelper = base_fempythonobject._PropHelper

ALGORITHMS = list(optimize.ALGORITHMS)
CONSTRAINT_HANDLERS = list(optimize.CONSTRAINT_HANDLERS)


class ExplorationOptimization(base_fempythonobject.BaseFemPythonObject):
    """An optimization study over the analysis' design variables."""

    Type = explorationutils.OPTIMIZATION_TYPE

    def __init__(self, obj):
        super().__init__(obj)
        obj.addExtension("App::GroupExtensionPython")
        for prop in self._get_properties():
            prop.add_to_object(obj)

    def _get_properties(self):
        prop = list(exploration_study_properties())
        return prop

    def onDocumentRestored(self, obj):
        exploration_base.restore_properties(self, obj)

    def execute(self, obj):
        return True


def exploration_study_properties():
    prop = list(exploration_base.common_study_properties())
    prop.append(
        _PropHelper(
            type="App::PropertyEnumeration",
            name="Algorithm",
            group="Optimization",
            doc=(
                "Screening: a space-filling batch and one predicted improvement. "
                "NelderMead: a simplex, one point at a time. "
                "DifferentialEvolution: a population, one generation per batch. "
                "NSGA2: multi-objective, produces a Pareto front"
            ),
            value=list(ALGORITHMS),
        )
    )
    prop.append(
        _PropHelper(
            type="App::PropertyInteger",
            name="MaxEvaluations",
            group="Optimization",
            doc="Design points the study may evaluate before stopping",
            value=100,
        )
    )
    prop.append(
        _PropHelper(
            type="App::PropertyInteger",
            name="PopulationSize",
            group="Optimization",
            doc="Points per generation, or per screening batch; 0 chooses from the variable count",
            value=0,
        )
    )
    prop.append(
        _PropHelper(
            type="App::PropertyFloat",
            name="Tolerance",
            group="Optimization",
            doc="Improvement of the objective below which a round counts as no progress",
            value=1e-4,
        )
    )
    prop.append(
        _PropHelper(
            type="App::PropertyInteger",
            name="Stagnation",
            group="Optimization",
            doc="Rounds without progress before stopping; 0 never stops on stagnation",
            value=10,
        )
    )
    prop.append(
        _PropHelper(
            type="App::PropertyFloat",
            name="WallClock",
            group="Optimization",
            doc="Seconds after which the study stops; 0 means no limit",
            value=0.0,
        )
    )
    prop.append(
        _PropHelper(
            type="App::PropertyEnumeration",
            name="ConstraintHandling",
            group="Optimization",
            doc=(
                "FeasibilityRules: feasible beats infeasible, infeasible ranked by violation. "
                "Penalty: objective plus PenaltyFactor times the violation. "
                "DeathPenalty: infeasible points rank after every feasible one"
            ),
            value=list(CONSTRAINT_HANDLERS),
        )
    )
    prop.append(
        _PropHelper(
            type="App::PropertyFloat",
            name="PenaltyFactor",
            group="Optimization",
            doc="Multiplier on the constraint violation when ConstraintHandling is Penalty",
            value=1000.0,
        )
    )
    return prop


##  @}
