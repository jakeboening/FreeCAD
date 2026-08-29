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

"""The design point table produced by a study.

The run directory on disk is authoritative.  This object mirrors it into the
document so that saving the ``.FCStd`` keeps the results, and so the table
survives being reopened on a machine that never had the run directory.

The rows live in a compressed blob rather than in properties: a five thousand
row table with twenty columns would otherwise bloat ``Document.xml`` with
a hundred thousand numbers that nothing ever queries individually.  The small
denormalised properties alongside it are the ones worth reading cheaply.
"""

__title__ = "FreeCAD FEM exploration design points object"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import gzip
import json
import os

import FreeCAD

from femexploration import model as expl_model
from femtools import explorationutils

from . import base_fempythonobject

_PropHelper = base_fempythonobject._PropHelper

#: order used to encode PointResult.state into the States property
STATE_CODES = list(expl_model.POINT_STATES)


class ExplorationDesignPoints(base_fempythonobject.BaseFemPythonObject):
    """Results of one study run."""

    Type = explorationutils.DESIGN_POINTS_TYPE

    def __init__(self, obj):
        super().__init__(obj)
        for prop in self._get_properties():
            prop.add_to_object(obj)
        self._rows = None

    def _get_properties(self):
        prop = []
        prop.append(
            _PropHelper(
                type="App::PropertyFileIncluded",
                name="Data",
                group="Results",
                doc="Compressed result journal, stored inside the document",
                value="",
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyPath",
                name="RunDirectory",
                group="Results",
                doc="Run directory this table was loaded from",
                value="",
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyStringList",
                name="ColumnNames",
                group="Results",
                doc="Column headings, inputs first then responses",
                value=[],
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyStringList",
                name="ColumnUnits",
                group="Results",
                doc="Unit of each column",
                value=[],
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyStringList",
                name="ColumnKeys",
                group="Results",
                doc="Machine name of each column, matching the keys in the stored rows",
                value=[],
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyStringList",
                name="ColumnRoles",
                group="Results",
                doc="Whether each column is an input, a response or a violation",
                value=[],
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyIntegerList",
                name="States",
                group="Results",
                doc="State of each design point, encoded",
                value=[],
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyInteger",
                name="Count",
                group="Results",
                doc="Number of design points",
                value=0,
            )
        )
        prop.append(
            _PropHelper(
                type="App::PropertyString",
                name="BestPoint",
                group="Results",
                doc="Identifier of the best feasible design point found",
                value="",
            )
        )
        return prop

    def onDocumentRestored(self, obj):
        from . import exploration_base

        exploration_base.restore_properties(self, obj)
        self._rows = None

    def onChanged(self, obj, prop):
        if prop == "Data":
            self._rows = None

    # ------------------------------------------------------------------ rows

    def rows(self, obj):
        """The design points, decoded from the stored blob and cached."""
        if self._rows is not None:
            return self._rows

        path = obj.Data
        if not path or not os.path.exists(path):
            self._rows = []
            return self._rows

        rows = []
        try:
            with gzip.open(path, "rt") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError as exc:
            FreeCAD.Console.PrintError(f"cannot read design point data: {exc}\n")
        self._rows = rows
        return rows

    def store(self, obj, results, spec=None):
        """Write *results* into the document, once, at the end of a run."""
        import tempfile

        handle, tmp = tempfile.mkstemp(suffix=".jsonl.gz")
        os.close(handle)
        with gzip.open(tmp, "wt") as fh:
            for result in results:
                from dataclasses import asdict

                fh.write(json.dumps(asdict(result), sort_keys=True, default=str) + "\n")

        # assigning the path the property already holds raises OSError, so
        # always hand it a fresh file
        obj.Data = tmp
        try:
            os.unlink(tmp)
        except OSError:
            pass

        obj.Count = len(results)
        obj.States = [
            STATE_CODES.index(r.state) if r.state in STATE_CODES else -1 for r in results
        ]
        if spec is not None:
            items = (
                list(spec.active_variables)
                + list(spec.responses)
                + list(spec.active_objectives)
                + list(spec.active_constraints)
            )
            # headings read as the user named them; the machine identity stays
            # the object name, which is what the stored rows are keyed by
            obj.ColumnNames = [getattr(i, "label", "") or i.name for i in items]
            obj.ColumnUnits = [getattr(i, "unit", "") for i in items]
            obj.ColumnRoles = (
                ["input"] * len(spec.active_variables)
                + ["response"] * len(spec.responses)
                + ["objective"] * len(spec.active_objectives)
                + ["violation"] * len(spec.active_constraints)
            )
            obj.ColumnKeys = [i.name for i in items]

        self._rows = None

    def execute(self, obj):
        return True


##  @}
