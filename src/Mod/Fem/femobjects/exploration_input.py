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

"""An input parameter: a numeric property promoted to a design variable.

The object *references* a property; it does not own a copy of its value.  The
value lives where it always did - normally on an ``App::VarSet`` that the model
binds to by expression - so the model stays the single source of truth and a
study never has to push a value back to keep things consistent.

Units are captured once, here, at promotion time.  Every bound and level below
is expressed in ``Unit``, never in FreeCAD's internal millimetre/degree units,
and the one place the conversion happens is the writer table in
``femexploration/evaluate.py``.  That is deliberate: unit handling smeared
across a dozen call sites is the classic way this kind of tool goes quietly
wrong by a factor of a thousand.
"""

__title__ = "FreeCAD FEM exploration input parameter object"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import FreeCAD

from femexploration import model
from femtools import explorationutils

from . import base_fempythonobject

_PropHelper = base_fempythonobject._PropHelper


class ExplorationInput(base_fempythonobject.BaseFemPythonObject):
    """One design variable, bound to a property elsewhere in the document."""

    Type = explorationutils.INPUT_TYPE

    def __init__(self, obj):
        super().__init__(obj)
        for prop in self._get_properties():
            prop.add_to_object(obj)

    def _get_properties(self):
        prop = []

        # ------------------------------------------------------------ binding

        prop.append(
            _PropHelper(
                type="App::PropertyXLink",
                name="Target",
                group="Target",
                doc="Object owning the driven property, normally the analysis variable set",
                value=None,
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyString",
                name="TargetProperty",
                group="Target",
                doc="Name of the driven property on Target",
                value="",
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyString",
                name="TargetTypeId",
                group="Target",
                doc=(
                    "Type of the driven property, captured at promotion. "
                    "Selects how a value is written back"
                ),
                value="",
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyString",
                name="Unit",
                group="Target",
                doc=(
                    "Unit every bound and level below is expressed in, "
                    "captured at promotion"
                ),
                value="",
            )
        )

        # ------------------------------------------------------------- domain

        prop.append(
            _PropHelper(
                type="App::PropertyEnumeration",
                name="VarType",
                group="Domain",
                doc="Kind of design variable",
                value=list(model.VAR_TYPES),
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyFloat",
                name="Lower",
                group="Domain",
                doc="Lower bound, in Unit",
                value=0.0,
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyFloat",
                name="Upper",
                group="Domain",
                doc="Upper bound, in Unit",
                value=1.0,
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyFloat",
                name="Nominal",
                group="Domain",
                doc="Baseline value, in Unit. Used when the variable is inactive",
                value=0.0,
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyFloatList",
                name="Levels",
                group="Domain",
                doc="Explicit values for a discrete variable, in Unit",
                value=[],
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyStringList",
                name="EnumLevels",
                group="Domain",
                doc="Allowed strings for an enumeration variable",
                value=[],
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyEnumeration",
                name="Scale",
                group="Domain",
                doc="Spacing used when the sweep grid is built",
                value=list(model.SCALES),
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyIntegerConstraint",
                name="LevelCount",
                group="Domain",
                doc=(
                    "Number of levels for a sweep. The grid is rebuilt from the "
                    "bounds each run rather than stored, so editing a bound "
                    "cannot leave a stale value array behind"
                ),
                value=(3, 2, 1000, 1),
            )
        )

        # ------------------------------------------------------------ control

        prop.append(
            _PropHelper(
                type="App::PropertyBool",
                name="GeometryAffecting",
                group="Control",
                doc=(
                    "Whether changing this variable changes the meshed shape. "
                    "When no active variable does, the mesh is built once and "
                    "reused, which is often the difference between minutes and "
                    "hours. Detected automatically, override if wrong"
                ),
                value=True,
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyBool",
                name="Active",
                group="Control",
                doc="When false the variable is held at Nominal instead of being swept",
                value=True,
            )
        )

        return prop

    def onDocumentRestored(self, obj):
        for prop in self._get_properties():
            try:
                obj.getPropertyByName(prop.name)
            except FreeCAD.Base.PropertyError:
                prop.add_to_object(obj)

    def execute(self, obj):
        return True


##  @}
