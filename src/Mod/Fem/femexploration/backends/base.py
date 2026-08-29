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

"""The contract every execution backend implements.

The engine hands a backend design vectors and collects results; it never knows
whether they were solved in this process, in subprocesses, or on other
machines.  That is what lets the same study definition move from a quick serial
check to a distributed run by changing one enumeration property.
"""

__title__ = "FreeCAD FEM design exploration backend base"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

from abc import ABC
from abc import abstractmethod


class Backend(ABC):
    """Evaluates design vectors somewhere."""

    name = "base"

    def prepare(self, spec, store):
        """Called once before any point is submitted."""

    @abstractmethod
    def submit(self, vectors):
        """Accept design vectors for evaluation."""

    @abstractmethod
    def poll(self, timeout=0.0):
        """Return results finished since the last call, possibly empty."""

    @abstractmethod
    def busy(self):
        """True while work is outstanding."""

    def cancel(self):
        """Ask for a graceful stop."""

    def cleanup(self):
        """Release resources; always called, including after a failure."""

    @abstractmethod
    def point_id(self, values):
        """The content address of a design vector, for cache lookups.

        The engine needs this before deciding whether to evaluate a point at
        all, and only the backend knows the model fingerprint it was prepared
        with.
        """

    @property
    def capacity(self):
        """How many points this backend can usefully run at once."""
        return 1


##  @}
