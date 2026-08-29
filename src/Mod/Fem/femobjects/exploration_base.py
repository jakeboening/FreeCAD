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

"""Properties shared by every runnable study.

Composition rather than inheritance: FEM's Python typesystem compares
``Proxy.Type`` for equality and has no notion of a base type, so a shared base
class would buy nothing at the type level while making every subclass's
identity ambiguous.  A function returning property descriptors gives the reuse
without the confusion.
"""

__title__ = "FreeCAD FEM exploration study common properties"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

from femexploration.version import FORMAT_VERSION

from . import base_fempythonobject

_PropHelper = base_fempythonobject._PropHelper

RETAIN_MODES = ["None", "Failed", "Best", "Marked", "All"]


def common_study_properties(backends=None):
    """The execution settings every study type carries."""
    if backends is None:
        try:
            from femexploration import backends as backend_registry

            backends = backend_registry.available()
        except Exception:
            backends = ["Serial"]
    if not backends:
        backends = ["Serial"]

    prop = []

    prop.append(
        _PropHelper(
            type="App::PropertyLink",
            name="ParameterSet",
            group="Study",
            doc="Parameter set defining the variables and responses of this study",
            value=None,
        )
    )
    prop.append(
        _PropHelper(
            type="App::PropertyEnumeration",
            name="Backend",
            group="Execution",
            doc=(
                "Where design points are evaluated. Serial runs them in this "
                "process and is the debugging fallback"
            ),
            value=list(backends),
        )
    )
    prop.append(
        _PropHelper(
            type="App::PropertyInteger",
            name="MaxParallel",
            group="Execution",
            doc="Design points to evaluate at once; 0 chooses automatically",
            value=0,
        )
    )
    prop.append(
        _PropHelper(
            type="App::PropertyInteger",
            name="PointsPerJob",
            group="Execution",
            doc=(
                "Points bundled into one dispatched job, to amortise process "
                "start up; 0 chooses automatically"
            ),
            value=0,
        )
    )
    prop.append(
        _PropHelper(
            type="App::PropertyEnumeration",
            name="Retain",
            group="Execution",
            doc=(
                "Which design points keep their full model and solver output. "
                "Everything else keeps only its extracted values"
            ),
            value=list(RETAIN_MODES),
        )
    )
    prop.append(
        _PropHelper(
            type="App::PropertyBool",
            name="ResumeFromCache",
            group="Execution",
            doc=(
                "Reuse design points already computed against an unchanged "
                "model, so an interrupted run restarts in seconds"
            ),
            value=True,
        )
    )
    prop.append(
        _PropHelper(
            type="App::PropertyInteger",
            name="Seed",
            group="Execution",
            doc="Random seed, so a study reproduces exactly",
            value=0,
        )
    )
    prop.append(
        _PropHelper(
            type="App::PropertyFloat",
            name="Timeout",
            group="Execution",
            doc="Seconds allowed per design point; 0 means no limit",
            value=0.0,
        )
    )
    prop.append(
        _PropHelper(
            type="App::PropertyInteger",
            name="FailureLimit",
            group="Execution",
            doc="Abort once more than this many points fail; 0 means no limit",
            value=0,
        )
    )
    prop.append(
        _PropHelper(
            type="App::PropertyPath",
            name="WorkingDirectory",
            group="Execution",
            doc="Where the run directory is created",
            value="",
        )
    )
    prop.append(
        _PropHelper(
            type="App::PropertyFile",
            name="ClusterConfig",
            group="Execution",
            doc=(
                "femfleet cluster.toml for the Fleet backend; empty uses the FEM "
                "preference, then ~/.config/femfleet/cluster.toml"
            ),
            value="",
        )
    )
    prop.append(
        _PropHelper(
            type="App::PropertyInteger",
            name="FormatVersion",
            group="Study",
            doc="Schema version of the data this study writes",
            value=FORMAT_VERSION,
        )
    )

    return prop


def restore_properties(proxy, obj):
    """Add any property that did not exist when the file was saved."""
    import FreeCAD

    for prop in proxy._get_properties():
        try:
            obj.getPropertyByName(prop.name)
        except FreeCAD.Base.PropertyError:
            prop.add_to_object(obj)


##  @}
