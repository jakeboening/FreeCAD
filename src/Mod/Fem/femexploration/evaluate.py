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

"""Evaluating one design point, end to end.

Recompute does **not** mesh and does **not** solve.  ``mesh_gmsh`` has no
``execute``, so the sequence has to be spelled out:

    apply inputs -> recompute -> mesh (if needed) -> solve -> extract

Every step is wrapped so that failure is *recorded* rather than raised.  A
design point that fails is a row in the table with a state and an error, not an
exception that takes the run down: on a 500 point study, a handful of corners
where the mesher gives up is normal, and losing the other 490 results to the
first one would be absurd.

Note the distinction the state machine keeps: ``solve_failed`` means no number
was produced, while a completed point that violates a constraint is
*infeasible*.  An optimizer must treat those differently, and conflating them -
by returning infinity for both - throws away the difference between "bad
design" and "broken model".
"""

__title__ = "FreeCAD FEM design exploration evaluation"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import platform
import time
import traceback

import FreeCAD

from femtools import membertools

from . import hashing
from .model import COMPLETED
from .model import EXPRESSION
from .model import EXTRACTOR
from .model import EXTRACT_FAILED
from .model import MESH_FAILED
from .model import MESH_ELEMENTS
from .model import MESH_NODES
from .model import PointResult
from .model import RECOMPUTE_FAILED
from .model import RESULT_STAT
from .model import SOLVE_FAILED

#: property types whose value is a Quantity and must be written as one
_QUANTITY_PREFIXES = (
    "App::PropertyLength",
    "App::PropertyDistance",
    "App::PropertyAngle",
    "App::PropertyArea",
    "App::PropertyVolume",
    "App::PropertyPressure",
    "App::PropertyForce",
    "App::PropertyTemperature",
    "App::PropertyMass",
    "App::PropertySpeed",
    "App::PropertyTime",
    "App::PropertyFrequency",
    "App::PropertyQuantity",
)

#: States meaning an object did not recompute cleanly.  FreeCAD reports a
#: failed expression binding as ``Invalid`` rather than ``Error``, so checking
#: only for the latter lets a stale value through as if it were fresh.
_BAD_STATES = ("Error", "Invalid")


def failed_state(obj):
    """The bad state *obj* is in, or an empty string."""
    for state in _BAD_STATES:
        if state in obj.State:
            return state
    return ""


_INTEGER_TYPES = (
    "App::PropertyInteger",
    "App::PropertyIntegerConstraint",
    "App::PropertyPercent",
)


def _write_quantity(obj, prop, value, unit):
    # a bare float means "already in internal units" and is silently unscaled,
    # so always go through a Quantity carrying the captured unit
    if unit:
        setattr(obj, prop, FreeCAD.Units.Quantity(float(value), unit))
    else:
        setattr(obj, prop, float(value))


def _write_integer(obj, prop, value, unit):
    setattr(obj, prop, int(round(float(value))))


def _write_float(obj, prop, value, unit):
    setattr(obj, prop, float(value))


def _write_bool(obj, prop, value, unit):
    setattr(obj, prop, bool(round(float(value))))


def _write_enumeration(obj, prop, value, unit):
    # the design vector carries the index; the variable carries the strings
    setattr(obj, prop, int(round(float(value))))


#: how long to wait for an out-of-process tool to publish its result
DEFAULT_TOOL_TIMEOUT = 120.0


def wait_for(doc, predicate, timeout=DEFAULT_TOOL_TIMEOUT):
    """Pump the event loop until *predicate* holds, or time runs out.

    FEM runs its mesher and solver as subprocesses and publishes their output
    through queued Qt signals, which are only delivered when the event loop
    turns.  Under FreeCADCmd nothing turns it for us, so anything that reads a
    result straight after ``waitForFinished`` sees the *previous* one.
    """
    try:
        from PySide import QtCore

        app = QtCore.QCoreApplication.instance()
    except Exception:
        app = None

    deadline = time.time() + timeout
    while time.time() < deadline:
        if app is not None:
            app.processEvents()
        doc.recompute()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def generate_mesh(mesh_obj, timeout=DEFAULT_TOOL_TIMEOUT):
    """Rebuild *mesh_obj* and do not return until it really is rebuilt.

    Returns the node count.  Clearing the mesh first makes "not yet
    regenerated" observable instead of silently looking like the previous
    mesh - which a solver would then happily run on, producing a plausible
    number for the wrong geometry.
    """
    import Fem

    from femtools.femutils import is_derived_from

    doc = mesh_obj.Document
    mesh_obj.FemMesh = Fem.FemMesh()

    if is_derived_from(mesh_obj, "Fem::FemMeshGmsh"):
        from femmesh import gmshtools

        error = gmshtools.GmshTools(mesh_obj).create_mesh()
        if error:
            raise RuntimeError(f"gmsh failed: {error}")
    elif is_derived_from(mesh_obj, "Fem::FemMeshNetgen"):
        from femmesh import netgentools

        netgentools.NetgenTools(mesh_obj).run(blocking=True)
    else:
        raise RuntimeError(f"do not know how to generate a {mesh_obj.Proxy.Type} mesh")

    if not wait_for(doc, lambda: mesh_obj.FemMesh.NodeCount > 0, timeout):
        raise RuntimeError("the mesher produced no nodes")
    return mesh_obj.FemMesh.NodeCount


def _leaf_blocks(data):
    """Yield the datasets inside a possibly nested VTK multiblock."""
    if data is None:
        return
    if hasattr(data, "GetNumberOfBlocks"):
        for index in range(data.GetNumberOfBlocks()):
            yield from _leaf_blocks(data.GetBlock(index))
    elif hasattr(data, "GetPointData"):
        yield data


def writer_for(type_id):
    """The one place that decides how a value reaches a property."""
    if type_id.startswith(_QUANTITY_PREFIXES):
        return _write_quantity
    if type_id in _INTEGER_TYPES:
        return _write_integer
    if type_id == "App::PropertyBool":
        return _write_bool
    if type_id == "App::PropertyEnumeration":
        return _write_enumeration
    return _write_float


class Evaluator:
    """Evaluates design points against one open document."""

    def __init__(self, doc, spec, keep_solver_dir=False):
        self.doc = doc
        self.spec = spec
        self.keep_solver_dir = keep_solver_dir
        self.analysis = doc.getObject(spec.analysis) if spec.analysis else None
        self.solver = doc.getObject(spec.solver) if spec.solver else None

    # ------------------------------------------------------------------ public

    def point_id(self, values):
        inputs = self._inputs_with_units(values)
        return hashing.point_id(self.spec.model_hash, inputs)

    def evaluate(self, vector, working_dir=None):
        """Run one design point and return its result, never raising."""
        values = dict(vector.values)
        started = time.time()
        result = PointResult(
            point_id=vector.point_id or self.point_id(values),
            inputs=self._inputs_with_units(values),
            host=platform.node(),
        )

        try:
            self._apply(values)
        except Exception as exc:
            return self._fail(result, RECOMPUTE_FAILED, exc, started)

        try:
            if not self._recompute_ok():
                return self._fail(
                    result, RECOMPUTE_FAILED, RuntimeError("recompute left objects in error"), started
                )
        except Exception as exc:
            return self._fail(result, RECOMPUTE_FAILED, exc, started)

        try:
            nodes, elements = self._mesh()
            result.outputs[MESH_NODES] = nodes
            result.outputs[MESH_ELEMENTS] = elements
        except Exception as exc:
            return self._fail(result, MESH_FAILED, exc, started)

        try:
            self._solve(working_dir)
        except Exception as exc:
            return self._fail(result, SOLVE_FAILED, exc, started)

        try:
            result.outputs.update(self._extract())
        except Exception as exc:
            return self._fail(result, EXTRACT_FAILED, exc, started)

        self._score(result)
        result.state = COMPLETED
        result.duration_s = time.time() - started
        return result

    # ----------------------------------------------------------------- helpers

    def _inputs_with_units(self, values):
        units = {v.name: v.unit for v in self.spec.variables}
        return {name: [float(value), units.get(name, "")] for name, value in sorted(values.items())}

    def _apply(self, values):
        """Write the design vector into the model."""
        by_name = {v.name: v for v in self.spec.variables}
        for name, value in values.items():
            var = by_name.get(name)
            if var is None or not var.target:
                continue
            target = self.doc.getObject(var.target)
            if target is None:
                raise RuntimeError(f"variable {name}: object {var.target} is gone")
            writer_for(var.target_type_id)(target, var.target_property, value, var.unit)

        # variables held inactive sit at their nominal value
        for var in self.spec.variables:
            if var.active or not var.target or var.name in values:
                continue
            target = self.doc.getObject(var.target)
            if target is not None:
                writer_for(var.target_type_id)(
                    target, var.target_property, var.nominal, var.unit
                )

    def _recompute_ok(self):
        self.doc.recompute()
        for obj in self.doc.Objects:
            state = failed_state(obj)
            if state:
                FreeCAD.Console.PrintError(f"{obj.Label} is {state.lower()} after recompute\n")
                return False
        return True

    def _mesh(self):
        """Generate the mesh if anything geometric moved.

        When no active variable feeds the shape there is nothing to
        regenerate, and skipping it is frequently the difference between a
        sweep taking minutes and taking hours.  When something did move the
        mesh must be rebuilt: a mesh belonging to the previous geometry maps
        onto none of the current faces, and the solver then fails obscurely
        about empty node sets rather than about the stale mesh.
        """
        if self.analysis is None:
            return 0, 0

        meshes = membertools.get_member(self.analysis, "Fem::FemMeshObject")
        if not meshes:
            # legitimate: responses may be expressions that need no solve
            return 0, 0
        if len(meshes) > 1:
            raise RuntimeError("more than one mesh in the analysis is not supported")
        mesh_obj = meshes[0]

        if self.spec.needs_remesh or mesh_obj.FemMesh.NodeCount == 0:
            self._generate_mesh(mesh_obj)

        femmesh = mesh_obj.FemMesh
        nodes = femmesh.NodeCount
        elements = femmesh.VolumeCount + femmesh.FaceCount + femmesh.EdgeCount
        if nodes == 0:
            raise RuntimeError("mesh generation produced no nodes")
        return nodes, elements

    def _generate_mesh(self, mesh_obj):
        """Rebuild the mesh for the current geometry, blocking until done.

        Deliberately not ``femexamples.manager.run_mesh_generation``: that
        skips any generator which already has nodes, which is right for the
        examples it was written for and wrong here, where the geometry differs
        at every design point.
        """
        generate_mesh(mesh_obj, timeout=self.RESULT_TIMEOUT)

    def _wait_for(self, predicate, timeout=None):
        return wait_for(
            self.doc, predicate, self.RESULT_TIMEOUT if timeout is None else timeout
        )

    def _solve(self, working_dir=None):
        """Run the solver and wait for it, whichever execution path it uses."""
        if self.solver is None:
            return

        from femsolver import run as solver_run

        # Remove whatever the previous design point left behind.  The solver
        # updates its result object *in place*, so a stale result is
        # indistinguishable from a fresh one; clearing first means any value
        # read afterwards can only have come from this solve.  Without this a
        # sweep silently reports one wrong point per run, a different one each
        # time, which is the worst kind of wrong.
        for stale in list(self._result_objects()):
            try:
                self.doc.removeObject(stale.Name)
            except Exception:
                pass
        self.doc.recompute()

        solver_run.run_fem_solver(self.solver, working_dir, blocking=True)

        # run_fem_solver only honours blocking on the tools path; the Machine
        # path is a plain thread that ignores it, so join it explicitly
        machine = solver_run._machines.get(self.solver)
        if machine is not None:
            machine.join()
            report = getattr(machine, "report", None)
            if report is not None and report.errors:
                raise RuntimeError("; ".join(report.errors))

        # ObjectTools builds the result inside a QProcess finished handler, so
        # the result is only refreshed once that signal has actually been
        # delivered.  waitForFinished returns before that happens, and reading
        # the result there gives whatever the previous design point left
        # behind - which shows up as one point in a sweep being wildly wrong,
        # a different point each run.  Wait for the process properly and let
        # the queued signal run.
        # The tools path reports failure only to the console, so a solve that
        # died looks exactly like one that worked.  Having produced no result
        # is the observable difference, and calling that a failed solve is far
        # more useful than letting extraction complain about a missing result.
        if not self._settle_solver():
            raise RuntimeError(
                "the solver produced no complete result; see the report view "
                "for its output"
            )

    #: how long to wait for a solver result to appear after the process exits
    RESULT_TIMEOUT = 120.0

    def _settle_solver(self):
        """Wait until this solve's result is present *and complete*.

        ObjectTools builds the result inside a QProcess ``finished`` handler,
        so it is only written once that queued signal is delivered - after
        ``waitForFinished`` has already returned.  Worse, the result appears
        before it is fully populated, and reading it then yields a field
        covering only part of the model: plausible looking numbers that are
        quietly too small.  In a sweep that shows up as one wrong design point
        per run, a different one each time.

        Waiting for the result to cover every mesh node is what makes the
        answer reproducible.  It is a real invariant rather than a delay: the
        solver reports at every node of the mesh it was given.
        """
        tool = getattr(self.solver, "Tool", None)
        process = getattr(tool, "process", None)
        if process is not None:
            try:
                process.waitForFinished(-1)
            except Exception:
                pass

        expected = self._mesh_node_count()
        return self._wait_for(lambda: self._result_is_complete(expected))

    def _mesh_node_count(self):
        meshes = membertools.get_member(self.analysis, "Fem::FemMeshObject")
        return meshes[0].FemMesh.NodeCount if meshes else 0

    def _result_is_complete(self, expected_nodes):
        results = self._result_objects()
        if not results:
            return False
        if not expected_nodes:
            return True
        return self._result_point_count(results[-1]) >= expected_nodes

    @staticmethod
    def _result_point_count(res_obj):
        """How many points the result actually carries."""
        if res_obj.isDerivedFrom("Fem::FemPostObject"):
            return sum(b.GetNumberOfPoints() for b in _leaf_blocks(res_obj.Data))
        try:
            return len(res_obj.NodeNumbers)
        except AttributeError:
            return 0

    def _extract(self):
        outputs = {}
        for response in self.spec.responses:
            outputs[response.name] = self._extract_one(response)
        return outputs

    def _extract_one(self, response):
        if response.source == RESULT_STAT:
            return self._extract_result_stat(response)
        if response.source == EXPRESSION:
            return self._extract_expression(response)
        if response.source == EXTRACTOR:
            return self._extract_extractor(response)
        raise RuntimeError(f"response {response.name}: unknown source {response.source!r}")

    #: How a result quantity is named in a post-processing pipeline, and which
    #: component of that array carries it.  ``-1`` means the vector magnitude.
    #: The classic result object spells the same quantities as the short keys
    #: of ``resulttools.get_all_stats``; the VTK pipeline spells them out.
    _PIPELINE_ARRAYS = {
        "U1": ("Displacement", 0),
        "U2": ("Displacement", 1),
        "U3": ("Displacement", 2),
        "Uabs": ("Displacement", -1),
        "Sabs": ("von Mises Stress", 0),
        "MaxPrin": ("Major Principal Stress", 0),
        "MidPrin": ("Intermediate Principal Stress", 0),
        "MinPrin": ("Minor Principal Stress", 0),
        "MaxShear": ("Tresca Stress", 0),
        "Peeq": ("Equivalent Plastic Strain", 0),
        "Temp": ("Temperature", 0),
        "MFlow": ("Mass Flow Rate", 0),
        "NPress": ("Network Pressure", 0),
    }

    def _result_objects(self):
        """Whatever the solver left behind, in either shape.

        The legacy CalculiX tools produce a ``Fem::FemResultObject`` carrying a
        flat ``Stats`` list; the current solver produces a
        ``Fem::FemPostPipeline`` holding VTK data.  Both are results, and a
        response should not have to care which solver was used.
        """
        if self.analysis is None:
            return []
        found = list(membertools.get_member(self.analysis, "Fem::FemResultObject"))
        found += list(membertools.get_member(self.analysis, "Fem::FemPostPipeline"))
        return found

    def _extract_result_stat(self, response):
        res_obj = None
        if response.result_object:
            res_obj = self.doc.getObject(response.result_object)
        if res_obj is None:
            results = self._result_objects()
            if results:
                index = response.load_step
                res_obj = results[index] if -len(results) <= index < len(results) else results[-1]
        if res_obj is None:
            raise RuntimeError(f"response {response.name}: no result object to read")

        if res_obj.isDerivedFrom("Fem::FemPostObject"):
            low, high = self._pipeline_range(response, res_obj)
        else:
            from femresult import resulttools

            stats = resulttools.get_stats(res_obj, response.quantity)
            if not stats:
                raise RuntimeError(
                    f"response {response.name}: {res_obj.Label} has no "
                    f"{response.quantity!r} statistic"
                )
            low, high = stats

        return self._aggregate(response.aggregate, low, high)

    def _pipeline_range(self, response, pipeline):
        """Range of a result quantity held in a post-processing pipeline."""
        try:
            array_name, component = self._PIPELINE_ARRAYS[response.quantity]
        except KeyError:
            raise RuntimeError(
                f"response {response.name}: quantity {response.quantity!r} "
                "cannot be read from a post processing pipeline"
            ) from None

        low, high = None, None
        for block in _leaf_blocks(pipeline.Data):
            array = block.GetPointData().GetArray(array_name)
            if array is None:
                continue
            block_low, block_high = array.GetRange(component)
            low = block_low if low is None else min(low, block_low)
            high = block_high if high is None else max(high, block_high)

        if low is None:
            raise RuntimeError(
                f"response {response.name}: the result has no {array_name!r} field; "
                "the solver may not have been asked to output it"
            )
        return low, high

    @staticmethod
    def _aggregate(aggregate, low, high):
        if aggregate == "Min":
            return float(low)
        if aggregate == "AbsMax":
            return float(max(abs(low), abs(high)))
        return float(high)

    def _extract_expression(self, response):
        obj = self.doc.getObject(response.name)
        if obj is None or not hasattr(obj, "Value"):
            # a spec built by hand may name no document object
            return float(
                self.doc.getObject(self.spec.analysis).evalExpression(response.expression)
            )

        # Value is expression-bound to Expression, and it keeps its previous
        # number if the binding failed.  Reading it blindly would return a
        # stale value that looks perfectly plausible, so require a binding to
        # exist at all.
        #
        # Only its presence is checked, not its text: FreeCAD reformats an
        # expression when it stores it - 7.85e-9 comes back as 7.85e-09 - so
        # comparing strings rejects perfectly good bindings.  Whether the
        # expression actually evaluated is answered by the object's state
        # below, which is the question that matters.
        bound = dict(obj.ExpressionEngine).get("Value")
        if response.expression and bound is None:
            raise RuntimeError(
                f"response {response.label or response.name}: the expression "
                f"{response.expression!r} could not be bound to Value; "
                "its value would be stale"
            )

        obj.recompute()
        state = failed_state(obj)
        if state:
            raise RuntimeError(
                f"response {response.label or response.name}: "
                f"{response.expression!r} could not be evaluated ({state.lower()}); "
                "Value still holds the previous result"
            )
        return float(obj.Value)

    def _extract_extractor(self, response):
        obj = self.doc.getObject(response.extractor)
        if obj is None:
            raise RuntimeError(f"response {response.name}: extractor {response.extractor} is gone")
        obj.recompute()
        table = obj.Table
        column = table.GetColumnByName(response.column)
        if column is None:
            raise RuntimeError(f"response {response.name}: no column {response.column!r}")
        values = [column.GetTuple(i)[0] for i in range(column.GetNumberOfTuples())]
        if not values:
            raise RuntimeError(f"response {response.name}: column {response.column!r} is empty")
        if response.reduction == "Min":
            return float(min(values))
        if response.reduction == "Mean":
            return float(sum(values) / len(values))
        return float(max(values))

    def _score(self, result):
        """Objectives and constraint violations: pure arithmetic on responses."""
        for objective in self.spec.active_objectives:
            try:
                result.objectives[objective.name] = objective.value(result.outputs)
            except KeyError:
                continue
        for constraint in self.spec.active_constraints:
            try:
                result.violations[constraint.name] = constraint.violation(result.outputs)
            except KeyError:
                continue

    @staticmethod
    def _fail(result, state, exc, started):
        result.state = state
        result.error = f"{type(exc).__name__}: {exc}"
        result.traceback = traceback.format_exc()
        result.duration_s = time.time() - started
        FreeCAD.Console.PrintError(f"design point {result.point_id} {state}: {result.error}\n")
        return result


##  @}
