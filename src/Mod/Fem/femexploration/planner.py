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

"""Deciding which points to evaluate next.

A planner proposes batches; the engine evaluates them on whatever backend is
configured and feeds the results back.  Keeping the two apart is what lets any
algorithm run on any backend:

* a sweep or a DOE proposes its whole plan up front, so it parallelises
  trivially;
* a population method proposes one generation at a time, which is also
  naturally parallel;
* a simplex method proposes one or two points, and is inherently serial.

None of them knows whether the points are being solved in this process, in
eight subprocesses, or on three machines.
"""

__title__ = "FreeCAD FEM design exploration planners"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

from abc import ABC
from abc import abstractmethod

from .algorithms import sampling
from .model import DesignVector


class Planner(ABC):
    """Proposes design vectors, and learns from the results."""

    #: how many points this planner can usefully have in flight at once, or
    #: None when it is limited only by the batch it just proposed
    parallelism = None

    @abstractmethod
    def initial_batch(self):
        """The first set of design vectors to evaluate."""

    def observe(self, results):
        """Take the outcomes of the batch just evaluated."""

    @abstractmethod
    def next_batch(self):
        """The next set, or an empty list when the study is finished."""

    def total_estimate(self):
        """Best guess at the total number of evaluations, for the progress bar.

        ``None`` when the planner genuinely cannot say, which is honest and
        better than a bar that lies.
        """
        return None


class StaticPlanner(Planner):
    """A planner whose entire plan is known before anything is evaluated.

    Covers sweeps and every classical DOE.  The plan is produced once, handed
    over in one batch, and that is the end of it.
    """

    #: what generated the plan, recorded in the study for reproducibility
    implementation = "static"

    def __init__(self, plan, implementation="static"):
        self._plan = [DesignVector(values=dict(values)) for values in plan]
        self._issued = False
        self.implementation = implementation

    def initial_batch(self):
        self._issued = True
        return list(self._plan)

    def next_batch(self):
        return []

    def total_estimate(self):
        return len(self._plan)

    def __len__(self):
        return len(self._plan)


class SweepPlanner(StaticPlanner):
    """A parametric sweep: every combination of every variable's levels."""

    def __init__(self, variables):
        super().__init__(sampling.full_factorial(variables))


class ExplicitPlanner(StaticPlanner):
    """Design points named outright rather than generated.

    This is what the design point table's Add, Duplicate and Update actions
    produce: a user typed values into a row and wants exactly that row run.
    Variables a point does not mention take their nominal value, so a row
    only has to say what differs.
    """

    def __init__(self, points, variables):
        nominal = {var.name: float(var.nominal) for var in variables}
        plan = []
        for point in points:
            values = dict(nominal)
            for name, value in dict(point).items():
                if name not in nominal:
                    raise ValueError(f"{name!r} is not an active design variable")
                values[name] = float(value[0] if isinstance(value, (list, tuple)) else value)
            plan.append(values)
        super().__init__(plan, "explicit")


def make_planner(spec, points=None):
    """Build the planner a study specification asks for.

    *points*, when given, overrides the study's own plan with an explicit list
    of design vectors - the table asking for particular rows to be evaluated.
    """
    variables = spec.active_variables
    if points is not None:
        return ExplicitPlanner(points, variables)

    method = spec.method or {}
    plan_name = method.get("plan", "FullFactorial")

    if spec.study_type in ("Sweep", "DOE"):
        plan, implementation = sampling.build_plan_with_impl(
            plan_name,
            variables,
            count=int(method.get("point_count", 0) or 0),
            seed=spec.seed,
            alpha=method.get("alpha", "FaceCentred"),
        )
        return StaticPlanner(plan, implementation)

    raise ValueError(
        f"no planner for study type {spec.study_type!r}; "
        "optimization and response surface studies arrive in a later release"
    )


##  @}
