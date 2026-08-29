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

"""Unit tests for the FEM design exploration package.

Note the deliberate absence of a module level ``import FreeCAD``.  The schema
and algorithm layers of ``femexploration`` do not depend on FreeCAD, and this
module preserves that property so they can be exercised without a build:

    python3 -m unittest femtest.app.test_exploration.TestExplorationSchema

run from ``src/Mod/Fem``.  The FreeCAD dependent cases import FreeCAD inside
``setUp`` instead, and the whole file still runs under ``FreeCADCmd`` as part of
the ``TestFemApp`` suite.
"""

__title__ = "FreeCAD FEM design exploration tests"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

import json
import math
import os
import shutil
import sys
import tempfile
import unittest

try:
    import FreeCAD as _FreeCAD
except ImportError:  # running under plain python3, outside a FreeCAD build
    _FreeCAD = None

#: The schema and hashing cases below never need FreeCAD.  The document object
#: cases obviously do, and are skipped rather than failed when this module is
#: run standalone, so the fast loop stays usable.
needs_freecad = unittest.skipIf(_FreeCAD is None, "requires a FreeCAD build")

from femexploration import hashing
from femexploration import model
from femexploration.model import Constraint
from femexploration.model import Objective
from femexploration.model import PointResult
from femexploration.model import Response
from femexploration.model import SchemaError
from femexploration.model import StudySpec
from femexploration.model import Variable
from femexploration.version import FORMAT_VERSION


def _demo_spec():
    """A small but complete study, used by several cases below."""
    return StudySpec(
        name="CantileverSweep",
        variables=[
            Variable(
                name="Width",
                unit="mm",
                lower=500.0,
                upper=1500.0,
                nominal=1000.0,
                level_count=3,
                target="VarSet",
                target_property="Width",
                target_type_id="App::PropertyLength",
            ),
            Variable(
                name="Height",
                unit="mm",
                lower=500.0,
                upper=1500.0,
                nominal=1000.0,
                level_count=3,
                target="VarSet",
                target_property="Height",
                target_type_id="App::PropertyLength",
            ),
        ],
        responses=[
            Response(name="MaxStress", unit="MPa", quantity="Sabs", aggregate="Max"),
            Response(
                name="Mass",
                unit="kg",
                source=model.EXPRESSION,
                expression="Box.Shape.Volume * 7.85e-6",
            ),
        ],
        objectives=[Objective(name="MinMass", response="Mass")],
        constraints=[Constraint(name="StressLimit", response="MaxStress", limit=250.0)],
        seed=42,
    )


def _variable_for(spec, prop):
    """The spec variable driving property *prop* of the promoted object.

    Variables are named after their Input object, not the property they
    drive, so tests look them up by the VarSet property the promotion made.
    """
    for var in spec.active_variables:
        if var.target_property.endswith("_" + prop):
            return var
    raise KeyError(prop)


class TestExplorationSchema(unittest.TestCase):
    """The data model.  No FreeCAD, no numpy, no filesystem."""

    def test_variable_grid_linear(self):
        v = Variable(name="t", lower=2.0, upper=6.0, level_count=5)
        self.assertEqual(v.grid(), [2.0, 3.0, 4.0, 5.0, 6.0])

    def test_variable_grid_log_spans_bounds(self):
        v = Variable(name="t", lower=1.0, upper=1000.0, level_count=4, scale=model.LOG)
        grid = v.grid()
        self.assertAlmostEqual(grid[0], 1.0)
        self.assertAlmostEqual(grid[-1], 1000.0)
        # geometric spacing: each ratio equal
        ratios = [grid[i + 1] / grid[i] for i in range(len(grid) - 1)]
        for r in ratios[1:]:
            self.assertAlmostEqual(r, ratios[0])

    def test_variable_grid_integer_collapses_to_distinct_values(self):
        # asking for 9 levels between 2 and 5 can only yield 4 distinct integers
        v = Variable(name="n", var_type=model.INTEGER, lower=2, upper=5, level_count=9)
        self.assertEqual(v.grid(), [2.0, 3.0, 4.0, 5.0])

    def test_variable_grid_discrete_is_verbatim(self):
        v = Variable(name="m", var_type=model.DISCRETE, levels=[1.5, 3.0, 4.5])
        self.assertEqual(v.grid(), [1.5, 3.0, 4.5])

    def test_spec_round_trips_through_json(self):
        spec = _demo_spec().validate()
        text = spec.to_json()
        back = StudySpec.from_json(text)
        self.assertEqual(back.to_json(), text)
        # and the nested items are dataclasses again, not dicts
        self.assertIsInstance(back.variables[0], Variable)
        self.assertIsInstance(back.responses[0], Response)
        self.assertIsInstance(back.objectives[0], Objective)
        self.assertIsInstance(back.constraints[0], Constraint)

    def test_validation_rejects_inverted_bounds(self):
        with self.assertRaises(SchemaError):
            Variable(name="a", lower=5.0, upper=5.0).validate()

    def test_validation_rejects_log_scale_with_nonpositive_lower(self):
        with self.assertRaises(SchemaError):
            Variable(name="a", lower=0.0, upper=5.0, scale=model.LOG).validate()

    def test_validation_rejects_dangling_objective_response(self):
        spec = _demo_spec()
        spec.objectives[0].response = "DoesNotExist"
        with self.assertRaises(SchemaError):
            spec.validate()

    def test_validation_rejects_study_with_no_active_variables(self):
        spec = _demo_spec()
        for v in spec.variables:
            v.active = False
        with self.assertRaises(SchemaError):
            spec.validate()

    def test_reader_refuses_future_format_version(self):
        data = _demo_spec().to_dict()
        data["format_version"] = FORMAT_VERSION + 1
        with self.assertRaises(SchemaError):
            StudySpec.from_dict(data)

    def test_reader_refuses_unknown_fields(self):
        data = _demo_spec().to_dict()
        data["bogus"] = 1
        with self.assertRaises(SchemaError):
            StudySpec.from_dict(data)

    def test_objective_directions_fold_to_minimisation(self):
        responses = {"Mass": 12.4, "MaxStress": 310.0}
        self.assertAlmostEqual(Objective(name="o", response="Mass").value(responses), 12.4)
        self.assertAlmostEqual(
            Objective(name="o", response="Mass", direction=model.MAXIMIZE).value(responses), -12.4
        )
        self.assertAlmostEqual(
            Objective(
                name="o", response="MaxStress", direction=model.TARGET, target_value=200.0
            ).value(responses),
            110.0,
        )

    def test_constraint_violation(self):
        responses = {"s": 310.0}
        self.assertAlmostEqual(Constraint(name="c", response="s", limit=250.0).violation(responses), 60.0)
        self.assertEqual(Constraint(name="c", response="s", limit=400.0).violation(responses), 0.0)
        self.assertAlmostEqual(
            Constraint(name="c", response="s", operator=model.GE, limit=400.0).violation(responses),
            90.0,
        )
        # tolerance widens the admissible band
        self.assertEqual(
            Constraint(name="c", response="s", limit=250.0, tolerance=100.0).violation(responses), 0.0
        )

    def test_failed_and_infeasible_are_distinguishable(self):
        """A crashed solve must not look like a merely bad design."""
        good = PointResult("a", state=model.COMPLETED, violations={"c": 0.0})
        infeasible = PointResult("b", state=model.COMPLETED, violations={"c": 12.0})
        crashed = PointResult("c", state=model.SOLVE_FAILED)

        self.assertTrue(good.ok and good.feasible and not good.failed)
        self.assertTrue(infeasible.ok and not infeasible.feasible and not infeasible.failed)
        self.assertTrue(crashed.failed and not crashed.ok and not crashed.feasible)
        self.assertAlmostEqual(infeasible.total_violation, 12.0)

    def test_needs_remesh_follows_geometry_affecting_flags(self):
        spec = _demo_spec()
        self.assertTrue(spec.needs_remesh)
        for v in spec.variables:
            v.geometry_affecting = False
        self.assertFalse(spec.needs_remesh)


class TestExplorationHashing(unittest.TestCase):
    """Content addressing.  No FreeCAD, no filesystem."""

    def setUp(self):
        self.entries = [
            ("Box", "Part::Box", {"Length": "8000.0", "Width": "1000.0"}),
            ("Fixed", "Fem::ConstraintFixed", {"Scale": "1"}),
        ]
        self.model_hash = hashing.model_fingerprint(self.entries)
        self.inputs = {"Width": (1000.0, "mm"), "Height": (500.0, "mm")}

    def test_fingerprint_is_order_independent(self):
        self.assertEqual(hashing.model_fingerprint(reversed(self.entries)), self.model_hash)

    def test_fingerprint_changes_when_the_model_changes(self):
        changed = [("Box", "Part::Box", {"Length": "8001.0", "Width": "1000.0"}), self.entries[1]]
        self.assertNotEqual(hashing.model_fingerprint(changed), self.model_hash)

    def test_point_id_is_dict_order_independent(self):
        a = hashing.point_id(self.model_hash, self.inputs)
        b = hashing.point_id(self.model_hash, dict(reversed(list(self.inputs.items()))))
        self.assertEqual(a, b)

    def test_point_id_ignores_float_noise_below_precision(self):
        noisy = {"Width": (1000.0 + 1e-15, "mm"), "Height": (500.0, "mm")}
        self.assertEqual(
            hashing.point_id(self.model_hash, noisy), hashing.point_id(self.model_hash, self.inputs)
        )

    def test_point_id_treats_units_as_part_of_identity(self):
        metres = {"Width": (1000.0, "m"), "Height": (500.0, "mm")}
        self.assertNotEqual(
            hashing.point_id(self.model_hash, metres), hashing.point_id(self.model_hash, self.inputs)
        )

    def test_point_id_changes_when_the_model_changes(self):
        other = hashing.model_fingerprint([("Box", "Part::Box", {"Length": "9999.0"})])
        self.assertNotEqual(
            hashing.point_id(other, self.inputs), hashing.point_id(self.model_hash, self.inputs)
        )

    def test_point_id_is_a_readable_length(self):
        self.assertEqual(len(hashing.point_id(self.model_hash, self.inputs)), 12)

    def test_study_fingerprint_ignores_execution_settings(self):
        """Moving a study to another backend must not invalidate its results."""
        spec = _demo_spec()
        base = hashing.study_fingerprint(spec.to_dict())
        spec.backend = "LocalParallel"
        spec.max_parallel = 8
        spec.retain = "All"
        spec.name = "renamed"
        self.assertEqual(hashing.study_fingerprint(spec.to_dict()), base)

    def test_study_fingerprint_notices_a_changed_bound(self):
        spec = _demo_spec()
        base = hashing.study_fingerprint(spec.to_dict())
        spec.variables[0].upper = 1600.0
        self.assertNotEqual(hashing.study_fingerprint(spec.to_dict()), base)


@needs_freecad
class TestExplorationObjects(unittest.TestCase):
    """The document objects.  Needs FreeCAD, but no solver and no mesher."""

    def setUp(self):
        import FreeCAD

        import ObjectsFem

        self.FreeCAD = FreeCAD
        self.ObjectsFem = ObjectsFem
        self.doc = FreeCAD.newDocument("TestExplorationObjects")

    def tearDown(self):
        self.FreeCAD.closeDocument(self.doc.Name)

    def test_objects_are_created_with_the_expected_types(self):
        from femtools import explorationutils as eu

        pset = self.ObjectsFem.makeExplorationParameters(self.doc)
        inp = self.ObjectsFem.makeExplorationInput(self.doc)
        out = self.ObjectsFem.makeExplorationOutput(self.doc)
        self.doc.recompute()

        self.assertTrue(eu.is_exploration_parameters(pset))
        self.assertTrue(eu.is_exploration_input(inp))
        self.assertTrue(eu.is_exploration_output(out))
        # the flat typesystem workaround: the set predicates must agree
        self.assertTrue(eu.is_exploration_parameter(inp))
        self.assertTrue(eu.is_exploration_parameter(out))
        self.assertFalse(eu.is_exploration_parameter(pset))

    def test_parameter_set_refuses_foreign_children(self):
        pset = self.ObjectsFem.makeExplorationParameters(self.doc)
        inp = self.ObjectsFem.makeExplorationInput(self.doc)
        box = self.doc.addObject("Part::Box", "Box")
        self.doc.recompute()

        pset.addObject(inp)
        pset.addObject(box)
        self.assertEqual([o.Name for o in pset.Group], [inp.Name])

    def test_output_hides_the_property_groups_it_is_not_using(self):
        out = self.ObjectsFem.makeExplorationOutput(self.doc)
        self.doc.recompute()

        out.Source = model.RESULT_STAT
        self.assertNotIn("Hidden", out.getEditorMode("Quantity"))
        self.assertIn("Hidden", out.getEditorMode("Expression"))

        out.Source = model.EXPRESSION
        self.assertIn("Hidden", out.getEditorMode("Quantity"))
        self.assertNotIn("Hidden", out.getEditorMode("Expression"))

    def test_objects_survive_save_and_reopen(self):
        import tempfile
        import os

        from femtools import explorationutils as eu

        pset = self.ObjectsFem.makeExplorationParameters(self.doc)
        inp = self.ObjectsFem.makeExplorationInput(self.doc)
        inp.Unit = "mm"
        inp.Lower = 2.0
        inp.Upper = 6.0
        pset.addObject(inp)
        self.doc.recompute()

        # capture names before closing: the object proxies die with the document
        pset_name = pset.Name
        path = os.path.join(tempfile.mkdtemp(prefix="fem_expl_"), "roundtrip.FCStd")
        self.doc.saveAs(path)
        self.FreeCAD.closeDocument(self.doc.Name)

        self.doc = self.FreeCAD.openDocument(path)  # tearDown closes this one
        restored_set = self.doc.getObject(pset_name)
        self.assertIsNotNone(restored_set)
        self.assertTrue(eu.is_exploration_parameters(restored_set))

        restored = restored_set.Group
        self.assertEqual(len(restored), 1)
        self.assertTrue(eu.is_exploration_input(restored[0]))
        self.assertEqual(restored[0].Unit, "mm")
        self.assertAlmostEqual(restored[0].Lower, 2.0)
        self.assertAlmostEqual(restored[0].Upper, 6.0)


@needs_freecad
class TestExplorationPromote(unittest.TestCase):
    """Promoting a model property to a design variable."""

    def setUp(self):
        import FreeCAD

        import ObjectsFem

        self.FreeCAD = FreeCAD
        self.doc = FreeCAD.newDocument("TestExplorationPromote")
        self.box = self.doc.addObject("Part::Box", "Box")
        self.box.Length = 8000
        self.box.Width = 1000
        self.box.Height = 1000
        self.doc.recompute()
        self.analysis = ObjectsFem.makeAnalysis(self.doc, "Analysis")

    def tearDown(self):
        self.FreeCAD.closeDocument(self.doc.Name)

    def test_filter_accepts_numeric_and_rejects_the_rest(self):
        from femtools import explorationpromote as promote

        self.assertTrue(promote.is_promotable(self.box, "Length")[0])
        for prop in ("Placement", "Label", "Shape"):
            ok, reason = promote.is_promotable(self.box, prop)
            self.assertFalse(ok, f"{prop} should not be promotable")
            self.assertTrue(reason)

    def test_promote_binds_through_a_varset(self):
        from femtools import explorationpromote as promote
        from femtools import explorationutils as eu

        inp = promote.promote(self.box, "Width", self.analysis)
        self.doc.recompute()

        varset = eu.get_varset(self.analysis)
        self.assertIsNotNone(varset)
        self.assertEqual(inp.Target, varset)
        self.assertIn(inp.TargetProperty, varset.PropertiesList)
        self.assertEqual(inp.TargetTypeId, "App::PropertyLength")
        self.assertEqual(inp.Unit, "mm")
        self.assertAlmostEqual(inp.Nominal, 1000.0)
        # the model property is now driven by the variable set
        self.assertEqual(promote.bound_expression(self.box, "Width"),
                         f"{varset.Name}.{inp.TargetProperty}")

    def test_driving_the_variable_propagates_through_the_graph(self):
        """The whole point of the VarSet indirection."""
        from femtools import explorationpromote as promote
        from femtools import explorationutils as eu

        inp = promote.promote(self.box, "Width", self.analysis)
        self.doc.recompute()
        varset = eu.get_varset(self.analysis)
        before = self.box.Shape.Volume

        setattr(varset, inp.TargetProperty, self.FreeCAD.Units.Quantity(1500.0, "mm"))
        self.doc.recompute()
        self.assertAlmostEqual(self.box.Width.getValueAs("mm"), 1500.0)
        self.assertGreater(self.box.Shape.Volume, before)

    def test_units_are_converted_not_assumed(self):
        """A value given in cm must not be read as millimetres."""
        from femtools import explorationpromote as promote
        from femtools import explorationutils as eu

        inp = promote.promote(self.box, "Width", self.analysis)
        self.doc.recompute()
        varset = eu.get_varset(self.analysis)

        setattr(varset, inp.TargetProperty, self.FreeCAD.Units.Quantity(50.0, "cm"))
        self.doc.recompute()
        self.assertAlmostEqual(self.box.Width.getValueAs("mm"), 500.0)

    def test_mismatched_units_are_rejected(self):
        from femtools import explorationpromote as promote
        from femtools import explorationutils as eu

        inp = promote.promote(self.box, "Width", self.analysis)
        varset = eu.get_varset(self.analysis)
        with self.assertRaises(Exception):
            setattr(varset, inp.TargetProperty, self.FreeCAD.Units.Quantity(4.2, "kg"))

    def test_promoting_an_already_bound_property_is_refused(self):
        """Writing to an expression-bound property would be silently undone."""
        from femtools import explorationpromote as promote

        promote.promote(self.box, "Width", self.analysis)
        self.doc.recompute()
        with self.assertRaises(promote.PromotionError):
            promote.promote(self.box, "Width", self.analysis)

    def test_promotion_creates_the_parameter_set_once(self):
        from femtools import explorationpromote as promote
        from femtools import explorationutils as eu

        promote.promote(self.box, "Width", self.analysis)
        promote.promote(self.box, "Height", self.analysis)
        self.doc.recompute()

        pset = eu.get_parameter_set(self.analysis)
        self.assertIsNotNone(pset)
        self.assertEqual(len(eu.get_inputs(pset)), 2)
        # exactly one variable set and one parameter set
        varsets = [o for o in self.analysis.Group if o.isDerivedFrom("App::VarSet")]
        self.assertEqual(len(varsets), 1)

    def test_geometry_affecting_is_detected(self):
        """A box dimension feeds the shape, so a sweep over it must remesh."""
        from femtools import explorationpromote as promote

        inp = promote.promote(self.box, "Width", self.analysis)
        self.assertTrue(inp.GeometryAffecting)


@needs_freecad
@unittest.skipUnless(_FreeCAD is not None and _FreeCAD.GuiUp, "requires a GUI session")
class TestExplorationCommands(unittest.TestCase):
    """The command Activated() paths.

    These exist because the underlying helpers being correct says nothing about
    the command wrappers around them: the first version of
    FEM_ExplorationPromoteParameter raised NameError on a missing FemGui import,
    and no amount of testing promote() directly would have caught it, because
    FreeCADGui.runCommand swallows the exception into the report view.
    """

    def setUp(self):
        import FreeCAD
        import FreeCADGui
        import FemGui

        import ObjectsFem

        self.FreeCAD = FreeCAD
        self.FreeCADGui = FreeCADGui
        self.FemGui = FemGui
        self.doc = FreeCAD.newDocument("TestExplorationCommands")
        self.box = self.doc.addObject("Part::Box", "Box")
        self.doc.recompute()
        self.analysis = ObjectsFem.makeAnalysis(self.doc, "Analysis")
        FemGui.setActiveAnalysis(self.analysis)

        from femguiutils import exploration_promote

        self.module = exploration_promote
        self._real_exec = exploration_promote.PromoteDialog.exec_
        self._real_sel = exploration_promote.PromoteDialog.selected_properties

    def tearDown(self):
        self.module.PromoteDialog.exec_ = self._real_exec
        self.module.PromoteDialog.selected_properties = self._real_sel
        # closing the document clears the active analysis; setActiveAnalysis(None)
        # is rejected by the binding
        self.FreeCAD.closeDocument(self.doc.Name)

    def _accept_with(self, props):
        """Stub the modal dialog as if the user ticked *props* and pressed OK."""
        from PySide import QtGui

        self.module.PromoteDialog.exec_ = lambda _self: QtGui.QDialog.Accepted
        self.module.PromoteDialog.selected_properties = lambda _self: list(props)

    def test_promote_command_activated(self):
        from femcommands import commands
        from femtools import explorationutils as eu

        self._accept_with(["Width"])
        cmd = commands._ExplorationPromoteParameter()
        cmd.selobj = self.box
        cmd.Activated()
        self.doc.recompute()

        pset = eu.get_parameter_set(self.analysis)
        self.assertIsNotNone(pset)
        self.assertEqual(len(eu.get_inputs(pset)), 1)

    def test_promote_command_cancelled_changes_nothing(self):
        from femcommands import commands
        from femtools import explorationutils as eu
        from PySide import QtGui

        self.module.PromoteDialog.exec_ = lambda _self: QtGui.QDialog.Rejected
        cmd = commands._ExplorationPromoteParameter()
        cmd.selobj = self.box
        cmd.Activated()
        self.doc.recompute()

        self.assertIsNone(eu.get_parameter_set(self.analysis))
        self.assertEqual(self.box.ExpressionEngine, [])

    def test_parameter_set_command_is_idempotent(self):
        """A second invocation must open the existing set, not create another."""
        from femcommands import commands
        from femtools import explorationutils as eu

        cmd = commands._ExplorationParameters()
        cmd.Activated()
        self.doc.recompute()
        first = eu.get_parameter_set(self.analysis)
        self.assertIsNotNone(first)

        cmd.Activated()
        self.doc.recompute()
        sets = [o for o in self.analysis.Group if eu.is_exploration_parameters(o)]
        self.assertEqual(len(sets), 1)
        self.assertEqual(sets[0].Name, first.Name)


@needs_freecad
class TestExplorationEngine(unittest.TestCase):
    """A sweep end to end, with no solver and no GUI.

    Responses are expressions over the geometry, so this runs in under a second
    and can never be skipped for a missing binary.  It is therefore the
    regression net for the whole apply -> recompute -> extract -> store ->
    resume path.
    """

    def setUp(self):
        import FreeCAD

        import ObjectsFem
        from femtools import explorationpromote
        from femtools import explorationutils

        self.FreeCAD = FreeCAD
        self.tmp = tempfile.mkdtemp(prefix="fem_expl_engine_")
        self.run_dir = os.path.join(self.tmp, "run")

        self.doc = FreeCAD.newDocument("TestExplorationEngine")
        self.box = self.doc.addObject("Part::Box", "Box")
        self.box.Length, self.box.Width, self.box.Height = 8000, 1000, 1000
        self.doc.recompute()
        self.analysis = ObjectsFem.makeAnalysis(self.doc, "Analysis")

        for prop in ("Width", "Height"):
            var = explorationpromote.promote(self.box, prop, self.analysis)
            var.Lower, var.Upper, var.LevelCount = 500.0, 1500.0, 3
        self.doc.recompute()

        pset = explorationutils.get_parameter_set(self.analysis)
        out = ObjectsFem.makeExplorationOutput(self.doc, "Volume")
        out.Source = model.EXPRESSION
        out.Expression = "Box.Shape.Volume"
        pset.addObject(out)

        self.sweep = ObjectsFem.makeExplorationSweep(self.doc, "Sweep")
        self.sweep.ParameterSet = pset
        self.analysis.addObject(self.sweep)
        self.doc.recompute()

    def tearDown(self):
        self.FreeCAD.closeDocument(self.doc.Name)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, **kwargs):
        from femexploration import api

        return api.run_study(self.sweep, run_dir=self.run_dir, **kwargs)

    def test_sweep_runs_every_design_point(self):
        result = self._run()
        self.assertEqual(len(result.completed), 9)
        self.assertEqual(len(result.failed), 0)
        # the grid really is the cartesian product of the two grids
        widths = {r.inputs[self.sweep.ParameterSet.Group[0].Name][0] for r in result.completed}
        self.assertEqual(widths, {500.0, 1000.0, 1500.0})

    def test_results_reach_the_journal_and_the_document(self):
        from femexploration.store import PointStore
        from femtools import explorationutils

        self._run()
        self.assertEqual(len(PointStore(self.run_dir).read_index()), 9)

        tables = [o for o in self.sweep.Group if explorationutils.is_design_points(o)]
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0].Count, 9)
        self.assertEqual(len(tables[0].Proxy.rows(tables[0])), 9)
        # headings read as the user named them
        self.assertIn("Volume", list(tables[0].ColumnNames))

    def test_rerunning_reuses_every_point(self):
        """The cache is the whole resume story; if it silently misses, an
        interrupted overnight run starts from nothing."""
        from femexploration.store import PointStore

        self._run()
        first = len(PointStore(self.run_dir).read_index())

        messages = []
        self._run(progress=lambda p: messages.append(p.message))
        reused = sum(1 for m in messages if "reused" in m)

        self.assertEqual(reused, 9)
        # nothing new was written, so the journal did not grow
        self.assertEqual(len(PointStore(self.run_dir).read_index()), first)

    def test_model_fingerprint_is_stable_across_a_run(self):
        """Regression: the fingerprint once included a property whose repr is a
        memory address, so it drifted whenever the object was reallocated and
        the cache missed intermittently."""
        from femexploration import api

        before = api.study_spec(self.sweep).model_hash
        self._run()
        self.assertEqual(api.study_spec(self.sweep).model_hash, before)

    def test_editing_the_model_invalidates_the_cache(self):
        from femexploration import api

        before = api.study_spec(self.sweep).model_hash
        self.box.Length = 9000
        self.doc.recompute()
        self.assertNotEqual(api.study_spec(self.sweep).model_hash, before)

    def test_the_document_is_left_as_it_was_found(self):
        """A sweep asks a question about the model; it must not edit it."""
        width, height = self.box.Width.Value, self.box.Height.Value
        self._run()
        self.assertAlmostEqual(self.box.Width.Value, width)
        self.assertAlmostEqual(self.box.Height.Value, height)

    def test_cancellation_stops_the_run(self):
        from femexploration.cancel import CancelToken

        token = CancelToken(self.run_dir)
        token.cancel()
        result = self._run(cancel=token)
        self.assertTrue(result.cancelled)
        self.assertLess(len(result.completed), 9)

    def test_csv_export_uses_labels_and_units(self):
        from femexploration import api

        self._run()
        path = os.path.join(self.tmp, "out.csv")
        api.export_csv(path, study=self.sweep)
        header = open(path).readline()
        self.assertIn("point_id", header)
        self.assertIn("[mm]", header)

    def test_expression_reformatting_is_not_mistaken_for_a_stale_binding(self):
        """FreeCAD rewrites 7.85e-9 as 7.85e-09 when it stores an expression.

        An exact string comparison against the stored binding therefore fails
        for a perfectly valid response, which once made every design point in
        the demo fail at extraction.
        """
        import ObjectsFem
        from femtools import explorationutils

        pset = explorationutils.get_parameter_set(self.analysis)
        out = ObjectsFem.makeExplorationOutput(self.doc, "Mass")
        out.Source = model.EXPRESSION
        out.Expression = "Box.Shape.Volume * 7.85e-9"
        pset.addObject(out)
        self.doc.recompute()

        # the stored form differs textually from what we set
        stored = dict(out.ExpressionEngine).get("Value")
        self.assertIsNotNone(stored)

        result = self._run()
        self.assertEqual(len(result.completed), 9)
        self.assertTrue(all(r.outputs.get("Mass") for r in result.completed))

    def test_a_point_proposed_again_later_in_the_run_is_not_solved_twice(self):
        from femexploration import api
        from femexploration import engine
        from femexploration import planner as planner_module
        from femexploration.backends.serial import SerialBackend
        from femexploration.cancel import CancelToken
        from femexploration.store import PointStore

        spec = api.study_spec(self.sweep)
        width = _variable_for(spec, "Width").name

        class TwoBatches(planner_module.StaticPlanner):
            def __init__(self):
                super().__init__([{width: 600.0}])
                self._second = False

            def next_batch(self):
                if self._second:
                    return []
                self._second = True
                return [planner_module.DesignVector(values={width: 600.0, **{v.name: v.nominal for v in spec.active_variables if v.name != width}})]

        # fill the nominal for the first batch too
        planner = TwoBatches()
        for vector in planner._plan:
            for v in spec.active_variables:
                vector.values.setdefault(v.name, v.nominal)
        store = PointStore(self.run_dir).prepare()
        token = CancelToken(self.run_dir)
        token.reset()
        result = engine.run_study(spec, store, SerialBackend(self.doc, cancel=token), planner, cancel=token)
        self.assertEqual(len(result.results), 1)
        self.assertEqual(len(store.read_index()), 1)

    def test_a_point_proposed_twice_in_one_batch_is_solved_once(self):
        from femexploration import api

        spec = api.study_spec(self.sweep)
        width = _variable_for(spec, "Width").name
        result = self._run(points=[{width: 700.0}, {width: 700.0}, {width: 800.0}])
        self.assertEqual(len(result.results), 2)

    def test_explicit_points_run_only_those(self):
        """The table's Add and Update actions hand the engine a list."""
        from femexploration import api

        spec = api.study_spec(self.sweep)
        width = _variable_for(spec, "Width").name
        height = _variable_for(spec, "Height").name
        result = self._run(points=[{width: 700.0}])
        self.assertEqual(len(result.completed), 1)
        inputs = {k: v[0] for k, v in result.completed[0].inputs.items()}
        self.assertEqual(inputs[width], 700.0)
        # the unmentioned variable takes its nominal value
        self.assertEqual(inputs[height], 1000.0)

    def test_a_failing_response_is_recorded_not_raised(self):
        """One broken point must not take the other eight down."""
        from femtools import explorationutils

        pset = explorationutils.get_parameter_set(self.analysis)
        broken = explorationutils.get_outputs(pset)[0]
        broken.Expression = "Box.NoSuchProperty * 2"
        self.doc.recompute()

        result = self._run()
        self.assertEqual(len(result.completed), 0)
        self.assertTrue(all(r.failed for r in result.results))
        self.assertTrue(all(r.error for r in result.results))
        # and it gives up early rather than grinding through all nine
        self.assertTrue(result.aborted_reason)


@needs_freecad
class TestExplorationResultCompleteness(unittest.TestCase):
    """Guards the invariant that made sweeps reproducible.

    FEM runs its mesher and solver as subprocesses and publishes their output
    through queued Qt signals.  The result object appears *before* it is fully
    populated, so reading it too early yields a field covering only part of the
    model - numbers that look entirely plausible and are quietly too small.  In
    a sweep that surfaced as exactly one wrong design point per run, a
    different one each time, which is far more dangerous than an outright
    failure.

    These tests do not need a solver: they check the completeness predicate
    itself, which is the thing that has to be right.
    """

    def setUp(self):
        import FreeCAD

        import ObjectsFem

        self.FreeCAD = FreeCAD
        self.doc = FreeCAD.newDocument("TestResultCompleteness")
        self.analysis = ObjectsFem.makeAnalysis(self.doc, "Analysis")

    def tearDown(self):
        self.FreeCAD.closeDocument(self.doc.Name)

    def _evaluator(self):
        from femexploration.evaluate import Evaluator
        from femexploration.model import StudySpec

        spec = StudySpec(analysis=self.analysis.Name, document=self.doc.Name)
        return Evaluator(self.doc, spec)

    def test_no_result_is_not_complete(self):
        evaluator = self._evaluator()
        self.assertFalse(evaluator._result_is_complete(100))

    def test_a_partial_result_is_not_accepted(self):
        """The failure mode being guarded: fewer points than mesh nodes."""
        import ObjectsFem

        result = ObjectsFem.makeResultMechanical(self.doc)
        self.analysis.addObject(result)
        result.NodeNumbers = list(range(1, 51))
        self.doc.recompute()

        evaluator = self._evaluator()
        self.assertEqual(evaluator._result_point_count(result), 50)
        self.assertFalse(evaluator._result_is_complete(100))
        self.assertTrue(evaluator._result_is_complete(50))

    def test_completeness_is_skipped_when_there_is_no_mesh(self):
        """An expression-only study has no mesh, and must not wait forever."""
        import ObjectsFem

        result = ObjectsFem.makeResultMechanical(self.doc)
        self.analysis.addObject(result)
        self.doc.recompute()
        self.assertTrue(self._evaluator()._result_is_complete(0))

    def test_pipeline_results_are_recognised(self):
        """The current CalculiX solver returns a post pipeline, not a
        classic result object; both are results."""
        from femexploration.evaluate import Evaluator

        self.assertIn("Fem::FemPostPipeline", Evaluator._result_objects.__doc__)
        self.assertIn("U3", Evaluator._PIPELINE_ARRAYS)
        self.assertEqual(Evaluator._PIPELINE_ARRAYS["U3"], ("Displacement", 2))
        self.assertEqual(Evaluator._PIPELINE_ARRAYS["Sabs"], ("von Mises Stress", 0))


class TestExplorationEvents(unittest.TestCase):
    """Classifying a run's output, with no Qt and no FreeCAD."""

    def test_a_record_is_an_event(self):
        from femexploration import events

        kind, payload = events.parse_line('{"event": "finished", "summary": "ok"}')
        self.assertEqual(kind, events.EVENT)
        self.assertEqual(payload["summary"], "ok")

    def test_bare_json_scalars_are_not_events(self):
        from femexploration import events

        self.assertEqual(events.parse_line("0")[0], events.NOISE)
        self.assertEqual(events.parse_line("true")[0], events.TEXT)
        self.assertEqual(events.parse_line('"a string"')[0], events.TEXT)

    def test_a_dict_without_an_event_key_is_text(self):
        from femexploration import events

        self.assertEqual(events.parse_line('{"something": "else"}')[0], events.TEXT)

    def test_freecad_banner_is_noise(self):
        from femexploration import events

        for line in (
            "FreeCAD 26.3.0, Libs: 26.3.0devR48263 (Git)",
            "(C) 2001-2026 FreeCAD contributors",
            "FreeCAD is free and open-source software licensed under LGPL2+",
            "",
            "   ",
            "-12.5",
        ):
            self.assertEqual(events.parse_line(line)[0], events.NOISE, line)

    def test_solver_chatter_is_text(self):
        from femexploration import events

        line = "[{'ccx_elset': 'Evolumes', 'mat_obj_name': 'Steel'}]"
        self.assertEqual(events.parse_line(line), (events.TEXT, line))

    def test_line_buffer_reassembles_and_flushes(self):
        from femexploration import events

        buffer = events.LineBuffer()
        self.assertEqual(buffer.feed("abc"), [])
        self.assertEqual(buffer.feed("def\nghi\nj"), ["abcdef", "ghi"])
        self.assertEqual(buffer.flush(), ["j"])
        self.assertEqual(buffer.flush(), [])

    def test_describe_point_reads_like_a_sentence(self):
        from femexploration import events

        text = events.describe_point(
            {"state": "completed", "inputs": {"Height": 400.0}, "outputs": {"Volume": 2.5, "_mesh_nodes": 9}},
            2, 5, {"Height": "Beam height"},
        )
        self.assertEqual(text, "2 of 5: Beam height 400 -> Volume 2.5")
        self.assertNotIn("_mesh_nodes", text)


class TestExplicitPlanner(unittest.TestCase):

    def test_missing_variables_take_their_nominal(self):
        from femexploration import planner

        spec = _demo_spec()
        plan = planner.make_planner(spec, points=[{"Width": 600.0}]).initial_batch()
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0].values, {"Width": 600.0, "Height": 1000.0})

    def test_unit_carrying_inputs_are_accepted(self):
        from femexploration import planner

        plan = planner.make_planner(
            _demo_spec(), points=[{"Width": [600.0, "mm"], "Height": [700.0, "mm"]}]
        ).initial_batch()
        self.assertEqual(plan[0].values, {"Width": 600.0, "Height": 700.0})

    def test_an_unknown_variable_is_refused(self):
        from femexploration import planner

        with self.assertRaises(ValueError):
            planner.make_planner(_demo_spec(), points=[{"Depth": 1.0}])

    def test_without_points_the_study_plan_is_used(self):
        from femexploration import planner

        self.assertEqual(len(planner.make_planner(_demo_spec()).initial_batch()), 9)


class TestSampling(unittest.TestCase):
    """The design of experiments plans, in the unit cube and decoded."""

    def setUp(self):
        self.vars = [
            Variable(name=f"v{i}", lower=10.0, upper=20.0, nominal=15.0, level_count=3)
            for i in range(3)
        ]

    def _plan(self, name, **kw):
        from femexploration.algorithms import sampling

        return sampling.build_plan(name, self.vars, **kw)

    def test_plan_sizes(self):
        from femexploration.algorithms import sampling

        expected = {
            "FullFactorial": 27,
            "OneAtATime": 7,
            "LatinHypercube": 8,
            "Sobol": 8,
            "RandomUniform": 8,
            "CentralComposite": 15,
            "BoxBehnken": 13,
        }
        for name, size in expected.items():
            plan = self._plan(name, count=8)
            self.assertEqual(len(plan), size, name)
            self.assertEqual(sampling.plan_size(name, self.vars, 8), size, name)

    def test_every_point_stays_inside_the_bounds(self):
        for name in ("LatinHypercube", "Sobol", "RandomUniform", "CentralComposite", "BoxBehnken"):
            for point in self._plan(name, count=16, alpha="Rotatable"):
                for var in self.vars:
                    self.assertGreaterEqual(point[var.name], var.lower - 1e-12, name)
                    self.assertLessEqual(point[var.name], var.upper + 1e-12, name)

    def test_latin_hypercube_is_stratified(self):
        """Exactly one point per stratum in every one-dimensional projection."""
        n = 12
        plan = self._plan("LatinHypercube", count=n, seed=5)
        for var in self.vars:
            strata = sorted(int((p[var.name] - var.lower) / (var.upper - var.lower) * n) for p in plan)
            strata = [min(s, n - 1) for s in strata]
            self.assertEqual(strata, list(range(n)), var.name)

    def test_a_seed_reproduces_and_changes(self):
        a = self._plan("LatinHypercube", count=6, seed=3)
        b = self._plan("LatinHypercube", count=6, seed=3)
        c = self._plan("LatinHypercube", count=6, seed=4)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_adding_a_variable_keeps_the_earlier_columns(self):
        """Per-variable random streams: the first variables' samples do not
        move when a third is added.  Holds for the stratified and random
        designs; maximin swapping of a Latin hypercube necessarily couples
        columns, so it is checked on the raw hypercube."""
        from femexploration.algorithms import sampling

        two = sampling.random_uniform(self.vars[:2], 8, seed=11)
        three = sampling.random_uniform(self.vars, 8, seed=11)
        for p2, p3 in zip(two, three):
            self.assertEqual(p2["v0"], p3["v0"])
            self.assertEqual(p2["v1"], p3["v1"])

        import numpy as np

        raw2 = sampling.unit_latin_hypercube(8, 2, np.random.default_rng(11), maximin_iterations=0)
        raw3 = sampling.unit_latin_hypercube(8, 3, np.random.default_rng(11), maximin_iterations=0)
        self.assertTrue(np.allclose(raw2, raw3[:, :2]))

    def test_the_implementation_is_reported(self):
        from femexploration.algorithms import sampling

        _plan, impl = sampling.build_plan_with_impl("Sobol", self.vars, count=8)
        try:
            import scipy.stats.qmc  # noqa: F401

            self.assertEqual(impl, "scipy.stats.qmc.Sobol")
        except ImportError:
            self.assertIn("scipy not available", impl)
        _plan, impl = sampling.build_plan_with_impl("LatinHypercube", self.vars, count=8)
        self.assertIn("LatinHypercube", impl)

    def test_central_composite_axial_points(self):
        face = self._plan("CentralComposite")
        centre = {v.name: 15.0 for v in self.vars}
        self.assertIn(centre, face)
        # face centred: axial points sit on the bounds
        axial = [p for p in face if sum(1 for v in self.vars if p[v.name] != 15.0) == 1]
        self.assertEqual(len(axial), 6)
        for p in axial:
            self.assertIn(next(p[v.name] for v in self.vars if p[v.name] != 15.0), (10.0, 20.0))

    def test_box_behnken_needs_three_variables(self):
        from femexploration.algorithms import sampling

        with self.assertRaises(ValueError):
            sampling.box_behnken(self.vars[:2])
        plan = sampling.box_behnken(self.vars)
        # never a corner: at most two variables off centre
        for p in plan:
            self.assertLessEqual(sum(1 for v in self.vars if p[v.name] != 15.0), 2)

    def test_the_planner_records_the_implementation(self):
        from femexploration import planner

        spec = _demo_spec()
        spec.study_type = "DOE"
        spec.method = {"plan": "LatinHypercube", "point_count": 7}
        made = planner.make_planner(spec)
        self.assertEqual(len(made.initial_batch()), 7)
        self.assertIn("LatinHypercube", made.implementation)


def _drive(planner, evaluate, limit=5000):
    """Run a planner the way the engine does, evaluating with *evaluate*.

    ``evaluate(values) -> (outputs dict)``; objectives and violations are
    scored from the spec exactly as the evaluator would.
    """
    spec = planner.spec
    results = []
    counter = [0]
    batch = planner.initial_batch()
    while batch and counter[0] < limit:
        for vector in batch:
            counter[0] += 1
            vector.point_id = f"p{counter[0]:05d}"
            outputs = evaluate(vector.values)
            result = PointResult(point_id=vector.point_id, inputs={k: [v, ""] for k, v in vector.values.items()})
            if outputs is None:
                result.state = model.SOLVE_FAILED
            else:
                result.state = model.COMPLETED
                result.outputs = dict(outputs)
                for objective in spec.active_objectives:
                    result.objectives[objective.name] = objective.value(outputs)
                for constraint in spec.active_constraints:
                    result.violations[constraint.name] = constraint.violation(outputs)
            results.append(result)
        planner.observe(results)
        batch = planner.next_batch()
    return results


def _opt_spec(k=2, objectives=None, constraints=None, method=None, seed=1):
    spec = StudySpec(
        name="Opt",
        study_type="Optimization",
        variables=[Variable(name=f"x{i}", lower=-5.0, upper=5.0, nominal=4.0) for i in range(k)],
        responses=[Response(name="f", source=model.EXPRESSION, expression="f"),
                   Response(name="g", source=model.EXPRESSION, expression="g"),
                   Response(name="f2", source=model.EXPRESSION, expression="f2")],
        objectives=objectives or [Objective(name="MinF", response="f")],
        constraints=constraints or [],
        method=method or {},
        seed=seed,
    )
    return spec


class TestOptimizers(unittest.TestCase):
    """The optimizers on functions with known answers, no FreeCAD."""

    @staticmethod
    def _sphere(values):
        x = [values[k] for k in sorted(values)]
        return {"f": sum(v * v for v in x), "g": x[0], "f2": 0.0}

    @staticmethod
    def _rosenbrock(values):
        x, y = values["x0"], values["x1"]
        return {"f": (1 - x) ** 2 + 100 * (y - x * x) ** 2, "g": x, "f2": 0.0}

    def _best(self, spec, results):
        feasible = [r for r in results if r.feasible]
        return min(feasible, key=lambda r: r.objectives["MinF"])

    def test_nelder_mead_finds_the_sphere_minimum(self):
        from femexploration.algorithms import optimize

        spec = _opt_spec(method={"algorithm": "NelderMead", "max_evaluations": 300, "tolerance": 1e-8, "stagnation": 50})
        planner = optimize.make_optimizer(spec)
        results = _drive(planner, self._sphere)
        best = self._best(spec, results)
        self.assertLess(best.objectives["MinF"], 1e-3)
        self.assertLessEqual(len(results), 300)
        self.assertTrue(planner.finished_reason)

    def test_nelder_mead_descends_rosenbrock(self):
        from femexploration.algorithms import optimize

        spec = _opt_spec(method={"algorithm": "NelderMead", "max_evaluations": 400, "tolerance": 1e-9, "stagnation": 60})
        results = _drive(optimize.make_optimizer(spec), self._rosenbrock)
        best = self._best(spec, results)
        self.assertLess(best.objectives["MinF"], 0.5)

    def test_differential_evolution_finds_the_sphere_minimum(self):
        from femexploration.algorithms import optimize

        spec = _opt_spec(k=3, method={"algorithm": "DifferentialEvolution", "max_evaluations": 600,
                                      "population": 20, "tolerance": 1e-8, "stagnation": 30})
        results = _drive(optimize.make_optimizer(spec), self._sphere)
        best = self._best(spec, results)
        self.assertLess(best.objectives["MinF"], 1e-2)
        # never past the budget: the last generation is cut to fit
        self.assertLessEqual(len(results), 600)

    def test_the_budget_is_a_hard_limit(self):
        from femexploration.algorithms import optimize

        for algorithm in ("DifferentialEvolution", "NSGA2"):
            spec = _opt_spec(method={"algorithm": algorithm, "max_evaluations": 30, "population": 8, "stagnation": 0})
            results = _drive(optimize.make_optimizer(spec), self._sphere)
            self.assertLessEqual(len(results), 30, algorithm)
            self.assertGreaterEqual(len(results), 24, algorithm)

    def test_screening_is_a_batch_and_one_step(self):
        from femexploration.algorithms import optimize

        spec = _opt_spec(method={"algorithm": "Screening", "population": 12, "max_evaluations": 50})
        planner = optimize.make_optimizer(spec)
        results = _drive(planner, self._sphere)
        self.assertEqual(len(results), 1 + 12 + 2)
        self.assertIn("complete", planner.finished_reason)

    def test_constraints_are_respected(self):
        """Minimise x0^2 + x1^2 subject to x0 >= 2: the answer is (2, 0)."""
        from femexploration.algorithms import optimize

        for handling in ("FeasibilityRules", "Penalty", "DeathPenalty"):
            spec = _opt_spec(
                constraints=[Constraint(name="Lo", response="g", operator=model.GE, limit=2.0)],
                method={"algorithm": "DifferentialEvolution", "population": 16, "max_evaluations": 480,
                        "constraints": handling, "tolerance": 1e-9, "stagnation": 40},
            )
            results = _drive(optimize.make_optimizer(spec), self._sphere)
            best = self._best(spec, results)
            self.assertGreaterEqual(best.inputs["x0"][0], 2.0 - 1e-9, handling)
            self.assertLess(best.objectives["MinF"], 4.3, handling)

    def test_failed_points_rank_last_but_do_not_stop_the_search(self):
        from femexploration.algorithms import optimize

        def flaky(values):
            if values["x0"] > 3.0:
                return None  # a region where the mesh will not build
            return self._sphere(values)

        spec = _opt_spec(method={"algorithm": "DifferentialEvolution", "population": 12, "max_evaluations": 240})
        results = _drive(optimize.make_optimizer(spec), flaky)
        self.assertTrue(any(r.state == model.SOLVE_FAILED for r in results))
        best = self._best(spec, results)
        self.assertLess(best.objectives["MinF"], 0.1)

    def test_stopping_reasons(self):
        from femexploration.algorithms.optimize import Stopping

        stop = Stopping(max_evaluations=10, tolerance=1e-3, stagnation=3)
        self.assertFalse(stop.should_stop(5))
        self.assertTrue(stop.should_stop(10))
        self.assertIn("10 evaluations", stop.reason)
        stop = Stopping(max_evaluations=0, tolerance=1e-3, stagnation=3)
        stop.record_round(1.0)
        for _ in range(3):
            stop.record_round(1.0 - 1e-4)
        self.assertTrue(stop.should_stop(1))
        self.assertIn("no improvement", stop.reason)

    def test_the_same_seed_gives_the_same_run(self):
        from femexploration.algorithms import optimize

        a = _drive(optimize.make_optimizer(_opt_spec(method={"algorithm": "DifferentialEvolution", "max_evaluations": 80, "population": 8}, seed=9)), self._sphere)
        b = _drive(optimize.make_optimizer(_opt_spec(method={"algorithm": "DifferentialEvolution", "max_evaluations": 80, "population": 8}, seed=9)), self._sphere)
        self.assertEqual([r.inputs for r in a], [r.inputs for r in b])

    def test_encode_inverts_decode(self):
        from femexploration.algorithms import sampling

        var = Variable(name="v", lower=2.0, upper=8.0, nominal=5.0)
        for u in (0.0, 0.3, 1.0):
            self.assertAlmostEqual(sampling.encode_one(sampling.decode_one(u, var), var), u)
        integer = Variable(name="i", var_type=model.INTEGER, lower=1, upper=5, nominal=3)
        self.assertEqual(sampling.decode_one(sampling.encode_one(4, integer), integer), 4.0)


class TestNSGA2(unittest.TestCase):
    """NSGA-II on ZDT1, whose front is f2 = 1 - sqrt(f1) on [0, 1]."""

    @staticmethod
    def _zdt1(values):
        x = [values[k] for k in sorted(values)]
        f1 = x[0]
        g = 1 + 9 * sum(x[1:]) / (len(x) - 1)
        f2 = g * (1 - (f1 / g) ** 0.5)
        return {"f": f1, "f2": f2, "g": 0.0}

    def _spec(self, k=4, evaluations=1200, population=40):
        spec = _opt_spec(
            k=k,
            objectives=[Objective(name="F1", response="f"), Objective(name="F2", response="f2")],
            method={"algorithm": "NSGA2", "population": population, "max_evaluations": evaluations, "stagnation": 0},
            seed=3,
        )
        for var in spec.variables:
            var.lower, var.upper, var.nominal = 0.0, 1.0, 0.5
        return spec

    def test_the_front_approaches_zdt1(self):
        from femexploration.algorithms import moo
        from femexploration.algorithms import optimize

        planner = optimize.make_optimizer(self._spec())
        _drive(planner, self._zdt1)
        front = planner.front()
        self.assertGreaterEqual(len(front), 10)
        # every front member is close to the analytic curve
        gaps = [abs(f[1] - (1 - f[0] ** 0.5)) for _u, f, _v, _m in front]
        self.assertLess(sum(gaps) / len(gaps), 0.15)
        # and the front spreads across f1
        f1 = [f[0] for _u, f, _v, _m in front]
        self.assertLess(min(f1), 0.15)
        self.assertGreater(max(f1), 0.6)

    def test_non_dominated_sort_and_crowding(self):
        from femexploration.algorithms import moo

        entries = [([1.0, 5.0], 0.0), ([2.0, 2.0], 0.0), ([5.0, 1.0], 0.0), ([3.0, 3.0], 0.0), ([1.0, 1.0], 2.0)]
        fronts = moo.non_dominated_sort(entries)
        self.assertEqual(fronts[0], [0, 1, 2])
        self.assertEqual(fronts[1], [3])
        # the infeasible point ranks after every feasible one
        self.assertEqual(fronts[-1], [4])
        distance = moo.crowding_distance([[1.0, 5.0], [2.0, 2.0], [5.0, 1.0]])
        self.assertEqual(distance[0], float("inf"))
        self.assertTrue(0 < distance[1] < float("inf"))

    def test_pareto_rows_in_the_table_agree_with_the_sorter(self):
        """The design point table computes its own Pareto filter."""
        from femexploration.algorithms import moo

        entries = [([1.0, 5.0], 0.0), ([2.0, 2.0], 0.0), ([3.0, 3.0], 0.0)]
        self.assertEqual(moo.pareto_front(entries), [0, 1])


class TestStats(unittest.TestCase):
    """Correlation and sensitivity, with no FreeCAD."""

    def _rows(self, n=12):
        rows = []
        for i in range(n):
            a = float(i)
            b = float((i * 7) % n)
            rows.append({
                "state": "completed",
                "inputs": {"a": [a, "mm"], "b": [b, "mm"]},
                "outputs": {"y": 2.0 * a + b, "z": -a, "_mesh_nodes": 100.0},
            })
        rows.append({"state": "solve_failed", "inputs": {"a": [99.0, "mm"], "b": [99.0, "mm"]}, "outputs": {}})
        return rows

    def test_failed_rows_are_left_out(self):
        from femexploration import stats

        x, y, kept = stats.table_arrays(self._rows(), ["a", "b"], ["y"])
        self.assertEqual(x.shape, (12, 2))
        self.assertEqual(y.shape, (12, 1))
        self.assertNotIn(12, kept)

    def test_pearson_of_a_linear_relation_is_one(self):
        from femexploration import stats

        keys, matrix = stats.correlation_matrix(self._rows(), ["a", "b"], ["y", "z"])
        self.assertEqual(keys, ["a", "b", "y", "z"])
        self.assertAlmostEqual(matrix[0, 3], -1.0)
        self.assertAlmostEqual(matrix[0, 0], 1.0)

    def test_a_constant_column_is_zero_not_nan(self):
        import numpy as np

        from femexploration import stats

        matrix = stats.pearson(np.array([[1.0, 5.0], [2.0, 5.0], [3.0, 5.0]]))
        self.assertEqual(matrix[0, 1], 0.0)
        self.assertFalse(np.isnan(matrix).any())

    def test_spearman_sees_a_monotone_relation(self):
        import numpy as np

        from femexploration import stats

        x = np.arange(1, 11, dtype=float)
        data = np.column_stack([x, np.exp(x)])
        self.assertAlmostEqual(stats.spearman(data)[0, 1], 1.0)
        self.assertLess(stats.pearson(data)[0, 1], 0.9)

    def test_sensitivity_recovers_the_coefficients(self):
        from femexploration import stats

        table = stats.sensitivity_table(self._rows(), ["a", "b"], ["y", "z"])
        y = table["y"]
        self.assertAlmostEqual(y["r2"], 1.0, places=6)
        # standardised: a's effect is twice b's, both positive
        self.assertGreater(y["coefficients"]["b"], 0)
        self.assertGreater(y["coefficients"]["a"], y["coefficients"]["b"])
        self.assertAlmostEqual(y["coefficients"]["a"] / y["coefficients"]["b"], 2.0, places=3)
        self.assertLess(table["z"]["coefficients"]["a"], 0)
        self.assertAlmostEqual(table["z"]["coefficients"]["b"], 0.0, places=6)

    def test_mesh_noise_is_flagged(self):
        from femexploration import stats

        rows = self._rows()
        for i, row in enumerate(rows):
            if row["state"] == "completed":
                row["outputs"]["_mesh_nodes"] = 100.0 + i
        flagged = stats.mesh_convergence_warning(rows, ["y", "z"])
        self.assertIn("y", flagged)
        # constant node count: nothing to flag
        self.assertEqual(stats.mesh_convergence_warning(self._rows(), ["y"]), {})


try:
    import matplotlib as _mpl

    _mpl.use("Agg")
except ImportError:  # pragma: no cover
    _mpl = None


@unittest.skipIf(_mpl is None, "requires matplotlib")
class TestCharts(unittest.TestCase):
    """The drawing functions, on an off-screen figure."""

    def _data(self):
        from types import SimpleNamespace

        from femguiutils import exploration_charts as charts

        def Column(key, label, unit, role):
            # the chart only reads these four; the Qt table's Column class
            # would drag PySide into a test that needs none of it
            return SimpleNamespace(key=key, label=label, unit=unit, role=role)

        rows = []
        for i in range(10):
            rows.append({
                "state": "completed",
                "inputs": {"Input": [float(i), "mm"], "Input001": [float((i * 3) % 10), "mm"]},
                "outputs": {"Volume": 3.0 * i + ((i * 3) % 10), "_mesh_nodes": 50.0},
            })
        columns = [
            Column("Input", "Width", "mm", "input"),
            Column("Input001", "Height", "mm", "input"),
            Column("Volume", "Volume", "mm^3", "response"),
        ]
        return charts.chart_data(rows, columns, "Sweep")

    def test_chart_data_sorts_columns_by_role(self):
        data = self._data()
        self.assertEqual(data.inputs, ["Input", "Input001"])
        self.assertEqual(data.outputs, ["Volume"])
        self.assertEqual(data.label("Input"), "Width")

    def test_correlation_draws_a_labelled_matrix(self):
        from matplotlib.figure import Figure

        from femguiutils import exploration_charts as charts

        axes = charts.draw_correlation(Figure(), self._data())
        self.assertEqual([t.get_text() for t in axes.get_xticklabels()], ["Width", "Height", "Volume"])
        self.assertEqual(len(axes.images), 1)

    def test_sensitivity_draws_one_panel_per_output(self):
        from matplotlib.figure import Figure

        from femguiutils import exploration_charts as charts

        axes = charts.draw_sensitivity(Figure(), self._data())
        self.assertEqual(len(axes), 1)
        self.assertEqual(len(axes[0].patches), 2)
        self.assertIn("R² = 1.00", axes[0].get_title())

    def test_too_few_points_is_refused_with_a_reason(self):
        from matplotlib.figure import Figure

        from femguiutils import exploration_charts as charts

        data = self._data()
        data.rows = data.rows[:2]
        with self.assertRaises(ValueError) as caught:
            charts.draw_correlation(Figure(), data)
        self.assertIn("completed design points", str(caught.exception))

    def test_the_registry_has_every_chart(self):
        from femguiutils import exploration_charts as charts

        self.assertTrue(set(charts.CHARTS) >= {"Correlation", "Sensitivity", "History", "Pareto"})

    def _opt_data(self):
        from types import SimpleNamespace

        from femguiutils import exploration_charts as charts

        rows = []
        for i in range(12):
            f1 = i / 11.0
            f2 = 1.0 - f1**0.5 + (0.3 if i % 4 == 0 else 0.0)
            rows.append({
                "point_id": f"p{i}", "state": "completed",
                "inputs": {"Input": [float(i), "mm"]},
                "outputs": {"A": f1, "B": f2},
                "objectives": {"MinA": f1, "MinB": f2},
                "violations": {"Lim": 0.5 if i == 5 else 0.0},
            })
        columns = [SimpleNamespace(key="Input", label="Width", unit="mm", role="input"),
                   SimpleNamespace(key="A", label="A", unit="", role="response"),
                   SimpleNamespace(key="B", label="B", unit="", role="response"),
                   SimpleNamespace(key="MinA", label="MinA", unit="", role="objective"),
                   SimpleNamespace(key="MinB", label="MinB", unit="", role="objective"),
                   SimpleNamespace(key="Lim", label="Lim", unit="", role="violation")]
        return charts.chart_data(rows, columns, "Opt")

    def test_history_draws_best_so_far_and_infeasible_rate(self):
        from matplotlib.figure import Figure

        from femguiutils import exploration_charts as charts

        axes = charts.draw_history(Figure(), self._opt_data())
        self.assertEqual(len(axes), 2)
        line = axes[0].lines[0]
        ys = list(line.get_ydata())
        # best so far never rises
        self.assertTrue(all(b <= a + 1e-12 for a, b in zip(ys, ys[1:])))

    def test_pareto_highlights_the_front_and_is_pickable(self):
        from matplotlib.figure import Figure

        from femguiutils import exploration_charts as charts

        picked = []
        figure = Figure()
        axes = charts.draw_pareto(figure, self._opt_data(), on_pick=picked.append)
        self.assertEqual(len(axes.collections), 1)
        self.assertEqual(len(axes.pareto_ids), 12)
        self.assertIn("non-dominated", axes.get_title())
        # the picker is armed on the scatter; simulate the callback path
        scatter = axes.collections[0]
        self.assertTrue(scatter.get_picker())


try:
    from PySide import QtCore as _QtCore
except ImportError:
    _QtCore = None


@unittest.skipIf(_QtCore is None, "requires PySide")
class TestDesignPointModel(unittest.TestCase):
    """The table model, exercised without a window.

    ``QAbstractTableModel`` needs no QApplication, so this runs under
    FreeCADCmd; only the state icons need a GUI and they are skipped then.
    """

    def setUp(self):
        from femguiutils import exploration_designpoints as dp

        self.dp = dp
        self.model = dp.DesignPointModel()
        self.model.set_columns(dp.columns_from_spec(_demo_spec()))
        self.model.set_rows([
            {"point_id": "a", "state": "completed",
             "inputs": {"Width": [500.0, "mm"], "Height": [500.0, "mm"]},
             "outputs": {"MaxStress": 100.0, "Mass": 1.0},
             "objectives": {"MinMass": 1.0}, "violations": {"StressLimit": 0.0}},
            {"point_id": "b", "state": "completed",
             "inputs": {"Width": [1500.0, "mm"], "Height": [500.0, "mm"]},
             "outputs": {"MaxStress": 300.0, "Mass": 3.0},
             "objectives": {"MinMass": 3.0}, "violations": {"StressLimit": 50.0}},
            {"point_id": "c", "state": "solve_failed", "error": "ccx exited 1",
             "inputs": {"Width": [1000.0, "mm"], "Height": [1000.0, "mm"]},
             "outputs": {}},
        ])

    def _cell(self, row, column, role=None):
        return self.model.data(self.model.index(row, column), role or _QtCore.Qt.DisplayRole)

    def test_columns_follow_the_spec(self):
        headings = [
            self.model.headerData(i, _QtCore.Qt.Horizontal) for i in range(self.model.columnCount())
        ]
        self.assertEqual(
            headings,
            ["State", "Keep", "Point", "Width [mm]", "Height [mm]", "MaxStress [MPa]",
             "Mass [kg]", "MinMass", "StressLimit"],
        )

    def test_values_are_read_from_the_right_place(self):
        self.assertEqual(self._cell(0, 3), "500")
        self.assertEqual(self._cell(1, 5), "300")
        self.assertEqual(self._cell(1, 7), "3")
        self.assertEqual(self._cell(1, 8), "50")
        self.assertEqual(self._cell(0, 3, self.dp.DesignPointModel.ValueRole), 500.0)

    def test_a_violated_constraint_is_tinted_and_infeasible(self):
        self.assertIsNotNone(self._cell(1, 8, _QtCore.Qt.BackgroundRole))
        self.assertIsNone(self._cell(0, 8, _QtCore.Qt.BackgroundRole))
        self.assertTrue(self.model.is_feasible(0))
        self.assertFalse(self.model.is_feasible(1))
        self.assertFalse(self.model.is_feasible(2))

    def test_a_failed_row_carries_its_error(self):
        self.assertEqual(self._cell(2, 0), "solve_failed")
        self.assertIn("ccx exited 1", self._cell(2, 0, _QtCore.Qt.ToolTipRole))
        self.assertTrue(self.model.is_failed(2))

    def test_editing_an_input_marks_the_row_out_of_date(self):
        """Not re-run: the outputs no longer belong to the inputs, and the
        row says so instead of quietly lying."""
        index = self.model.index(0, 3)
        self.assertTrue(self.model.flags(index) & _QtCore.Qt.ItemIsEditable)
        self.assertTrue(self.model.setData(index, "750", _QtCore.Qt.EditRole))
        self.assertEqual(self.model.row(0)["state"], "out_of_date")
        self.assertEqual(self.model.row(0)["inputs"]["Width"], [750.0, "mm"])
        self.assertEqual(self.model.stale_points(), [{"Width": 750.0, "Height": 500.0}])

    def test_outputs_are_not_editable(self):
        self.assertFalse(self.model.flags(self.model.index(0, 5)) & _QtCore.Qt.ItemIsEditable)
        self.assertFalse(self.model.setData(self.model.index(0, 5), "1", _QtCore.Qt.EditRole))

    def test_a_non_number_is_rejected(self):
        self.assertFalse(self.model.setData(self.model.index(0, 3), "wide", _QtCore.Qt.EditRole))
        self.assertEqual(self.model.row(0)["state"], "completed")

    def test_keep_is_a_checkbox(self):
        index = self.model.index(0, 1)
        self.assertEqual(self._cell(0, 1, _QtCore.Qt.CheckStateRole), _QtCore.Qt.Unchecked)
        self.model.setData(index, _QtCore.Qt.Checked, _QtCore.Qt.CheckStateRole)
        self.assertTrue(self.model.row(0)["retained"])

    def test_upsert_adds_then_updates(self):
        self.model.upsert({"id": "d", "state": "running", "inputs": {"Width": 800.0, "Height": 800.0}, "outputs": {}})
        self.assertEqual(self.model.rowCount(), 4)
        self.model.upsert({"id": "d", "state": "completed", "inputs": {"Width": 800.0, "Height": 800.0},
                           "outputs": {"Mass": 2.0}})
        self.assertEqual(self.model.rowCount(), 4)
        self.assertEqual(self._cell(3, 0), "completed")
        self.assertEqual(self._cell(3, 6), "2")

    def test_a_typed_row_is_claimed_by_its_result(self):
        """A row added by hand has no id until the engine names it; the
        result must land on that row rather than beside it."""
        r = self.model.add_row({"Width": [900.0, "mm"], "Height": [500.0, "mm"]})
        self.assertEqual(self.model.rowCount(), 4)
        self.assertTrue(self.model.is_stale(r))
        self.model.upsert({"id": "e", "state": "completed",
                           "inputs": {"Width": 900.0, "Height": 500.0}, "outputs": {"Mass": 9.0}})
        self.assertEqual(self.model.rowCount(), 4)
        self.assertEqual(self.model.row(r)["point_id"], "e")
        self.assertEqual(self.model.row(r)["state"], "completed")

    def test_duplicate_and_remove(self):
        r = self.model.duplicate_row(1)
        self.assertEqual(r, 2)
        self.assertEqual(self.model.rowCount(), 4)
        self.assertEqual(self.model.inputs_of(2), {"Width": 1500.0, "Height": 500.0})
        self.assertEqual(self.model.row(2)["state"], "pending")
        self.model.remove_rows([2, 0])
        self.assertEqual([row["point_id"] for row in self.model.rows()], ["b", "c"])

    def test_pareto_front_ignores_infeasible_and_failed(self):
        self.model.upsert({"id": "f", "state": "completed",
                           "inputs": {"Width": 600.0, "Height": 600.0}, "outputs": {},
                           "objectives": {"MinMass": 0.5}, "violations": {"StressLimit": 0.0}})
        self.assertEqual(self.model.pareto_rows(), {3})

    def test_filter_modes(self):
        proxy = self.dp.DesignPointFilter()
        proxy.setSourceModel(self.model)
        counts = {}
        for mode in self.dp.FILTERS:
            proxy.set_mode(mode)
            counts[mode] = proxy.rowCount()
        self.assertEqual(counts["All"], 3)
        self.assertEqual(counts["Completed"], 2)
        self.assertEqual(counts["Failed"], 1)
        self.assertEqual(counts["Feasible"], 1)
        self.assertEqual(counts["Pareto"], 1)
        self.assertEqual(counts["Out of date"], 0)

    def test_csv_round_trip(self):
        tmp = tempfile.mkdtemp(prefix="fem_dp_")
        try:
            path = os.path.join(tmp, "points.csv")
            self.model.export_csv(path)
            with open(path) as fh:
                header = fh.readline().strip()
            self.assertEqual(
                header,
                "point_id,state,Width [mm],Height [mm],MaxStress [MPa],Mass [kg],MinMass,StressLimit",
            )
            added = self.model.import_csv(path)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(added, 3)
        self.assertEqual(self.model.rowCount(), 6)
        self.assertEqual(self.model.inputs_of(3), {"Width": 500.0, "Height": 500.0})
        self.assertTrue(self.model.is_stale(3))


@needs_freecad
class TestExplorationTools(unittest.TestCase):
    """The run object behind the panels and the Run Study command."""

    def setUp(self):
        import FreeCAD

        import ObjectsFem
        from femtools import explorationpromote
        from femtools import explorationutils

        self.FreeCAD = FreeCAD
        self.tmp = tempfile.mkdtemp(prefix="fem_expl_tools_")
        self.doc = FreeCAD.newDocument("TestExplorationTools")
        self.box = self.doc.addObject("Part::Box", "Box")
        self.box.Length, self.box.Width, self.box.Height = 8000, 1000, 1000
        self.doc.recompute()
        analysis = ObjectsFem.makeAnalysis(self.doc, "Analysis")
        for prop in ("Width", "Height"):
            var = explorationpromote.promote(self.box, prop, analysis)
            var.Lower, var.Upper, var.LevelCount = 500.0, 1500.0, 3
        pset = explorationutils.get_parameter_set(analysis)
        out = ObjectsFem.makeExplorationOutput(self.doc, "Volume")
        out.Source = model.EXPRESSION
        out.Expression = "Box.Shape.Volume"
        pset.addObject(out)
        self.sweep = ObjectsFem.makeExplorationSweep(self.doc, "Sweep")
        self.sweep.ParameterSet = pset
        self.sweep.WorkingDirectory = os.path.join(self.tmp, "run")
        analysis.addObject(self.sweep)
        self.doc.recompute()

    def tearDown(self):
        self.FreeCAD.closeDocument(self.doc.Name)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _tool(self):
        from femtools import explorationtools

        return explorationtools.ExplorationTools(self.sweep)

    def test_prepare_lays_out_the_run_directory(self):
        from femtools import objecttools

        tool = self._tool()
        self.assertIs(objecttools.tool_of(self.sweep), tool)
        tool.prepare()
        self.assertEqual(tool.run_dir, self.sweep.WorkingDirectory)
        self.assertTrue(os.path.exists(os.path.join(tool.run_dir, "model.FCStd")))
        with open(os.path.join(tool.run_dir, "run_study.py")) as fh:
            self.assertIn("os.setsid()", fh.read())
        self.assertEqual(len(tool.plan), 9)
        self.assertTrue(os.path.exists(tool.binary))
        args = tool.arguments()
        self.assertEqual(args[1], "--")
        self.assertIn("--study", args)
        self.assertNotIn("--points", args)

    def test_a_stale_cancel_flag_is_removed(self):
        from femexploration.cancel import CANCEL_FILE

        os.makedirs(self.sweep.WorkingDirectory, exist_ok=True)
        flag = os.path.join(self.sweep.WorkingDirectory, CANCEL_FILE)
        with open(flag, "w") as fh:
            fh.write("old\n")
        self._tool().prepare()
        self.assertFalse(os.path.exists(flag))

    def test_explicit_points_are_written_and_passed(self):
        import json

        from femexploration import api

        spec = api.study_spec(self.sweep)
        width = _variable_for(spec, "Width").name
        height = _variable_for(spec, "Height").name
        tool = self._tool()
        tool.points = [{width: 700.0}]
        tool.prepare()
        args = tool.arguments()
        self.assertIn("--points", args)
        with open(args[args.index("--points") + 1]) as fh:
            self.assertEqual(json.load(fh), [{width: 700.0}])
        self.assertEqual(tool.plan, [{width: 700.0, height: 1000.0}])

    def test_prepare_refuses_a_study_that_cannot_run(self):
        for child in list(self.sweep.ParameterSet.Group):
            if child.Name.startswith("Volume"):
                self.sweep.ParameterSet.removeObject(child)
        self.doc.recompute()
        with self.assertRaises(Exception):
            self._tool().prepare()

    def test_cancel_writes_the_flag(self):
        from femexploration.cancel import CANCEL_FILE

        tool = self._tool()
        tool.prepare()
        tool.cancel()
        self.assertTrue(os.path.exists(os.path.join(tool.run_dir, CANCEL_FILE)))

    @unittest.skipUnless(hasattr(os, "killpg") and shutil.which("python3"), "POSIX only")
    def test_terminate_reaps_the_whole_process_group(self):
        """The mesher and solver are grandchildren.  Killing the direct child
        alone leaves them running - the exact leak this exists to stop."""
        import subprocess
        import time

        tool = self._tool()
        tool.prepare()
        # stand in for FreeCADCmd: become a group leader and spawn a grandchild
        tool.binary = shutil.which("python3")
        tool.arguments = lambda: [
            "-c",
            "import os, subprocess, time\n"
            "os.setsid()\n"
            "subprocess.Popen(['sleep', '60'])\n"
            "time.sleep(60)\n",
        ]
        tool.compute()
        self.assertTrue(tool.process.waitForStarted(5000))
        pid = int(tool.process.processId())
        deadline = time.time() + 5
        while time.time() < deadline:
            group = subprocess.run(["pgrep", "-g", str(pid)], capture_output=True, text=True).stdout.split()
            if len(group) >= 2:
                break
            time.sleep(0.05)
        self.assertGreaterEqual(len(group), 2, "grandchild never appeared")

        tool.terminate()
        self.assertTrue(tool.process.waitForFinished(5000))
        deadline = time.time() + 5
        while time.time() < deadline:
            left = subprocess.run(["pgrep", "-g", str(pid)], capture_output=True, text=True).stdout.split()
            if not left:
                break
            time.sleep(0.05)
        self.assertEqual(left, [], "processes survived in the group")

    def test_apply_point_sets_the_model(self):
        from femexploration import api

        # rows are keyed by the Input object's name, not the property it drives
        spec = api.study_spec(self.sweep)
        width = _variable_for(spec, "Width").name
        height = _variable_for(spec, "Height").name
        api.apply_point(self.sweep, {width: [750.0, "mm"], height: 650.0})
        self.assertAlmostEqual(self.box.Width.Value, 750.0)
        self.assertAlmostEqual(self.box.Height.Value, 650.0)


class TestWorker(unittest.TestCase):
    """The worker's job loop, with the evaluation injected."""

    def _run(self, job, evaluate, cancelled=False):
        import io
        from contextlib import redirect_stdout

        from femexploration import worker

        out = io.StringIO()
        with redirect_stdout(out):
            done = worker.run_job(job, evaluate=evaluate)
        lines = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
        return done, lines

    def _job(self, tmp):
        return {
            "model": "unused.FCStd",
            "spec": _demo_spec().to_dict(),
            "run_dir": tmp,
            "worker": "slot-7",
            "points": [
                {"point_id": "p1", "values": {"Width": 500.0, "Height": 500.0}},
                {"point_id": "p2", "values": {"Width": 700.0, "Height": 500.0}},
            ],
        }

    def test_every_point_is_reported_with_the_worker_name(self):
        tmp = tempfile.mkdtemp(prefix="fem_worker_")
        try:
            def evaluate(vector):
                return PointResult(point_id=vector.point_id, state=model.COMPLETED,
                                   outputs={"Mass": vector.values["Width"]})

            done, lines = self._run(self._job(tmp), evaluate)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(done, 2)
        results = [line["point"] for line in lines if line["event"] == "result"]
        self.assertEqual([r["point_id"] for r in results], ["p1", "p2"])
        self.assertEqual({r["worker"] for r in results}, {"slot-7"})
        self.assertEqual(results[1]["outputs"]["Mass"], 700.0)

    def test_a_cancel_flag_stops_the_job_after_the_point_in_flight(self):
        from femexploration.cancel import CANCEL_FILE

        tmp = tempfile.mkdtemp(prefix="fem_worker_")
        try:
            def evaluate(vector):
                with open(os.path.join(tmp, CANCEL_FILE), "w") as fh:
                    fh.write("stop\n")
                return PointResult(point_id=vector.point_id, state=model.COMPLETED)

            done, lines = self._run(self._job(tmp), evaluate)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        results = [line["point"] for line in lines if line["event"] == "result"]
        self.assertEqual(done, 1)
        self.assertEqual(results[0]["state"], model.COMPLETED)


@needs_freecad
class TestExplorationDOE(unittest.TestCase):
    """The design of experiments study object, end to end."""

    def setUp(self):
        import FreeCAD

        import ObjectsFem
        from femtools import explorationpromote
        from femtools import explorationutils

        self.FreeCAD = FreeCAD
        self.tmp = tempfile.mkdtemp(prefix="fem_expl_doe_")
        self.doc = FreeCAD.newDocument("TestExplorationDOE")
        box = self.doc.addObject("Part::Box", "Box")
        box.Length, box.Width, box.Height = 8000, 1000, 1000
        self.doc.recompute()
        analysis = ObjectsFem.makeAnalysis(self.doc, "Analysis")
        for prop in ("Width", "Height"):
            var = explorationpromote.promote(box, prop, analysis)
            var.Lower, var.Upper = 500.0, 1500.0
        pset = explorationutils.get_parameter_set(analysis)
        out = ObjectsFem.makeExplorationOutput(self.doc, "Volume")
        out.Source = model.EXPRESSION
        out.Expression = "Box.Shape.Volume"
        pset.addObject(out)
        self.doe = ObjectsFem.makeExplorationDOE(self.doc, "DOE")
        self.doe.ParameterSet = pset
        self.doe.Plan = "LatinHypercube"
        self.doe.PointCount = 5
        self.doe.Seed = 7
        analysis.addObject(self.doe)
        self.doc.recompute()

    def tearDown(self):
        self.FreeCAD.closeDocument(self.doc.Name)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_type_and_spec(self):
        from femexploration import api
        from femtools import explorationutils

        self.assertEqual(self.doe.Proxy.Type, explorationutils.DOE_TYPE)
        self.assertTrue(explorationutils.is_exploration_study(self.doe))
        spec = api.study_spec(self.doe)
        self.assertEqual(spec.study_type, "DOE")
        self.assertEqual(spec.method["plan"], "LatinHypercube")
        self.assertEqual(spec.method["point_count"], 5)
        self.assertEqual(spec.method["alpha"], "FaceCentred")
        self.assertEqual(spec.seed, 7)

    def test_a_latin_hypercube_runs_and_records_its_sampler(self):
        import json

        from femexploration import api

        run_dir = os.path.join(self.tmp, "run")
        result = api.run_study(self.doe, run_dir=run_dir)
        self.assertEqual(len(result.completed), 5)
        with open(os.path.join(run_dir, "study.json")) as fh:
            study = json.load(fh)
        self.assertIn("LatinHypercube", study["provenance"]["sampler_impl"])
        # reproducible: the same seed re-uses every point
        again = api.run_study(self.doe, run_dir=run_dir)
        self.assertEqual({r.point_id for r in again.completed}, {r.point_id for r in result.completed})

    def test_a_classical_design_ignores_the_count(self):
        from femexploration import api

        self.doe.Plan = "CentralComposite"
        self.doc.recompute()
        result = api.run_study(self.doe, run_dir=os.path.join(self.tmp, "ccd"))
        self.assertEqual(len(result.completed), 2**2 + 2 * 2 + 1)

    def test_settings_survive_save_and_reopen(self):
        path = os.path.join(self.tmp, "doe.FCStd")
        self.doe.Plan = "Sobol"
        self.doe.PointCount = 9
        self.doc.saveAs(path)
        name = self.doc.Name
        self.FreeCAD.closeDocument(name)
        self.doc = self.FreeCAD.openDocument(path)
        doe = self.doc.getObject("DOE")
        self.assertEqual(doe.Plan, "Sobol")
        self.assertEqual(doe.PointCount, 9)
        self.assertEqual(doe.Proxy.Type, "Fem::ExplorationDOE")


@needs_freecad
class TestExplorationOptimization(unittest.TestCase):
    """Objectives, constraints and the optimization study, end to end."""

    def setUp(self):
        import FreeCAD

        import ObjectsFem
        from femtools import explorationpromote
        from femtools import explorationutils

        self.FreeCAD = FreeCAD
        self.tmp = tempfile.mkdtemp(prefix="fem_expl_opt_")
        self.doc = FreeCAD.newDocument("TestExplorationOptimization")
        self.box = self.doc.addObject("Part::Box", "Box")
        self.box.Length, self.box.Width, self.box.Height = 8000, 1000, 1000
        self.doc.recompute()
        analysis = ObjectsFem.makeAnalysis(self.doc, "Analysis")
        for prop in ("Width", "Height"):
            var = explorationpromote.promote(self.box, prop, analysis)
            var.Lower, var.Upper = 500.0, 1500.0
        self.pset = explorationutils.get_parameter_set(analysis)
        volume = ObjectsFem.makeExplorationOutput(self.doc, "Volume")
        volume.Source = model.EXPRESSION
        volume.Expression = "Box.Shape.Volume"
        self.pset.addObject(volume)
        area = ObjectsFem.makeExplorationOutput(self.doc, "Section")
        area.Source = model.EXPRESSION
        area.Expression = "Box.Width * Box.Height"
        self.pset.addObject(area)

        self.objective = ObjectsFem.makeExplorationObjective(self.doc, "MinVolume")
        self.objective.Output = volume
        self.pset.addObject(self.objective)
        self.constraint = ObjectsFem.makeExplorationConstraint(self.doc, "MinSection")
        self.constraint.Output = area
        self.constraint.Operator = model.GE
        self.constraint.Limit = 900000.0
        self.pset.addObject(self.constraint)

        self.study = ObjectsFem.makeExplorationOptimization(self.doc, "Optimization")
        self.study.ParameterSet = self.pset
        self.study.Algorithm = "DifferentialEvolution"
        self.study.PopulationSize = 8
        self.study.MaxEvaluations = 64
        self.study.Stagnation = 0
        self.study.Seed = 5
        analysis.addObject(self.study)
        self.doc.recompute()

    def tearDown(self):
        self.FreeCAD.closeDocument(self.doc.Name)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_objects_have_their_types(self):
        from femtools import explorationutils

        self.assertEqual(self.objective.Proxy.Type, explorationutils.OBJECTIVE_TYPE)
        self.assertEqual(self.constraint.Proxy.Type, explorationutils.CONSTRAINT_TYPE)
        self.assertEqual(self.study.Proxy.Type, explorationutils.OPTIMIZATION_TYPE)
        self.assertEqual(explorationutils.get_objectives(self.pset), [self.objective])
        self.assertEqual(explorationutils.get_constraints(self.pset), [self.constraint])

    def test_the_spec_carries_objective_constraint_and_method(self):
        from femexploration import api

        spec = api.study_spec(self.study)
        self.assertEqual(spec.study_type, "Optimization")
        self.assertEqual(spec.method["algorithm"], "DifferentialEvolution")
        self.assertEqual(spec.method["max_evaluations"], 64)
        self.assertEqual(spec.method["constraints"], "FeasibilityRules")
        self.assertEqual([o.response for o in spec.objectives], ["Volume"])
        self.assertEqual(spec.constraints[0].operator, model.GE)
        self.assertEqual(spec.constraints[0].limit, 900000.0)

    def test_the_optimization_runs_and_respects_the_constraint(self):
        """Minimise volume with Width*Height >= 0.9e6: the answer is on that boundary."""
        from femexploration import api

        run_dir = os.path.join(self.tmp, "run")
        result = api.run_study(self.study, run_dir=run_dir)
        self.assertEqual(len(result.failed), 0, [r.error for r in result.failed])
        self.assertLessEqual(len(result.results), 64)
        best = result.best()
        self.assertIsNotNone(best)
        self.assertTrue(best.feasible)
        section = best.outputs["Section"]
        self.assertGreaterEqual(section, 900000.0 - 1e-6)
        self.assertLess(section, 1.3e6)
        # the table carries objective and violation columns for the charts
        table = [c for c in self.study.Group if c.Proxy.Type == "Fem::ExplorationDesignPoints"][0]
        self.assertIn("objective", table.ColumnRoles)
        self.assertIn("violation", table.ColumnRoles)
        self.assertEqual(table.BestPoint, best.point_id)
        rows = table.Proxy.rows(table)
        self.assertTrue(any(r["violations"].get("MinSection", 0) > 0 for r in rows))

    def test_the_study_refuses_to_run_without_an_objective(self):
        from femexploration import api

        self.doc.removeObject(self.objective.Name)
        self.doc.recompute()
        with self.assertRaises(ValueError):
            api.run_study(self.study, run_dir=os.path.join(self.tmp, "none"))

    def test_settings_survive_save_and_reopen(self):
        path = os.path.join(self.tmp, "opt.FCStd")
        self.study.Algorithm = "NSGA2"
        self.study.ConstraintHandling = "Penalty"
        self.doc.saveAs(path)
        self.FreeCAD.closeDocument(self.doc.Name)
        self.doc = self.FreeCAD.openDocument(path)
        study = self.doc.getObject("Optimization")
        self.assertEqual(study.Algorithm, "NSGA2")
        self.assertEqual(study.ConstraintHandling, "Penalty")
        objective = self.doc.getObject("MinVolume")
        self.assertEqual(objective.Output.Name, "Volume")
        self.assertEqual(self.doc.getObject("MinSection").Limit, 900000.0)


@needs_freecad
class TestLocalParallelBackend(unittest.TestCase):
    """Several FreeCADCmd workers on this machine."""

    def setUp(self):
        import FreeCAD

        import ObjectsFem
        from femtools import explorationpromote
        from femtools import explorationutils

        self.FreeCAD = FreeCAD
        self.tmp = tempfile.mkdtemp(prefix="fem_expl_par_")
        self.run_dir = os.path.join(self.tmp, "run")
        self.doc = FreeCAD.newDocument("TestLocalParallel")
        self.box = self.doc.addObject("Part::Box", "Box")
        self.box.Length, self.box.Width, self.box.Height = 8000, 1000, 1000
        self.doc.recompute()
        self.analysis = ObjectsFem.makeAnalysis(self.doc, "Analysis")
        for prop in ("Width", "Height"):
            var = explorationpromote.promote(self.box, prop, self.analysis)
            var.Lower, var.Upper, var.LevelCount = 500.0, 1500.0, 3
        pset = explorationutils.get_parameter_set(self.analysis)
        out = ObjectsFem.makeExplorationOutput(self.doc, "Volume")
        out.Source = model.EXPRESSION
        out.Expression = "Box.Shape.Volume"
        pset.addObject(out)
        self.sweep = ObjectsFem.makeExplorationSweep(self.doc, "Sweep")
        self.sweep.ParameterSet = pset
        self.sweep.MaxParallel = 2
        self.analysis.addObject(self.sweep)
        self.doc.recompute()

    def tearDown(self):
        self.FreeCAD.closeDocument(self.doc.Name)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _backend(self, **kwargs):
        from femexploration.backends.localparallel import LocalParallelBackend
        from femexploration.cancel import CancelToken

        token = CancelToken(self.run_dir)
        return LocalParallelBackend(self.doc, cancel=token, **kwargs), token

    def _drive(self, backend, token):
        from femexploration import api
        from femexploration import engine
        from femexploration import planner as planner_module
        from femexploration.store import PointStore

        spec = api.study_spec(self.sweep)
        store = PointStore(self.run_dir).prepare()
        token.reset()
        return engine.run_study(spec, store, backend, planner_module.make_planner(spec), cancel=token)

    def test_the_backend_is_offered(self):
        from femexploration import backends

        self.assertIn("LocalParallel", backends.available())

    def test_a_sweep_runs_across_workers(self):
        from femexploration import api

        result = api.run_study(self.sweep, run_dir=self.run_dir, backend_name="LocalParallel")
        self.assertEqual(len(result.failed), 0, [r.error for r in result.failed])
        self.assertEqual(len(result.completed), 9)
        workers = {r.worker for r in result.completed}
        self.assertEqual(workers, {"slot-0", "slot-1"})
        volumes = sorted(r.outputs["Volume"] for r in result.completed)
        self.assertAlmostEqual(volumes[0], 8000 * 500 * 500, delta=1e-3)
        self.assertAlmostEqual(volumes[-1], 8000 * 1500 * 1500, delta=1e-3)
        # the workers' journal reached the document
        table = [c for c in self.sweep.Group if c.Proxy.Type == "Fem::ExplorationDesignPoints"][0]
        self.assertEqual(table.Count, 9)

    def test_points_of_a_dying_worker_are_retried_once_then_marked_lost(self):
        launches = []

        backend, token = self._backend()
        real_command = backend.command

        def command(job_path):
            launches.append(job_path)
            return [shutil.which("python3") or "python3", "-c", "import sys; sys.exit(3)"]

        backend.command = command
        result = self._drive(backend, token)
        states = {r.state for r in result.results}
        self.assertEqual(states, {model.WORKER_LOST})
        self.assertEqual(len(result.results), 9)
        # 9 points on 2 slots: 2 jobs, each retried once
        self.assertEqual(len(launches), 4)
        self.assertIn("exited with code 3", result.results[0].error)

    @unittest.skipUnless(hasattr(os, "killpg"), "POSIX only")
    def test_cleanup_reaps_every_worker_group(self):
        import subprocess
        import time

        from femexploration import api
        from femexploration.model import DesignVector
        from femexploration.store import PointStore

        backend, token = self._backend()
        backend.cancel_grace = 0.2
        # start_new_session already made the worker a group leader, so a
        # second setsid() would fail; the real stub guards it the same way
        backend.command = lambda job_path: [
            shutil.which("python3") or "python3",
            "-c",
            "import os, subprocess, time\n"
            "try:\n    os.setsid()\nexcept OSError:\n    pass\n"
            "subprocess.Popen(['sleep', '60'])\ntime.sleep(60)\n",
        ]
        spec = api.study_spec(self.sweep)
        store = PointStore(self.run_dir).prepare()
        backend.prepare(spec, store)
        backend.submit([DesignVector(values={"Width": 500.0, "Height": 500.0}, point_id="x"),
                        DesignVector(values={"Width": 600.0, "Height": 500.0}, point_id="y")])
        pids = [w.process.pid for w in backend._workers]
        self.assertEqual(len(pids), 2)
        deadline = time.time() + 5
        while time.time() < deadline:
            groups = [subprocess.run(["pgrep", "-g", str(pid)], capture_output=True, text=True).stdout.split()
                      for pid in pids]
            if all(len(g) >= 2 for g in groups):
                break
            time.sleep(0.05)
        self.assertTrue(all(len(g) >= 2 for g in groups), "grandchildren never appeared")

        backend.cancel()
        backend.cleanup()
        deadline = time.time() + 5
        while time.time() < deadline:
            left = [subprocess.run(["pgrep", "-g", str(pid)], capture_output=True, text=True).stdout.split()
                    for pid in pids]
            if not any(left):
                break
            time.sleep(0.05)
        self.assertFalse(any(left), f"processes survived: {left}")


@needs_freecad
@unittest.skipUnless(_FreeCAD is not None and _FreeCAD.GuiUp, "requires a GUI session")
class TestDOEPanel(unittest.TestCase):
    """The design of experiments panel previews the plan it will run."""

    def setUp(self):
        import FreeCAD

        import ObjectsFem
        from femtools import explorationpromote
        from femtools import explorationutils

        self.FreeCAD = FreeCAD
        self.doc = FreeCAD.newDocument("TestDOEPanel")
        box = self.doc.addObject("Part::Box", "Box")
        self.doc.recompute()
        analysis = ObjectsFem.makeAnalysis(self.doc, "Analysis")
        for prop in ("Width", "Height", "Length"):
            explorationpromote.promote(box, prop, analysis)
        self.doc.recompute()
        pset = explorationutils.get_parameter_set(analysis)
        out = ObjectsFem.makeExplorationOutput(self.doc, "Volume")
        out.Source = model.EXPRESSION
        out.Expression = "Box.Shape.Volume"
        pset.addObject(out)
        self.doe = ObjectsFem.makeExplorationDOE(self.doc, "DOE")
        self.doe.ParameterSet = pset
        self.doe.PointCount = 12
        analysis.addObject(self.doe)
        self.doc.recompute()

        from femtaskpanels import task_exploration_doe

        self.panel = task_exploration_doe._TaskPanel(self.doe)

    def tearDown(self):
        self.FreeCAD.closeDocument(self.doc.Name)

    def test_the_preview_follows_the_plan(self):
        self.panel.plan.setCurrentText("LatinHypercube")
        self.assertIn("12 design points", self.panel.preview.text())
        self.assertEqual(self.panel.table.rowCount(), 12)
        self.panel.plan.setCurrentText("BoxBehnken")
        self.assertIn("13 design points", self.panel.preview.text())
        self.assertFalse(self.panel.count.isEnabled())
        self.panel.plan.setCurrentText("CentralComposite")
        self.assertIn("15 design points", self.panel.preview.text())
        self.assertTrue(self.panel.alpha.isEnabled())

    def test_the_count_is_written_back(self):
        self.panel.plan.setCurrentText("Sobol")
        self.panel.count.setValue(9)
        self.panel.set_object_params()
        self.assertEqual(self.doe.Plan, "Sobol")
        self.assertEqual(self.doe.PointCount, 9)


@needs_freecad
@unittest.skipUnless(_FreeCAD is not None and _FreeCAD.GuiUp, "requires a GUI session")
class TestOptimizationPanels(unittest.TestCase):
    """The parameter set panel's objective rows and the optimization panel."""

    def setUp(self):
        import FreeCAD

        import ObjectsFem
        from femtools import explorationpromote
        from femtools import explorationutils

        self.FreeCAD = FreeCAD
        self.doc = FreeCAD.newDocument("TestOptimizationPanels")
        box = self.doc.addObject("Part::Box", "Box")
        self.doc.recompute()
        analysis = ObjectsFem.makeAnalysis(self.doc, "Analysis")
        for prop in ("Width", "Height"):
            explorationpromote.promote(box, prop, analysis)
        self.doc.recompute()
        self.pset = explorationutils.get_parameter_set(analysis)
        out = ObjectsFem.makeExplorationOutput(self.doc, "Volume")
        out.Source = model.EXPRESSION
        out.Expression = "Box.Shape.Volume"
        self.pset.addObject(out)
        self.study = ObjectsFem.makeExplorationOptimization(self.doc, "Optimization")
        self.study.ParameterSet = self.pset
        analysis.addObject(self.study)
        self.doc.recompute()

    def tearDown(self):
        self.FreeCAD.closeDocument(self.doc.Name)

    def test_adding_an_objective_through_the_panel(self):
        from femtaskpanels import task_exploration_parameters
        from femtools import explorationutils

        panel = task_exploration_parameters._TaskPanel(self.pset)
        self.assertEqual(panel.objective_model.rowCount(), 0)
        panel.add_objective()
        panel.add_constraint()
        self.assertEqual(panel.objective_model.rowCount(), 1)
        self.assertEqual(panel.constraint_model.rowCount(), 1)
        objective = explorationutils.get_objectives(self.pset)[0]
        self.assertEqual(objective.Output.Label, "Volume")
        self.assertEqual(panel.objective_model.output_labels(), ["Volume"])
        self.doc.abortTransaction()

    def test_the_optimization_panel_previews_the_first_batch(self):
        import ObjectsFem

        objective = ObjectsFem.makeExplorationObjective(self.doc, "MinVolume")
        objective.Output = self.doc.getObject("Volume")
        self.pset.addObject(objective)
        self.doc.recompute()
        from femtaskpanels import task_exploration_optimization

        panel = task_exploration_optimization._TaskPanel(self.study)
        panel.plan.setCurrentText("NelderMead")
        self.assertEqual(panel.table.rowCount(), 3)  # k + 1 simplex vertices
        panel.plan.setCurrentText("DifferentialEvolution")
        panel.population_size.setValue(6)
        self.assertEqual(panel.table.rowCount(), 6)
        panel.evaluations.setValue(42)
        panel.set_object_params()
        self.assertEqual(self.study.Algorithm, "DifferentialEvolution")
        self.assertEqual(self.study.MaxEvaluations, 42)
        self.assertEqual(self.study.PopulationSize, 6)


@needs_freecad
@unittest.skipUnless(_FreeCAD is not None and _FreeCAD.GuiUp, "requires a GUI session")
class TestSweepPanelOutput(unittest.TestCase):
    """Parsing the child process's output.

    The sweep runs in a separate FreeCADCmd process and reports progress as one
    JSON object per line, with anything unrecognised passed through to the log.
    The trap is that "unrecognised" is not the same as "not JSON": FreeCAD
    prints bare numbers on stdout, and ``0`` parses perfectly well - to an int.
    Calling ``.get`` on that raised inside a Qt signal handler, once per line,
    which buried the report view under AttributeErrors and lost the real
    solver output.
    """

    def setUp(self):
        import FreeCAD

        import ObjectsFem
        from femtools import explorationpromote
        from femtools import explorationutils

        self.FreeCAD = FreeCAD
        self.doc = FreeCAD.newDocument("TestSweepPanelOutput")
        box = self.doc.addObject("Part::Box", "Box")
        self.doc.recompute()
        analysis = ObjectsFem.makeAnalysis(self.doc, "Analysis")
        explorationpromote.promote(box, "Height", analysis)
        self.doc.recompute()

        pset = explorationutils.get_parameter_set(analysis)
        out = ObjectsFem.makeExplorationOutput(self.doc, "Volume")
        out.Source = model.EXPRESSION
        out.Expression = "Box.Shape.Volume"
        pset.addObject(out)

        sweep = ObjectsFem.makeExplorationSweep(self.doc, "Sweep")
        sweep.ParameterSet = pset
        analysis.addObject(sweep)
        self.doc.recompute()

        from femtaskpanels import task_exploration_sweep

        self.panel = task_exploration_sweep._TaskPanel(sweep)

    def tearDown(self):
        self.FreeCAD.closeDocument(self.doc.Name)

    def _log(self):
        return self.panel.text_log.toPlainText()

    def _status(self):
        return self.panel.run_widget.l_status.text()

    def _cell(self, row, column, role=None):
        from PySide import QtCore

        model = self.panel.results.model
        return model.data(model.index(row, column), role or QtCore.Qt.DisplayRole)

    def test_a_bare_number_is_not_an_event(self):
        """The exact line that broke it: valid JSON, but an int.

        It must not be read as a progress record, and since FreeCAD prints
        these constantly it is dropped rather than logged.
        """
        self.panel._handle("0")          # must not raise
        self.assertNotIn("0", self._log())

    def test_freecad_banner_lines_are_dropped(self):
        for line in (
            "FreeCAD 26.3.0, Libs: 26.3.0devR48263 (Git)",
            "(C) 2001-2026 FreeCAD contributors",
            "FreeCAD is free and open-source software licensed under LGPL2+",
        ):
            self.panel._handle(line)
        self.assertEqual(self._log(), "")

    def test_other_bare_json_scalars_do_not_raise(self):
        for line in ('"a string"', "true", "null", "[1, 2, 3]"):
            self.panel._handle(line)
        self.assertIn("a string", self._log())
        self.assertIn("[1, 2, 3]", self._log())

    def test_plain_console_output_is_kept(self):
        """Solver messages are the reason the detail view exists."""
        line = "[{'ccx_elset': 'Evolumes', 'mat_obj_name': 'Steel'}]"
        self.panel._handle(line)
        self.assertIn("ccx_elset", self._log())

    def test_a_point_event_fills_the_results_table(self):
        """The whole point of the rework: results, not a wall of chatter."""
        self.panel._handle(
            '{"event": "progress", "total": 3, "completed": 1, "message": "x",'
            ' "point": {"id": "abc123", "state": "completed",'
            ' "inputs": {"Input": 400.0}, "outputs": {"Volume": 1234.5}}}'
        )
        self.assertEqual(self.panel.results.model.rowCount(), 1)
        self.assertEqual(self._cell(0, 0), "completed")
        self.assertIn("400", self._status())

    def test_a_repeated_point_updates_its_row_rather_than_adding_one(self):
        event = ('{"event": "progress", "total": 3, "completed": %d, "point":'
                 ' {"id": "abc123", "state": "%s", "inputs": {"Input": 400.0},'
                 ' "outputs": {}}}')
        self.panel._handle(event % (1, "running"))
        self.panel._handle(event % (1, "completed"))
        self.assertEqual(self.panel.results.model.rowCount(), 1)
        self.assertEqual(self._cell(0, 0), "completed")

    def test_a_failed_point_is_marked_and_carries_its_error(self):
        from PySide import QtCore

        self.panel._handle(
            '{"event": "progress", "total": 3, "completed": 1, "point":'
            ' {"id": "bad", "state": "solve_failed", "error": "ccx exited 1",'
            ' "inputs": {"Input": 400.0}, "outputs": {}}}'
        )
        self.assertEqual(self._cell(0, 0), "solve_failed")
        self.assertIn("ccx exited 1", self._cell(0, 0, QtCore.Qt.ToolTipRole))

    def test_a_dict_without_an_event_key_is_passed_through(self):
        self.panel._handle('{"something": "else"}')
        self.assertIn("something", self._log())

    def test_a_real_progress_event_updates_the_bar(self):
        self.panel._handle(
            '{"event": "progress", "total": 9, "completed": 4, "message": "dp0004 completed"}'
        )
        bar = self.panel.run_widget.pb_progress
        self.assertEqual(bar.maximum(), 9)
        self.assertEqual(bar.value(), 4)
        self.assertIn("dp0004 completed", self._log())

    def test_the_dialog_is_three_boxes(self):
        """Settings, run and results each collapse on their own."""
        self.assertEqual(len(self.panel.form), 3)
        self.assertIs(self.panel.form[1], self.panel.run_widget)

    def test_the_run_widget_has_what_the_base_panel_expects(self):
        self.assertIs(self.panel.text_log, self.panel.run_widget.te_output)
        self.assertIs(self.panel.text_time, self.panel.run_widget.l_time)

    def test_a_split_event_is_reassembled(self):
        """A pipe can deliver half a record; the half must not become text."""
        from femexploration import events

        buffer = events.LineBuffer()
        first = buffer.feed('{"event": "progress", "total": 9, ')
        rest = buffer.feed('"completed": 4, "message": "dp0004 completed"}\n')
        self.assertEqual(first, [])
        self.assertEqual(len(rest), 1)
        self.panel._handle(rest[0])
        self.assertEqual(self.panel.run_widget.pb_progress.value(), 4)

    def test_a_malformed_event_cannot_raise(self):
        """This runs in a Qt signal handler, where an exception has nowhere to
        go, so a bad record must degrade to a log line."""
        self.panel._handle('{"event": "progress", "total": "not a number"}')
        self.assertTrue(self._log())

    def test_empty_lines_are_ignored(self):
        before = self._log()
        self.panel._handle("")
        self.panel._handle("   ")
        self.assertEqual(self._log(), before)


@needs_freecad
class TestSolverRegistry(unittest.TestCase):
    """The solver plugin registry.

    Its whole purpose is that a solver which will never ship with FreeCAD can
    be added without patching the workbench, so the cases that matter are the
    ones an add-on exercises: registering, being found, being run, and being
    offered in the menu.
    """

    def setUp(self):
        from femsolver import registry

        self.registry = registry
        self.added = []

    def tearDown(self):
        for type_id in self.added:
            self.registry.unregister_solver(type_id)

    def _register(self, **kwargs):
        kwargs.setdefault("type_id", "Fem::SolverUnitTest")
        kwargs.setdefault("name", "UnitTestSolver")
        plugin = self.registry.register_solver(self.registry.SolverPlugin(**kwargs))
        self.added.append(plugin.type_id)
        return plugin

    def test_the_shipped_solvers_are_registered(self):
        ids = self.registry.solver_type_ids()
        for expected in (
            "Fem::SolverCalculiX",
            "Fem::SolverElmer",
            "Fem::SolverZ88",
            "Fem::SolverMystran",
        ):
            self.assertIn(expected, ids)

    def test_the_dropdown_keeps_its_original_contents_and_order(self):
        """The DefaultSolver preference is an index into this list, so its
        order is part of the interface, not an implementation detail.

        Addons register after the built-ins, so anything installed on this
        machine may follow; the shipped four must come first, in this order.
        """
        self.assertEqual(
            self.registry.command_names()[:4],
            [
                "FEM_SolverCalculiX",
                "FEM_SolverElmer",
                "FEM_SolverMystran",
                "FEM_SolverZ88",
            ],
        )

    def test_a_solver_object_resolves_to_its_plugin(self):
        import FreeCAD

        import ObjectsFem

        doc = FreeCAD.newDocument("TestSolverRegistry")
        try:
            for maker, expected in (
                ("makeSolverCalculiX", "Fem::SolverCalculiX"),
                ("makeSolverElmer", "Fem::SolverElmer"),
                ("makeSolverZ88", "Fem::SolverZ88"),
            ):
                obj = getattr(ObjectsFem, maker)(doc)
                plugin = self.registry.get_solver_for(obj)
                self.assertIsNotNone(plugin, maker)
                self.assertEqual(plugin.type_id, expected)
        finally:
            FreeCAD.closeDocument(doc.Name)

    def test_capabilities_are_queryable(self):
        equations = [p.type_id for p in self.registry.with_capability(self.registry.CAP_EQUATIONS)]
        self.assertIn("Fem::SolverElmer", equations)
        self.assertNotIn("Fem::SolverZ88", equations)

    def test_registering_a_duplicate_is_refused(self):
        self._register()
        with self.assertRaises(ValueError):
            self.registry.register_solver(
                self.registry.SolverPlugin(type_id="Fem::SolverUnitTest", name="Other")
            )

    def test_replace_is_possible_when_asked_for(self):
        self._register()
        self.registry.register_solver(
            self.registry.SolverPlugin(type_id="Fem::SolverUnitTest", name="Replacement"),
            replace=True,
        )
        self.assertEqual(self.registry.get_solver("Fem::SolverUnitTest").name, "Replacement")

    def test_registering_adds_binary_settings(self):
        """An add-on solver must reach get_binary through the same machinery
        the built-ins use, not a parallel one."""
        from femsolver import settings

        self._register(
            settings={
                "UnitTestSolver": settings._SolverDlg(
                    default="unittest-solve",
                    param_path="User parameter:BaseApp/Preferences/Mod/Fem/UnitTest",
                    custom_path="unittestBinaryPath",
                )
            }
        )
        self.assertIn("UnitTestSolver", settings._SOLVER_PARAM)
        self.assertEqual(settings._SOLVER_PARAM["UnitTestSolver"].default, "unittest-solve")

    def test_late_registration_is_announced(self):
        """An add-on loaded after the workbench built its menus would
        otherwise be in the registry but absent from every menu."""
        seen = []
        self.registry.on_register(seen.append)
        plugin = self._register()
        self.assertIn(plugin, seen)

    def test_a_plugin_without_tools_falls_back_to_the_machine_path(self):
        plugin = self._register()
        self.assertIsNone(plugin.make_tools(None))

    def test_validation_defaults_to_the_calculix_rules(self):
        plugin = self._register()
        self.assertEqual(plugin.check_module, "femtools.checksanalysis")


if __name__ == "__main__":
    unittest.main()
