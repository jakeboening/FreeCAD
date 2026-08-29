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


def encode_one(value, variable):
    """A real value back onto [0,1]; the inverse of decode_one."""
    if value is None:
        return 0.5
    value = float(value)
    if variable.var_type == DISCRETE:
        levels = list(variable.levels)
        if not levels:
            return 0.5
        index = min(range(len(levels)), key=lambda i: abs(float(levels[i]) - value))
        return (index + 0.5) / len(levels)
    if variable.var_type == ENUMERATION:
        n = len(variable.enum_levels)
        return (int(value) + 0.5) / n if n else 0.5
    if variable.var_type == INTEGER:
        lo, hi = int(round(variable.lower)), int(round(variable.upper))
        return (value - lo + 0.5) / (hi - lo + 1)
    if variable.scale == LOG:
        lo, hi = math.log(variable.lower), math.log(variable.upper)
        return (math.log(max(value, 1e-300)) - lo) / (hi - lo) if hi > lo else 0.5
    span = variable.upper - variable.lower
    return (value - variable.lower) / span if span else 0.5


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


# ---------------------------------------------------------------------------
# space-filling and classical designs, all in the unit cube then decoded
# ---------------------------------------------------------------------------


def _rng(seed):
    import numpy as np

    return np.random.default_rng(int(seed))


def _streams(rng, k):
    """One independent child stream per variable.

    Column *j* of every design below is drawn from stream *j*, so adding a
    variable to a study leaves the earlier columns exactly as they were for
    the random and stratified designs.  The maximin improvement of a Latin
    hypercube necessarily couples columns, so there the guarantee covers
    the strata, not the final arrangement.  The reference implementation's
    unseeded sampler made "why did my first ten points change?" a recurring
    question; a seed makes a study reproduce exactly.
    """
    return list(rng.spawn(k)) if k else []


def unit_latin_hypercube(n, k, rng, maximin_iterations=None):
    """An n-by-k Latin hypercube in [0,1]^k, improved by maximin swaps.

    Each column is a random permutation of the n strata with a jitter inside
    the stratum, so every one-dimensional projection is evenly covered.  The
    swap phase then trades pairs of entries within a column when that raises
    the smallest pairwise distance, which is the cheap part of a maximin
    design and most of its benefit.
    """
    import numpy as np

    n, k = int(n), int(k)
    if n <= 0 or k <= 0:
        return np.zeros((0, k))
    streams = _streams(rng, k)
    u = np.empty((n, k))
    for j, stream in enumerate(streams):
        perm = stream.permutation(n)
        u[:, j] = (perm + stream.random(n)) / n
    if n < 3 or k < 2:
        return u

    swaps = maximin_iterations if maximin_iterations is not None else min(200, 20 * n * k)
    swap_rng = rng.spawn(1)[0]

    def min_dist(points):
        diff = points[:, None, :] - points[None, :, :]
        d2 = (diff * diff).sum(axis=2)
        np.fill_diagonal(d2, np.inf)
        return d2.min()

    best = min_dist(u)
    for _ in range(int(swaps)):
        j = int(swap_rng.integers(k))
        a, b = swap_rng.choice(n, 2, replace=False)
        u[a, j], u[b, j] = u[b, j], u[a, j]
        candidate = min_dist(u)
        if candidate > best:
            best = candidate
        else:
            u[a, j], u[b, j] = u[b, j], u[a, j]
    return u


def latin_hypercube(variables, count, seed=0):
    """A maximin Latin hypercube over the variables' domains."""
    if not variables or count <= 0:
        return []
    u = unit_latin_hypercube(count, len(variables), _rng(seed))
    return decode_all(u, variables)


def random_uniform(variables, count, seed=0):
    """Independent uniform samples; the honest baseline for everything else."""
    if not variables or count <= 0:
        return []
    import numpy as np

    streams = _streams(_rng(seed), len(variables))
    u = np.column_stack([stream.random(int(count)) for stream in streams])
    return decode_all(u, variables)


def sobol(variables, count, seed=0):
    """A scrambled Sobol' sequence, when scipy is here to provide one.

    Returns ``(plan, implementation)``.  Without scipy the plan falls back to
    a scrambled Latin hypercube, and says so: the two have different
    projection properties and a study must record which one it actually ran.
    """
    if not variables or count <= 0:
        return [], "none"
    k = len(variables)
    try:
        from scipy.stats import qmc
    except ImportError:
        qmc = None
    if qmc is not None:
        sampler = qmc.Sobol(d=k, scramble=True, seed=int(seed))
        u = sampler.random(int(count))
        return decode_all(u, variables), "scipy.stats.qmc.Sobol"
    u = unit_latin_hypercube(count, k, _rng(seed))
    return decode_all(u, variables), "latin_hypercube (scipy not available)"


def _centre_and_span(variables):
    """Coded-unit mapping: centre and half range in the unit cube."""
    return [(0.5, 0.5) for _ in variables]


def central_composite(variables, alpha="FaceCentred"):
    """A central composite design: factorial corners, axial points, centre.

    ``alpha`` is ``FaceCentred`` (axial points on the faces, so nothing lands
    outside the bounds) or ``Rotatable`` (``alpha = (2^k)^(1/4)``, which needs
    the bounds to be the *inner* box; axial points are clipped to the domain
    since a solver cannot be asked for a value the variable refuses).
    """
    import itertools

    import numpy as np

    k = len(variables)
    if k == 0:
        return []
    if str(alpha) == "Rotatable":
        a = (2.0**k) ** 0.25
    else:
        a = 1.0
    rows = []
    for corner in itertools.product((-1.0, 1.0), repeat=k):
        rows.append(np.array(corner))
    for j in range(k):
        for sign in (-a, a):
            axial = np.zeros(k)
            axial[j] = sign
            rows.append(axial)
    rows.append(np.zeros(k))
    coded = np.array(rows)
    u = np.clip(0.5 + 0.5 * coded, 0.0, 1.0)
    return decode_all(u, variables)


def box_behnken(variables):
    """A Box-Behnken design: edge midpoints of the cube plus the centre.

    Three-level, never visits a corner - so the extreme combinations a
    factorial would try (and a mesher would choke on) are avoided - and needs
    at least three variables to mean anything.
    """
    import itertools

    import numpy as np

    k = len(variables)
    if k < 3:
        raise ValueError("a Box-Behnken design needs at least three variables")
    rows = []
    for i, j in itertools.combinations(range(k), 2):
        for si, sj in itertools.product((-1.0, 1.0), repeat=2):
            row = np.zeros(k)
            row[i], row[j] = si, sj
            rows.append(row)
    rows.append(np.zeros(k))
    u = 0.5 + 0.5 * np.array(rows)
    return decode_all(u, variables)


#: Plans a sweep offers: defined by the variables' own grids, no point count.
SWEEP_PLANS = ("FullFactorial", "OneAtATime")

#: Plans a design of experiments offers.  Those marked True take a point
#: count; the others are fixed by the number of variables.
DOE_PLANS = {
    "LatinHypercube": True,
    "Sobol": True,
    "RandomUniform": True,
    "CentralComposite": False,
    "BoxBehnken": False,
    "FullFactorial": False,
}

#: Every plan by name, so the study objects' enumerations and the engine
#: agree on one list.
PLANS = {
    "FullFactorial": full_factorial,
    "OneAtATime": one_at_a_time,
    "LatinHypercube": latin_hypercube,
    "Sobol": sobol,
    "RandomUniform": random_uniform,
    "CentralComposite": central_composite,
    "BoxBehnken": box_behnken,
}


def build_plan(name, variables, count=None, seed=0, alpha="FaceCentred"):
    """Return the design vectors for plan *name*."""
    return build_plan_with_impl(name, variables, count=count, seed=seed, alpha=alpha)[0]


def build_plan_with_impl(name, variables, count=None, seed=0, alpha="FaceCentred"):
    """Return ``(plan, implementation)``.

    The implementation string names what actually generated the points -
    scipy's Sobol' or the numpy fallback, for instance - so it can be written
    into the study record.  "It gave different numbers on the cluster" is a
    question that record answers.
    """
    if name not in PLANS:
        raise ValueError(f"unknown sampling plan {name!r}; available: {sorted(PLANS)}")
    if name == "Sobol":
        return sobol(variables, count or 0, seed)
    if name in ("LatinHypercube", "RandomUniform"):
        return PLANS[name](variables, count or 0, seed), f"numpy {name}"
    if name == "CentralComposite":
        return central_composite(variables, alpha), f"{name} {alpha}"
    return PLANS[name](variables), name


def plan_size(name, variables, count=None):
    """How many points a plan will have, without materialising it."""
    k = len(variables)
    if name == "FullFactorial":
        return full_factorial_size(variables)
    if name == "OneAtATime":
        return len(one_at_a_time(variables))
    if name == "CentralComposite":
        return 2**k + 2 * k + 1 if k else 0
    if name == "BoxBehnken":
        return 2 * k * (k - 1) + 1 if k >= 3 else 0
    return int(count or 0)


##  @}
