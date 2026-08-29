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

"""Evaluate design points one at a time, in this process.

The simplest backend and the reference the others are checked against.  It is
also the only one that mutates the user's own document, which is why it wraps
each point in a transaction and clears the undo stack afterwards: without that,
a few hundred iterations quietly accumulate a few hundred undo records.
"""

__title__ = "FreeCAD FEM design exploration serial backend"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

from ..evaluate import Evaluator
from ..model import CANCELLED
from ..model import PointResult
from .base import Backend


class SerialBackend(Backend):
    """One point at a time, on the calling thread."""

    name = "Serial"

    def __init__(self, doc, cancel=None, keep_undo=False):
        self.doc = doc
        self.cancel_token = cancel
        self.keep_undo = keep_undo
        self._evaluator = None
        self._queue = []

    def prepare(self, spec, store):
        self._evaluator = Evaluator(self.doc, spec)
        self._store = store

    def point_id(self, values):
        return self._evaluator.point_id(values)

    def submit(self, vectors):
        self._queue.extend(vectors)

    def busy(self):
        return bool(self._queue)

    def poll(self, timeout=0.0):
        """Evaluate the next queued point and return its result."""
        if not self._queue:
            return []

        vector = self._queue.pop(0)
        if self.cancel_token is not None and self.cancel_token.cancelled:
            self._queue.clear()
            return [
                PointResult(
                    point_id=vector.point_id or self._evaluator.point_id(vector.values),
                    state=CANCELLED,
                    inputs=self._evaluator._inputs_with_units(vector.values),
                )
            ]

        # keep the user's document as we found it: the sweep is a question
        # about the model, not an edit to it
        self.doc.openTransaction("Design point")
        try:
            result = self._evaluator.evaluate(
                vector, working_dir=self._store.point_dir(
                    vector.point_id or self._evaluator.point_id(vector.values)
                )
            )
        finally:
            self.doc.abortTransaction()
            if not self.keep_undo:
                # hundreds of iterations would otherwise leave hundreds of
                # undo records behind
                self.doc.clearUndos()
            self.doc.recompute()

        return [result]

    def cancel(self):
        if self.cancel_token is not None:
            self.cancel_token.cancel()
        self._queue.clear()

    @property
    def capacity(self):
        return 1


##  @}
