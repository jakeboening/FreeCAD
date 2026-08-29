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

"""Task panel for a parametric sweep.

Two jobs: show what the sweep will do before it does it, and run it without
freezing FreeCAD.

The preview matters because a full factorial grows alarmingly - a third
variable at five levels turns twenty-five points into a hundred and
twenty-five - and the cheapest place to notice that is before any solving
starts.

The run happens in a separate FreeCADCmd process rather than on a worker
thread.  FreeCAD's document API is not thread safe, so the alternative is
pumping events on the GUI thread, which is exactly the design that makes the
reference implementation unable to run anything headlessly.  Running the same
command line front end the CLI and the agent bridge use means all three
exercise one code path.
"""

__title__ = "FreeCAD FEM exploration sweep task panel"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import json
import os
import re
import shutil

import FreeCAD
import FreeCADGui

from PySide import QtCore
from PySide import QtGui

from femexploration import api
from femexploration.algorithms import sampling
from femexploration.cancel import CANCEL_FILE

from . import base_femtaskpanel

def _format(value):
    """Render a cell: readable numbers, blank for nothing."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return str(value)


_STUB = """import femexploration.cli as cli
import sys

sys.exit(cli.main())
"""


class _TaskPanel(base_femtaskpanel._BaseTaskPanel):
    """Configure, preview and run a sweep."""

    def __init__(self, obj):
        super().__init__(obj)
        self.process = None
        self.run_dir = None
        self._buffer = ""

        self.form = QtGui.QWidget()
        self.form.setWindowTitle("Parametric Sweep")
        layout = QtGui.QVBoxLayout(self.form)

        form = QtGui.QFormLayout()
        self.plan = QtGui.QComboBox()
        from femobjects.exploration_sweep import PLANS

        self.plan.addItems(list(PLANS))
        self.plan.setCurrentText(obj.Plan)
        self.plan.currentTextChanged.connect(self.refresh)
        form.addRow("Plan", self.plan)

        self.backend = QtGui.QComboBox()
        self.backend.addItems(api.available_backends())
        self.backend.setCurrentText(obj.Backend)
        form.addRow("Run on", self.backend)

        self.resume = QtGui.QCheckBox("Reuse points already computed for this model")
        self.resume.setChecked(bool(obj.ResumeFromCache))
        form.addRow("", self.resume)
        layout.addLayout(form)

        self.preview = QtGui.QLabel()
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)

        layout.addWidget(QtGui.QLabel("<b>Planned design points</b>"))
        self.table = QtGui.QTableWidget()
        self.table.setEditTriggers(QtGui.QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setMaximumHeight(160)
        layout.addWidget(self.table)

        row = QtGui.QHBoxLayout()
        self.run_button = QtGui.QPushButton("Run sweep")
        self.run_button.clicked.connect(self.run)
        row.addWidget(self.run_button)
        self.stop_button = QtGui.QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop)
        row.addWidget(self.stop_button)
        self.clear_button = QtGui.QPushButton("Clear results")
        self.clear_button.setToolTip(
            "Delete this study's run directory.\n"
            "Results accumulate across runs, so old design points from earlier "
            "bounds stay in the table until they are cleared."
        )
        self.clear_button.clicked.connect(self.clear_results)
        row.addWidget(self.clear_button)
        row.addStretch(1)
        layout.addLayout(row)

        self.progress = QtGui.QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.status = QtGui.QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        # the results as they arrive, rather than a wall of solver chatter
        layout.addWidget(QtGui.QLabel("<b>Results</b>"))
        self.results = QtGui.QTableWidget()
        self.results.setEditTriggers(QtGui.QAbstractItemView.NoEditTriggers)
        self.results.verticalHeader().setVisible(False)
        self.results.setMinimumHeight(140)
        layout.addWidget(self.results)

        # the mesher and solver are talkative and almost none of it is
        # interesting unless something went wrong, so keep it out of the way
        self.show_detail = QtGui.QCheckBox("Show solver output")
        self.show_detail.toggled.connect(self._toggle_detail)
        layout.addWidget(self.show_detail)

        self.log = QtGui.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(160)
        self.log.setVisible(False)
        layout.addWidget(self.log)

        self._row_of = {}
        self.refresh()

    # ----------------------------------------------------------------- preview

    def refresh(self, *args):
        """Show the plan without solving anything."""
        try:
            spec = api.study_spec(self.obj)
        except Exception as exc:
            self.preview.setText(f"<b>Cannot run:</b> {exc}")
            self.table.clear()
            self.table.setRowCount(0)
            self.run_button.setEnabled(False)
            return

        self.run_button.setEnabled(True)
        variables = spec.active_variables
        plan = sampling.build_plan(self.plan.currentText(), variables)

        remesh = " Every point is re-meshed." if spec.needs_remesh else " The mesh is reused."
        self.preview.setText(
            f"<b>{len(plan)} design points</b> over {len(variables)} variable"
            f"{'' if len(variables) == 1 else 's'}, "
            f"measuring {len(spec.responses)} response"
            f"{'' if len(spec.responses) == 1 else 's'}.{remesh}"
        )

        names = [v.label or v.name for v in variables]
        self.table.setColumnCount(len(names))
        self.table.setHorizontalHeaderLabels(names)
        shown = plan[:50]
        self.table.setRowCount(len(shown))
        for r, point in enumerate(shown):
            for c, var in enumerate(variables):
                item = QtGui.QTableWidgetItem(f"{point[var.name]:g}")
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()

    # --------------------------------------------------------------------- run

    def run(self):
        obj = self.obj
        obj.Plan = self.plan.currentText()
        obj.Backend = self.backend.currentText()
        obj.ResumeFromCache = self.resume.isChecked()

        self.run_dir = api.run_directory(obj)
        os.makedirs(self.run_dir, exist_ok=True)
        # a stale flag from a previous run would stop this one immediately
        try:
            os.remove(os.path.join(self.run_dir, CANCEL_FILE))
        except OSError:
            pass

        model = os.path.join(self.run_dir, "model.FCStd")
        try:
            obj.Document.saveCopy(model)
        except Exception as exc:
            self._append(f"Cannot copy the document for the run: {exc}")
            return

        stub = os.path.join(self.run_dir, "run_sweep.py")
        with open(stub, "w") as fh:
            fh.write(_STUB)

        binary = _freecadcmd()
        if binary is None:
            self._append("Cannot find FreeCADCmd; the sweep cannot be run out of process.")
            return

        try:
            spec = api.study_spec(obj)
        except Exception as exc:
            self._status_text(f"Cannot run: {exc}")
            return
        self._prepare_results_table(spec)

        self.log.clear()
        self._buffer = ""
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.progress.setFormat("preparing...")
        self.run_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.clear_button.setEnabled(False)

        self.process = QtCore.QProcess(self.form)
        self.process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.finished.connect(self._finished)
        args = [
            stub,
            "--",
            model,
            "--study",
            obj.Name,
            "--run-dir",
            self.run_dir,
        ]
        self._status_text(
            f"Running {self.table.rowCount()} design points... "
            "this window stays usable while it works."
        )
        self._append(f"run directory: {self.run_dir}")
        self.process.start(binary, args)

    def stop(self):
        """Ask the run to stop, and only kill it if it will not."""
        if self.run_dir:
            with open(os.path.join(self.run_dir, CANCEL_FILE), "w") as fh:
                fh.write("cancelled\n")
            self._append("Cancellation requested; finishing the current design point...")
        if self.process is not None:
            QtCore.QTimer.singleShot(5000, self._force_stop)

    def _force_stop(self):
        if self.process is not None and self.process.state() != QtCore.QProcess.NotRunning:
            self._append("Still running after the grace period; terminating.")
            self.process.kill()

    # ------------------------------------------------------------------ output

    def _read(self):
        # readyRead can arrive after finished() has already cleared the process
        if self.process is None:
            return
        data = self.process.readAllStandardOutput().data().decode("utf-8", "replace")
        self._buffer += data
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._handle(line.strip())

    def _handle(self, line):
        """Interpret one line of the child's output.

        Anything that is not one of our own progress records is passed through
        to the log verbatim.  Note that "not ours" is not the same as "not
        JSON": FreeCAD itself prints bare numbers on stdout, and ``0`` parses
        perfectly well as JSON - just to an int, not to a record.  Treating any
        parseable line as an event is what turned a solver's console output
        into a flood of AttributeErrors.

        This runs from a Qt signal handler, where an exception has nowhere
        useful to go, so a malformed line must never be able to raise.
        """
        line = (line or "").strip()
        if not line:
            return
        try:
            event = json.loads(line)
        except ValueError:
            self._append(line)
            return

        if not isinstance(event, dict) or "event" not in event:
            self._append(line)
            return

        try:
            self._handle_event(event, line)
        except Exception as exc:
            self._append(f"{line}   [could not be read: {exc}]")

    def _handle_event(self, event, line):
        kind = event.get("event")
        if kind == "progress":
            self._on_progress(event)
        elif kind == "finished":
            self._status_text(event.get("summary", "finished"))
            self._append(event.get("summary", "finished"))
        elif kind == "error":
            self._status_text(f"Error: {event.get('message')}")
            self._append(f"ERROR: {event.get('message')}")
        elif kind == "started":
            self._append(f"Started {event.get('study')}")
        else:
            self._append(line)

    def _on_progress(self, event):
        total = event.get("total") or 0
        completed = event.get("completed", 0)
        if total:
            self.progress.setRange(0, total)
            self.progress.setValue(completed)
            self.progress.setFormat(f"%v of %p%% - {completed} of {total} design points")

        point = event.get("point")
        if isinstance(point, dict):
            self._add_result_row(point)
            self._status_text(self._describe_point(point, completed, total))
            # the row and the status line already say this; repeating it in the
            # detail log just pushes the solver output the user came for
            # further out of view
            if point.get("state") != "completed":
                self._append(f"{point.get('id', '')}: {point.get('error', '')}")
        elif event.get("message"):
            self._append(event["message"])

    def _describe_point(self, point, completed, total):
        """One readable line about the point that just finished."""
        inputs = ", ".join(
            f"{self._column_label(name)} {_format(value)}"
            for name, value in point.get("inputs", {}).items()
        )
        state = point.get("state", "")
        if state != "completed":
            return f"{completed} of {total}: {inputs} - {state} ({point.get('error', '')})"

        outputs = ", ".join(
            f"{self._column_label(name)} {_format(value)}"
            for name, value in point.get("outputs", {}).items()
            if not name.startswith("_")
        )
        suffix = "  (reused)" if point.get("reused") else ""
        return f"{completed} of {total}: {inputs} -> {outputs}{suffix}"

    # ------------------------------------------------------------ result table

    def _prepare_results_table(self, spec):
        """Columns follow the study, so the table reads like the CSV will."""
        self._columns = [(v.name, v.label or v.name, v.unit) for v in spec.active_variables]
        self._columns += [(r.name, r.label or r.name, r.unit) for r in spec.responses]
        self._labels = {name: label for name, label, _unit in self._columns}
        self._row_of = {}

        headings = ["State"] + [
            f"{label} [{unit}]" if unit else label for _name, label, unit in self._columns
        ]
        self.results.clear()
        self.results.setColumnCount(len(headings))
        self.results.setHorizontalHeaderLabels(headings)
        self.results.setRowCount(0)

    def _column_label(self, name):
        return getattr(self, "_labels", {}).get(name, name)

    def _add_result_row(self, point):
        point_id = point.get("id", "")
        row = self._row_of.get(point_id)
        if row is None:
            row = self.results.rowCount()
            self.results.insertRow(row)
            self._row_of[point_id] = row

        state = point.get("state", "")
        cell = QtGui.QTableWidgetItem(
            state + (" (reused)" if point.get("reused") else "")
        )
        if state != "completed":
            cell.setForeground(QtGui.QColor(200, 60, 60))
            cell.setToolTip(point.get("error", ""))
        self.results.setItem(row, 0, cell)

        values = dict(point.get("inputs", {}))
        values.update(point.get("outputs", {}))
        for column, (name, _label, _unit) in enumerate(getattr(self, "_columns", []), start=1):
            item = QtGui.QTableWidgetItem(_format(values.get(name)))
            item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            self.results.setItem(row, column, item)

        self.results.resizeColumnsToContents()
        self.results.scrollToBottom()

    def _status_text(self, text):
        self.status.setText(text)

    def _toggle_detail(self, shown):
        self.log.setVisible(shown)

    def _finished(self, code, status):
        # drain anything still buffered before letting go of the process
        self._read()
        if self._buffer.strip():
            self._handle(self._buffer.strip())
            self._buffer = ""

        self.progress.setVisible(False)
        self.run_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.clear_button.setEnabled(True)

        planned = self.results.rowCount()
        if self.run_dir and os.path.isdir(self.run_dir):
            try:
                table, results = api.import_run(self.obj, self.run_dir)
                extra = len(results) - planned
                note = ""
                if extra > 0:
                    # the journal spans runs, so points from earlier bounds are
                    # still there; say so rather than let the count look wrong
                    note = (
                        f"  ({extra} from earlier runs are also in the table; "
                        "use Clear results to start fresh)"
                    )
                self._status_text(f"Loaded {len(results)} design points into {table.Label}.{note}")
            except Exception as exc:
                self._status_text(f"Could not load the results: {exc}")
        if code != 0:
            self._status_text(self.status.text() + f"  The run exited with code {code}.")
        self.process = None

    def clear_results(self):
        """Delete the run directory, so the next run starts from nothing."""
        run_dir = api.run_directory(self.obj)
        if not os.path.isdir(run_dir):
            self._status_text("There are no stored results to clear.")
            return

        reply = QtGui.QMessageBox.question(
            self.form,
            "Clear results",
            f"Delete every stored design point in\n{run_dir}?\n\n"
            "The next run will re-evaluate everything from scratch.",
            QtGui.QMessageBox.Yes | QtGui.QMessageBox.No,
            QtGui.QMessageBox.No,
        )
        if reply != QtGui.QMessageBox.Yes:
            return

        try:
            shutil.rmtree(run_dir)
        except OSError as exc:
            self._status_text(f"Could not clear the results: {exc}")
            return

        self.results.setRowCount(0)
        self._row_of = {}
        self._status_text("Stored results cleared.")

    #: Lines the child emits that carry no information at all.  FreeCAD prints
    #: bare numbers and its own banner on stdout; repeating them in a log the
    #: user is reading to find a solver error is pure obstruction.
    _NOISE = re.compile(
        r"^\s*$"
        r"|^-?\d+(\.\d+)?$"
        r"|^FreeCAD \d"
        r"|^\(C\) \d{4}"
        r"|^FreeCAD is free and open-source"
    )

    def _append(self, text):
        """Add a line to the detail log, unless it is known noise."""
        text = (text or "").rstrip()
        if not text or self._NOISE.match(text):
            return
        self.log.appendPlainText(text)

    # ------------------------------------------------------------- task dialog

    def getStandardButtons(self):
        return int(QtGui.QDialogButtonBox.Close)

    def accept(self):
        if self.process is not None:
            FreeCAD.Console.PrintWarning("The sweep is still running.\n")
            return False
        self.obj.Plan = self.plan.currentText()
        self.obj.Backend = self.backend.currentText()
        self.obj.ResumeFromCache = self.resume.isChecked()
        return super().accept()

    def reject(self):
        if self.process is not None:
            self.stop()
            return False
        gui_doc = self.obj.ViewObject.Document
        gui_doc.resetEdit()
        return True


def _freecadcmd():
    """Locate the console binary belonging to this installation."""
    candidate = os.path.join(FreeCAD.getHomePath(), "bin", "FreeCADCmd")
    if os.path.exists(candidate):
        return candidate
    found = shutil.which("FreeCADCmd")
    if found:
        return found
    if "FreeCADCmd" in os.path.basename(FreeCAD.ConfigGet("ExeName") or ""):
        import sys

        return sys.executable
    return None


##  @}
