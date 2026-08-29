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

"""Running a design exploration study from the GUI.

The same shape as the solver and mesher tools: ``prepare`` gets everything
ready, ``compute`` starts a ``QProcess``, and the task panel watches the
process.  What runs in that process is the whole engine - the command line
front end under ``FreeCADCmd`` - even for the serial backend.  FreeCAD's
document API is not thread safe, so the alternative is pumping events on the
GUI thread, and that road ends at a frozen window during every solve.

Cancellation is polite first and forceful second.  A ``CANCEL`` file asks the
engine to stop after the point in flight; if the process is still alive after
the grace period, the whole process *group* is signalled.  The group matters:
gmsh and the solver are grandchildren, and killing only the direct child
leaves them running with nobody to read their output.
"""

__title__ = "FreeCAD FEM design exploration tools"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import json
import os
import shutil
import signal
import sys

from PySide.QtCore import QProcess
from PySide.QtCore import QTimer

import FreeCAD

from femexploration import api
from femexploration import events
from femexploration import planner as planner_module
from femexploration.cancel import CANCEL_FILE

from .objecttools import ObjectTools

#: the script FreeCADCmd runs.  ``os.setsid`` makes the worker the leader of a
#: new process group, so everything it spawns can be reaped together.
_STUB = """import os
import sys

try:
    os.setsid()
except (AttributeError, OSError):
    pass  # not POSIX, or already a session leader; cancel falls back to kill()

import femexploration.cli as cli

sys.exit(cli.main())
"""

#: seconds between asking a run to stop and terminating it
GRACE_SECONDS = 5.0
#: seconds between SIGTERM and SIGKILL
KILL_SECONDS = 2.0


class ExplorationTools(ObjectTools):
    """Drive one study in a child FreeCADCmd process."""

    name = "Design exploration"

    def __init__(self, obj):
        #: explicit design vectors to run instead of the study's plan, or None
        self.points = None
        #: the design vectors the run will evaluate, filled by prepare()
        self.plan = []
        self.spec = None
        self.run_dir = None
        self.binary = None
        self._stub = None
        self._points_file = None
        self._console = False
        self._buffer = events.LineBuffer()
        super().__init__(obj)
        self.process.setProcessChannelMode(QProcess.SeparateChannels)

    def _create_working_directory(self):
        # The base class would allocate a fresh temporary directory per
        # session.  A study's run directory has to be stable across sessions:
        # it is where resume-from-cache looks, so a new one every time would
        # silently re-solve everything.  api.run_directory already honours the
        # study's own setting and the FEM working directory preferences.
        return None

    # ------------------------------------------------------------- lifecycle

    def prepare(self):
        """Validate the study, copy the model and lay out the run directory.

        Raises on anything that would make the run pointless, so the panel can
        say why before starting a process that would only fail.  Reads the
        document but never mutates it, and must be called on the GUI thread:
        ``saveCopy`` is a document operation.
        """
        self.spec = api.study_spec(self.obj)
        self.run_dir = api.run_directory(self.obj, self.spec)
        os.makedirs(self.run_dir, exist_ok=True)

        # a stale flag from a previous run would stop this one immediately
        try:
            os.remove(os.path.join(self.run_dir, CANCEL_FILE))
        except OSError:
            pass

        self.binary = find_freecadcmd()
        if self.binary is None:
            raise RuntimeError(
                "cannot find FreeCADCmd next to this FreeCAD; the study cannot be "
                "run out of process"
            )

        self.model_file = os.path.join(self.run_dir, "model.FCStd")
        self.obj.Document.saveCopy(self.model_file)

        self._stub = os.path.join(self.run_dir, "run_study.py")
        with open(self._stub, "w") as fh:
            fh.write(_STUB)

        if self.points is not None:
            self._points_file = os.path.join(self.run_dir, "points.json")
            with open(self._points_file, "w") as fh:
                json.dump([dict(p) for p in self.points], fh, indent=1)
        else:
            self._points_file = None

        self.plan = [
            dict(vector.values)
            for vector in planner_module.make_planner(self.spec, self.points).initial_batch()
        ]

    def arguments(self):
        """The command line, for the log and for tests."""
        args = [
            self._stub,
            "--",
            self.model_file,
            "--study",
            self.obj.Name,
            "--run-dir",
            self.run_dir,
        ]
        if self._points_file:
            args += ["--points", self._points_file]
        return args

    def compute(self):
        if not self._stub:
            raise RuntimeError("prepare() has not been called")
        self._buffer = events.LineBuffer()
        self.process.start(self.binary, self.arguments())

    def update_properties(self):
        """Bring the run's journal into the document's design point table."""
        if not self.run_dir:
            return None
        index = os.path.join(self.run_dir, "index.jsonl")
        if not os.path.exists(index):
            return None
        return api.import_run(self.obj, self.run_dir)

    def _process_finished(self, code, status):
        # unlike a solver, a study that stopped early still produced results
        # worth having - the points it finished before being cancelled
        try:
            self.update_properties()
        except Exception as exc:
            FreeCAD.Console.PrintError(f"could not load the study's results: {exc}\n")

    # ---------------------------------------------------------------- cancel

    def running(self):
        return self.process.state() != QProcess.ProcessState.NotRunning

    def cancel(self, grace=GRACE_SECONDS):
        """Ask the run to stop, then make it.

        Writes the cancel flag so the engine finishes the point in flight and
        records it, and arms the escalation timers.  Returns immediately.
        """
        if self.run_dir:
            with open(os.path.join(self.run_dir, CANCEL_FILE), "w") as fh:
                fh.write("cancelled\n")
        if self.running():
            QTimer.singleShot(int(grace * 1000), self.terminate)

    def terminate(self):
        """SIGTERM the process group, and SIGKILL it if that is ignored."""
        if not self.running():
            return
        self._signal_group(signal.SIGTERM)
        QTimer.singleShot(int(KILL_SECONDS * 1000), self.kill)

    def kill(self):
        if not self.running():
            return
        if hasattr(signal, "SIGKILL"):
            self._signal_group(signal.SIGKILL)
        self.process.kill()

    def _signal_group(self, sig):
        pid = int(self.process.processId() or 0)
        if pid <= 0:
            return
        try:
            if hasattr(os, "killpg"):
                # the stub made itself a session leader, so pid == pgid
                os.killpg(pid, sig)
            else:
                os.kill(pid, sig)
        except ProcessLookupError:
            pass
        except OSError as exc:
            FreeCAD.Console.PrintWarning(f"could not signal the study's processes: {exc}\n")

    # ------------------------------------------------------------- reporting

    def read_events(self):
        """Progress records and text lines the child wrote since last time.

        Returns a list of ``(kind, payload)`` pairs as classified by
        :mod:`femexploration.events`; noise is dropped here.
        """
        data = self.process.readAllStandardOutput().data().decode("utf-8", "replace")
        out = []
        for line in self._buffer.feed(data):
            kind, payload = events.parse_line(line)
            if kind != events.NOISE:
                out.append((kind, payload))
        return out

    def drain(self):
        """Whatever is left in the buffer once the process has ended."""
        out = []
        for line in self._buffer.flush():
            kind, payload = events.parse_line(line)
            if kind != events.NOISE:
                out.append((kind, payload))
        return out

    def attach_console(self):
        """Report progress to the report view, for a run with no panel."""
        if self._console:
            return
        self._console = True
        labels = {}
        if self.spec is not None:
            labels = {v.name: v.label or v.name for v in self.spec.variables}
            labels.update({r.name: r.label or r.name for r in self.spec.responses})

        def on_output():
            for kind, payload in self.read_events():
                if kind == events.EVENT:
                    _print_event(payload, labels)
                else:
                    FreeCAD.Console.PrintLog(payload + "\n")

        def on_error():
            data = self.process.readAllStandardError().data().decode("utf-8", "replace")
            if data.strip():
                FreeCAD.Console.PrintLog(data)

        def on_finished(code, status):
            for kind, payload in self.drain():
                if kind == events.EVENT:
                    _print_event(payload, labels)
            FreeCAD.Console.PrintMessage(
                f"{self.obj.Label}: run finished with exit code {code}; "
                f"results are in {self.run_dir}\n"
            )

        self.process.readyReadStandardOutput.connect(on_output)
        self.process.readyReadStandardError.connect(on_error)
        self.process.finished.connect(on_finished)

    def version(self):
        from femexploration.version import FORMAT_VERSION

        return f"femexploration format {FORMAT_VERSION}, FreeCADCmd at {self.binary or find_freecadcmd()}"


def _print_event(event, labels):
    kind = event.get("event")
    if kind == "progress":
        point = event.get("point")
        if isinstance(point, dict):
            FreeCAD.Console.PrintMessage(
                events.describe_point(
                    point, event.get("completed"), event.get("total"), labels
                )
                + "\n"
            )
        elif event.get("message"):
            FreeCAD.Console.PrintLog(str(event["message"]) + "\n")
    elif kind == "error":
        FreeCAD.Console.PrintError(f"{event.get('message')}\n")
    elif kind == "finished":
        FreeCAD.Console.PrintMessage(f"{event.get('summary', 'finished')}\n")


def find_freecadcmd():
    """Locate the console binary belonging to this installation."""
    candidate = os.path.join(FreeCAD.getHomePath(), "bin", "FreeCADCmd")
    if os.path.exists(candidate):
        return candidate
    if sys.platform == "win32":
        candidate += ".exe"
        if os.path.exists(candidate):
            return candidate
    found = shutil.which("FreeCADCmd")
    if found:
        return found
    if "FreeCADCmd" in os.path.basename(FreeCAD.ConfigGet("ExeName") or ""):
        return sys.executable
    return None


##  @}
