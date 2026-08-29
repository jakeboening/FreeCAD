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

"""Plain data model for design exploration studies.

This module deliberately imports **nothing** from FreeCAD, PySide or numpy.
Everything here is standard library only, so the schema layer can be exercised
by ``python3 -m unittest`` without a FreeCAD build, and a remote worker can
parse a job spec before FreeCAD is even loaded.

The document objects in ``femobjects/exploration_*.py`` are the user-facing,
persistent form of these types; :mod:`femexploration.spec` translates between
the two.  Nothing below knows that FreeCAD exists.

Enumerated values are plain strings rather than ``enum.Enum`` members.  That
keeps JSON round-tripping trivial and matches the ``App::PropertyEnumeration``
values on the document objects one-for-one.
"""

__title__ = "FreeCAD FEM design exploration data model"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import json
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field

from .version import FORMAT_VERSION
from .version import is_compatible


# ************************************************************************************************
# enumerated values

# Variable.var_type
CONTINUOUS = "Continuous"
INTEGER = "Integer"
DISCRETE = "Discrete"
ENUMERATION = "Enumeration"
VAR_TYPES = (CONTINUOUS, INTEGER, DISCRETE, ENUMERATION)

# Variable.scale
LINEAR = "Linear"
LOG = "Log"
SCALES = (LINEAR, LOG)

# Response.source
RESULT_STAT = "ResultStat"
EXPRESSION = "Expression"
EXTRACTOR = "Extractor"
RESPONSE_SOURCES = (RESULT_STAT, EXPRESSION, EXTRACTOR)

# Response.quantity, for source == RESULT_STAT.  These are exactly the keys of
# femresult.resulttools.get_all_stats(), which reads the flat 26-float
# ``Stats`` list on a result object.  That list is a plain C++ property, so this
# path needs no VTK and works in a minimal build - which matters because a
# remote worker may well be one.
RESULT_QUANTITIES = (
    "U1",
    "U2",
    "U3",
    "Uabs",
    "Sabs",
    "MaxPrin",
    "MidPrin",
    "MinPrin",
    "MaxShear",
    "Peeq",
    "Temp",
    "MFlow",
    "NPress",
)

# Response.aggregate / Response.reduction
MIN = "Min"
MAX = "Max"
ABS_MAX = "AbsMax"
AGGREGATES = (MIN, MAX, ABS_MAX)
REDUCTIONS = (MIN, MAX, "Mean", "AtIndex")

# Objective.direction
MINIMIZE = "Minimize"
MAXIMIZE = "Maximize"
TARGET = "Target"
DIRECTIONS = (MINIMIZE, MAXIMIZE, TARGET)

# Constraint.operator
LE = "<="
GE = ">="
EQ = "=="
OPERATORS = (LE, GE, EQ)

# PointResult.state.  Note the deliberate split between FAILED_* states, which
# mean "this evaluation did not produce a number", and feasibility, which is a
# property of a *successful* evaluation.  Conflating the two is what made the
# reference implementation treat a crashed mesh as a merely bad design.
PENDING = "pending"
RUNNING = "running"
COMPLETED = "completed"
RECOMPUTE_FAILED = "recompute_failed"
MESH_FAILED = "mesh_failed"
SOLVE_FAILED = "solve_failed"
EXTRACT_FAILED = "extract_failed"
TIMEOUT = "timeout"
CANCELLED = "cancelled"
WORKER_LOST = "worker_lost"
OUT_OF_DATE = "out_of_date"

POINT_STATES = (
    PENDING,
    RUNNING,
    COMPLETED,
    RECOMPUTE_FAILED,
    MESH_FAILED,
    SOLVE_FAILED,
    EXTRACT_FAILED,
    TIMEOUT,
    CANCELLED,
    WORKER_LOST,
    OUT_OF_DATE,
)

#: States that mean the evaluation produced no usable outputs.
FAILED_STATES = (
    RECOMPUTE_FAILED,
    MESH_FAILED,
    SOLVE_FAILED,
    EXTRACT_FAILED,
    TIMEOUT,
    WORKER_LOST,
)

#: Implicit outputs recorded for every design point without the user asking.
#: They exist so that discretisation noise masquerading as a design gradient is
#: detectable after the fact - see the mesh convergence warning in the
#: sensitivity chart.
MESH_NODES = "_mesh_nodes"
MESH_ELEMENTS = "_mesh_elements"
IMPLICIT_OUTPUTS = (MESH_NODES, MESH_ELEMENTS)


class SchemaError(Exception):
    """Raised when persisted data cannot be interpreted by this version."""


# ************************************************************************************************
# building blocks


@dataclass
class Variable:
    """One input parameter: a numeric property promoted to a design variable.

    ``lower``, ``upper``, ``nominal`` and every entry of ``levels`` are
    expressed in ``unit``, never in FreeCAD's internal units.  The unit is
    captured once when the property is promoted and is the single place the
    conversion lives.
    """

    name: str
    target: str = ""
    target_property: str = ""
    target_type_id: str = ""
    unit: str = ""
    label: str = ""
    var_type: str = CONTINUOUS
    lower: float = 0.0
    upper: float = 1.0
    nominal: float = 0.0
    levels: list = field(default_factory=list)
    enum_levels: list = field(default_factory=list)
    scale: str = LINEAR
    level_count: int = 3
    geometry_affecting: bool = True
    active: bool = True

    def validate(self):
        if self.var_type not in VAR_TYPES:
            raise SchemaError(f"variable {self.name}: unknown var_type {self.var_type!r}")
        if self.scale not in SCALES:
            raise SchemaError(f"variable {self.name}: unknown scale {self.scale!r}")
        if self.var_type in (CONTINUOUS, INTEGER):
            if not self.upper > self.lower:
                raise SchemaError(
                    f"variable {self.name}: upper ({self.upper}) must exceed lower ({self.lower})"
                )
            if self.scale == LOG and self.lower <= 0.0:
                raise SchemaError(
                    f"variable {self.name}: log scale needs a positive lower bound, got {self.lower}"
                )
        elif self.var_type == DISCRETE and not self.levels:
            raise SchemaError(f"variable {self.name}: discrete variable has no levels")
        elif self.var_type == ENUMERATION and not self.enum_levels:
            raise SchemaError(f"variable {self.name}: enumeration variable has no enum_levels")
        if self.level_count < 2:
            raise SchemaError(f"variable {self.name}: level_count must be >= 2")

    def grid(self):
        """Return the swept values for this variable.

        Reconstructed from ``{lower, upper, level_count, scale}`` rather than
        read from a persisted array, so a saved study stays small and stays
        correct when a bound is edited.
        """
        if self.var_type == DISCRETE:
            return list(self.levels)
        if self.var_type == ENUMERATION:
            return list(range(len(self.enum_levels)))
        n = self.level_count
        if self.var_type == INTEGER:
            lo, hi = int(round(self.lower)), int(round(self.upper))
            if hi - lo + 1 <= n:
                return [float(v) for v in range(lo, hi + 1)]
            step = (hi - lo) / (n - 1)
            return sorted({float(int(round(lo + i * step))) for i in range(n)})
        if self.scale == LOG:
            import math

            lo, hi = math.log(self.lower), math.log(self.upper)
            return [math.exp(lo + (hi - lo) * i / (n - 1)) for i in range(n)]
        return [self.lower + (self.upper - self.lower) * i / (n - 1) for i in range(n)]


@dataclass
class Response:
    """One output parameter: how to reduce a solved model to a single scalar."""

    name: str
    label: str = ""
    unit: str = ""
    source: str = RESULT_STAT
    # source == RESULT_STAT
    result_object: str = ""
    quantity: str = ""
    aggregate: str = "Max"
    # source == EXPRESSION
    expression: str = ""
    # source == EXTRACTOR
    extractor: str = ""
    column: str = ""
    reduction: str = "Max"
    #: which solution stage to read; -1 means the last one
    load_step: int = -1

    def validate(self):
        if self.source not in RESPONSE_SOURCES:
            raise SchemaError(f"response {self.name}: unknown source {self.source!r}")
        if self.source == RESULT_STAT:
            if not self.quantity:
                raise SchemaError(f"response {self.name}: ResultStat source needs a quantity")
            if self.quantity not in RESULT_QUANTITIES:
                raise SchemaError(
                    f"response {self.name}: unknown result quantity {self.quantity!r}"
                )
            if self.aggregate not in AGGREGATES:
                raise SchemaError(
                    f"response {self.name}: unknown aggregate {self.aggregate!r}"
                )
        if self.source == EXPRESSION and not self.expression:
            raise SchemaError(f"response {self.name}: Expression source needs an expression")
        if self.source == EXTRACTOR and not self.extractor:
            raise SchemaError(f"response {self.name}: Extractor source needs an extractor link")


@dataclass
class Objective:
    """A response to drive up, down, or towards a target value."""

    name: str
    response: str = ""
    direction: str = MINIMIZE
    target_value: float = 0.0
    weight: float = 1.0
    active: bool = True

    def validate(self, response_names):
        if self.direction not in DIRECTIONS:
            raise SchemaError(f"objective {self.name}: unknown direction {self.direction!r}")
        if self.response not in response_names:
            raise SchemaError(f"objective {self.name}: unknown response {self.response!r}")

    def value(self, responses):
        """Return the scalar an optimizer should minimise.

        Maximisation and target-seeking are folded into minimisation here so
        every algorithm sees one convention.
        """
        raw = responses[self.response]
        if self.direction == MINIMIZE:
            return raw
        if self.direction == MAXIMIZE:
            return -raw
        return abs(raw - self.target_value)


@dataclass
class Constraint:
    """A bound on a response.

    ``auto`` marks constraints generated from the model's own physics (a
    material's yield strength, say) rather than typed by the user.
    """

    name: str
    response: str = ""
    operator: str = LE
    limit: float = 0.0
    tolerance: float = 0.0
    active: bool = True
    auto: bool = False

    def validate(self, response_names):
        if self.operator not in OPERATORS:
            raise SchemaError(f"constraint {self.name}: unknown operator {self.operator!r}")
        if self.response not in response_names:
            raise SchemaError(f"constraint {self.name}: unknown response {self.response!r}")

    def violation(self, responses):
        """Return how far this constraint is violated; 0.0 when satisfied."""
        raw = responses[self.response]
        tol = abs(self.tolerance)
        if self.operator == LE:
            return max(0.0, raw - self.limit - tol)
        if self.operator == GE:
            return max(0.0, self.limit - raw - tol)
        return max(0.0, abs(raw - self.limit) - tol)


@dataclass
class DesignVector:
    """One point in the design space: a value per active variable."""

    values: dict = field(default_factory=dict)
    point_id: str = ""

    def ordered(self, variables):
        return [self.values[v.name] for v in variables if v.name in self.values]


@dataclass
class PointResult:
    """The outcome of evaluating one design vector."""

    point_id: str
    state: str = PENDING
    inputs: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    objectives: dict = field(default_factory=dict)
    violations: dict = field(default_factory=dict)
    error: str = ""
    traceback: str = ""
    duration_s: float = 0.0
    host: str = ""
    worker: str = ""
    versions: dict = field(default_factory=dict)
    retained: bool = False
    format_version: int = FORMAT_VERSION

    @property
    def ok(self):
        return self.state == COMPLETED

    @property
    def failed(self):
        return self.state in FAILED_STATES

    @property
    def feasible(self):
        """True when the point solved *and* violates nothing.

        Deliberately False for a failed point, but distinguishable from it via
        :attr:`failed` - an optimizer must penalise the two differently.
        """
        return self.ok and not any(v > 0.0 for v in self.violations.values())

    @property
    def total_violation(self):
        return sum(v for v in self.violations.values() if v > 0.0)


@dataclass
class Progress:
    """A snapshot handed to the progress callback and serialised as NDJSON."""

    completed: int = 0
    total: int = 0
    failed: int = 0
    running: int = 0
    best_objective: float = None
    best_point_id: str = ""
    message: str = ""
    elapsed_s: float = 0.0
    #: compact summary of the design point that just finished, if any, so a
    #: caller can report "depth 400 mm gave 0.761 mm" rather than a bare hash
    point: dict = None


# ************************************************************************************************
# the study


@dataclass
class StudySpec:
    """Everything needed to run a study, with no reference to a live document."""

    name: str = "Study"
    study_type: str = "Sweep"
    variables: list = field(default_factory=list)
    responses: list = field(default_factory=list)
    objectives: list = field(default_factory=list)
    constraints: list = field(default_factory=list)
    method: dict = field(default_factory=dict)
    seed: int = 0
    backend: str = "Serial"
    max_parallel: int = 0
    points_per_job: int = 0
    retain: str = "Failed"
    resume_from_cache: bool = True
    timeout: float = 0.0
    failure_limit: int = 0
    model_hash: str = ""
    document: str = ""
    analysis: str = ""
    solver: str = ""
    format_version: int = FORMAT_VERSION

    # -------------------------------------------------------------- accessors

    @property
    def active_variables(self):
        return [v for v in self.variables if v.active]

    @property
    def active_objectives(self):
        return [o for o in self.objectives if o.active]

    @property
    def active_constraints(self):
        return [c for c in self.constraints if c.active]

    @property
    def needs_remesh(self):
        """True when any active variable feeds the meshed shape.

        When False the mesh is generated once and reused for every design
        point, which is usually the difference between a sweep that takes
        minutes and one that takes hours.
        """
        return any(v.geometry_affecting for v in self.active_variables)

    def response_names(self):
        return {r.name for r in self.responses}

    def validate(self):
        if not is_compatible(self.format_version):
            raise SchemaError(
                f"study format_version {self.format_version} != {FORMAT_VERSION}; "
                "it was written by a different version of FreeCAD"
            )
        if not self.active_variables:
            raise SchemaError("study has no active design variables")
        if not self.responses:
            raise SchemaError("study has no output parameters")
        names = set()
        for v in self.variables:
            v.validate()
            if v.name in names:
                raise SchemaError(f"duplicate variable name {v.name!r}")
            names.add(v.name)
        rnames = self.response_names()
        if len(rnames) != len(self.responses):
            raise SchemaError("duplicate response names")
        for r in self.responses:
            r.validate()
        for o in self.objectives:
            o.validate(rnames)
        for c in self.constraints:
            c.validate(rnames)
        return self

    # ---------------------------------------------------------- serialisation

    def to_dict(self):
        return asdict(self)

    def to_json(self, indent=2):
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    @classmethod
    def from_dict(cls, data):
        version = data.get("format_version", 0)
        if not is_compatible(version):
            raise SchemaError(
                f"study format_version {version} != {FORMAT_VERSION}; "
                "it was written by a different version of FreeCAD"
            )
        data = dict(data)
        data["variables"] = [Variable(**v) for v in data.get("variables", [])]
        data["responses"] = [Response(**r) for r in data.get("responses", [])]
        data["objectives"] = [Objective(**o) for o in data.get("objectives", [])]
        data["constraints"] = [Constraint(**c) for c in data.get("constraints", [])]
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise SchemaError(f"unknown study fields: {sorted(unknown)}")
        return cls(**data)

    @classmethod
    def from_json(cls, text):
        return cls.from_dict(json.loads(text))


##  @}
