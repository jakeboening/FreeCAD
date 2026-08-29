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

"""Schema version for all persisted design exploration data.

Every artefact written by this package - ``study.json``, ``point.json``,
``index.jsonl`` and the in-document blob - carries this number.  A reader
refuses data it does not understand rather than silently misinterpreting it,
and the cache treats a version mismatch as a miss.

Bump ``FORMAT_VERSION`` whenever the meaning of an existing field changes or a
required field is added.  Adding an optional field with a safe default does not
require a bump.
"""

__title__ = "FreeCAD FEM design exploration schema version"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{


FORMAT_VERSION = 1


def is_compatible(version):
    """Return True if data written with *version* can be read by this package."""
    return version == FORMAT_VERSION


##  @}
