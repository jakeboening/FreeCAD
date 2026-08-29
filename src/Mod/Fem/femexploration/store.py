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

"""On-disk storage for a study run.

The run directory is authoritative; the copy held in the document is a mirror
written once at the end.  That split is what lets several worker processes
contribute to one run without coordinating: a worker only ever writes inside
its own ``points/<id>/`` directory, and the coordinator only ever *appends* a
line to ``index.jsonl``.  No process rewrites a file another might have open.

    <run_dir>/
        study.json          the specification, plus provenance
        model.FCStd         a copy of the model taken at run start
        index.jsonl         append-only journal, one completed point per line
        jobs/<id>.json      work handed to a worker
        points/<id>/        per-point results and artefacts
        CANCEL              present once cancellation is requested

The journal being append-only also makes a crash survivable: a half-written
final line is skipped on read rather than poisoning the whole file.
"""

__title__ = "FreeCAD FEM design exploration store"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import csv
import json
import os
import platform
import tempfile
from dataclasses import asdict

from .model import COMPLETED
from .model import PointResult
from .model import StudySpec
from .version import FORMAT_VERSION
from .version import is_compatible

STUDY_FILE = "study.json"
INDEX_FILE = "index.jsonl"
MODEL_FILE = "model.FCStd"
POINTS_DIR = "points"
JOBS_DIR = "jobs"
POINT_FILE = "point.json"


class PointStore:
    """Reads and writes everything belonging to one study run."""

    def __init__(self, run_dir):
        self.run_dir = os.path.abspath(run_dir)

    # ----------------------------------------------------------------- paths

    @property
    def index_path(self):
        return os.path.join(self.run_dir, INDEX_FILE)

    @property
    def study_path(self):
        return os.path.join(self.run_dir, STUDY_FILE)

    @property
    def model_path(self):
        return os.path.join(self.run_dir, MODEL_FILE)

    def point_dir(self, point_id):
        return os.path.join(self.run_dir, POINTS_DIR, point_id)

    def point_path(self, point_id):
        return os.path.join(self.point_dir(point_id), POINT_FILE)

    def job_path(self, job_id):
        return os.path.join(self.run_dir, JOBS_DIR, f"{job_id}.json")

    # --------------------------------------------------------------- lifecycle

    def prepare(self):
        """Create the directory skeleton."""
        for path in (
            self.run_dir,
            os.path.join(self.run_dir, POINTS_DIR),
            os.path.join(self.run_dir, JOBS_DIR),
        ):
            os.makedirs(path, exist_ok=True)
        return self

    def write_study(self, spec, extra=None):
        """Record the specification and how it was run."""
        payload = spec.to_dict()
        payload["provenance"] = self._provenance()
        if extra:
            payload["provenance"].update(extra)
        self._write_json_atomic(self.study_path, payload)

    def read_study(self):
        with open(self.study_path) as fh:
            payload = json.load(fh)
        payload.pop("provenance", None)
        return StudySpec.from_dict(payload)

    @staticmethod
    def _provenance():
        """What was true when the run started, so a result can be explained.

        Recording the host and versions per run - and per point - is what turns
        "these numbers look odd" into "these numbers came from the one machine
        with a different solver build".
        """
        info = {
            "format_version": FORMAT_VERSION,
            "host": platform.node(),
            "platform": platform.platform(),
            "python": platform.python_version(),
        }
        try:
            import numpy

            info["numpy"] = numpy.__version__
        except ImportError:
            info["numpy"] = None
        try:
            import FreeCAD

            info["freecad"] = FreeCAD.Version()[0:3]
        except Exception:
            info["freecad"] = None
        return info

    # ------------------------------------------------------------------ points

    def write_point(self, result):
        """Persist one point and append it to the journal."""
        os.makedirs(self.point_dir(result.point_id), exist_ok=True)
        self._write_json_atomic(self.point_path(result.point_id), asdict(result))
        self._append_index(result)

    def read_point(self, point_id):
        path = self.point_path(point_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path) as fh:
                payload = json.load(fh)
        except (OSError, ValueError):
            return None
        if not is_compatible(payload.get("format_version", 0)):
            return None
        return _result_from_dict(payload)

    def has_result(self, point_id):
        """True when this point is already computed and reusable.

        The whole resume story is this one predicate: a run that died at
        evaluation 340 of 500 skips the first 340 because their directories are
        still there under hashes that still match.
        """
        result = self.read_point(point_id)
        return result is not None and result.state == COMPLETED

    def _append_index(self, result):
        line = json.dumps(asdict(result), sort_keys=True, default=str)
        with open(self.index_path, "a") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def read_index(self):
        """Every completed point, in the order it finished.

        A truncated final line - the signature of a crash mid-write - is
        skipped rather than raising, because losing one point is far better
        than losing the run.
        """
        if not os.path.exists(self.index_path):
            return []

        results = []
        with open(self.index_path) as fh:
            lines = fh.readlines()

        for number, line in enumerate(lines, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                if number == len(lines):
                    break  # partial write at the tail, expected after a crash
                continue  # corrupt line in the middle: skip it, keep the rest
            results.append(_result_from_dict(payload))
        return results

    def results_by_id(self):
        """Latest result per design point.

        The journal is append-only and spans runs, so a point re-evaluated
        after a model change appears more than once.  Later wins.
        """
        return {r.point_id: r for r in self.read_index()}

    def latest_results(self):
        """One row per design point, in first-seen order, most recent value.

        What a results table should show: the current state of each design
        point, not the history of every attempt.
        """
        order = []
        latest = {}
        for result in self.read_index():
            if result.point_id not in latest:
                order.append(result.point_id)
            latest[result.point_id] = result
        return [latest[pid] for pid in order]

    # --------------------------------------------------------------------- jobs

    def write_job(self, job_id, payload):
        os.makedirs(os.path.join(self.run_dir, JOBS_DIR), exist_ok=True)
        self._write_json_atomic(self.job_path(job_id), payload)
        return self.job_path(job_id)

    def read_job(self, job_id):
        with open(self.job_path(job_id)) as fh:
            return json.load(fh)

    # --------------------------------------------------------------------- csv

    def export_csv(self, path, spec=None):
        """Write the results table as CSV.

        Columns are the inputs then the outputs, with the point id and state
        first so a failed row is still self explanatory.  Headings use the
        labels the user chose, with the unit appended, rather than the internal
        object names - the file is meant to be read.
        """
        results = self.read_index()
        input_names, output_names = self._column_names(results, spec)
        headings = self._column_headings(input_names + output_names, spec)

        with open(path, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["point_id", "state"] + headings)
            for r in results:
                row = [r.point_id, r.state]
                row += [_csv_value(r.inputs.get(n)) for n in input_names]
                row += [_csv_value(r.outputs.get(n)) for n in output_names]
                writer.writerow(row)
        return path

    @staticmethod
    def _column_headings(names, spec):
        """Human readable headings, falling back to the raw name."""
        if spec is None:
            return list(names)
        pretty = {}
        for item in list(spec.active_variables) + list(spec.responses):
            label = getattr(item, "label", "") or item.name
            unit = getattr(item, "unit", "")
            pretty[item.name] = f"{label} [{unit}]" if unit else label
        return [pretty.get(name, name) for name in names]

    @staticmethod
    def _column_names(results, spec):
        if spec is not None:
            return (
                [v.name for v in spec.active_variables],
                [r.name for r in spec.responses],
            )
        inputs, outputs = [], []
        for r in results:
            for name in r.inputs:
                if name not in inputs:
                    inputs.append(name)
            for name in r.outputs:
                if name not in outputs:
                    outputs.append(name)
        return inputs, outputs

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _write_json_atomic(path, payload):
        """Write via a temporary file so a reader never sees a partial file."""
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        handle, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(handle, "w") as fh:
                json.dump(payload, fh, indent=2, sort_keys=True, default=str)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def _result_from_dict(payload):
    known = set(PointResult.__dataclass_fields__)
    return PointResult(**{k: v for k, v in payload.items() if k in known})


def _csv_value(entry):
    """Inputs are stored as ``[value, unit]``; outputs as plain numbers."""
    if isinstance(entry, (list, tuple)) and len(entry) == 2:
        return entry[0]
    return entry


##  @}
