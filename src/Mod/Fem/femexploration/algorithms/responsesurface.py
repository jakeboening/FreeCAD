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

"""The response surface study as a planner.

Fit a surrogate per response on an existing design point table, optimise
on the surrogate - which costs nothing - and hand back the best candidates
for real solves.  That last step is the whole point: a surrogate optimum
is a hypothesis, and only the solver can confirm it.  With more than one
round, the confirmed points are added to the training set and the surrogate
refitted, which is the cheapest form of adaptive sampling that still keeps
the honest verification step.

Candidates are chosen for value and for doubt: the surrogate's best points
by merit, then the points where a Kriging model is least sure, so the round
both exploits and learns.
"""

__title__ = "FreeCAD FEM design exploration response surface planner"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

from ..model import COMPLETED
from ..model import PointResult
from ..model import StudySpec
from ..planner import Planner
from . import optimize
from . import sampling
from . import surrogate as surrogate_module


class SurrogateEvaluator:
    """Scores a spec's objectives and constraints from surrogate predictions."""

    def __init__(self, spec, models):
        self.spec = spec
        self.models = models

    def outputs_at(self, u):
        import numpy as np

        u = np.asarray(u, dtype=float).reshape(1, -1)
        return {key: float(model.predict(u)[0]) for key, model in self.models.items()}

    def uncertainty_at(self, u):
        import numpy as np

        u = np.asarray(u, dtype=float).reshape(1, -1)
        return max((float(model.predict_std(u)[0]) for model in self.models.values()), default=0.0)

    def result_for(self, vector):
        """A PointResult as if the surrogate were the solver."""
        planner_vars = self.spec.active_variables
        by_name = {v.name: v for v in planner_vars}
        u = [sampling.encode_one(vector.values[v.name], v) for v in planner_vars]
        outputs = self.outputs_at(u)
        result = PointResult(point_id=vector.point_id or "", state=COMPLETED,
                             inputs={k: [float(x), by_name[k].unit] for k, x in vector.values.items()},
                             outputs=outputs)
        for objective in self.spec.active_objectives:
            try:
                result.objectives[objective.name] = objective.value(outputs)
            except KeyError:
                pass
        for constraint in self.spec.active_constraints:
            try:
                result.violations[constraint.name] = constraint.violation(outputs)
            except KeyError:
                pass
        return result


def optimise_on_surrogate(spec, models, evaluations=2000, seed=0):
    """Run the spec's optimizer against the surrogate; returns the planner."""
    surrogate_spec = StudySpec.from_dict(spec.to_dict())
    surrogate_spec.study_type = "Optimization"
    method = dict(spec.method or {})
    method.setdefault("algorithm", optimize.NSGA2 if len(spec.active_objectives) > 1 else optimize.DIFFERENTIAL_EVOLUTION)
    method["max_evaluations"] = int(evaluations)
    method.setdefault("population", 0)
    method["stagnation"] = int(method.get("stagnation", 20) or 0)
    surrogate_spec.method = method
    surrogate_spec.seed = seed
    planner = optimize.make_optimizer(surrogate_spec)
    scorer = SurrogateEvaluator(surrogate_spec, models)

    counter = 0
    batch = planner.initial_batch()
    results = []
    while batch and counter < evaluations * 2:
        for vector in batch:
            counter += 1
            vector.point_id = f"s{counter:06d}"
            results.append(scorer.result_for(vector))
        planner.observe(results)
        batch = planner.next_batch()
    return planner, results, scorer


class ResponseSurfacePlanner(Planner):
    """Fit, optimise on the fit, verify the best candidates for real."""

    implementation = "surrogate"

    def __init__(self, spec, training_rows, model=surrogate_module.KRIGING, candidates=5, rounds=1,
                 surrogate_evaluations=2000):
        self.spec = spec
        self.variables = list(spec.active_variables)
        self.responses = [r.name for r in spec.responses]
        self.rows = [dict(r) for r in training_rows if (r.get("state") == COMPLETED)]
        if not self.spec.active_objectives:
            raise ValueError("a response surface study needs at least one active objective")
        if len(self.rows) < len(self.variables) + 2:
            raise ValueError(
                f"only {len(self.rows)} completed design points to fit on; run a design of "
                f"experiments with at least {len(self.variables) + 2} first"
            )
        self.model_name = model
        self.candidates = int(candidates)
        self.rounds = int(rounds)
        self.surrogate_evaluations = int(surrogate_evaluations)
        self.models = {}
        self.summaries = {}
        self.round = 0
        self.verified = []
        self.implementation = f"surrogate {model}"

    def fit(self):
        keys = [v.name for v in self.variables]
        kwargs = {"seed": self.spec.seed} if self.model_name == surrogate_module.KRIGING else {}
        self.models = surrogate_module.fit_table(self.rows, keys, self.responses, self.variables,
                                                 name=self.model_name, **kwargs)
        self.summaries = {key: model.summary() for key, model in self.models.items()}
        return self.models

    def propose(self):
        """The candidates to verify: best by surrogate merit, then most uncertain."""
        from ..model import DesignVector

        self.fit()
        planner, results, scorer = optimise_on_surrogate(
            self.spec, self.models, evaluations=self.surrogate_evaluations, seed=self.spec.seed + self.round
        )
        handler = planner.handler
        objectives = planner.objectives
        scored = []
        for result in results:
            merit = handler.merit(result, objectives)
            if merit is None:
                continue
            scored.append((merit, result))
        scored.sort(key=lambda mr: mr[0])

        chosen = []
        seen = set()
        for _merit, result in scored:
            key = tuple(round(float(v[0]), 9) for _k, v in sorted(result.inputs.items()))
            if key in seen:
                continue
            seen.add(key)
            chosen.append(result)
            if len(chosen) >= max(1, self.candidates):
                break
        if self.model_name == surrogate_module.KRIGING and scored:
            # one exploratory point on top of the candidates: where the
            # surrogate is least sure among the promising region
            best_u, best_std = None, -1.0
            for _merit, result in scored[: max(50, len(scored) // 4)]:
                u = [sampling.encode_one(result.inputs[v.name][0], v) for v in self.variables]
                std = scorer.uncertainty_at(u)
                if std > best_std:
                    best_u, best_std = u, std
            if best_u is not None:
                values = sampling.decode(best_u, self.variables)
                key = tuple(round(float(values[k]), 9) for k in sorted(values))
                if key not in seen:
                    chosen.append(PointResult(point_id="", inputs={k: [float(x), ""] for k, x in values.items()}))
        return [DesignVector(values={k: float(v[0]) for k, v in r.inputs.items()}) for r in chosen]

    def initial_batch(self):
        self.round = 1
        return self.propose()

    def observe(self, results):
        known = {r.get("point_id") for r in self.rows}
        for result in results:
            if result.point_id in known or result.state != COMPLETED:
                continue
            known.add(result.point_id)
            row = {"point_id": result.point_id, "state": result.state, "inputs": dict(result.inputs),
                   "outputs": dict(result.outputs)}
            self.rows.append(row)
            self.verified.append(result)

    def next_batch(self):
        if self.round >= self.rounds:
            return []
        self.round += 1
        return self.propose()

    def total_estimate(self):
        extra = 1 if self.model_name == surrogate_module.KRIGING else 0
        return (self.candidates + extra) * self.rounds


##  @}
