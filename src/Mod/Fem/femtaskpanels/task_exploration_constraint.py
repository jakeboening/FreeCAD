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

"""Task panel for a design exploration constraint.

The grid in the parameter set panel is where these are normally edited;
double-clicking one in the tree opens that panel with the row in view.
"""

__title__ = "FreeCAD FEM exploration constraint task panel"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import FreeCADGui

from . import task_exploration_parameters


class _TaskPanel(task_exploration_parameters._TaskPanel):
    """Open the parameter set panel for the constraint's set."""

    def __init__(self, obj):
        param_set = obj.getParentGroup()
        if param_set is None:
            raise ValueError("the constraint is not inside a parameter set")
        super().__init__(param_set)
        self.form.setWindowTitle("Constraint")
        self._child = obj

    def accept(self):
        gui_doc = self._child.ViewObject.Document
        gui_doc.Document.recompute()
        gui_doc.resetEdit()
        gui_doc.Document.commitTransaction()
        return True

    def reject(self):
        gui_doc = self._child.ViewObject.Document
        gui_doc.Document.abortTransaction()
        gui_doc.resetEdit()
        gui_doc.Document.recompute()
        return True


##  @}
