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

"""Registry of the solvers the FEM workbench can run.

Adding a solver used to mean editing the workbench: a branch in
``run_fem_solver``, an entry in the solver dropdown, a dictionary in
``settings``, and a type check or two elsewhere.  A solver that will never be
part of FreeCAD - an in-house code, a research solver - therefore could not be
added at all without patching the core.

Now every solver, built in or not, is described by one :class:`SolverPlugin`
and registered here.  The built-ins go through exactly the same call as an
add-on, so there is no privileged path that only they exercise, and nothing can
work for CalculiX but silently not for anyone else.

An external solver registers from its add-on's ``InitGui.py``::

    from femsolver import registry

    registry.register_solver(registry.SolverPlugin(
        type_id="Fem::SolverAxiFem",
        name="AxiFEM",
        module="freecad.axifem.objects",
        factory="make_solver",
        tools_module="freecad.axifem.tools",
        tools_factory="AxiFemTools",
        icon="/path/to/icon.svg",
        capabilities=frozenset({registry.CAP_STRUCTURED_INPUT}),
    ))

Module and factory are stored as *strings*, not callables.  That keeps the
registry cheap to import, lets the generated commands emit recordable macro
lines, and means registering a solver never imports its implementation.
"""

__title__ = "FreeCAD FEM solver registry"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import importlib
from dataclasses import dataclass
from dataclasses import field

import FreeCAD


# ************************************************************************************************
# capabilities
#
# Declared rather than inferred, so the workbench can answer "will this solver
# cope with this analysis?" without importing the solver or running it.

#: the solver is handed a written CalculiX input deck
CAP_CCX_DECK = "ccx_deck"
#: the solver is handed the mesh and analysis members and writes its own input
CAP_STRUCTURED_INPUT = "structured_input"
#: the solver owns child equation objects, as Elmer does
CAP_EQUATIONS = "equations"
#: beam rotations cannot be recovered without the shape the mesh came from
CAP_NEEDS_SHAPE_FOR_BEAMS = "needs_shape_for_beams"


@dataclass(frozen=True)
class SolverPlugin:
    """Everything the workbench needs to know about one solver."""

    #: value of ``Proxy.Type`` on the solver object; the registry key
    type_id: str
    #: short human name, also the key used for binary and core settings
    name: str

    #: module and factory that create the solver object, as import strings
    module: str = "ObjectsFem"
    factory: str = ""

    #: module and class implementing the run, an ``ObjectTools`` subclass.
    #: Empty means the solver uses the older Machine framework instead.
    tools_module: str = ""
    tools_factory: str = ""

    #: presentation
    icon: str = ""
    menu_text: str = ""
    tooltip: str = ""
    accel: str = ""

    #: what the solver can be given and what it supports
    capabilities: frozenset = field(default_factory=frozenset)

    #: module and function validating an analysis before a run;
    #: ``(analysis, solver, mesh, member) -> str`` where "" means no problem
    check_module: str = "femtools.checksanalysis"
    check_factory: str = "check_member_for_solver_calculix"

    #: binary settings, ``{settings name: femsolver.settings._SolverDlg}``
    settings: dict = field(default_factory=dict)

    #: whether the solver appears in the Solvers dropdown.  The legacy
    #: CalculiX tools object is registered so that running and checking it go
    #: through the registry like everything else, but it was never offered in
    #: the menu and adding it would shift the indices the DefaultSolver
    #: preference is stored against.
    show_in_menu: bool = True

    def command_name(self):
        """Name of the generated command that creates this solver."""
        return "FEM_Solver" + self.type_id.replace("Fem::Solver", "")

    def has(self, capability):
        return capability in self.capabilities

    def make_tools(self, obj):
        """Instantiate the tools object that runs this solver, or None."""
        if not self.tools_module or not self.tools_factory:
            return None
        module = importlib.import_module(self.tools_module)
        return getattr(module, self.tools_factory)(obj)

    def check(self, analysis, solver, mesh, member):
        """Validate an analysis for this solver; "" means it is runnable."""
        if not self.check_module or not self.check_factory:
            return ""
        module = importlib.import_module(self.check_module)
        return getattr(module, self.check_factory)(analysis, solver, mesh, member)


# ************************************************************************************************
# the registry

_registry = {}
_callbacks = []


def register_solver(plugin, replace=False):
    """Add *plugin* to the registry.

    Registering a type that is already present is refused unless *replace* is
    given: two add-ons quietly claiming the same solver would produce a
    workbench whose behaviour depends on load order.
    """
    if not isinstance(plugin, SolverPlugin):
        raise TypeError("register_solver expects a SolverPlugin")
    if plugin.type_id in _registry and not replace:
        raise ValueError(
            f"solver {plugin.type_id!r} is already registered by "
            f"{_registry[plugin.type_id].name!r}"
        )

    _registry[plugin.type_id] = plugin

    # binary paths and core counts come from the same settings machinery the
    # built-ins use, so an external solver gets them without new code
    if plugin.settings:
        from . import settings as solver_settings

        for name, dialog in plugin.settings.items():
            solver_settings.register_solver_settings(name, dialog)

    for callback in list(_callbacks):
        try:
            callback(plugin)
        except Exception as exc:
            FreeCAD.Console.PrintError(f"solver registration callback failed: {exc}\n")
    return plugin


def unregister_solver(type_id):
    return _registry.pop(type_id, None)


def get_solver(type_id):
    """The plugin for *type_id*, or None when nothing claims it."""
    return _registry.get(type_id)


def get_solver_for(obj):
    """The plugin describing solver object *obj*, or None."""
    from femtools.femutils import type_of_obj

    try:
        return _registry.get(type_of_obj(obj))
    except Exception:
        return None


def registered():
    """Every registered plugin, in registration order."""
    return list(_registry.values())


def solver_type_ids():
    return list(_registry)


def command_names():
    """Command names for the solver dropdown, in registration order."""
    return [p.command_name() for p in _registry.values() if p.show_in_menu]


def with_capability(capability):
    return [p for p in _registry.values() if p.has(capability)]


def on_register(callback):
    """Call *callback* for every solver registered from now on.

    An add-on loaded after the workbench built its menus needs a way to be
    noticed; without this its solver would exist but never appear.
    """
    _callbacks.append(callback)
    return callback


# ************************************************************************************************
# the solvers shipped with FreeCAD
#
# Registered here rather than in each solver package so that importing the
# registry is enough to see them, whatever order modules happen to load in.


def _register_builtin_solvers():
    from . import settings as solver_settings

    param = solver_settings._PARAM_PATH
    dlg = solver_settings._SolverDlg

    register_solver(
        SolverPlugin(
            type_id="Fem::SolverCalculiX",
            name="Calculix",
            factory="makeSolverCalculiX",
            tools_module="femsolver.calculix.calculixtools",
            tools_factory="CalculiXTools",
            icon="FEM_SolverStandard",
            menu_text="Solver CalculiX",
            tooltip="Creates a FEM solver CalculiX",
            accel="S, C",
            capabilities=frozenset({CAP_CCX_DECK, CAP_NEEDS_SHAPE_FOR_BEAMS}),
            settings={
                "Calculix": dlg(
                    default="ccx", param_path=param + "Ccx", custom_path="ccxBinaryPath"
                )
            },
        )
    )
    register_solver(
        SolverPlugin(
            type_id="Fem::SolverCcxTools",
            name="Calculix",
            factory="makeSolverCalculiXCcxTools",
            icon="FEM_SolverStandard",
            menu_text="Solver CalculiX Standard",
            tooltip="Creates a FEM solver CalculiX (legacy tools)",
            capabilities=frozenset({CAP_CCX_DECK, CAP_NEEDS_SHAPE_FOR_BEAMS}),
            show_in_menu=False,
        )
    )
    register_solver(
        SolverPlugin(
            type_id="Fem::SolverElmer",
            name="ElmerSolver",
            factory="makeSolverElmer",
            tools_module="femsolver.elmer.elmertools",
            tools_factory="ElmerTools",
            icon="FEM_SolverElmer",
            menu_text="Solver Elmer",
            tooltip="Creates a FEM solver Elmer",
            accel="S, E",
            capabilities=frozenset({CAP_STRUCTURED_INPUT, CAP_EQUATIONS}),
            settings={
                "ElmerSolver": dlg(
                    default="ElmerSolver",
                    param_path=param + "Elmer",
                    custom_path="elmerBinaryPath",
                ),
                "ElmerGrid": dlg(
                    default="ElmerGrid",
                    param_path=param + "Elmer",
                    custom_path="gridBinaryPath",
                ),
                "MPIElmer": dlg(
                    default="mpiexec",
                    param_path=param + "Elmer",
                    custom_path="mpiBinaryPath",
                ),
            },
        )
    )
    register_solver(
        SolverPlugin(
            type_id="Fem::SolverMystran",
            name="Mystran",
            factory="makeSolverMystran",
            icon="FEM_SolverMystran",
            menu_text="Solver Mystran",
            tooltip="Creates a FEM solver Mystran",
            accel="S, M",
            capabilities=frozenset({CAP_STRUCTURED_INPUT}),
            settings={
                "Mystran": dlg(
                    default="mystran",
                    param_path=param + "Mystran",
                    custom_path="mystranBinaryPath",
                )
            },
        )
    )
    register_solver(
        SolverPlugin(
            type_id="Fem::SolverZ88",
            name="Z88",
            factory="makeSolverZ88",
            tools_module="femsolver.z88.z88tools",
            tools_factory="Z88Tools",
            icon="FEM_SolverZ88",
            menu_text="Solver Z88",
            tooltip="Creates a FEM solver Z88",
            accel="S, Z",
            capabilities=frozenset({CAP_STRUCTURED_INPUT}),
            settings={
                "Z88": dlg(
                    default="z88r", param_path=param + "Z88", custom_path="z88BinaryPath"
                )
            },
        )
    )


_register_builtin_solvers()


##  @}
