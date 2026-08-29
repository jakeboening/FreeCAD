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

"""Design exploration for the FEM workbench.

Parametric sweeps, design of experiments, optimization and response surfaces.

The package is layered so the lower half never needs FreeCAD:

``version``, ``model``, ``hashing``, ``store`` and everything under
``algorithms`` are standard library (plus numpy for the algorithms) and can be
tested with plain ``unittest``.  ``spec``, ``evaluate`` and the backends import
FreeCAD but never FreeCADGui.  Only the task panels and view providers, which
live outside this package, touch Qt.

That layering is not cosmetic: it is what lets a study run headlessly under
``FreeCADCmd``, in parallel worker processes, and from an MCP session, all
through the same code path the GUI uses.
"""

__title__ = "FreeCAD FEM design exploration"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

from .version import FORMAT_VERSION
from .version import is_compatible

##  @}
