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

"""Command line front end, run inside FreeCADCmd.

    FreeCADCmd -- femexploration/cli.py model.FCStd --study Sweep

This is the single execution path.  The task panel runs it in a QProcess so the
GUI stays responsive; the MCP bridge runs it so a tool call does not block for
minutes; a user runs it directly on a headless machine.  All three therefore
exercise the same code.

Progress is one JSON object per line on stdout.  No sockets, no serialisation
library: a parent process reads lines, and anything it does not recognise falls
through to the log unchanged.
"""

__title__ = "FreeCAD FEM design exploration command line"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import argparse
import json
import sys
from dataclasses import asdict


def emit(kind, **payload):
    """Write one newline delimited JSON progress record."""
    payload["event"] = kind
    sys.stdout.write(json.dumps(payload, default=str) + "\n")
    sys.stdout.flush()


def _progress_printer(progress):
    emit(
        "progress",
        completed=progress.completed,
        total=progress.total,
        failed=progress.failed,
        best=progress.best_objective,
        message=progress.message,
        elapsed=round(progress.elapsed_s, 3),
        point=progress.point,
    )


def build_parser():
    parser = argparse.ArgumentParser(
        prog="femexploration", description="Run a FEM design exploration study"
    )
    parser.add_argument("document", help="FCStd file to open")
    parser.add_argument("--study", help="label or name of the study object to run")
    parser.add_argument("--analysis", help="analysis to look in, when there are several")
    parser.add_argument("--backend", help="override the study's execution backend")
    parser.add_argument("--run-dir", help="override the run directory")
    parser.add_argument(
        "--csv", help="also write the results table to this CSV file when finished"
    )
    parser.add_argument(
        "--list", action="store_true", help="list the studies in the document and exit"
    )
    parser.add_argument(
        "--save", action="store_true", help="save the document when the run finishes"
    )
    return parser


def _own_argv():
    """Our arguments, from however FreeCADCmd was invoked.

    FreeCAD consumes arguments after the script name unless they follow a bare
    ``--``, so the supported form is::

        FreeCADCmd run.py -- model.FCStd --study Sweep

    Everything after the separator is ours; without one, fall back to whatever
    follows the script path.
    """
    argv = sys.argv[1:]
    if "--" in argv:
        return argv[argv.index("--") + 1 :]
    return argv[1:] if argv else []


def main(argv=None):
    import FreeCAD

    from femtools import explorationutils

    from . import api

    args = build_parser().parse_args(argv if argv is not None else _own_argv())

    doc = FreeCAD.openDocument(args.document)

    analyses = [o for o in doc.Objects if o.isDerivedFrom("Fem::FemAnalysis")]
    if args.analysis:
        analyses = [o for o in analyses if args.analysis in (o.Name, o.Label)]
    if not analyses:
        emit("error", message="no analysis found in the document")
        return 2

    studies = []
    for analysis in analyses:
        studies.extend(explorationutils.get_studies(analysis))

    if args.list:
        emit("studies", studies=[{"name": s.Name, "label": s.Label} for s in studies])
        return 0

    if args.study:
        studies = [s for s in studies if args.study in (s.Name, s.Label)]
    if not studies:
        emit("error", message=f"no study matching {args.study!r}")
        return 2
    study = studies[0]

    emit("started", study=study.Label, document=doc.Name)
    try:
        result = api.run_study(
            study,
            progress=_progress_printer,
            backend_name=args.backend,
            run_dir=args.run_dir,
        )
    except Exception as exc:
        import traceback

        emit("error", message=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
        return 1

    if args.csv:
        api.export_csv(args.csv, study=study)
        emit("csv", path=args.csv)

    if args.save and doc.FileName:
        doc.save()

    emit(
        "finished",
        completed=len(result.completed),
        failed=len(result.failed),
        cancelled=result.cancelled,
        aborted=result.aborted_reason,
        summary=result.summary(),
        best=asdict(result.best()) if result.best() else None,
    )
    return 1 if result.aborted_reason else 0


if __name__ == "__main__":
    sys.exit(main())


##  @}
