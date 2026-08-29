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

"""An objective: a response to drive down, up, or towards a target.

Maximisation and target-seeking are folded into minimisation by the schema
layer, so every optimizer sees one convention and the direction lives in
exactly one place.
"""

__title__ = "FreeCAD FEM exploration objective object"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

from femexploration import model
from femtools import explorationutils

from . import base_fempythonobject

_PropHelper = base_fempythonobject._PropHelper


class ExplorationObjective(base_fempythonobject.BaseFemPythonObject):
    """One optimization objective over an output parameter."""

    Type = explorationutils.OBJECTIVE_TYPE

    def __init__(self, obj):
        super().__init__(obj)
        for prop in self._get_properties():
            prop.add_to_object(obj)

    def _get_properties(self):
        prop = []
        prop.append(
            _PropHelper(
                type="App::PropertyLink",
                name="Output",
                group="Objective",
                doc="Output parameter this objective drives",
                value=None,
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyEnumeration",
                name="Direction",
                group="Objective",
                doc="Minimize, Maximize, or reach TargetValue",
                value=list(model.DIRECTIONS),
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyFloat",
                name="TargetValue",
                group="Objective",
                doc="Value to reach when Direction is Target, in the output's unit",
                value=0.0,
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyFloat",
                name="Weight",
                group="Objective",
                doc="Relative weight when several objectives are combined into one",
                value=1.0,
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyBool",
                name="Active",
                group="Objective",
                doc="Use this objective; when off it is recorded but not optimized",
                value=True,
            )
        )
        return prop

    def onDocumentRestored(self, obj):
        from . import exploration_base

        exploration_base.restore_properties(self, obj)

    def execute(self, obj):
        return True


##  @}
