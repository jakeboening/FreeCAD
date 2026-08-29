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

"""Single-objective optimizers, as planners.

Each optimizer proposes a batch of design vectors, learns from the results
the engine hands back, and proposes the next batch - nothing here knows
whether the points were solved in this process, in eight subprocesses or on
three machines.  All of them work in the unit cube and decode through the
one ``sampling.decode`` that knows about integer, discrete and log-scaled
variables, so a discrete variable is handled once rather than once per
algorithm.

Constraints are handled by a pluggable rule rather than by rejecting
infeasible points outright.  Rejection - the ``+inf`` the reference
implementation used - destroys every bit of information a violated point
carries, including the direction back to feasibility.  Deb's feasibility
rules keep that information and are the default.

Failed evaluations (a mesh that would not build, a solver that crashed) are
a third category, distinct from infeasible: they carry no number at all.
They are ranked last, behind every infeasible point, so an optimizer walks
away from them without mistaking a crash for a bad design.

Everything is numpy only.  Every random draw comes from a seeded generator,
so a study reproduces exactly.
"""

__title__ = "FreeCAD FEM design exploration optimizers"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import math
import time

from ..model import COMPLETED
from ..model import DesignVector
from ..planner import Planner
from . import sampling

FEASIBILITY_RULES = "FeasibilityRules"
PENALTY = "Penalty"
DEATH_PENALTY = "DeathPenalty"
CONSTRAINT_HANDLERS = (FEASIBILITY_RULES, PENALTY, DEATH_PENALTY)

SCREENING = "Screening"
NELDER_MEAD = "NelderMead"
DIFFERENTIAL_EVOLUTION = "DifferentialEvolution"
NSGA2 = "NSGA2"
ALGORITHMS = (SCREENING, NELDER_MEAD, DIFFERENTIAL_EVOLUTION, NSGA2)


# ------------------------------------------------------------------ scoring


def scalar_objective(result, objectives):
    """Weighted sum of the folded objectives, or None when any is missing."""
    total = 0.0
    for objective in objectives:
        if objective.name not in result.objectives:
            return None
        total += float(objective.weight) * float(result.objectives[objective.name])
    return total


#: merit offset that puts every infeasible point behind every feasible one.
#: A constant rather than "worst feasible seen so far": a merit stored in a
#: population must mean the same thing later, or early infeasible points
#: keep stale low scores and never get displaced.
INFEASIBLE_OFFSET = 1e15


class ConstraintHandler:
    """Turns a result into one comparable merit; lower is better."""

    name = "base"

    def observe(self, merits):
        """Hook for handlers that adapt; the shipped ones are stateless."""

    def merit(self, result, objectives):
        raise NotImplementedError


class FeasibilityRules(ConstraintHandler):
    """Deb's rules: feasible beats infeasible; infeasible ranked by violation.

    Scalarised as the objective when feasible and as a large constant plus
    the violation otherwise, which reproduces the pairwise rules exactly
    while giving simplex and population methods the single number they
    want.  The violation is kept: an infeasible point still knows which of
    its neighbours is closer to the feasible region.
    """

    name = FEASIBILITY_RULES

    def merit(self, result, objectives):
        if result.state != COMPLETED:
            return None
        value = scalar_objective(result, objectives)
        if value is None:
            return None
        if result.feasible:
            return value
        return INFEASIBLE_OFFSET + result.total_violation


class Penalty(ConstraintHandler):
    """Objective plus a fixed multiple of the total violation."""

    name = PENALTY

    def __init__(self, factor=1000.0):
        super().__init__()
        self.factor = float(factor)

    def merit(self, result, objectives):
        if result.state != COMPLETED:
            return None
        value = scalar_objective(result, objectives)
        if value is None:
            return None
        return value + self.factor * result.total_violation


class DeathPenalty(ConstraintHandler):
    """The classic rule: an infeasible point is simply rejected.

    Kept finite so the arithmetic stays sane, but deliberately carrying no
    violation information - that is what makes it the death penalty, and
    what makes FeasibilityRules the better default.
    """

    name = DEATH_PENALTY

    def merit(self, result, objectives):
        if result.state != COMPLETED:
            return None
        value = scalar_objective(result, objectives)
        if value is None:
            return None
        if result.feasible:
            return value
        return INFEASIBLE_OFFSET


def make_handler(name, penalty_factor=1000.0):
    if name == PENALTY:
        return Penalty(penalty_factor)
    if name == DEATH_PENALTY:
        return DeathPenalty()
    return FeasibilityRules()


# ----------------------------------------------------------------- stopping


class Stopping:
    """When to stop: evaluations, tolerance, stagnation, wall clock - all at once.

    Any one of them ends the study, and ``reason`` says which.
    """

    def __init__(self, max_evaluations=100, tolerance=1e-4, stagnation=10, wall_clock=0.0):
        self.max_evaluations = int(max_evaluations)
        self.tolerance = float(tolerance)
        self.stagnation = int(stagnation)
        self.wall_clock = float(wall_clock)
        self.started = time.time()
        self.reason = ""
        self._best = None
        self._stale_rounds = 0

    def record_round(self, best):
        """Note the best merit after a round; returns True on improvement."""
        if best is None:
            self._stale_rounds += 1
            return False
        if self._best is None or best < self._best - self.tolerance:
            self._best = best
            self._stale_rounds = 0
            return True
        self._stale_rounds += 1
        return False

    def should_stop(self, evaluations):
        if self.max_evaluations and evaluations >= self.max_evaluations:
            self.reason = f"reached {self.max_evaluations} evaluations"
            return True
        if self.stagnation and self._stale_rounds >= self.stagnation:
            self.reason = f"no improvement beyond {self.tolerance:g} in {self.stagnation} rounds"
            return True
        if self.wall_clock and time.time() - self.started >= self.wall_clock:
            self.reason = f"wall clock limit of {self.wall_clock:g} s reached"
            return True
        return False


# ------------------------------------------------------------- base planner


class OptimizerPlanner(Planner):
    """Shared bookkeeping: unit-cube encoding, history, merit, stopping."""

    implementation = "numpy"

    def __init__(self, spec, handler=None, stopping=None, seed=None, population=0):
        import numpy as np

        self.spec = spec
        self.variables = list(spec.active_variables)
        self.k = len(self.variables)
        self.objectives = list(spec.active_objectives)
        if not self.objectives:
            raise ValueError("an optimization needs at least one active objective")
        self.handler = handler or FeasibilityRules()
        self.stopping = stopping or Stopping()
        self.rng = np.random.default_rng(spec.seed if seed is None else seed)
        self.population = int(population) or max(8, 10 * self.k)
        self.evaluations = 0
        #: every result seen, in arrival order: (point_id, unit vector, merit, result)
        self.history = []
        self._issued = {}
        self.best = None
        self.finished_reason = ""

    # ------------------------------------------------------------ encoding

    def decode(self, u):
        return sampling.decode(list(u), self.variables)

    def encode(self, values):
        """Real values back onto the unit cube; the inverse of decode."""
        out = []
        for var in self.variables:
            value = values.get(var.name)
            value = value[0] if isinstance(value, (list, tuple)) else value
            out.append(sampling.encode_one(value, var))
        return out

    def vector(self, u):
        import numpy as np

        u = np.clip(np.asarray(u, dtype=float), 0.0, 1.0)
        vector = DesignVector(values=self.decode(u))
        self._issued[id(vector)] = (vector, u)
        return vector

    # ------------------------------------------------------------- results

    def observe(self, results):
        """Take the engine's results for the vectors issued so far."""
        known = {pid for pid, _u, _m, _r in self.history}
        by_id = {}
        for vector, u in self._issued.values():
            if vector.point_id:
                by_id[vector.point_id] = u
        merits = []
        new = []
        for result in results:
            if result.point_id in known or result.point_id not in by_id:
                continue
            u = by_id[result.point_id]
            merit = self.handler.merit(result, self.objectives)
            self.history.append((result.point_id, u, merit, result))
            known.add(result.point_id)
            new.append((u, merit, result))
            merits.append(merit)
            self.evaluations += 1
            if merit is not None and (self.best is None or merit < self.best[1]):
                self.best = (u, merit, result)
        self.handler.observe(merits)
        self._issued = {k: v for k, v in self._issued.items() if not v[0].point_id or v[0].point_id not in known}
        self.learn(new)
        return new

    def learn(self, new):
        """Algorithm-specific update from the newly observed points."""

    def merit_of(self, u):
        for _pid, hu, merit, _r in self.history:
            if len(hu) == len(u) and max(abs(a - b) for a, b in zip(hu, u)) < 1e-12:
                return merit
        return None

    def worst_merit(self):
        finite = [m for _p, _u, m, _r in self.history if m is not None]
        return max(finite) if finite else 0.0

    def rank_value(self, merit):
        """A finite number for a merit, with failures behind everything."""
        return merit if merit is not None else self.worst_merit() + 1e9

    def total_estimate(self):
        return self.stopping.max_evaluations or None

    def remaining(self):
        """Evaluations left in the budget, or None when it is unlimited."""
        limit = self.stopping.max_evaluations
        if not limit:
            return None
        return max(0, limit - self.evaluations)

    def stop(self, reason):
        self.finished_reason = reason
        return []


# ------------------------------------------------------------ screening


class Screening(OptimizerPlanner):
    """A space-filling batch, a linear fit, one predicted improvement.

    The honest default for expensive FEM: it spends its budget where every
    point is informative, its results are exactly what the sensitivity and
    correlation charts want, and its single exploitation step lands on the
    corner the linear model predicts best - which is often most of the gain
    a real optimizer would find.
    """

    implementation = "numpy Screening"

    def initial_batch(self):
        import numpy as np

        n = min(self.population, self.stopping.max_evaluations or self.population)
        u = sampling.unit_latin_hypercube(n, self.k, self.rng)
        nominal = np.array(self.encode({v.name: v.nominal for v in self.variables}))
        return [self.vector(nominal)] + [self.vector(row) for row in u]

    def next_batch(self):
        if self.evaluations >= (self.stopping.max_evaluations or 0) and self.stopping.max_evaluations:
            return self.stop("budget spent")
        if getattr(self, "_exploited", False):
            return self.stop("screening complete")
        self._exploited = True

        import numpy as np

        rows = [(u, m) for _p, u, m, _r in self.history if m is not None]
        feasible = [(u, m) for u, m in rows if m < INFEASIBLE_OFFSET]
        if len(feasible) >= self.k + 2:
            rows = feasible
        if len(rows) < self.k + 2:
            return self.stop("too few successful points to fit a trend")
        x = np.array([u for u, _m in rows])
        y = np.array([m for _u, m in rows])
        design = np.hstack([x, np.ones((len(x), 1))])
        coeffs, _res, _rank, _sv = np.linalg.lstsq(design, y, rcond=None)
        slope = coeffs[: self.k]
        # steepest descent to the box edge, then a half step from the best
        corner = np.where(slope > 0, 0.0, 1.0)
        best_u = self.best[0] if self.best else x[np.argmin(y)]
        halfway = 0.5 * (best_u + corner)
        return [self.vector(corner), self.vector(halfway)]


# --------------------------------------------------------- nelder-mead


class NelderMead(OptimizerPlanner):
    """The simplex method, bound-clipped, restarting when the simplex collapses.

    Inherently serial: one or two points per step.  Right for a cheap model
    or a serial backend, wrong for a cluster, and the panel says so.
    """

    implementation = "numpy NelderMead"
    parallelism = 1

    def __init__(self, spec, initial_size=0.25, **kwargs):
        super().__init__(spec, **kwargs)
        self.initial_size = float(initial_size)
        self.simplex = []          # list of (u, merit)
        self._pending = None       # what the outstanding proposal means
        self._restarts = 0

    def _init_simplex(self, centre, size):
        import numpy as np

        centre = np.clip(np.asarray(centre, dtype=float), 0.0, 1.0)
        points = [centre]
        for j in range(self.k):
            step = np.zeros(self.k)
            step[j] = size if centre[j] + size <= 1.0 else -size
            points.append(np.clip(centre + step, 0.0, 1.0))
        return points

    def initial_batch(self):
        import numpy as np

        nominal = np.array(self.encode({v.name: v.nominal for v in self.variables}))
        points = self._init_simplex(nominal, self.initial_size)
        self._pending = ("init", points)
        return [self.vector(p) for p in points]

    def _sorted_simplex(self):
        self.simplex.sort(key=lambda um: self.rank_value(um[1]))

    def learn(self, new):
        import numpy as np

        if self._pending is None:
            return
        kind = self._pending[0]
        if kind == "init":
            self.simplex = [(u, m) for u, m, _r in new]
            self._sorted_simplex()
            self._pending = None
            return
        # a single proposal: work out where it goes
        if not new:
            self._pending = None
            return
        u_new, m_new, _r = new[0]
        v_new = self.rank_value(m_new)
        self._sorted_simplex()
        best_v = self.rank_value(self.simplex[0][1])
        worst_v = self.rank_value(self.simplex[-1][1])
        second_v = self.rank_value(self.simplex[-2][1]) if len(self.simplex) > 1 else worst_v
        step = self._pending[1]
        if kind == "reflect":
            if v_new < best_v:
                self._pending = ("expand", step, (u_new, m_new))
                self._next = np.clip(step["centroid"] + 2.0 * (step["centroid"] - step["worst"]), 0, 1)
                return
            if v_new < second_v:
                self.simplex[-1] = (u_new, m_new)
            elif v_new < worst_v:
                self._pending = ("contract_out", step, (u_new, m_new))
                self._next = np.clip(step["centroid"] + 0.5 * (u_new - step["centroid"]), 0, 1)
                return
            else:
                self._pending = ("contract_in", step, None)
                self._next = np.clip(step["centroid"] + 0.5 * (step["worst"] - step["centroid"]), 0, 1)
                return
        elif kind == "expand":
            reflected = self._pending[2]
            self.simplex[-1] = (u_new, m_new) if v_new < self.rank_value(reflected[1]) else reflected
        elif kind == "contract_out":
            reflected = self._pending[2]
            if v_new <= self.rank_value(reflected[1]):
                self.simplex[-1] = (u_new, m_new)
            else:
                return self._shrink()
        elif kind == "contract_in":
            if v_new < worst_v:
                self.simplex[-1] = (u_new, m_new)
            else:
                return self._shrink()
        elif kind == "shrink":
            # the best vertex was kept; the others were re-evaluated
            self.simplex = [self._pending[1]] + [(u, m) for u, m, _r in new]
        self._pending = None
        self._sorted_simplex()

    def _shrink(self):
        """Pull every vertex halfway to the best; they are evaluated next."""
        self._sorted_simplex()
        best_u, best_m = self.simplex[0]
        points = [best_u + 0.5 * (u - best_u) for u, _m in self.simplex[1:]]
        self._pending = ("shrink", (best_u, best_m))
        self._shrink_points = points

    def _size(self):
        import numpy as np

        if len(self.simplex) < 2:
            return 0.0
        pts = np.array([u for u, _m in self.simplex])
        return float(np.max(np.abs(pts - pts[0])))

    def next_batch(self):
        import numpy as np

        if self.stopping.should_stop(self.evaluations):
            return self.stop(self.stopping.reason)
        if self._pending is not None and self._pending[0] == "shrink":
            points = getattr(self, "_shrink_points", None) or []
            self._shrink_points = None
            return [self.vector(p) for p in points]
        if self._pending is not None and hasattr(self, "_next"):
            u = self._next
            del self._next
            return [self.vector(u)]

        if len(self.simplex) < self.k + 1:
            return self.stop("simplex lost vertices to failed evaluations")
        self._sorted_simplex()
        best_merit = self.simplex[0][1]
        if self._size() < 1e-3 * max(1.0, self.k):
            self.stopping.record_round(best_merit)
            if self._restarts >= 3:
                return self.stop("simplex collapsed")
            self._restarts += 1
            points = self._init_simplex(self.simplex[0][0], self.initial_size * 0.5**self._restarts)
            self._pending = ("init", points)
            return [self.vector(p) for p in points]

        improved = self.stopping.record_round(best_merit)
        if self.stopping.should_stop(self.evaluations):
            return self.stop(self.stopping.reason)
        worst = self.simplex[-1][0]
        centroid = np.mean([u for u, _m in self.simplex[:-1]], axis=0)
        reflected = np.clip(centroid + (centroid - worst), 0.0, 1.0)
        self._pending = ("reflect", {"centroid": centroid, "worst": worst})
        return [self.vector(reflected)]


# ------------------------------------------------ differential evolution


class DifferentialEvolution(OptimizerPlanner):
    """DE/rand/1/bin.  One generation is one batch, so it fills a cluster."""

    implementation = "numpy DifferentialEvolution"

    def __init__(self, spec, mutation=0.7, crossover=0.9, **kwargs):
        super().__init__(spec, **kwargs)
        self.mutation = float(mutation)
        self.crossover = float(crossover)
        self.pop = []              # list of (u, merit)
        self._trials = []          # unit vectors of the outstanding generation

    @property
    def parallelism(self):
        return self.population

    def initial_batch(self):
        import numpy as np

        n = self.population
        u = sampling.unit_latin_hypercube(n, self.k, self.rng)
        nominal = np.array(self.encode({v.name: v.nominal for v in self.variables}))
        u[0] = nominal
        self._trials = [row for row in u]
        self._generation = 0
        return [self.vector(row) for row in self._trials]

    def learn(self, new):
        by_key = {tuple(np.round(u, 12)): (u, m) for u, m, _r in new} if (np := __import__("numpy")) else {}
        if not self.pop:
            self.pop = [by_key.get(tuple(np.round(t, 12)), (t, None)) for t in self._trials]
            return
        for i, trial in enumerate(self._trials):
            entry = by_key.get(tuple(np.round(trial, 12)))
            if entry is None:
                continue
            if self.rank_value(entry[1]) <= self.rank_value(self.pop[i][1]):
                self.pop[i] = entry

    def next_batch(self):
        import numpy as np

        best = min((m for _u, m in self.pop if m is not None), default=None)
        self.stopping.record_round(best)
        if self.stopping.should_stop(self.evaluations):
            return self.stop(self.stopping.reason)
        n = len(self.pop)
        if n < 4:
            return self.stop("population too small for differential evolution")
        # the last generation is cut to fit the budget: MaxEvaluations is a
        # promise about how many solves may happen, not a rounding hint
        remaining = self.remaining()
        count = n if remaining is None else min(n, remaining)
        trials = []
        for i in range(count):
            choices = [j for j in range(n) if j != i]
            a, b, c = self.rng.choice(choices, 3, replace=False)
            mutant = self.pop[a][0] + self.mutation * (self.pop[b][0] - self.pop[c][0])
            cross = self.rng.random(self.k) < self.crossover
            cross[self.rng.integers(self.k)] = True
            trial = np.where(cross, mutant, self.pop[i][0])
            trials.append(np.clip(trial, 0.0, 1.0))
        self._trials = trials
        self._generation += 1
        return [self.vector(t) for t in trials]


def make_optimizer(spec):
    """The optimizer a study specification asks for."""
    method = spec.method or {}
    name = method.get("algorithm", SCREENING)
    handler = make_handler(method.get("constraints", FEASIBILITY_RULES), method.get("penalty", 1000.0))
    stopping = Stopping(
        max_evaluations=int(method.get("max_evaluations", 100) or 0),
        tolerance=float(method.get("tolerance", 1e-4)),
        stagnation=int(method.get("stagnation", 10) or 0),
        wall_clock=float(method.get("wall_clock", 0.0) or 0.0),
    )
    population = int(method.get("population", 0) or 0)
    kwargs = dict(handler=handler, stopping=stopping, population=population)
    if name == NELDER_MEAD:
        return NelderMead(spec, **kwargs)
    if name == DIFFERENTIAL_EVOLUTION:
        return DifferentialEvolution(spec, **kwargs)
    if name == NSGA2:
        from .moo import NSGA2Planner

        return NSGA2Planner(spec, **kwargs)
    if name == SCREENING:
        return Screening(spec, **kwargs)
    raise ValueError(f"unknown optimizer {name!r}; available: {ALGORITHMS}")


##  @}
