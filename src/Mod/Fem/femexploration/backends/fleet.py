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

"""Evaluate design points across a femfleet cluster.

femfleet is a pull-model dispatcher: a server here hands jobs to clients on
every reachable host, and each job's artifacts come back as a tarball.  The
backend builds one job per chunk of design points, ships the run's kit -
the model, the worker stub, the shim and the job files - with a SyncSpec,
runs the server on a background thread and harvests results files as they
land.  Zero femfleet changes: the only seam is that femfleet prefixes every
job with the host's python, so the job is the shim, which finds that host's
FreeCADCmd and runs the worker under it.

femfleet is imported lazily and this module fails to import without it,
which is exactly how the backend registry decides not to offer it.
"""

__title__ = "FreeCAD FEM design exploration fleet backend"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import json
import math
import os
import shutil
import socket
import threading
import time
from pathlib import Path

from femfleet import core as fleet_core
from femfleet import server as fleet_server

from ..events import EVENT
from ..events import parse_line
from ..model import CANCELLED
from ..model import PointResult
from ..model import WORKER_LOST
from ..store import _result_from_dict
from ..worker import STUB
from .base import Backend

#: subdirectory of every host's workdir that holds one run's kit
KIT_ROOT = "fem_exploration"
#: seconds each point is allowed on a host before the job is killed
DEFAULT_POINT_TIMEOUT = 1800.0
#: preference holding the cluster configuration path
PREF_GROUP = "User parameter:BaseApp/Preferences/Mod/Fem/Exploration"
PREF_KEY = "ClusterConfig"


def default_cluster_config():
    """The cluster file to use: the study's, else the preference, else ~/.config."""
    try:
        import FreeCAD

        configured = FreeCAD.ParamGet(PREF_GROUP).GetString(PREF_KEY, "")
    except ImportError:
        configured = ""
    if configured:
        return os.path.expanduser(configured)
    return os.path.expanduser("~/.config/femfleet/cluster.toml")


def port_is_free(port, host="0.0.0.0"):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, int(port)))
        except OSError:
            return False
    return True


class FleetBackend(Backend):
    """Design points as femfleet jobs, results harvested from artifacts."""

    name = "Fleet"

    def __init__(self, doc, cancel=None, config=None, run_jobs=None, points_per_job=0):
        self.doc = doc
        self.cancel_token = cancel
        self.config = config
        self._run_jobs = run_jobs or fleet_server.run_jobs
        self._requested_chunk = int(points_per_job)
        self.spec = None
        self.store = None
        self._evaluator = None
        self._queue = []
        self._jobs = {}            # job id -> {"vectors": [...], "path": results file}
        self._seen = set()         # point ids already reported
        self._offsets = {}         # results file -> bytes consumed
        self._thread = None
        self._ok = None
        self._error = ""
        self._cancelled = False
        self._batch = 0
        self.hosts = []

    # ------------------------------------------------------------------ setup

    def prepare(self, spec, store):
        from ..evaluate import Evaluator

        self.spec = spec
        self.store = store
        self._evaluator = Evaluator(self.doc, spec)
        store.prepare()

        self.config = self.config or getattr(spec, "cluster_config", "") or default_cluster_config()
        if not os.path.exists(self.config):
            raise RuntimeError(
                f"no cluster configuration at {self.config}; write a femfleet cluster.toml "
                "there or set the study's ClusterConfig property"
            )
        self.port = fleet_core.cluster_port(Path(self.config))
        if not port_is_free(self.port):
            raise RuntimeError(
                f"port {self.port} is already in use, so the femfleet server cannot start. "
                "On this machine the icecc scheduler also uses 8765; set a different "
                "[server] port in the cluster configuration"
            )
        self.hosts = fleet_core.load_hosts(Path(self.config))

        # the kit: everything a host needs, under one run-specific directory,
        # laid out identically on the server so local and remote jobs share
        # one command line
        self.run_name = os.path.basename(os.path.normpath(store.run_dir))
        self.cwd = os.path.join(store.run_dir, "fleet")
        self.kit_rel = f"{KIT_ROOT}/{self.run_name}"
        self.kit = os.path.join(self.cwd, KIT_ROOT, self.run_name)
        os.makedirs(os.path.join(self.kit, "jobs"), exist_ok=True)
        self.doc.saveCopy(os.path.join(self.kit, "model.FCStd"))
        with open(os.path.join(self.kit, "worker_stub.py"), "w") as fh:
            fh.write(STUB)
        from .. import shim

        shutil.copyfile(shim.__file__, os.path.join(self.kit, "femexploration_shim.py"))
        self.sync_specs = [
            fleet_core.SyncSpec(
                local_root=Path(self.kit),
                paths="model.FCStd worker_stub.py femexploration_shim.py jobs",
                remote_subdir=self.kit_rel,
            )
        ]
        self.chunk = self._requested_chunk or int(spec.points_per_job or 0)

    def point_id(self, values):
        return self._evaluator.point_id(values)

    @property
    def capacity(self):
        slots = sum(int(getattr(h, "client_slots", 0) or 0) for h in self.hosts)
        return max(1, slots or 4 * max(1, len(self.hosts)))

    # ------------------------------------------------------------------- work

    def submit(self, vectors):
        vectors = list(vectors)
        if not vectors:
            return
        self._queue.extend(vectors)
        if self._thread is None or not self._thread.is_alive():
            self._start_batch()

    def _start_batch(self):
        """Turn the queue into jobs and serve them on a background thread."""
        pending, self._queue = self._queue, []
        if not pending:
            return
        chunk = self.chunk or max(1, math.ceil(len(pending) / self.capacity))
        jobs = []
        for start in range(0, len(pending), chunk):
            vectors = pending[start : start + chunk]
            self._batch += 1
            job_id = f"job-{self._batch:04d}"
            payload = {
                # paths are workdir-relative on every host; the kit mirrors them
                "model": f"{self.kit_rel}/model.FCStd",
                "spec": self.spec.to_dict(),
                "run_dir": f"{self.kit_rel}/run",
                "worker": job_id,
                "points": [{"point_id": v.point_id, "values": dict(v.values)} for v in vectors],
            }
            with open(os.path.join(self.kit, "jobs", f"{job_id}.json"), "w") as fh:
                json.dump(payload, fh, indent=1)
            results_rel = f"{self.kit_rel}/jobs/{job_id}.results.jsonl"
            self._jobs[job_id] = {
                "vectors": {v.point_id: v for v in vectors},
                "path": os.path.join(self.cwd, results_rel),
            }
            timeout = (float(self.spec.timeout) or DEFAULT_POINT_TIMEOUT) * len(vectors)
            jobs.append(
                fleet_core.Job(
                    jid=job_id,
                    argv=(
                        f"{self.kit_rel}/femexploration_shim.py --job "
                        f"{self.kit_rel}/jobs/{job_id}.json --out {results_rel}"
                    ),
                    artifacts=[results_rel, f"{self.kit_rel}/run/points/*"],
                    cost=float(len(vectors)),
                    timeout_s=timeout,
                )
            )

        def serve():
            try:
                self._ok = self._run_jobs(
                    jobs,
                    config_path=Path(self.config),
                    cwd=Path(self.cwd),
                    port=self.port,
                    sync=tuple(self.sync_specs),
                    stats_path=os.path.join(self.cwd, "out", "stats.json"),
                )
            except Exception as exc:  # the engine reports it per point
                self._ok = False
                self._error = f"{type(exc).__name__}: {exc}"

        self._thread = threading.Thread(target=serve, name="femfleet-server", daemon=True)
        self._thread.start()

    def busy(self):
        if self._queue:
            return True
        if self._thread is not None and self._thread.is_alive():
            return True
        return any(self._unreported(job) for job in self._jobs.values())

    def _unreported(self, job):
        return [pid for pid in job["vectors"] if pid not in self._seen]

    def poll(self, timeout=0.0):
        deadline = time.time() + max(0.0, timeout)
        out = self._harvest()
        while not out and time.time() < deadline and self._thread is not None and self._thread.is_alive():
            time.sleep(min(0.25, max(0.0, deadline - time.time())))
            out = self._harvest()
        if self._thread is not None and not self._thread.is_alive():
            out.extend(self._harvest())
            out.extend(self._settle())
            if self._queue and not self._cancelled:
                self._start_batch()
        return out

    def _harvest(self):
        """Results appended to any job's results file since the last look."""
        out = []
        for job in self._jobs.values():
            path = job["path"]
            if not os.path.exists(path):
                continue
            offset = self._offsets.get(path, 0)
            with open(path, "rb") as fh:
                fh.seek(offset)
                data = fh.read()
            if not data:
                continue
            lines = data.decode("utf-8", "replace").split("\n")
            complete = lines[:-1]
            consumed = len("\n".join(complete).encode("utf-8")) + (1 if complete else 0)
            self._offsets[path] = offset + consumed
            for line in complete:
                kind, payload = parse_line(line)
                if kind != EVENT or payload.get("event") != "result":
                    continue
                try:
                    result = _result_from_dict(payload.get("point") or {})
                except Exception:
                    continue
                if result.point_id in self._seen or result.point_id not in job["vectors"]:
                    continue
                self._seen.add(result.point_id)
                out.append(result)
        return out

    def _settle(self):
        """Once the server has drained, account for points that never came back."""
        out = []
        for job_id, job in self._jobs.items():
            for pid in self._unreported(job):
                vector = job["vectors"][pid]
                state = CANCELLED if self._cancelled else WORKER_LOST
                error = (
                    "cancelled before the fleet reported it"
                    if self._cancelled
                    else (self._error or f"{job_id} produced no result for this point on the fleet")
                )
                self._seen.add(pid)
                out.append(
                    PointResult(
                        point_id=pid,
                        state=state,
                        inputs=self._evaluator._inputs_with_units(vector.values),
                        error=error,
                    )
                )
        return out

    # --------------------------------------------------------------- stopping

    def cancel(self):
        self._cancelled = True
        self._queue = []
        if self.cancel_token is not None:
            self.cancel_token.cancel()

    def cleanup(self):
        # femfleet's server blocks until its jobs drain and has no cancel;
        # it runs on a daemon thread, so the engine can return and jobs already
        # claimed by a host finish there, bounded by their timeout
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)


##  @}
