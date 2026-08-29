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

import os
import shutil
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
        return self.panel.log.toPlainText()

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
        from femexploration import api

        self.panel._prepare_results_table(api.study_spec(self.panel.obj))
        self.panel._handle(
            '{"event": "progress", "total": 3, "completed": 1, "message": "x",'
            ' "point": {"id": "abc123", "state": "completed",'
            ' "inputs": {"Input": 400.0}, "outputs": {"Volume": 1234.5}}}'
        )
        self.assertEqual(self.panel.results.rowCount(), 1)
        self.assertEqual(self.panel.results.item(0, 0).text(), "completed")
        self.assertIn("400", self.panel.status.text())

    def test_a_repeated_point_updates_its_row_rather_than_adding_one(self):
        from femexploration import api

        self.panel._prepare_results_table(api.study_spec(self.panel.obj))
        event = ('{"event": "progress", "total": 3, "completed": %d, "point":'
                 ' {"id": "abc123", "state": "%s", "inputs": {"Input": 400.0},'
                 ' "outputs": {}}}')
        self.panel._handle(event % (1, "running"))
        self.panel._handle(event % (1, "completed"))
        self.assertEqual(self.panel.results.rowCount(), 1)
        self.assertEqual(self.panel.results.item(0, 0).text(), "completed")

    def test_a_failed_point_is_marked_and_carries_its_error(self):
        from femexploration import api

        self.panel._prepare_results_table(api.study_spec(self.panel.obj))
        self.panel._handle(
            '{"event": "progress", "total": 3, "completed": 1, "point":'
            ' {"id": "bad", "state": "solve_failed", "error": "ccx exited 1",'
            ' "inputs": {"Input": 400.0}, "outputs": {}}}'
        )
        cell = self.panel.results.item(0, 0)
        self.assertEqual(cell.text(), "solve_failed")
        self.assertIn("ccx exited 1", cell.toolTip())

    def test_a_dict_without_an_event_key_is_passed_through(self):
        self.panel._handle('{"something": "else"}')
        self.assertIn("something", self._log())

    def test_a_real_progress_event_updates_the_bar(self):
        self.panel._handle(
            '{"event": "progress", "total": 9, "completed": 4, "message": "dp0004 completed"}'
        )
        self.assertEqual(self.panel.progress.maximum(), 9)
        self.assertEqual(self.panel.progress.value(), 4)
        self.assertIn("dp0004 completed", self._log())

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
        order is part of the interface, not an implementation detail."""
        self.assertEqual(
            self.registry.command_names(),
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
