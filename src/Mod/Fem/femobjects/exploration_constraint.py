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

"""A constraint: a bound on a response.

A violated constraint is a number, how far past the limit the response
went, not a yes or no.  That number is what lets an optimizer find its way
back into the feasible region instead of merely learning that it left.
"""

__title__ = "FreeCAD FEM exploration constraint object"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

from femexploration import model
from femtools import explorationutils

from . import base_fempythonobject

_PropHelper = base_fempythonobject._PropHelper


class ExplorationConstraint(base_fempythonobject.BaseFemPythonObject):
    """One bound on an output parameter."""

    Type = explorationutils.CONSTRAINT_TYPE

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
                group="Constraint",
                doc="Output parameter this constraint bounds",
                value=None,
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyEnumeration",
                name="Operator",
                group="Constraint",
                doc="How the output relates to the limit",
                value=list(model.OPERATORS),
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyFloat",
                name="Limit",
                group="Constraint",
                doc="Bound, in the output's unit",
                value=0.0,
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyFloat",
                name="Tolerance",
                group="Constraint",
                doc="Slack allowed before the constraint counts as violated",
                value=0.0,
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyBool",
                name="Active",
                group="Constraint",
                doc="Enforce this constraint; when off its violation is still recorded",
                value=True,
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyBool",
                name="Auto",
                group="Constraint",
                doc="Generated from the model's own physics rather than typed by the user",
                value=False,
            )
        )
        return prop

    def onDocumentRestored(self, obj):
        from . import exploration_base

        exploration_base.restore_properties(self, obj)

    def execute(self, obj):
        return True


##  @}
