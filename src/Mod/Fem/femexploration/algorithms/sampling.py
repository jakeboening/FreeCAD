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

"""Design of experiments: which points in the design space to evaluate.

Sampling plans that work in the unit hypercube produce points in ``[0,1]^d``
and are mapped to real values by :func:`decode`.  That indirection is what
keeps variable typing - continuous, log-spaced, integer, discrete, enumerated -
in exactly one place instead of being re-derived by every plan.

A full factorial is the exception: it is defined by each variable's own grid
rather than by a density in the unit cube, so it is built directly.
"""

__title__ = "FreeCAD FEM design exploration sampling"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import itertools
import math

from ..model import DISCRETE
from ..model import ENUMERATION
from ..model import INTEGER
from ..model import LOG


def decode_one(u, variable):
    """Map a single coordinate ``u`` in [0,1] onto a variable's real domain."""
    u = min(1.0, max(0.0, float(u)))

    if variable.var_type == DISCRETE:
        levels = list(variable.levels)
        if not levels:
            return variable.nominal
        index = min(int(u * len(levels)), len(levels) - 1)
        return float(levels[index])

    if variable.var_type == ENUMERATION:
        levels = list(variable.enum_levels)
        if not levels:
            return 0.0
        return float(min(int(u * len(levels)), len(levels) - 1))

    if variable.var_type == INTEGER:
        lo, hi = int(round(variable.lower)), int(round(variable.upper))
        # uniform over the closed integer range; the min() catches u == 1.0
        return float(min(hi, lo + int(u * (hi - lo + 1))))

    if variable.scale == LOG:
        lo, hi = math.log(variable.lower), math.log(variable.upper)
        return math.exp(lo + u * (hi - lo))

    return variable.lower + u * (variable.upper - variable.lower)


def decode(u_row, variables):
    """Map one unit hypercube point onto ``{variable_name: value}``."""
    return {var.name: decode_one(u, var) for u, var in zip(u_row, variables)}


def decode_all(u_rows, variables):
    return [decode(row, variables) for row in u_rows]


def full_factorial(variables):
    """Every combination of every variable's levels.

    Exact and exhaustive: ``prod(len(v.grid()))`` points.  Fine for two or
    three variables, ruinous for six, which is why the panel shows the count
    before anything is solved.
    """
    if not variables:
        return []
    grids = [var.grid() for var in variables]
    return [
        {var.name: float(value) for var, value in zip(variables, combo)}
        for combo in itertools.product(*grids)
    ]


def full_factorial_size(variables):
    """Point count without materialising the plan."""
    size = 1
    for var in variables:
        size *= max(1, len(var.grid()))
    return size


def one_at_a_time(variables):
    """Baseline plus each variable swept alone, the others held at nominal.

    The classic screening plan, and the one the tornado chart is built from.
    """
    if not variables:
        return []
    baseline = {var.name: float(var.nominal) for var in variables}
    plan = [dict(baseline)]
    for var in variables:
        for value in var.grid():
            if abs(value - var.nominal) < 1e-15:
                continue
            point = dict(baseline)
            point[var.name] = float(value)
            plan.append(point)
    return plan


#: Plans available in this release.  Latin hypercube, Sobol, central composite
#: and Box-Behnken arrive with the DOE object; the registry exists now so the
#: study object's enumeration and the engine agree on one list.
PLANS = {
    "FullFactorial": full_factorial,
    "OneAtATime": one_at_a_time,
}


def build_plan(name, variables):
    """Return the design vectors for plan *name*."""
    try:
        plan = PLANS[name]
    except KeyError:
        raise ValueError(
            f"unknown sampling plan {name!r}; available: {sorted(PLANS)}"
        ) from None
    return plan(variables)


##  @}
