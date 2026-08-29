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

"""Evaluate design points in several FreeCADCmd processes on this machine.

Each slot gets a private copy of the model and a job file, and runs
:mod:`femexploration.worker` under ``FreeCADCmd`` with ``subprocess.Popen`` -
not ``QProcess``, because the engine is GUI-free and this backend runs inside
the command line front end.  Results stream back on stdout, one JSON line per
point, and the engine writes the journal.

Two things the reference implementation got wrong are handled here on
purpose.  Workers start in their own session, so cancelling sends one signal
to a whole process group and the mesher and solver underneath a worker die
with it rather than living on as orphans.  And a worker that exits with points
unaccounted for does not lose them silently: they are marked ``worker_lost``
and re-queued once on another slot, because a crash is usually a bad point
but is sometimes a bad moment.
"""

__title__ = "FreeCAD FEM design exploration local parallel backend"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import json
import math
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time

from ..events import EVENT
from ..events import parse_line
from ..model import CANCELLED
from ..model import PointResult
from ..model import WORKER_LOST
from ..store import _result_from_dict
from ..worker import STUB
from .base import Backend

#: seconds a cancelled run waits for workers to finish their point in flight
#: before their process groups are terminated
CANCEL_GRACE = 5.0
#: seconds between SIGTERM and SIGKILL
KILL_GRACE = 2.0
#: how many times a point lost to a dying worker is tried again
RETRY_LIMIT = 1


def find_freecadcmd():
    """The console binary of this installation, or None."""
    try:
        import FreeCAD

        home = FreeCAD.getHomePath()
    except ImportError:
        home = ""
    candidates = []
    if home:
        candidates.append(os.path.join(home, "bin", "FreeCADCmd"))
        candidates.append(os.path.join(home, "bin", "FreeCADCmd.exe"))
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    found = shutil.which("FreeCADCmd")
    if found:
        return found
    if "FreeCADCmd" in os.path.basename(sys.executable):
        return sys.executable
    return None


class _Worker:
    """One running job: the process, its reader thread, and what it owes."""

    def __init__(self, slot, job_id, points, process, results):
        self.slot = slot
        self.job_id = job_id
        self.points = {p.point_id: p for p in points}
        self.process = process
        self.results = results
        self.owed = set(self.points)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            for raw in self.process.stdout:
                kind, payload = parse_line(raw.decode("utf-8", "replace"))
                if kind == EVENT:
                    self.results.put((self, payload))
        except (OSError, ValueError):
            pass

    def alive(self):
        return self.process.poll() is None

    def signal(self, sig):
        try:
            if hasattr(os, "killpg"):
                os.killpg(self.process.pid, sig)
            else:
                self.process.send_signal(sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass


class LocalParallelBackend(Backend):
    """Several points at once, each in its own FreeCADCmd."""

    name = "LocalParallel"

    def __init__(self, doc, cancel=None, max_parallel=0, binary=None):
        self.doc = doc
        self.cancel_token = cancel
        self._requested = int(max_parallel)
        self.binary = binary
        self.spec = None
        self.store = None
        self._evaluator = None
        self._queue = []
        self._workers = []
        self._results = queue.Queue()
        self._attempts = {}
        self._job_count = 0
        self._chunk = 1
        self._cancelled = False
        self._previous_sigterm = None
        #: seconds cleanup() waits for a point in flight before terminating
        self.cancel_grace = CANCEL_GRACE

    # -------------------------------------------------------------- set up

    def prepare(self, spec, store):
        from ..evaluate import Evaluator

        self.spec = spec
        self.store = store
        self._evaluator = Evaluator(self.doc, spec)

        self.binary = self.binary or find_freecadcmd()
        if self.binary is None:
            raise RuntimeError("cannot find FreeCADCmd; the LocalParallel backend needs it")

        store.prepare()
        # the workers open a copy: the live document stays untouched, and
        # the user's file need not even be saved
        self.doc.saveCopy(store.model_path)
        self._stub = os.path.join(store.run_dir, "worker_stub.py")
        with open(self._stub, "w") as fh:
            fh.write(STUB)

        wanted = self._requested or int(spec.max_parallel or 0)
        if wanted <= 0:
            wanted = max(1, (os.cpu_count() or 2) // 2)
        self._slots = wanted
        self._points_per_job = int(spec.points_per_job or 0)

        # a worker killed from outside would leave its children behind; make
        # sure a SIGTERM to this process takes them along
        if threading.current_thread() is threading.main_thread():
            try:
                self._previous_sigterm = signal.signal(signal.SIGTERM, self._on_sigterm)
            except (ValueError, OSError):
                self._previous_sigterm = None

    def point_id(self, values):
        return self._evaluator.point_id(values)

    @property
    def capacity(self):
        return getattr(self, "_slots", 1)

    # ---------------------------------------------------------------- work

    def submit(self, vectors):
        vectors = list(vectors)
        self._queue.extend(vectors)
        # size jobs so every slot gets work on the first pass, and FreeCAD's
        # start up is paid once per chunk rather than once per point
        if self._points_per_job > 0:
            self._chunk = self._points_per_job
        else:
            self._chunk = max(1, math.ceil(len(self._queue) / max(1, self._slots)))
        self._launch()

    def busy(self):
        return bool(self._queue) or bool(self._workers) or not self._results.empty()

    def poll(self, timeout=0.0):
        self._launch()
        out = self._drain(timeout)
        out.extend(self._reap())
        self._launch()
        return out

    def _drain(self, timeout=0.0):
        """Results queued by the reader threads, waiting up to *timeout*."""
        out = []
        deadline = time.time() + max(0.0, timeout)
        while True:
            try:
                worker, event = self._results.get(timeout=max(0.0, deadline - time.time()))
            except queue.Empty:
                break
            result = self._consume(worker, event)
            if result is not None:
                out.append(result)
            if time.time() >= deadline:
                break
        return out

    def _consume(self, worker, event):
        kind = event.get("event")
        if kind == "result":
            payload = event.get("point") or {}
            try:
                result = _result_from_dict(payload)
            except Exception:
                return None
            worker.owed.discard(result.point_id)
            return result
        if kind == "error":
            worker.error = event.get("message", "worker error")
        return None

    def _launch(self):
        if self._cancelled:
            return
        while self._queue and len(self._workers) < self._slots:
            points, self._queue = self._queue[: self._chunk], self._queue[self._chunk :]
            for vector in points:
                self._attempts[vector.point_id] = self._attempts.get(vector.point_id, 0) + 1
            self._workers.append(self._start(points))

    def _start(self, points):
        slot = self._free_slot()
        self._job_count += 1
        job_id = f"job-{self._job_count:04d}"
        job = {
            "model": self.store.model_path,
            "spec": self.spec.to_dict(),
            "run_dir": self.store.run_dir,
            "worker": f"slot-{slot}",
            "points": [{"point_id": v.point_id, "values": dict(v.values)} for v in points],
        }
        job_path = self.store.write_job(job_id, job)
        process = subprocess.Popen(
            self.command(job_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return _Worker(slot, job_id, points, process, self._results)

    def command(self, job_path):
        """The command line for one worker; overridable for tests."""
        return [self.binary, self._stub, "--", "--job", job_path]

    def _free_slot(self):
        used = {w.slot for w in self._workers}
        slot = 0
        while slot in used:
            slot += 1
        return slot

    def _reap(self):
        """Results for points a finished worker never reported."""
        out = []
        still = []
        dead = []
        for worker in self._workers:
            if worker.alive():
                still.append(worker)
            else:
                dead.append(worker)
        if not dead:
            return out
        for worker in dead:
            worker.reader.join(timeout=1.0)
        # a worker that exited cleanly may still have results waiting in the
        # queue; take them first, or every one of them would look owed
        out.extend(self._drain(0.0))
        for worker in dead:
            code = worker.process.returncode
            for point_id in sorted(worker.owed):
                vector = worker.points[point_id]
                if self._cancelled:
                    out.append(self._result_for(vector, CANCELLED, "cancelled before evaluation"))
                elif self._attempts.get(point_id, 0) <= RETRY_LIMIT:
                    # try once more, on whichever slot frees up first
                    self._queue.insert(0, vector)
                else:
                    out.append(
                        self._result_for(
                            vector,
                            WORKER_LOST,
                            getattr(worker, "error", "")
                            or f"worker exited with code {code} before reporting this point",
                        )
                    )
        self._workers = still
        return out

    def _result_for(self, vector, state, error):
        return PointResult(
            point_id=vector.point_id,
            state=state,
            inputs=self._evaluator._inputs_with_units(vector.values),
            error=error,
        )

    # -------------------------------------------------------------- stopping

    def cancel(self):
        self._cancelled = True
        if self.cancel_token is not None:
            self.cancel_token.cancel()
        self._queue = []

    def cleanup(self):
        try:
            if self._workers:
                self._stop_workers(self.cancel_grace)
        finally:
            if self._previous_sigterm is not None:
                try:
                    signal.signal(signal.SIGTERM, self._previous_sigterm)
                except (ValueError, OSError):
                    pass

    def _stop_workers(self, grace=CANCEL_GRACE):
        """Let workers finish their point, then terminate, then kill."""
        deadline = time.time() + grace
        while time.time() < deadline and any(w.alive() for w in self._workers):
            time.sleep(0.05)
        for sig, wait in ((signal.SIGTERM, KILL_GRACE), (getattr(signal, "SIGKILL", signal.SIGTERM), 0)):
            live = [w for w in self._workers if w.alive()]
            if not live:
                break
            for worker in live:
                worker.signal(sig)
            deadline = time.time() + wait
            while time.time() < deadline and any(w.alive() for w in live):
                time.sleep(0.05)
        for worker in self._workers:
            try:
                worker.process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass
        self._workers = []

    def _on_sigterm(self, signum, frame):
        self._cancelled = True
        for worker in self._workers:
            worker.signal(signal.SIGTERM)
        previous = self._previous_sigterm
        if callable(previous):
            previous(signum, frame)
        else:
            raise SystemExit(128 + signum)


##  @}
