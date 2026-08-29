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

"""Multi-objective optimization: NSGA-II.

Non-dominated sorting, crowding distance, binary tournament selection,
simulated binary crossover and polynomial mutation - the standard
components, in numpy, working in the unit cube like every other planner
here.  Constraints use constraint-domination: a feasible point dominates an
infeasible one, and two infeasible points compare by total violation.
Failed evaluations rank behind everything.

One generation is one batch, so the whole population solves in parallel.
The result is not a single best point but a front, and the Pareto chart is
how it is read.
"""

__title__ = "FreeCAD FEM design exploration multi-objective optimizer"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

from ..model import COMPLETED
from . import sampling
from .optimize import OptimizerPlanner


def objective_vector(result, objectives):
    """The folded objective values of a completed point, or None."""
    if result.state != COMPLETED:
        return None
    try:
        return [float(result.objectives[o.name]) for o in objectives]
    except KeyError:
        return None


def dominates(a, b):
    """Constraint domination of two entries ``(objectives, violation)``."""
    fa, va = a
    fb, vb = b
    if fa is None or fb is None:
        return fa is not None and fb is None
    if va > 0.0 or vb > 0.0:
        if va == 0.0:
            return True
        if vb == 0.0:
            return False
        return va < vb
    return all(x <= y for x, y in zip(fa, fb)) and any(x < y for x, y in zip(fa, fb))


def non_dominated_sort(entries):
    """Fronts of indices, best first."""
    n = len(entries)
    dominated_by = [0] * n
    dominates_list = [[] for _ in range(n)]
    fronts = [[]]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            if dominates(entries[i], entries[j]):
                dominates_list[i].append(j)
            elif dominates(entries[j], entries[i]):
                dominated_by[i] += 1
        if dominated_by[i] == 0:
            fronts[0].append(i)
    current = 0
    while fronts[current]:
        nxt = []
        for i in fronts[current]:
            for j in dominates_list[i]:
                dominated_by[j] -= 1
                if dominated_by[j] == 0:
                    nxt.append(j)
        current += 1
        fronts.append(nxt)
    return [f for f in fronts if f]


def crowding_distance(objective_rows):
    """Crowding distance of each row; boundary rows get infinity."""
    import numpy as np

    n = len(objective_rows)
    if n == 0:
        return []
    f = np.array(objective_rows, dtype=float)
    distance = np.zeros(n)
    if n <= 2:
        distance[:] = np.inf
        return list(distance)
    for m in range(f.shape[1]):
        order = np.argsort(f[:, m])
        distance[order[0]] = distance[order[-1]] = np.inf
        span = f[order[-1], m] - f[order[0], m]
        if span <= 0:
            continue
        for idx in range(1, n - 1):
            distance[order[idx]] += (f[order[idx + 1], m] - f[order[idx - 1], m]) / span
    return list(distance)


def pareto_front(entries):
    """Indices of the non-dominated feasible entries, for charts."""
    if not entries:
        return []
    return non_dominated_sort(entries)[0]


def sbx_crossover(p1, p2, rng, eta=15.0, probability=0.9):
    """Simulated binary crossover of two unit-cube parents."""
    import numpy as np

    c1, c2 = p1.copy(), p2.copy()
    if rng.random() > probability:
        return c1, c2
    for j in range(len(p1)):
        if rng.random() > 0.5 or abs(p1[j] - p2[j]) < 1e-12:
            continue
        u = rng.random()
        beta = (2.0 * u) ** (1.0 / (eta + 1.0)) if u <= 0.5 else (1.0 / (2.0 * (1.0 - u))) ** (1.0 / (eta + 1.0))
        c1[j] = 0.5 * ((1 + beta) * p1[j] + (1 - beta) * p2[j])
        c2[j] = 0.5 * ((1 - beta) * p1[j] + (1 + beta) * p2[j])
    return np.clip(c1, 0.0, 1.0), np.clip(c2, 0.0, 1.0)


def polynomial_mutation(u, rng, eta=20.0, probability=None):
    import numpy as np

    k = len(u)
    probability = 1.0 / k if probability is None else probability
    out = u.copy()
    for j in range(k):
        if rng.random() > probability:
            continue
        r = rng.random()
        if r < 0.5:
            delta = (2.0 * r) ** (1.0 / (eta + 1.0)) - 1.0
        else:
            delta = 1.0 - (2.0 * (1.0 - r)) ** (1.0 / (eta + 1.0))
        out[j] = out[j] + delta
    return np.clip(out, 0.0, 1.0)


class NSGA2Planner(OptimizerPlanner):
    """NSGA-II as a planner: one generation per batch."""

    implementation = "numpy NSGA2"

    def __init__(self, spec, **kwargs):
        super().__init__(spec, **kwargs)
        self.pop = []          # list of (u, objectives or None, violation, merit)
        self._offspring = []
        self._front_size_history = []

    @property
    def parallelism(self):
        return self.population

    def _entry(self, u, merit, result):
        f = objective_vector(result, self.objectives)
        violation = result.total_violation if result.state == COMPLETED else float("inf")
        return (u, f, violation, merit)

    def initial_batch(self):
        import numpy as np

        u = sampling.unit_latin_hypercube(self.population, self.k, self.rng)
        u[0] = np.array(self.encode({v.name: v.nominal for v in self.variables}))
        self._offspring = [row for row in u]
        return [self.vector(row) for row in self._offspring]

    def learn(self, new):
        import numpy as np

        by_key = {tuple(np.round(u, 12)): (u, m, r) for u, m, r in new}
        arrived = []
        for trial in self._offspring:
            entry = by_key.get(tuple(np.round(trial, 12)))
            if entry is not None:
                arrived.append(self._entry(*entry))
        combined = self.pop + arrived
        self.pop = self._select(combined, self.population)

    def _select(self, combined, size):
        if not combined:
            return []
        entries = [(f, v) for _u, f, v, _m in combined]
        fronts = non_dominated_sort(entries)
        chosen = []
        for front in fronts:
            if len(chosen) + len(front) <= size:
                chosen.extend(front)
                continue
            rows = [combined[i][1] if combined[i][1] is not None else [float("inf")] * len(self.objectives)
                    for i in front]
            distance = crowding_distance(rows)
            order = sorted(range(len(front)), key=lambda i: -distance[i])
            chosen.extend(front[i] for i in order[: size - len(chosen)])
            break
        return [combined[i] for i in chosen]

    def front(self):
        """The current non-dominated feasible members of the population."""
        entries = [(f, v) for _u, f, v, _m in self.pop]
        if not entries:
            return []
        return [self.pop[i] for i in non_dominated_sort(entries)[0] if self.pop[i][1] is not None and self.pop[i][2] == 0.0]

    def _tournament(self, ranks, distance):
        i, j = self.rng.integers(len(self.pop), size=2)
        if ranks[i] != ranks[j]:
            return i if ranks[i] < ranks[j] else j
        return i if distance[i] >= distance[j] else j

    def next_batch(self):
        import numpy as np

        front = self.front()
        self._front_size_history.append(len(front))
        # progress for the stopping rule: the mean of the first objective on
        # the front is a crude but monotone-enough summary
        summary = float(np.mean([f[0] for _u, f, _v, _m in front])) if front else None
        self.stopping.record_round(summary)
        if self.stopping.should_stop(self.evaluations):
            return self.stop(self.stopping.reason)
        if len(self.pop) < 4:
            return self.stop("population too small")

        entries = [(f, v) for _u, f, v, _m in self.pop]
        fronts = non_dominated_sort(entries)
        ranks = [0] * len(self.pop)
        distance = [0.0] * len(self.pop)
        for r, members in enumerate(fronts):
            rows = [self.pop[i][1] if self.pop[i][1] is not None else [float("inf")] * len(self.objectives)
                    for i in members]
            crowd = crowding_distance(rows)
            for i, d in zip(members, crowd):
                ranks[i] = r
                distance[i] = d

        remaining = self.remaining()
        wanted = self.population if remaining is None else min(self.population, remaining)
        offspring = []
        while len(offspring) < wanted:
            a = self.pop[self._tournament(ranks, distance)][0]
            b = self.pop[self._tournament(ranks, distance)][0]
            c1, c2 = sbx_crossover(a, b, self.rng)
            offspring.append(polynomial_mutation(c1, self.rng))
            if len(offspring) < wanted:
                offspring.append(polynomial_mutation(c2, self.rng))
        self._offspring = offspring
        return [self.vector(u) for u in offspring]


##  @}
