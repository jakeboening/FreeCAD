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

Three collapsible boxes: the settings and a preview of the plan, the run
(log, progress, elapsed time - the same ``ExplorationRun.ui`` every study
panel uses), and the design point table filling in as results arrive.

The preview matters because a full factorial grows alarmingly - a third
variable at five levels turns twenty-five points into a hundred and
twenty-five - and the cheapest place to notice that is before any solving
starts.

The run itself is :class:`femtools.explorationtools.ExplorationTools`: a
child ``FreeCADCmd`` process, the same one the command line and the agent
bridge use, so all three exercise one code path and the GUI never blocks.
"""

__title__ = "FreeCAD FEM exploration sweep task panel"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import os
import shutil

import FreeCAD
import FreeCADGui

from PySide import QtCore
from PySide import QtGui

from femexploration import api
from femexploration import events
from femexploration.algorithms import sampling
from femguiutils import exploration_designpoints as dp
from femtools import explorationtools
from femtools.femutils import getOutputWinColor

from . import base_femlogtaskpanel


class _TaskPanel(base_femlogtaskpanel._BaseLogTaskPanel):
    """Configure, preview and run a sweep."""

    def __init__(self, obj):
        explorationtools.ExplorationTools(obj)

        self.settings = self._build_settings(obj)
        self.run_widget = FreeCADGui.PySideUic.loadUi(
            FreeCAD.getHomePath() + "Mod/Fem/Resources/ui/ExplorationRun.ui"
        )
        self.results = dp.DesignPointView(run_dir=api.last_run_directory(obj))

        # the base class picks its log and timer labels off ``self.form``, and
        # the dialog wants a list so each widget becomes its own box.  Hand it
        # the run widget first, then the list.
        self.form = self.run_widget
        super().__init__(obj)
        self.form = [self.settings, self.run_widget, self.results]

        self._labels = {}
        self._total = 0
        self.setup_connections()
        self.get_object_params()
        self.load_results()
        self.refresh()

    # ------------------------------------------------------------- widgets

    #: window title of the settings box
    settings_title = "Parametric Sweep"
    #: label of the plan combo
    plan_label = "Plan"

    def current_plan(self, obj):
        return getattr(obj, "Plan", "")

    def plan_names(self):
        from femobjects.exploration_sweep import PLANS

        return list(PLANS)

    def _build_settings(self, obj):
        widget = QtGui.QWidget()
        widget.setWindowTitle(self.settings_title)
        layout = QtGui.QVBoxLayout(widget)

        form = QtGui.QFormLayout()
        self.plan = QtGui.QComboBox()
        self.plan.addItems(self.plan_names())
        self.plan.setCurrentText(self.current_plan(obj))
        form.addRow(self.plan_label, self.plan)
        self._extra_settings(form, obj)

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
        return widget

    def _extra_settings(self, form, obj):
        """Hook for study types with settings beyond the plan name."""

    def plan_points(self, spec):
        """The design vectors the current settings produce."""
        return sampling.build_plan(self.plan.currentText(), spec.active_variables)

    def setup_connections(self):
        super().setup_connections()
        self.plan.currentTextChanged.connect(self.refresh)
        self.run_widget.pb_stop.clicked.connect(self.stop)
        self.run_widget.pb_clear.clicked.connect(self.clear_results)
        self.results.runRequested.connect(self.run_points)
        self.results.currentRequested.connect(self.apply_point)

    # ------------------------------------------------------------ settings

    def get_object_params(self):
        self.plan.setCurrentText(self.current_plan(self.obj))
        self.backend.setCurrentText(self.obj.Backend)
        self.resume.setChecked(bool(self.obj.ResumeFromCache))

    def set_object_params(self):
        if hasattr(self.obj, "Plan"):
            self.obj.Plan = self.plan.currentText()
        self.obj.Backend = self.backend.currentText()
        self.obj.ResumeFromCache = self.resume.isChecked()

    # ------------------------------------------------------------- preview

    def refresh(self, *args):
        """Show the plan without solving anything."""
        try:
            spec = api.study_spec(self.obj)
        except Exception as exc:
            self.preview.setText(f"<b>Cannot run:</b> {exc}")
            self.table.clear()
            self.table.setRowCount(0)
            return

        variables = spec.active_variables
        try:
            plan = self.plan_points(spec)
        except Exception as exc:
            self.preview.setText(f"<b>Cannot run:</b> {exc}")
            self.table.setRowCount(0)
            return

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
                self.table.setItem(r, c, QtGui.QTableWidgetItem(f"{point[var.name]:g}"))
        self.table.resizeColumnsToContents()

        self._configure_results(spec)

    def _configure_results(self, spec):
        columns = dp.columns_from_spec(spec)
        self._labels = {c.key: c.label for c in columns}
        if [c.key for c in self.results.model.columns()] != [c.key for c in columns]:
            self.results.set_columns(columns)
        self.results.set_defaults({v.name: [float(v.nominal), v.unit] for v in spec.active_variables})

    # --------------------------------------------------------------- results

    def load_results(self):
        """Fill the table from the document's design point object, if any."""
        table = None
        for child in getattr(self.obj, "Group", []):
            if getattr(getattr(child, "Proxy", None), "Type", "") == "Fem::ExplorationDesignPoints":
                table = child
                break
        if table is None:
            return
        columns = dp.columns_from_table(table)
        if columns:
            self.results.set_columns(columns)
            self._labels = {c.key: c.label for c in columns}
        self.results.set_rows(table.Proxy.rows(table))
        self.results.run_dir = table.RunDirectory or self.results.run_dir

    # ------------------------------------------------------------------ run

    def run_process(self):
        """Start a run of the whole plan."""
        self.tool.points = None
        self._start()

    def run_points(self, points):
        """Run just these design vectors: the table's Update action."""
        self.tool.points = list(points)
        self._start()

    def _start(self):
        if self.tool.running():
            FreeCAD.Console.PrintWarning("A run is already in progress\n")
            return
        self.set_object_params()
        self.text_log.clear()

        # prepare() copies the document, which is a document operation and
        # therefore stays on the GUI thread; it takes well under a second.
        try:
            self.tool.prepare()
        except Exception as exc:
            self.write_log(f"Cannot run: {exc}\n", QtGui.QColor(getOutputWinColor("Error")))
            self._status(f"Cannot run: {exc}")
            return

        self._configure_results(self.tool.spec)
        self.results.run_dir = self.tool.run_dir
        self._total = len(self.tool.plan)
        bar = self.run_widget.pb_progress
        bar.setRange(0, 0)
        bar.setFormat("preparing...")
        self.run_widget.pb_stop.setEnabled(True)
        self.run_widget.pb_clear.setEnabled(False)
        self._status(
            f"Running {self._total} design points... this window stays usable while it works."
        )
        self.write_log(
            f"run directory: {self.tool.run_dir}\n", QtGui.QColor(getOutputWinColor("Text"))
        )

        self.elapsed.start()
        self.update_timer_text()
        self.timer.start(100)
        self.tool.compute()

    def stop(self):
        """Ask the run to stop, and only kill it if it will not."""
        if not self.tool.running():
            return
        self.tool.cancel()
        self._status("Cancellation requested; finishing the current design point...")
        self.write_log(
            "Cancellation requested; finishing the current design point...\n",
            QtGui.QColor(getOutputWinColor("Warning")),
        )

    # --------------------------------------------------------------- output

    def write_output(self):
        for kind, payload in self.tool.read_events():
            self._dispatch(kind, payload)

    def _handle(self, line):
        """Interpret one line of the child's output.  Never raises."""
        kind, payload = events.parse_line(line)
        if kind == events.NOISE:
            return
        try:
            self._dispatch(kind, payload)
        except Exception as exc:
            self.write_log(f"{line}   [could not be read: {exc}]\n", QtGui.QColor(getOutputWinColor("Error")))

    def _dispatch(self, kind, payload):
        if kind == events.TEXT:
            self.write_log(payload + "\n", QtGui.QColor(getOutputWinColor("Logging")))
            return
        try:
            self._handle_event(payload)
        except Exception as exc:
            self.write_log(
                f"{payload}   [could not be read: {exc}]\n", QtGui.QColor(getOutputWinColor("Error"))
            )

    def _handle_event(self, event):
        kind = event.get("event")
        if kind == "progress":
            self._on_progress(event)
        elif kind == "finished":
            summary = event.get("summary", "finished")
            self._status(summary)
            self.write_log(summary + "\n", QtGui.QColor(getOutputWinColor("Text")))
        elif kind == "error":
            self._status(f"Error: {event.get('message')}")
            self.write_log(f"ERROR: {event.get('message')}\n", QtGui.QColor(getOutputWinColor("Error")))
        elif kind == "started":
            self.write_log(f"Started {event.get('study')}\n", QtGui.QColor(getOutputWinColor("Text")))
        else:
            self.write_log(f"{event}\n", QtGui.QColor(getOutputWinColor("Logging")))

    def _on_progress(self, event):
        total = event.get("total") or self._total
        completed = event.get("completed", 0)
        bar = self.run_widget.pb_progress
        if total:
            bar.setRange(0, total)
            bar.setValue(completed)
            bar.setFormat(f"%p% - {completed} of {total} design points")

        point = event.get("point")
        if isinstance(point, dict):
            self.results.upsert(point)
            self._status(events.describe_point(point, completed, total, self._labels))
            if point.get("state") != "completed":
                self.write_log(
                    f"{point.get('id', '')}: {point.get('state')} {point.get('error', '')}\n",
                    QtGui.QColor(getOutputWinColor("Error")),
                )
        elif event.get("message"):
            self.write_log(f"{event['message']}\n", QtGui.QColor(getOutputWinColor("Logging")))

    def _status(self, text):
        self.run_widget.l_status.setText(text)

    def process_finished(self, code, status):
        for kind, payload in self.tool.drain():
            self._dispatch(kind, payload)
        super().process_finished(code, status)

        self.run_widget.pb_stop.setEnabled(False)
        self.run_widget.pb_clear.setEnabled(True)
        bar = self.run_widget.pb_progress
        if bar.maximum() == 0:
            bar.setRange(0, 1)
            bar.setValue(0)

        # the tool has already loaded the journal into the document; show it
        planned = self._total
        self.load_results()
        count = self.results.model.rowCount()
        note = ""
        if count > planned > 0:
            note = (
                f"  ({count - planned} from earlier runs are also in the table; "
                "use Clear results to start fresh)"
            )
        text = f"Loaded {count} design points.{note}"
        if code != 0:
            text += f"  The run exited with code {code}."
        self._status(text)

    def process_failed(self, error):
        super().process_failed(error)
        self.run_widget.pb_stop.setEnabled(False)
        self.run_widget.pb_clear.setEnabled(True)

    # -------------------------------------------------------------- actions

    def apply_point(self, row):
        try:
            api.apply_point(self.obj, row.get("inputs", {}))
        except Exception as exc:
            QtGui.QMessageBox.warning(self.run_widget, "Could not apply the design point", str(exc))
            return
        self._status(f"Model set to design point {row.get('point_id', '')}")

    def clear_results(self):
        """Delete the run directory, so the next run starts from nothing."""
        run_dir = api.last_run_directory(self.obj)
        if not os.path.isdir(run_dir):
            self._status("There are no stored results to clear.")
            return

        reply = QtGui.QMessageBox.question(
            self.run_widget,
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
            self._status(f"Could not clear the results: {exc}")
            return
        self.results.model.clear()
        self._status("Stored results cleared.")

    # ---------------------------------------------------------- task dialog

    def reject(self):
        if self.tool.running():
            # the base class would kill the process outright, orphaning the
            # mesher and solver underneath it; ask nicely first
            self.stop()
            return None
        self.timer.stop()
        QtGui.QApplication.restoreOverrideCursor()
        return base_femlogtaskpanel.base_femtaskpanel._BaseTaskPanel.reject(self)


##  @}
