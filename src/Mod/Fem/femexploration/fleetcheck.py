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

"""Probe every fleet host before trusting a distributed result.

A design study spread over three machines is only as good as the least
consistent of them: a different FreeCAD, a different gmsh, a missing
CalculiX, and the design points that ran there disagree with the rest for
reasons that have nothing to do with the design.  This runs the shim's
``--check`` on each host and compares.
"""

__title__ = "FreeCAD FEM design exploration fleet check"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

#: keys whose values must agree across hosts for results to be comparable
MUST_MATCH = ("freecad", "gmsh", "ccx", "femexploration")


def local_report():
    """This machine's own check, run in-process."""
    from . import shim

    return shim.check()


def remote_report(host, shim_path, timeout=300):
    """Run the shim's check on *host* over ssh, via femfleet's helper."""
    from femfleet import core as fleet_core

    remote_dir = f"{host.workdir}/fem_exploration"
    fleet_core._ssh(host, f"mkdir -p {remote_dir}")
    push = subprocess.run(
        f"tar czf - femexploration_shim.py | ssh -o BatchMode=yes {host.ssh_host} "
        f"{shlex.quote(fleet_core._remote_cmd(host, f'cd {remote_dir} && tar xzf -'))}",
        shell=True,
        cwd=os.path.dirname(shim_path),
        capture_output=True,
        timeout=timeout,
    )
    if push.returncode != 0:
        return {"name": host.name, "reachable": False, "problems": ["could not ship the shim"]}
    result = fleet_core._ssh(
        host, f"cd {remote_dir} && {host.python} femexploration_shim.py --check", timeout=timeout
    )
    if result.returncode != 0:
        return {
            "name": host.name,
            "reachable": True,
            "problems": [f"check failed: {(result.stderr or result.stdout).strip()[:200]}"],
        }
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                report = json.loads(line)
            except ValueError:
                continue
            report["name"] = host.name
            report["reachable"] = True
            return report
    return {"name": host.name, "reachable": True, "problems": ["no report from the shim"]}


def check_fleet(config=None, hosts=None):
    """Check every host; returns ``{"config": path, "hosts": [reports]}``."""
    from femfleet import core as fleet_core

    from . import shim
    from .backends.fleet import default_cluster_config
    from .backends.fleet import port_is_free

    config = config or default_cluster_config()
    if hosts is None:
        if not os.path.exists(config):
            raise FileNotFoundError(f"no cluster configuration at {config}")
        hosts = fleet_core.load_hosts(Path(config))
    port = fleet_core.cluster_port(Path(config)) if os.path.exists(config) else 8765

    reports = []
    for host in hosts:
        if host.kind == "local":
            report = local_report()
            report["name"] = host.name
            report["reachable"] = True
        else:
            if not fleet_core.probe(host):
                report = {"name": host.name, "reachable": False, "problems": ["unreachable"]}
            else:
                report = remote_report(host, shim.__file__)
        report.setdefault("problems", [])
        if report.get("reachable") and not report.get("freecadcmd"):
            report["problems"].append("no FreeCADCmd found; set FREECADCMD on the host")
        reports.append(report)

    reference = next((r for r in reports if r.get("reachable") and r.get("freecad")), None)
    for report in reports:
        if reference is None or report is reference or not report.get("reachable"):
            continue
        for key in MUST_MATCH:
            if report.get(key) != reference.get(key):
                report["problems"].append(
                    f"{key} is {report.get(key)!r}, but {reference['name']} has {reference.get(key)!r}"
                )
    out = {"config": config, "port": port, "port_free": port_is_free(port), "hosts": reports}
    if not out["port_free"]:
        out["problems"] = [f"port {port} is in use on this machine; the fleet server cannot bind it"]
    return out


def format_report(report):
    lines = [f"Fleet check ({report['config']}), server port {report['port']}"
             + ("" if report.get("port_free") else "  ** PORT IN USE **")]
    for host in report["hosts"]:
        if not host.get("reachable"):
            lines.append(f"  {host['name']:16s} unreachable")
            continue
        lines.append(
            f"  {host['name']:16s} FreeCAD {host.get('freecad') or '-'}  gmsh {host.get('gmsh') or '-'}  "
            f"ccx {host.get('ccx') or '-'}  ({host.get('platform', '')})"
        )
        for problem in host.get("problems", []):
            lines.append(f"      ! {problem}")
    for problem in report.get("problems", []):
        lines.append(f"  ! {problem}")
    return "\n".join(lines)


##  @}
