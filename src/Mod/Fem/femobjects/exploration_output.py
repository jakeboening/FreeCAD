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

"""An output parameter: how to reduce a solved model to one number.

Three sources are offered because they answer genuinely different questions:

``ResultStat``
    A named quantity off a result object's ``Stats`` list.  Cheap, universal,
    and free of any VTK dependency, so it works in a minimal build - which
    matters because a parallel or remote worker may well be one.  This is the
    default.

``Expression``
    A FreeCAD expression, evaluated through the object's own expression engine.
    The escape hatch for anything reachable as a property: a mass from volume
    and density, a section print reaction, a derived ratio.  Because the value
    is expression-bound, the output is a real node in the dependency graph and
    recomputes on its own.

``Extractor``
    A column of a post-processing extractor's table, reduced.  This is the path
    to field and line data, and it reuses the whole existing post stack rather
    than duplicating it.
"""

__title__ = "FreeCAD FEM exploration output parameter object"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import FreeCAD

from femexploration import model
from femtools import explorationutils

from . import base_fempythonobject

_PropHelper = base_fempythonobject._PropHelper


class ExplorationOutput(base_fempythonobject.BaseFemPythonObject):
    """One response quantity extracted from a solved analysis."""

    Type = explorationutils.OUTPUT_TYPE

    def __init__(self, obj):
        super().__init__(obj)
        for prop in self._get_properties():
            prop.add_to_object(obj)
        self._apply_visibility(obj)

    def _get_properties(self):
        prop = []

        prop.append(
            _PropHelper(
                type="App::PropertyEnumeration",
                name="Source",
                group="Output",
                doc="Where the value comes from",
                value=list(model.RESPONSE_SOURCES),
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyString",
                name="Unit",
                group="Output",
                doc="Unit the extracted value is reported in",
                value="",
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyInteger",
                name="LoadStep",
                group="Output",
                doc=(
                    "Which solution stage to read; -1 is the last. Recording a "
                    "response at every stage, not only the final one, is what "
                    "lets a preload or thermal step be constrained separately"
                ),
                value=-1,
            )
        )

        # ------------------------------------------------------- ResultStat

        prop.append(
            _PropHelper(
                type="App::PropertyLink",
                name="ResultObject",
                group="Result Statistic",
                doc="Result object to read. Empty means the analysis' only result",
                value=None,
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyEnumeration",
                name="Quantity",
                group="Result Statistic",
                doc="Result quantity to read from the result object statistics",
                value=list(model.RESULT_QUANTITIES),
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyEnumeration",
                name="Aggregate",
                group="Result Statistic",
                doc="Which end of the quantity's range to take",
                value=list(model.AGGREGATES),
            )
        )

        # ------------------------------------------------------- Expression

        prop.append(
            _PropHelper(
                type="App::PropertyString",
                name="Expression",
                group="Expression",
                doc="FreeCAD expression evaluated after the solve",
                value="",
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyFloat",
                name="Value",
                group="Expression",
                doc="Most recently evaluated value. Bound to Expression when that source is used",
                value=0.0,
            )
        )

        # -------------------------------------------------------- Extractor

        prop.append(
            _PropHelper(
                type="App::PropertyLink",
                name="Extractor",
                group="Extractor",
                doc="Post processing extractor supplying the table",
                value=None,
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyString",
                name="Column",
                group="Extractor",
                doc="Column of the extractor table to reduce",
                value="",
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyEnumeration",
                name="Reduction",
                group="Extractor",
                doc="How the column is reduced to a single value",
                value=list(model.REDUCTIONS),
            )
        )

        return prop

    def _apply_visibility(self, obj):
        """Show only the property group that the selected source uses.

        The alternative - showing all three groups always - makes the property
        editor a guessing game about which fields matter.
        """
        source = obj.Source
        groups = {
            model.RESULT_STAT: ("ResultObject", "Quantity", "Aggregate"),
            model.EXPRESSION: ("Expression", "Value"),
            model.EXTRACTOR: ("Extractor", "Column", "Reduction"),
        }
        for src, names in groups.items():
            hidden = 2 if src != source else 0
            for name in names:
                try:
                    obj.setEditorMode(name, hidden)
                except Exception:
                    # property not present yet during restore; onDocumentRestored fixes it
                    pass

    def onDocumentRestored(self, obj):
        for prop in self._get_properties():
            try:
                obj.getPropertyByName(prop.name)
            except FreeCAD.Base.PropertyError:
                prop.add_to_object(obj)
        self._apply_visibility(obj)

    def onChanged(self, obj, prop):
        if prop == "Source":
            self._apply_visibility(obj)
            self._bind_value(obj)
        elif prop == "Expression":
            self._bind_value(obj)

    def _bind_value(self, obj):
        """Keep Value expression-bound to Expression.

        Binding rather than evaluating is what makes the output a real node in
        the dependency graph: it recomputes when the model does, with no
        bookkeeping here.  Callers set ``Expression`` and nothing else.
        """
        # during construction the properties are added one at a time, so both
        # Expression and Value may be missing; binding then is meaningless and
        # only produces an alarming console error
        if "Expression" not in obj.PropertiesList or "Value" not in obj.PropertiesList:
            return
        try:
            expression = obj.Expression
            bound = dict(obj.ExpressionEngine).get("Value")
        except AttributeError:
            return

        if obj.Source == model.EXPRESSION and expression:
            if bound != expression:
                try:
                    obj.setExpression("Value", expression)
                except Exception as exc:
                    FreeCAD.Console.PrintError(
                        f"{obj.Label}: cannot use {expression!r} as an expression: {exc}\n"
                    )
        elif bound is not None:
            obj.setExpression("Value", None)

    def execute(self, obj):
        return True


##  @}
