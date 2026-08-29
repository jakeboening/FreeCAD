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

"""The fleet shim: run one worker job under whichever FreeCADCmd this host has.

femfleet starts every job as ``<host python> <argv>`` in the host's workdir.
That python is not FreeCAD's, so the job cannot be the worker itself; it is
this file, which finds a FreeCADCmd on the host and hands the job over.
It is shipped to every host inside the run's kit, imports nothing outside
the standard library, and works under any Python 3.

    python femexploration_shim.py --job jobs/job-0001.json --out jobs/job-0001.results.jsonl
    python femexploration_shim.py --check

``--check`` reports what the host can offer - FreeCAD, gmsh and CalculiX
versions - as one JSON line, which is what the fleet check command reads
before any distributed result is trusted.
"""

__title__ = "FreeCAD FEM design exploration fleet shim"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import argparse
import glob
import json
import os
import platform
import shutil
import subprocess
import sys

#: places a FreeCADCmd tends to live, after $FREECADCMD and $PATH
CANDIDATES = (
    "~/src/freecad/build/bin/FreeCADCmd",
    "~/freecad/build/bin/FreeCADCmd",
    "/usr/bin/FreeCADCmd",
    "/usr/local/bin/FreeCADCmd",
    "/opt/freecad/bin/FreeCADCmd",
    "/Applications/FreeCAD.app/Contents/MacOS/FreeCADCmd",
    "/Applications/FreeCAD.app/Contents/Resources/bin/FreeCADCmd",
    "C:/Program Files/FreeCAD*/bin/FreeCADCmd.exe",
    "~/AppData/Local/Programs/FreeCAD*/bin/FreeCADCmd.exe",
)


def find_freecadcmd():
    """The console binary on this host, as a command list, or None.

    Order: the FREECADCMD environment variable, PATH, the usual install
    locations, then a Flatpak.  The Flatpak needs a different invocation,
    hence a list rather than a path.
    """
    explicit = os.environ.get("FREECADCMD")
    if explicit:
        return [os.path.expanduser(explicit)]
    found = shutil.which("FreeCADCmd") or shutil.which("freecadcmd")
    if found:
        return [found]
    for pattern in CANDIDATES:
        for path in sorted(glob.glob(os.path.expanduser(pattern)), reverse=True):
            if os.access(path, os.X_OK):
                return [path]
    if shutil.which("flatpak"):
        probe = subprocess.run(
            ["flatpak", "info", "org.freecad.FreeCAD"], capture_output=True, text=True
        )
        if probe.returncode == 0:
            return ["flatpak", "run", "--command=FreeCADCmd", "org.freecad.FreeCAD"]
    return None


def _version_of(command, args, pattern=None):
    try:
        out = subprocess.run(command + args, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"error: {exc}"
    text = (out.stdout + out.stderr).strip()
    return text.splitlines()[0][:120] if text else f"exit {out.returncode}"


def check():
    """What this host offers, as a dict."""
    report = {
        "host": platform.node(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
    }
    freecad = find_freecadcmd()
    report["freecadcmd"] = " ".join(freecad) if freecad else None
    if freecad:
        script = (
            "import FreeCAD, json, sys\n"
            "info = {'freecad': '.'.join(FreeCAD.Version()[:3])}\n"
            "try:\n    import femexploration.version as v\n"
            "    info['femexploration'] = v.FORMAT_VERSION\n"
            "except Exception as exc:\n    info['femexploration'] = 'missing: %s' % exc\n"
            "print('FEMCHECK ' + json.dumps(info)); sys.stdout.flush()\n"
        )
        try:
            out = subprocess.run(
                freecad + ["-c", script], capture_output=True, text=True, timeout=180
            )
            for line in (out.stdout + out.stderr).splitlines():
                if line.startswith("FEMCHECK "):
                    report.update(json.loads(line[len("FEMCHECK ") :]))
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            report["freecad"] = f"error: {exc}"
    for name, args in (("gmsh", ["--version"]), ("ccx", ["-v"])):
        binary = shutil.which(name)
        report[name] = _version_of([binary], args) if binary else None
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(prog="femexploration_shim")
    parser.add_argument("--job", help="job file to run with the worker")
    parser.add_argument("--out", help="results file the worker appends to")
    parser.add_argument("--stub", default="worker_stub.py", help="worker stub next to the job's kit")
    parser.add_argument("--check", action="store_true", help="report this host's FreeCAD and solvers")
    args = parser.parse_args(argv)

    if args.check:
        print(json.dumps(check()))
        return 0
    if not args.job:
        parser.error("--job is required unless --check is given")

    freecad = find_freecadcmd()
    if freecad is None:
        print("femexploration_shim: no FreeCADCmd found on this host; set FREECADCMD", file=sys.stderr)
        return 3
    kit = os.path.dirname(os.path.abspath(args.job))
    stub = args.stub if os.path.isabs(args.stub) else os.path.join(os.path.dirname(kit), args.stub)
    if not os.path.exists(stub):
        stub = os.path.join(kit, args.stub)
    command = freecad + [stub, "--", "--job", args.job]
    if args.out:
        command += ["--out", args.out]
    return subprocess.call(command)


if __name__ == "__main__":
    sys.exit(main())


##  @}
