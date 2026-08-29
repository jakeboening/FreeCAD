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

"""The parameter set: one per analysis, holding inputs, outputs and criteria.

This is the equivalent of the "Parameter Set" node in commercial design
exploration tools.  It exists as a separate object, rather than as properties
on each study, so that two studies over the same analysis share one definition
of what "Thickness" means instead of drifting apart.
"""

__title__ = "FreeCAD FEM exploration parameter set object"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import FreeCAD

from femtools import explorationutils

from . import base_fempythonobject


class ExplorationParameters(base_fempythonobject.BaseFemPythonObject):
    """Container for the design variables and responses of one analysis."""

    Type = explorationutils.PARAMETERS_TYPE

    def __init__(self, obj):
        super().__init__(obj)
        obj.addExtension("App::GroupExtensionPython")
        for prop in self._get_properties():
            prop.add_to_object(obj)

    def _get_properties(self):
        prop = []

        prop.append(
            base_fempythonobject._PropHelper(
                type="App::PropertyLink",
                name="VarSet",
                group="Parameters",
                doc=(
                    "Variable set holding the driving design variables. "
                    "Model properties bind to it by expression, so FreeCAD's "
                    "dependency graph propagates a change on its own."
                ),
                value=None,
            )
        )

        return prop

    def onDocumentRestored(self, obj):
        # add any property that did not exist when the file was saved
        for prop in self._get_properties():
            try:
                obj.getPropertyByName(prop.name)
            except FreeCAD.Base.PropertyError:
                prop.add_to_object(obj)

    def onChanged(self, obj, prop):
        # keep foreign objects out of the group; anything else in here would
        # be silently ignored by the study, which is worse than refusing it
        if prop != "Group":
            return

        children = list(obj.Group)
        allowed = [c for c in children if explorationutils.is_exploration_parameter(c)]
        if len(allowed) != len(children):
            for child in children:
                if child not in allowed:
                    FreeCAD.Console.PrintWarning(
                        f"{child.Label} is not a design exploration parameter "
                        "and cannot be added to the parameter set\n"
                    )
            obj.Group = allowed

    def execute(self, obj):
        return True


##  @}
