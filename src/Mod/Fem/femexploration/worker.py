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

"""A worker process: evaluate a list of design points and report each one.

Run under ``FreeCADCmd`` with a job file::

    FreeCADCmd worker_stub.py -- --job jobs/slot-0-3.json

The job names the model to open, the study specification, the run directory
and the points to evaluate.  Every result goes to stdout as one JSON line,
``{"event": "result", "point": {...}}``, and the parent - a local backend or
a fleet host - writes the journal.  Workers never touch the shared journal
themselves: several processes appending to one file is exactly the kind of
"works until it doesn't" that a results store cannot afford.

The worker checks the run's cancel flag between points, so a cancelled study
stops after the point in flight without anyone having to kill it.
"""

__title__ = "FreeCAD FEM design exploration worker"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import argparse
import json
import platform
import sys
from dataclasses import asdict

#: the script FreeCADCmd is handed.  ``os.setsid`` puts the worker and every
#: mesher or solver it starts into one process group, so the parent can stop
#: all of them together.
STUB = """import os
import sys

try:
    os.setsid()
except (AttributeError, OSError):
    pass

import femexploration.worker as worker

sys.exit(worker.main())
"""


def emit(kind, **payload):
    payload["event"] = kind
    sys.stdout.write(json.dumps(payload, default=str) + "\n")
    sys.stdout.flush()


def _own_argv():
    argv = sys.argv[1:]
    if "--" in argv:
        return argv[argv.index("--") + 1 :]
    return argv[1:] if argv else []


def build_parser():
    parser = argparse.ArgumentParser(prog="femexploration-worker")
    parser.add_argument("--job", required=True, help="job file written by the backend")
    return parser


def run_job(job, evaluate=None):
    """Evaluate every point in *job*, emitting a result record for each.

    *evaluate* is injectable for tests; the default opens the model and runs
    the real :class:`femexploration.evaluate.Evaluator`.
    """
    from .cancel import CancelToken
    from .model import CANCELLED
    from .model import DesignVector
    from .model import PointResult
    from .model import StudySpec
    from .store import PointStore

    spec = StudySpec.from_dict(job["spec"])
    store = PointStore(job["run_dir"])
    cancel = CancelToken(store.run_dir)
    worker_name = job.get("worker", platform.node())

    if evaluate is None:
        import FreeCAD

        from .evaluate import Evaluator

        doc = FreeCAD.openDocument(job["model"])
        evaluator = Evaluator(doc, spec)

        def evaluate(vector):
            return evaluator.evaluate(vector, working_dir=store.point_dir(vector.point_id))

    emit("worker", message=f"{worker_name}: {len(job['points'])} points", worker=worker_name)

    done = 0
    for entry in job["points"]:
        vector = DesignVector(values=dict(entry["values"]), point_id=entry["point_id"])
        if cancel.cancelled:
            result = PointResult(
                point_id=vector.point_id,
                state=CANCELLED,
                inputs={k: [float(v), ""] for k, v in sorted(vector.values.items())},
            )
        else:
            result = evaluate(vector)
        result.worker = worker_name
        emit("result", point=asdict(result))
        done += 1
        if cancel.cancelled:
            break
    return done


def main(argv=None):
    args = build_parser().parse_args(argv if argv is not None else _own_argv())
    with open(args.job) as fh:
        job = json.load(fh)
    try:
        run_job(job)
    except Exception as exc:
        import traceback

        emit("error", message=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
        return 1
    emit("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())


##  @}
