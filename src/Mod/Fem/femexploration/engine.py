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

"""The study driver: plan, evaluate, record, repeat.

One loop serves sweeps, DOEs and optimizers alike, because the planner decides
*what* to evaluate and the backend decides *where*.  The engine only sequences
them and owns the things neither should: the cache, the failure budget, the
progress reports and cancellation.
"""

__title__ = "FreeCAD FEM design exploration engine"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import time

from .cancel import CancelToken
from .model import CANCELLED
from .model import COMPLETED
from .model import PointResult
from .model import Progress

#: If the first few points all fail the model itself is broken, and grinding
#: through the remaining hundreds to prove it wastes an afternoon.  A genuinely
#: infeasible corner of the design space, by contrast, tends to fail later and
#: intermittently - hence a leading run rather than a simple rate.
_LEADING_FAILURE_LIMIT = 5


class StudyResult:
    """What a completed run produced."""

    def __init__(self, spec, results, cancelled=False, aborted_reason=""):
        self.spec = spec
        self.results = results
        self.cancelled = cancelled
        self.aborted_reason = aborted_reason

    @property
    def completed(self):
        return [r for r in self.results if r.state == COMPLETED]

    @property
    def failed(self):
        return [r for r in self.results if r.failed]

    @property
    def feasible(self):
        return [r for r in self.results if r.feasible]

    def best(self, objective_name=None):
        """The feasible point with the lowest objective value."""
        candidates = self.feasible or self.completed
        scored = [r for r in candidates if r.objectives]
        if not scored:
            return None
        if objective_name is None:
            objective_name = next(iter(scored[0].objectives))
        scored = [r for r in scored if objective_name in r.objectives]
        if not scored:
            return None
        return min(scored, key=lambda r: r.objectives[objective_name])

    def __len__(self):
        return len(self.results)

    def summary(self):
        return (
            f"{len(self.completed)} completed, {len(self.failed)} failed"
            + (", cancelled" if self.cancelled else "")
            + (f", aborted: {self.aborted_reason}" if self.aborted_reason else "")
        )


def _summarise(result, reused=False):
    """The compact form of a result that a progress consumer can render."""
    return {
        "id": result.point_id,
        "state": result.state,
        "reused": reused,
        "inputs": {name: value[0] if isinstance(value, (list, tuple)) else value
                   for name, value in result.inputs.items()},
        "outputs": dict(result.outputs),
        "error": result.error,
        "duration_s": round(result.duration_s, 2),
    }


def run_study(spec, store, backend, planner, progress=None, cancel=None):
    """Drive one study to completion.

    :param spec: the specification, already validated
    :param store: a :class:`femexploration.store.PointStore`
    :param backend: where points are evaluated
    :param planner: what points to evaluate
    :param progress: optional callable taking a :class:`Progress`
    :param cancel: optional :class:`femexploration.cancel.CancelToken`
    """
    cancel = cancel or CancelToken(store.run_dir)
    started = time.time()

    store.prepare()
    store.write_study(
        spec,
        extra={
            "backend": backend.name,
            "sampler_impl": getattr(planner, "implementation", type(planner).__name__),
        },
    )
    backend.prepare(spec, store)

    cached = store.results_by_id() if spec.resume_from_cache else {}
    results = []
    produced = {}   # point id -> result, for everything this run has seen
    failures = 0
    leading_failures = 0
    aborted_reason = ""

    def report(message="", point=None):
        if progress is None:
            return
        best = None
        scored = [r for r in results if r.objectives]
        if scored:
            key = next(iter(scored[0].objectives))
            best_result = min(
                (r for r in scored if key in r.objectives),
                key=lambda r: r.objectives[key],
                default=None,
            )
            if best_result is not None:
                best = best_result.objectives[key]
        progress(
            Progress(
                completed=len(results),
                total=planner.total_estimate() or 0,
                failed=failures,
                running=0,
                best_objective=best,
                message=message,
                elapsed_s=time.time() - started,
                point=point,
            )
        )

    try:
        batch = planner.initial_batch()
        while batch:
            if cancel.cancelled:
                break

            pending = []
            issued = set()
            for vector in batch:
                if not vector.point_id:
                    vector.point_id = backend.point_id(vector.values)
                if vector.point_id in issued:
                    # a population method can propose the same point twice
                    # (two trials clipped to one corner); solve it once
                    continue
                issued.add(vector.point_id)
                if vector.point_id in produced:
                    # proposed again later in the same run; the planner
                    # already has this result and the table needs no twin
                    report(f"reused {vector.point_id}", point=_summarise(produced[vector.point_id], reused=True))
                    continue
                hit = cached.get(vector.point_id)
                if hit is not None and hit.state == COMPLETED:
                    # already computed against this exact model; reuse it
                    results.append(hit)
                    produced[vector.point_id] = hit
                    report(f"reused {vector.point_id}", point=_summarise(hit, reused=True))
                    continue
                pending.append(vector)

            if pending:
                backend.submit(pending)

            while backend.busy():
                if cancel.cancelled:
                    backend.cancel()
                    break

                for result in backend.poll(timeout=0.1):
                    store.write_point(result)
                    results.append(result)
                    produced[result.point_id] = result

                    if result.state == CANCELLED:
                        continue
                    if result.failed:
                        failures += 1
                        leading_failures += 1
                    else:
                        leading_failures = 0

                    report(f"{result.point_id} {result.state}", point=_summarise(result))

                if leading_failures >= _LEADING_FAILURE_LIMIT:
                    aborted_reason = (
                        f"the first {leading_failures} design points all failed, "
                        "which usually means the model itself cannot solve"
                    )
                    backend.cancel()
                    break
                if spec.failure_limit and failures > spec.failure_limit:
                    aborted_reason = f"more than {spec.failure_limit} design points failed"
                    backend.cancel()
                    break

            if aborted_reason or cancel.cancelled:
                break

            planner.observe(results)
            batch = planner.next_batch()
    finally:
        backend.cleanup()

    report("finished")
    return StudyResult(spec, results, cancelled=cancel.cancelled, aborted_reason=aborted_reason)


##  @}
