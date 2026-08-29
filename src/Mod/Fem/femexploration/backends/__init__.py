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

"""Execution backends, discovered rather than hard-coded.

A backend whose imports fail is simply not offered.  That is what keeps the
fleet backend's dependency optional, and what lets it be dropped from a build
without touching anything else.
"""

__title__ = "FreeCAD FEM design exploration backends"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import importlib

#: backend name -> (module, class)
_BACKENDS = {
    "Serial": ("femexploration.backends.serial", "SerialBackend"),
    "LocalParallel": ("femexploration.backends.localparallel", "LocalParallelBackend"),
    "Fleet": ("femexploration.backends.fleet", "FleetBackend"),
}


def available():
    """Backend names that can actually be constructed in this installation."""
    names = []
    for name, (module_name, class_name) in _BACKENDS.items():
        try:
            module = importlib.import_module(module_name)
            getattr(module, class_name)
        except Exception:
            continue
        names.append(name)
    return names


def get_backend_class(name):
    try:
        module_name, class_name = _BACKENDS[name]
    except KeyError:
        raise ValueError(
            f"unknown backend {name!r}; available: {available()}"
        ) from None
    module = importlib.import_module(module_name)
    return getattr(module, class_name)


##  @}
