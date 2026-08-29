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

"""Translating between document objects and the plain data model.

The document objects are what the user edits and what the ``.FCStd`` stores;
:class:`femexploration.model.StudySpec` is what the engine, the workers and the
MCP layer consume.  This module is the only place that knows both.

It also computes the model fingerprint: a hash of everything in the analysis
that is *not* a design variable.  That is deliberately over-inclusive - any
model edit at all invalidates every cached point - because reusing a result
computed from a model that has since changed would silently corrupt an
engineering answer, and recomputing merely costs time.
"""

__title__ = "FreeCAD FEM design exploration specification"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import re

import FreeCAD

from femtools import explorationutils

from . import hashing
from .model import Constraint
from .model import Objective
from .model import Response
from .model import StudySpec
from .model import Variable

#: Property status flags that exclude a property from the fingerprint.  Output
#: properties are results, not inputs, so hashing them would make every point
#: invalidate the next one.
_SKIP_STATUS = ("Output", "Transient", "Hidden")

#: Matches the memory address in a default object repr, e.g.
#: ``<Material at 0x60aeecd4d270>``.  Such a repr identifies an *instance*, not
#: a value: it changes whenever the object is reallocated, which happens
#: routinely during a run.  Hashing one makes the fingerprint drift for no
#: reason and silently defeats the cache - intermittently, which is worse.
_ADDRESS = re.compile(r"0x[0-9a-fA-F]+")

#: Properties that change without changing the answer.
_SKIP_NAMES = frozenset(
    {
        "Label",
        "Label2",
        "Visibility",
        "ExpressionEngine",
        "Proxy",
        "Shape",
        "FemMesh",
        "Stats",
        "NodeNumbers",
        "Group",
    }
)


# ************************************************************************************************
# document objects -> data model


def variable_from_object(obj):
    return Variable(
        name=obj.Name,
        label=obj.Label,
        target=obj.Target.Name if obj.Target else "",
        target_property=obj.TargetProperty,
        target_type_id=obj.TargetTypeId,
        unit=obj.Unit,
        var_type=obj.VarType,
        lower=float(obj.Lower),
        upper=float(obj.Upper),
        nominal=float(obj.Nominal),
        levels=[float(v) for v in obj.Levels],
        enum_levels=list(obj.EnumLevels),
        scale=obj.Scale,
        level_count=int(obj.LevelCount),
        geometry_affecting=bool(obj.GeometryAffecting),
        active=bool(obj.Active),
    )


def response_from_object(obj):
    return Response(
        name=obj.Name,
        label=obj.Label,
        unit=obj.Unit,
        source=obj.Source,
        result_object=obj.ResultObject.Name if obj.ResultObject else "",
        quantity=obj.Quantity,
        aggregate=obj.Aggregate,
        expression=obj.Expression,
        extractor=obj.Extractor.Name if obj.Extractor else "",
        column=obj.Column,
        reduction=obj.Reduction,
        load_step=int(obj.LoadStep),
    )


def objective_from_object(obj):
    return Objective(
        name=obj.Name,
        response=obj.Output.Name if obj.Output else "",
        direction=obj.Direction,
        target_value=float(obj.TargetValue),
        weight=float(obj.Weight),
        active=bool(obj.Active),
    )


def constraint_from_object(obj):
    return Constraint(
        name=obj.Name,
        response=obj.Output.Name if obj.Output else "",
        operator=obj.Operator,
        limit=float(obj.Limit),
        tolerance=float(obj.Tolerance),
        active=bool(obj.Active),
        auto=bool(getattr(obj, "Auto", False)),
    )


def spec_from_study(study):
    """Build the engine's view of *study*, a study document object."""
    analysis = study.getParentGroup()
    if analysis is None:
        raise ValueError(f"{study.Label} is not inside an analysis")

    param_set = study.ParameterSet or explorationutils.get_parameter_set(analysis)
    if param_set is None:
        raise ValueError(
            f"{study.Label} has no parameter set; add design variables first"
        )

    doc = study.Document
    variables = [variable_from_object(o) for o in explorationutils.get_inputs(param_set)]
    responses = [response_from_object(o) for o in explorationutils.get_outputs(param_set)]
    objectives = [objective_from_object(o) for o in explorationutils.get_objectives(param_set)]
    constraints = [constraint_from_object(o) for o in explorationutils.get_constraints(param_set)]

    spec = StudySpec(
        name=study.Label,
        study_type=_study_type(study),
        variables=variables,
        responses=responses,
        objectives=objectives,
        constraints=constraints,
        method=_method(study),
        seed=int(getattr(study, "Seed", 0)),
        backend=str(getattr(study, "Backend", "Serial")),
        max_parallel=int(getattr(study, "MaxParallel", 0)),
        points_per_job=int(getattr(study, "PointsPerJob", 0)),
        retain=str(getattr(study, "Retain", "Failed")),
        resume_from_cache=bool(getattr(study, "ResumeFromCache", True)),
        timeout=float(getattr(study, "Timeout", 0.0)),
        failure_limit=int(getattr(study, "FailureLimit", 0)),
        cluster_config=str(getattr(study, "ClusterConfig", "") or ""),
        document=doc.Name,
        analysis=analysis.Name,
        solver=_solver_name(analysis),
        model_hash=model_hash(analysis, variables),
    )
    return spec


def _study_type(study):
    from femtools.explorationutils import DOE_TYPE
    from femtools.explorationutils import OPTIMIZATION_TYPE
    from femtools.explorationutils import RESPONSE_SURFACE_TYPE
    from femtools.explorationutils import SWEEP_TYPE

    return {
        SWEEP_TYPE: "Sweep",
        DOE_TYPE: "DOE",
        OPTIMIZATION_TYPE: "Optimization",
        RESPONSE_SURFACE_TYPE: "ResponseSurface",
    }.get(study.Proxy.Type, "Sweep")


def _method(study):
    method = {}
    if hasattr(study, "Plan"):
        method["plan"] = str(study.Plan)
    if hasattr(study, "PointCount"):
        method["point_count"] = int(study.PointCount)
    if hasattr(study, "Alpha"):
        method["alpha"] = str(study.Alpha)
    if hasattr(study, "Algorithm"):
        method["algorithm"] = str(study.Algorithm)
        method["max_evaluations"] = int(study.MaxEvaluations)
        method["population"] = int(study.PopulationSize)
        method["tolerance"] = float(study.Tolerance)
        method["stagnation"] = int(study.Stagnation)
        method["wall_clock"] = float(study.WallClock)
        method["constraints"] = str(study.ConstraintHandling)
        method["penalty"] = float(study.PenaltyFactor)
    return method


def _solver_name(analysis):
    from femtools.femutils import is_derived_from

    for member in analysis.Group:
        if is_derived_from(member, "Fem::FemSolverObjectPython"):
            return member.Name
    return ""


# ************************************************************************************************
# the model fingerprint


def model_hash(analysis, variables):
    """Hash everything in the analysis that is not a design variable."""
    driven = {(v.target, v.target_property) for v in variables}
    entries = []

    for obj in _fingerprint_objects(analysis):
        props = {}
        for name in obj.PropertiesList:
            if name in _SKIP_NAMES or (obj.Name, name) in driven:
                continue
            try:
                if any(flag in obj.getTypeOfProperty(name) for flag in _SKIP_STATUS):
                    continue
                props[name] = _stable_repr(obj.getPropertyByName(name))
            except Exception:
                # a property that cannot be read cannot affect reproducibility
                continue
        entries.append((obj.Name, obj.TypeId, props))

    return hashing.model_fingerprint(entries)


def _fingerprint_objects(analysis):
    """Everything in the document that could change a result.

    That means every object except two kinds:

    * **design exploration objects**, which describe the study and hold its
      output rather than feeding it.  Including them would be actively harmful:
      a completed run stamps results onto a design point table inside the
      analysis, so every run would invalidate the cache it had just written and
      resume would never reuse anything.
    * **the variable set**, whose values *are* the design vector.  They are
      hashed separately, per point; hashing them here as well would make every
      point in a sweep look like a different model.

    Everything else is included, deliberately including geometry that is not
    reachable from an analysis member.  It would be tempting to walk only the
    analysis and its dependencies, but a response expression can reference any
    object in the document, and a fingerprint that misses the thing a response
    actually reads would hand back a stale number - which is far worse than
    re-running a sweep because an unrelated body moved.
    """
    doc = analysis.Document
    seen = {
        obj.Name: obj
        for obj in doc.Objects
        if not _is_exploration_object(obj)
    }
    return [seen[name] for name in sorted(seen)]


def _stable_repr(value):
    """A repr that depends on the value rather than on where it happens to live.

    Memory addresses are replaced with a placeholder, so a property whose repr
    is only an instance identity contributes its type and nothing more.
    """
    return _ADDRESS.sub("0xADDR", repr(value))


def _is_exploration_object(obj):
    """True for anything belonging to the design exploration feature itself."""
    from femtools.femutils import type_of_obj

    type_string = type_of_obj(obj)
    if not isinstance(type_string, str):
        return False
    if type_string.startswith("Fem::Exploration"):
        return True
    # the variable set holds the design variables; their values are the design
    # vector, which is hashed separately and must not be hashed twice
    return obj.isDerivedFrom("App::VarSet")


##  @}
