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

"""The stable surface for driving design exploration from outside.

Macros, the task panels, the command line front end and the MCP bridge all come
through here.  Everything else in the package is internal and may be
rearranged; these names are the contract.

The point of the layering is that one execution path serves all three callers.
A study launched from a task panel, from ``FreeCADCmd`` and from an agent
session runs exactly the same code, so a bug found in one is fixed for all.
"""

__title__ = "FreeCAD FEM design exploration API"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import os

import FreeCAD

from femtools import explorationutils

from . import backends as backend_registry
from . import engine
from . import planner as planner_module
from . import spec as spec_module
from .cancel import CancelToken
from .model import StudySpec
from .store import PointStore

__all__ = [
    "study_spec",
    "last_run_directory",
    "spec_from_json",
    "run_directory",
    "run_study",
    "run_study_sync",
    "read_results",
    "export_csv",
    "import_run",
    "mesh_analysis",
    "list_studies",
    "promote",
    "available_backends",
]


# ------------------------------------------------------------------ inspection


def list_studies(analysis):
    """Every runnable study in *analysis*."""
    return explorationutils.get_studies(analysis)


def available_backends():
    return backend_registry.available()


def study_spec(study):
    """The engine's view of a study document object, validated."""
    return spec_module.spec_from_study(study).validate()


def spec_from_json(text):
    return StudySpec.from_json(text).validate()


# ------------------------------------------------------------------- promotion


def promote(obj, prop, analysis, label=None):
    """Make a property of *obj* a design variable of *analysis*."""
    from femtools import explorationpromote

    return explorationpromote.promote(obj, prop, analysis, label=label)


# --------------------------------------------------------------------- running


def run_directory(study, spec=None):
    """Where this study's run directory lives.

    Honours the study's own setting, then the FEM working directory
    preferences, so exploration output lands wherever solver output already
    does rather than somewhere new the user has to discover.
    """
    configured = str(getattr(study, "WorkingDirectory", "") or "")
    if configured:
        return configured

    doc = study.Document
    param = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/General")
    name = f"{doc.Name}_{study.Name}"

    if param.GetBool("UseBesideDirectory", False) and doc.FileName:
        root, _ext = os.path.splitext(doc.FileName)
        return os.path.join(root, study.Label)
    if param.GetBool("UseCustomDirectory", False):
        base = param.GetString("CustomDirectoryPath") or FreeCAD.ConfigGet("UserHomePath")
        return os.path.join(base, name)

    import tempfile

    return os.path.join(tempfile.gettempdir(), "fem_exploration", name)


def run_study(study, progress=None, cancel=None, backend_name=None, run_dir=None):
    """Run a study document object to completion and return its result.

    Blocking.  The GUI and the MCP bridge do not call this directly; they run
    the command line front end in a separate process so that a study taking
    minutes cannot freeze either of them.
    """
    spec = study_spec(study)
    store = PointStore(run_dir or run_directory(study, spec)).prepare()
    if cancel is None:
        # a fresh token starts clean, so a stale CANCEL file from a previous
        # run cannot stop this one.  A token handed in by the caller is left
        # exactly as they set it - resetting it here would discard a
        # cancellation they had already requested.
        cancel = CancelToken(store.run_dir)
        cancel.reset()

    name = backend_name or spec.backend or "Serial"
    backend = _make_backend(name, study.Document, cancel)
    plan = planner_module.make_planner(spec)

    result = engine.run_study(spec, store, backend, plan, progress=progress, cancel=cancel)
    _store_results(study, result, spec, store)
    return result


def run_study_sync(spec, doc, run_dir, progress=None, cancel=None, backend_name=None):
    """Run from a specification rather than a document object.

    This is what a worker process uses: it has a spec and an open document, but
    no study object to consult.
    """
    spec.validate()
    store = PointStore(run_dir).prepare()
    cancel = cancel or CancelToken(store.run_dir)
    backend = _make_backend(backend_name or spec.backend or "Serial", doc, cancel)
    plan = planner_module.make_planner(spec)
    return engine.run_study(spec, store, backend, plan, progress=progress, cancel=cancel)


def _make_backend(name, doc, cancel):
    cls = backend_registry.get_backend_class(name)
    try:
        return cls(doc, cancel=cancel)
    except TypeError:
        return cls(doc)


def _store_results(study, result, spec, store):
    """Mirror the run into the document, once."""
    import ObjectsFem

    table = None
    for child in getattr(study, "Group", []):
        if explorationutils.is_design_points(child):
            table = child
            break
    if table is None:
        table = ObjectsFem.makeExplorationDesignPoints(study.Document)
        study.addObject(table)

    table.Proxy.store(table, result.results, spec)
    table.RunDirectory = store.run_dir
    best = result.best()
    table.BestPoint = best.point_id if best else ""
    study.Document.recompute()
    return table


# --------------------------------------------------------------------- results


def last_run_directory(study):
    """Where this study actually last ran.

    The configured directory is only a default; a run may have been given an
    explicit one.  The design point table records what was really used, so
    reading results back does not depend on the setting being unchanged since.
    """
    for child in getattr(study, "Group", []):
        if explorationutils.is_design_points(child):
            recorded = str(getattr(child, "RunDirectory", "") or "")
            if recorded and os.path.isdir(recorded):
                return recorded
    return run_directory(study)


def import_run(study, run_dir):
    """Load a run directory's results into *study*'s design point table.

    Used after the run happened in another process: the engine wrote the
    journal, and this brings it back into the live document so the table, and
    anything reading it, sees the same rows.
    """
    store = PointStore(run_dir)
    results = store.latest_results()
    try:
        spec = store.read_study()
    except Exception:
        spec = None

    class _Result:
        pass

    carrier = _Result()
    carrier.results = results
    carrier.best = lambda: _best_of(results)

    table = _store_results(study, carrier, spec, store)
    return table, results


def _best_of(results):
    scored = [r for r in results if r.feasible and r.objectives]
    if not scored:
        return None
    key = next(iter(scored[0].objectives))
    return min(
        (r for r in scored if key in r.objectives),
        key=lambda r: r.objectives[key],
        default=None,
    )


def mesh_analysis(analysis, timeout=None):
    """Generate the analysis' mesh now, and wait for it.  Returns node count.

    Worth having as an API rather than leaving callers to drive GmshTools
    themselves: the mesher publishes its result through a queued Qt signal, so
    the obvious code reads the *previous* mesh.
    """
    from femtools import membertools

    from .evaluate import DEFAULT_TOOL_TIMEOUT
    from .evaluate import generate_mesh

    meshes = membertools.get_member(analysis, "Fem::FemMeshObject")
    if not meshes:
        raise ValueError("the analysis has no mesh object")
    if len(meshes) > 1:
        raise ValueError("more than one mesh in the analysis is not supported")
    return generate_mesh(meshes[0], DEFAULT_TOOL_TIMEOUT if timeout is None else timeout)


def read_results(study=None, run_dir=None):
    """Design points of a study, from its run directory."""
    if run_dir is None:
        if study is None:
            raise ValueError("give either a study or a run directory")
        run_dir = last_run_directory(study)
    return PointStore(run_dir).read_index()


def export_csv(path, study=None, run_dir=None, spec=None):
    if run_dir is None:
        if study is None:
            raise ValueError("give either a study or a run directory")
        run_dir = last_run_directory(study)
    if spec is None and study is not None:
        spec = study_spec(study)
    return PointStore(run_dir).export_csv(path, spec=spec)


##  @}
