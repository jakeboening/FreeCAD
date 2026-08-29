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

"""A design of experiments: sample the design space by a chosen plan.

Where a sweep is exhaustive, a DOE is economical: a Latin hypercube covers
three variables at thirty points about as well as a factorial does at a
hundred and twenty-five, and the correlation and sensitivity charts are
built from exactly this kind of table.  The classical designs - central
composite, Box-Behnken - are here for response surfaces, where their
structure is the point.
"""

__title__ = "FreeCAD FEM exploration design of experiments object"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

from femexploration.algorithms import sampling
from femtools import explorationutils

from . import base_fempythonobject
from . import exploration_base

_PropHelper = base_fempythonobject._PropHelper

PLANS = list(sampling.DOE_PLANS)
ALPHAS = ["FaceCentred", "Rotatable"]


class ExplorationDOE(base_fempythonobject.BaseFemPythonObject):
    """A sampled set of design points over the analysis' design variables."""

    Type = explorationutils.DOE_TYPE

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
                    "LatinHypercube and Sobol fill the space evenly with the "
                    "requested number of points; RandomUniform is the baseline. "
                    "CentralComposite and BoxBehnken are the classical designs "
                    "for fitting a response surface, and set their own size"
                ),
                value=list(PLANS),
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyInteger",
                name="PointCount",
                group="Study",
                doc="Design points to sample, for the plans that take a count",
                value=20,
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyEnumeration",
                name="Alpha",
                group="Study",
                doc=(
                    "Axial distance of a central composite design. FaceCentred "
                    "stays inside the bounds; Rotatable places the axial points "
                    "further out and clips them to the bounds"
                ),
                value=list(ALPHAS),
            )
        )
        return prop

    def onDocumentRestored(self, obj):
        exploration_base.restore_properties(self, obj)

    def execute(self, obj):
        return True


##  @}
