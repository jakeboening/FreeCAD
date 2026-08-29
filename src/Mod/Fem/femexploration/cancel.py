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

"""Cooperative cancellation.

Two ways to ask a run to stop, because a study may be executing in this process
or in half a dozen others:

* an in-memory flag, for the same process;
* a CANCEL file in the run directory, which any worker can see.

Both are polled rather than signalled.  Killing a worker outright would leave a
half-written point directory and orphaned solver children; letting it notice
between points leaves the run resumable.
"""

__title__ = "FreeCAD FEM design exploration cancellation"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import os


CANCEL_FILE = "CANCEL"


class CancelToken:
    """A flag that a long running study polls between units of work."""

    def __init__(self, run_dir=None):
        self._cancelled = False
        self._run_dir = run_dir

    def cancel(self):
        """Request cancellation, both in memory and for other processes."""
        self._cancelled = True
        if self._run_dir:
            try:
                os.makedirs(self._run_dir, exist_ok=True)
                with open(os.path.join(self._run_dir, CANCEL_FILE), "w") as fh:
                    fh.write("cancelled\n")
            except OSError:
                # an unwritable run directory must not stop us cancelling
                pass

    @property
    def cancelled(self):
        if self._cancelled:
            return True
        if self._run_dir and os.path.exists(os.path.join(self._run_dir, CANCEL_FILE)):
            self._cancelled = True
        return self._cancelled

    def reset(self):
        """Clear the flag and the file, so a run directory can be reused."""
        self._cancelled = False
        if self._run_dir:
            try:
                os.remove(os.path.join(self._run_dir, CANCEL_FILE))
            except OSError:
                pass

    def raise_if_cancelled(self):
        if self.cancelled:
            raise Cancelled()


class Cancelled(Exception):
    """Raised where unwinding is cleaner than checking a flag at every step."""


##  @}
