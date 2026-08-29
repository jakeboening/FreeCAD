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

"""Charts over a study's design points.

A registry of chart types, each a function drawing onto a matplotlib figure
from the rows of a design point table, and the plumbing to show one from a
toolbar command in a ``Plot`` window parented to the main window - the same
arrangement the post-processing line plot uses.  matplotlib is optional in a
FreeCAD build, so the drawing code is imported only when a chart is shown and
a build without it gets a message naming Export CSV as the way out.

The data preparation is separate from the drawing and needs neither Qt nor
FreeCAD, so it is tested without either.
"""

__title__ = "FreeCAD FEM exploration charts"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

from dataclasses import dataclass
from dataclasses import field

from femexploration import model as expl_model
from femexploration import stats


@dataclass
class ChartInfo:
    name: str
    title: str
    tooltip: str
    icon: str
    draw: object
    #: which roles the chart needs data for; a study without them is refused
    needs: tuple = ("input", "response")


@dataclass
class ChartData:
    """Rows plus the column bookkeeping every chart needs."""

    rows: list
    inputs: list = field(default_factory=list)
    outputs: list = field(default_factory=list)
    objectives: list = field(default_factory=list)
    violations: list = field(default_factory=list)
    labels: dict = field(default_factory=dict)
    title: str = ""

    def label(self, key):
        return self.labels.get(key, key)

    @property
    def completed(self):
        return [r for r in self.rows if r.get("state") == expl_model.COMPLETED]


CHARTS = {}

#: windows kept alive; a Plot widget with no Python reference closes itself
_windows = []


def register_chart(name, title, tooltip, icon, draw, needs=("input", "response")):
    CHARTS[name] = ChartInfo(name, title, tooltip, icon, draw, tuple(needs))


def chart_data(rows, columns, title=""):
    """Sort the table's columns by role and attach the labels."""
    data = ChartData(rows=list(rows), title=title)
    for column in columns:
        data.labels[column.key] = column.label
        role = getattr(column, "role", "input")
        if role == "input":
            data.inputs.append(column.key)
        elif role == "response":
            data.outputs.append(column.key)
        elif role == "objective":
            data.objectives.append(column.key)
        elif role == "violation":
            data.violations.append(column.key)
    return data


def chart_data_from_table(table):
    """Chart data straight from a design point document object."""
    from . import exploration_designpoints as dp

    study = table.getParentGroup()
    title = study.Label if study is not None else table.Label
    return chart_data(table.Proxy.rows(table), dp.columns_from_table(table), title)


# --------------------------------------------------------------------- drawing


def draw_correlation(figure, data, method="pearson"):
    """A correlation matrix over inputs and outputs, values annotated.

    Inputs come first, then outputs, so the block that matters - which
    input moves which output - is the off-diagonal corner.
    """
    keys = data.inputs + data.outputs
    if len(keys) < 2:
        raise ValueError("a correlation chart needs at least two columns")
    labels, matrix = stats.correlation_matrix(data.rows, data.inputs, data.outputs, method)
    n = len(data.completed)
    if n < 3:
        raise ValueError(f"only {n} completed design points; a correlation needs at least three")

    figure.clear()
    axes = figure.add_subplot(111)
    image = axes.imshow(matrix, cmap="coolwarm", vmin=-1.0, vmax=1.0)
    names = [data.label(k) for k in labels]
    axes.set_xticks(range(len(names)))
    axes.set_yticks(range(len(names)))
    axes.set_xticklabels(names, rotation=45, ha="right")
    axes.set_yticklabels(names)
    for i in range(len(names)):
        for j in range(len(names)):
            value = matrix[i, j]
            axes.text(
                j, i, f"{value:.2f}", ha="center", va="center",
                color="white" if abs(value) > 0.6 else "black", fontsize=8,
            )
    # mark the input/output boundary
    k = len(data.inputs)
    if 0 < k < len(names):
        axes.axhline(k - 0.5, color="black", linewidth=0.8)
        axes.axvline(k - 0.5, color="black", linewidth=0.8)
    figure.colorbar(image, ax=axes, fraction=0.046, pad=0.04)
    axes.set_title(f"{data.title}: {method} correlation over {n} design points")
    figure.tight_layout()
    return axes


def draw_sensitivity(figure, data, outputs=None, max_outputs=4):
    """Standardised linear effect of every input on each output.

    One panel per output, bars sorted by magnitude, the fit's R² in the
    title.  A low R² is shown, not hidden: it means a linear picture is
    incomplete, which is exactly what the reader needs to know before
    trusting the ranking.
    """
    outputs = list(outputs or data.outputs)[:max_outputs]
    if not data.inputs or not outputs:
        raise ValueError("a sensitivity chart needs at least one input and one output")
    n = len(data.completed)
    if n < len(data.inputs) + 2:
        raise ValueError(
            f"only {n} completed design points; fitting {len(data.inputs)} inputs "
            f"needs at least {len(data.inputs) + 2}"
        )
    table = stats.sensitivity_table(data.rows, data.inputs, outputs)
    noisy = stats.mesh_convergence_warning(data.rows, outputs)

    figure.clear()
    axes_list = []
    for i, key in enumerate(outputs):
        axes = figure.add_subplot(len(outputs), 1, i + 1)
        entry = table[key]
        items = sorted(entry["coefficients"].items(), key=lambda kv: abs(kv[1]))
        names = [data.label(k) for k, _v in items]
        values = [v for _k, v in items]
        colours = ["#5aa02c" if v >= 0 else "#c0392b" for v in values]
        axes.barh(names, values, color=colours)
        axes.axvline(0, color="black", linewidth=0.8)
        title = f"{data.label(key)}: standardised effect, R² = {entry['r2']:.2f}"
        if key in noisy:
            title += f"  (varies with mesh size, r = {noisy[key]:.2f} - check convergence)"
        axes.set_title(title, fontsize=9)
        axes.set_xlabel("output std. dev. per input std. dev.")
        axes_list.append(axes)
    figure.suptitle(f"{data.title}: sensitivity over {n} design points")
    figure.tight_layout()
    return axes_list


def _merit_rows(data):
    """(index, merit, feasible) per completed row, in journal order."""
    out = []
    for i, row in enumerate(data.rows):
        if row.get("state") != expl_model.COMPLETED or not row.get("objectives"):
            continue
        objectives = row["objectives"]
        keys = data.objectives or sorted(objectives)
        try:
            merit = sum(float(objectives[k]) for k in keys if k in objectives)
        except (TypeError, ValueError):
            continue
        feasible = all(float(v or 0.0) <= 0.0 for v in row.get("violations", {}).values())
        out.append((i, merit, feasible))
    return out


def draw_history(figure, data, window=10):
    """Best feasible objective so far against evaluation order.

    Plus the fraction of infeasible designs over a moving window, so a run
    that is spending its budget outside the feasible region says so.
    """
    rows = _merit_rows(data)
    if len(rows) < 2:
        raise ValueError("a history chart needs at least two completed design points with objectives")
    figure.clear()
    axes = figure.add_subplot(211)
    best = []
    current = None
    for _i, merit, feasible in rows:
        if feasible and (current is None or merit < current):
            current = merit
        best.append(current)
    xs = list(range(1, len(rows) + 1))
    axes.plot(xs, [b if b is not None else float("nan") for b in best], drawstyle="steps-post", color="#2c5aa0")
    axes.scatter(xs, [m for _i, m, _f in rows], s=10, c=["#5aa02c" if f else "#c0392b" for _i, _m, f in rows])
    label = " + ".join(data.label(k) for k in data.objectives) if data.objectives else "objective"
    axes.set_ylabel(label)
    axes.set_title(f"{data.title}: best so far over {len(rows)} evaluations")

    below = figure.add_subplot(212, sharex=axes)
    rate = []
    for i in range(len(rows)):
        chunk = rows[max(0, i - window + 1) : i + 1]
        rate.append(100.0 * sum(1 for _i, _m, f in chunk if not f) / len(chunk))
    below.plot(xs, rate, color="#c0392b")
    below.set_ylim(0, 100)
    below.set_ylabel(f"% infeasible (last {window})")
    below.set_xlabel("evaluation")
    figure.tight_layout()
    return [axes, below]


def draw_pareto(figure, data, objectives=None, on_pick=None):
    """Two objectives against each other, the front highlighted.

    Every point is pickable: clicking one calls ``on_pick(point_id)``, which
    the command wires to the design point table so a promising design can
    be selected, opened or applied.  A Pareto plot that cannot be clicked
    is decoration.
    """
    from femexploration.algorithms import moo

    keys = list(objectives or data.objectives)
    if len(keys) < 2:
        raise ValueError("a Pareto chart needs two objectives; use the history chart for one")
    keys = keys[:2]
    points = []
    for row in data.rows:
        if row.get("state") != expl_model.COMPLETED:
            continue
        objectives_ = row.get("objectives", {})
        if not all(k in objectives_ for k in keys):
            continue
        violation = sum(float(v or 0.0) for v in row.get("violations", {}).values() if float(v or 0.0) > 0)
        points.append((row.get("point_id", ""), [float(objectives_[k]) for k in keys], violation))
    if len(points) < 2:
        raise ValueError("a Pareto chart needs at least two completed design points with both objectives")
    front = set(moo.pareto_front([(f, v) for _pid, f, v in points]))

    figure.clear()
    axes = figure.add_subplot(111)
    xs = [f[0] for _p, f, _v in points]
    ys = [f[1] for _p, f, _v in points]
    colours = ["#2c5aa0" if i in front else ("#c0392b" if v > 0 else "#9e9e9e") for i, (_p, _f, v) in enumerate(points)]
    sizes = [40 if i in front else 18 for i in range(len(points))]
    scatter = axes.scatter(xs, ys, c=colours, s=sizes, picker=5)
    ordered = sorted((points[i][1] for i in front), key=lambda f: f[0])
    if len(ordered) > 1:
        axes.plot([f[0] for f in ordered], [f[1] for f in ordered], color="#2c5aa0", linewidth=1)
    axes.set_xlabel(data.label(keys[0]))
    axes.set_ylabel(data.label(keys[1]))
    axes.set_title(f"{data.title}: {len(front)} non-dominated of {len(points)} designs (click a point to select it)")

    ids = [pid for pid, _f, _v in points]
    if on_pick is not None:
        def _picked(event):
            if event.artist is scatter and len(event.ind):
                on_pick(ids[int(event.ind[0])])

        figure.canvas.mpl_connect("pick_event", _picked)
    axes.pareto_ids = ids
    figure.tight_layout()
    return axes


def select_design_point(point_id):
    """Select *point_id* in every open design point table, and in the tree."""
    from . import exploration_designpoints as dp

    dp.select_point(point_id)


register_chart(
    "History",
    "Optimization History",
    "Best objective so far and the infeasible rate, in evaluation order",
    "FEM_ExplorationHistory",
    draw_history,
    needs=("input", "objective"),
)
register_chart(
    "Pareto",
    "Pareto Front",
    "Two objectives against each other, non-dominated designs highlighted; click a point to select it",
    "FEM_ExplorationPareto",
    draw_pareto,
    needs=("input", "objective"),
)
register_chart(
    "Correlation",
    "Correlation Matrix",
    "Shows how strongly each design variable and response move together",
    "FEM_ExplorationCorrelation",
    draw_correlation,
)
register_chart(
    "Sensitivity",
    "Sensitivity Chart",
    "Ranks the design variables by their linear effect on each response",
    "FEM_ExplorationSensitivity",
    draw_sensitivity,
)


# --------------------------------------------------------------------- showing


def find_table(study=None):
    """The design point table to chart: the selection, else the analysis'."""
    import FreeCADGui

    from femtools import explorationutils

    if study is None:
        for obj in FreeCADGui.Selection.getSelection():
            if explorationutils.is_design_points(obj):
                return obj
            if explorationutils.is_exploration_study(obj):
                study = obj
                break
    if study is None:
        import FemGui

        analysis = FemGui.getActiveAnalysis()
        studies = explorationutils.get_studies(analysis) if analysis else []
        if len(studies) != 1:
            raise ValueError(
                "select the study or design point table to chart"
                if studies
                else "the analysis has no study yet"
            )
        study = studies[0]
    for child in getattr(study, "Group", []):
        if explorationutils.is_design_points(child):
            return child
    raise ValueError(f"{study.Label} has not been run yet, so there is nothing to chart")


def plot_window(title):
    """A Plot widget parented to the main window, or a clear refusal."""
    try:
        import Plot
    except ImportError as exc:
        raise RuntimeError(
            "charts need matplotlib, which this FreeCAD build does not have; "
            "use Export CSV on the design point table instead"
        ) from exc
    import FreeCADGui
    from PySide import QtCore

    main = FreeCADGui.getMainWindow()
    window = Plot.Plot(winTitle=title, parent=main, flags=QtCore.Qt.Dialog)
    window.setWindowTitle(title)
    window.resize(int(main.size().height() * 0.6), int(main.size().height() * 0.5))
    return window


def show_chart(name, study=None):
    """Draw chart *name* for the selected (or only) study and show it."""
    info = CHARTS[name]
    table = find_table(study)
    data = chart_data_from_table(table)
    window = plot_window(f"{info.title} - {data.title}")
    if name == "Pareto":
        info.draw(window.fig, data, on_pick=select_design_point)
    else:
        info.draw(window.fig, data)
    window.canvas.draw()
    window.show()
    _windows.append(window)
    return window


##  @}
