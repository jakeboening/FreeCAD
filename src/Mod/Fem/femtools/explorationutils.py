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

"""Type predicates and lookups for design exploration objects.

FEM's Python typesystem has no inheritance: ``femutils.is_of_type`` compares
``Proxy.Type`` for equality only.  So "is this any kind of study?" cannot be
asked of the type itself and needs an explicit set, the way
``femobjects/post_table.py`` handles the same problem for extractors.

Keeping those sets here, rather than scattered through the commands and task
panels, means adding a study type is a one-line change in one file.
"""

__title__ = "FreeCAD FEM design exploration utilities"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import FreeCAD

from femtools.femutils import is_of_type
from femtools.femutils import type_of_obj


# ************************************************************************************************
# the type vocabulary

PARAMETERS_TYPE = "Fem::ExplorationParameters"

INPUT_TYPE = "Fem::ExplorationInput"
OUTPUT_TYPE = "Fem::ExplorationOutput"
OBJECTIVE_TYPE = "Fem::ExplorationObjective"
CONSTRAINT_TYPE = "Fem::ExplorationConstraint"

SWEEP_TYPE = "Fem::ExplorationSweep"
DOE_TYPE = "Fem::ExplorationDOE"
OPTIMIZATION_TYPE = "Fem::ExplorationOptimization"
RESPONSE_SURFACE_TYPE = "Fem::ExplorationResponseSurface"

DESIGN_POINTS_TYPE = "Fem::ExplorationDesignPoints"

#: Every parameter kind that may live inside an ExplorationParameters group.
PARAMETER_TYPES = frozenset({INPUT_TYPE, OUTPUT_TYPE, OBJECTIVE_TYPE, CONSTRAINT_TYPE})

#: Every object that can be run as a study.
STUDY_TYPES = frozenset({SWEEP_TYPE, DOE_TYPE, OPTIMIZATION_TYPE, RESPONSE_SURFACE_TYPE})


# ************************************************************************************************
# predicates


def is_exploration_parameters(obj):
    return is_of_type(obj, PARAMETERS_TYPE)


def is_exploration_parameter(obj):
    """True for an input, output, objective or constraint object."""
    return type_of_obj(obj) in PARAMETER_TYPES


def is_exploration_input(obj):
    return is_of_type(obj, INPUT_TYPE)


def is_exploration_output(obj):
    return is_of_type(obj, OUTPUT_TYPE)


def is_exploration_objective(obj):
    return is_of_type(obj, OBJECTIVE_TYPE)


def is_exploration_constraint(obj):
    return is_of_type(obj, CONSTRAINT_TYPE)


def is_exploration_study(obj):
    """True for any runnable study object."""
    return type_of_obj(obj) in STUDY_TYPES


def is_design_points(obj):
    return is_of_type(obj, DESIGN_POINTS_TYPE)


# ************************************************************************************************
# lookups


def get_parameter_set(analysis):
    """Return the analysis' ExplorationParameters object, or None.

    There is at most one per analysis.  A study links to it explicitly rather
    than looking it up, so this is a convenience for commands and for creating
    the set on first use - not the binding itself.
    """
    if analysis is None:
        return None
    for member in analysis.Group:
        if is_exploration_parameters(member):
            return member
    return None


def get_studies(analysis):
    """Return every study object in *analysis*, in tree order."""
    if analysis is None:
        return []
    return [m for m in analysis.Group if is_exploration_study(m)]


def get_parameters(param_set, type_string=None):
    """Return the parameter objects inside *param_set*.

    With *type_string* given, filter to that one kind.
    """
    if param_set is None:
        return []
    members = [m for m in param_set.Group if is_exploration_parameter(m)]
    if type_string is None:
        return members
    return [m for m in members if is_of_type(m, type_string)]


def get_inputs(param_set):
    return get_parameters(param_set, INPUT_TYPE)


def get_outputs(param_set):
    return get_parameters(param_set, OUTPUT_TYPE)


def get_objectives(param_set):
    return get_parameters(param_set, OBJECTIVE_TYPE)


def get_constraints(param_set):
    return get_parameters(param_set, CONSTRAINT_TYPE)


def get_varset(analysis, create=False, doc=None):
    """Return the App::VarSet holding this analysis' design variables.

    Design variables are not stored on the exploration objects.  They live on a
    stock ``App::VarSet`` and the model binds to them by expression, so
    FreeCAD's own dependency graph propagates a change - there is no bespoke
    "recompute what depends on this" code anywhere in this feature.
    """
    if analysis is not None:
        for member in analysis.Group:
            if member.isDerivedFrom("App::VarSet"):
                return member
    if not create:
        return None

    doc = doc or (analysis.Document if analysis is not None else FreeCAD.ActiveDocument)
    varset = doc.addObject("App::VarSet", "DesignVariables")
    varset.Label = "Design Variables"
    if analysis is not None:
        analysis.addObject(varset)
    return varset


##  @}
