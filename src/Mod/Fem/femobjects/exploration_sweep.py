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

"""A parametric sweep: evaluate a grid of design points.

The simplest study, and the one worth having first.  Every combination of every
active variable's levels, which is exhaustive and therefore honest - the panel
shows the resulting point count before anything is solved, because the number
grows alarmingly with the third variable.
"""

__title__ = "FreeCAD FEM exploration sweep object"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

from femtools import explorationutils

from . import base_fempythonobject
from . import exploration_base

_PropHelper = base_fempythonobject._PropHelper

PLANS = ["FullFactorial", "OneAtATime"]


class ExplorationSweep(base_fempythonobject.BaseFemPythonObject):
    """A grid of design points over the analysis' design variables."""

    Type = explorationutils.SWEEP_TYPE

    def __init__(self, obj):
        super().__init__(obj)
        obj.addExtension("App::GroupExtensionPython")
        for prop in self._get_properties():
            prop.add_to_object(obj)

    def _get_properties(self):
        prop = list(exploration_base.common_study_properties())
        prop.append(
            _PropHelper(
                type="App::PropertyEnumeration",
                name="Plan",
                group="Study",
                doc=(
                    "FullFactorial evaluates every combination of levels. "
                    "OneAtATime varies each variable alone from the nominal "
                    "design, which is the basis of a sensitivity chart"
                ),
                value=list(PLANS),
            )
        )
        return prop

    def onDocumentRestored(self, obj):
        exploration_base.restore_properties(self, obj)

    def execute(self, obj):
        return True


##  @}
