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

"""Content addressing for design points.

A design point is identified by a hash of *everything that could change its
result*: the schema version, a fingerprint of the model as it stands apart from
the design variables, and the design vector itself.  Two consequences follow,
and both are the point of the exercise:

* **Resume.**  A run that died at evaluation 340 of 500 restarts in seconds,
  because the first 340 point directories are still on disk under hashes that
  still match.
* **Cache.**  Re-running an unchanged study costs nothing, and re-running one
  where a single bound moved re-evaluates only the points that actually differ.

The fingerprint is deliberately *conservative*: it hashes the repr of every
non-design-variable property of every analysis member, so any model edit at all
invalidates the whole cache.  Being wrong in the other direction - reusing a
result computed from a model that has since changed - would silently corrupt an
engineering answer, which is far worse than recomputing.

Nothing here imports FreeCAD.  Collecting the property reprs is the caller's
job (see :mod:`femexploration.spec`), which keeps this module unit-testable.
"""

__title__ = "FreeCAD FEM design exploration hashing"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import hashlib
import json

from .version import FORMAT_VERSION


#: Digest size in bytes for point ids.  6 bytes -> 12 hex characters, which is
#: short enough to be a readable directory name and long enough that a
#: collision within one study is not a practical concern.
_POINT_DIGEST_SIZE = 6

#: Decimal places design variable values are rounded to before hashing.  Without
#: this, a value that round-trips through JSON or through a Quantity with a
#: different last bit would hash differently and silently miss the cache.
_VALUE_PRECISION = 12


def canonical_json(payload):
    """Serialise *payload* so equal content always produces equal bytes.

    Sorted keys, no insignificant whitespace, and ``ensure_ascii`` so the
    encoding cannot vary by platform.
    """
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")


def _round(value):
    """Round a design variable value to the hashing precision."""
    try:
        return round(float(value), _VALUE_PRECISION)
    except (TypeError, ValueError):
        # non-numeric (an enumeration string, say) hashes as itself
        return value


def model_fingerprint(entries):
    """Hash the parts of the model that are *not* design variables.

    :param entries:
        an iterable of ``(object_name, type_id, {property_name: repr})``.  The
        caller is responsible for excluding design variable targets and
        ``Output``-status properties, and for including anything else that
        changes the answer - mesher settings and the solver binary version in
        particular.

    Order does not matter; entries are sorted before hashing so a change in
    document iteration order does not invalidate the cache.
    """
    normalised = sorted(
        (str(name), str(type_id), {str(k): str(v) for k, v in sorted(props.items())})
        for name, type_id, props in entries
    )
    payload = {"format_version": FORMAT_VERSION, "entries": normalised}
    return hashlib.blake2b(canonical_json(payload), digest_size=16).hexdigest()


def point_id(model_hash, inputs):
    """Return the stable identifier for one design point.

    :param model_hash: the value from :func:`model_fingerprint`.
    :param inputs: ``{variable_name: (value, unit)}`` or ``{variable_name: value}``.

    The unit is part of the identity on purpose.  A study re-specified in
    metres must not silently reuse results computed in millimetres.
    """
    normalised = {}
    for name in sorted(inputs):
        entry = inputs[name]
        if isinstance(entry, (tuple, list)) and len(entry) == 2:
            value, unit = entry
        else:
            value, unit = entry, ""
        normalised[str(name)] = [_round(value), str(unit)]

    payload = {
        "format_version": FORMAT_VERSION,
        "model_hash": str(model_hash),
        "inputs": normalised,
    }
    return hashlib.blake2b(canonical_json(payload), digest_size=_POINT_DIGEST_SIZE).hexdigest()


def study_fingerprint(spec_dict):
    """Hash a study specification, ignoring fields that cannot change results.

    Used to detect that a saved run directory belongs to a materially different
    study.  Execution settings - backend, parallelism, retention - are excluded
    because changing where a point runs must not invalidate its result.
    """
    ignored = {
        "backend",
        "max_parallel",
        "points_per_job",
        "retain",
        "resume_from_cache",
        "timeout",
        "failure_limit",
        "name",
    }
    filtered = {k: v for k, v in spec_dict.items() if k not in ignored}
    return hashlib.blake2b(canonical_json(filtered), digest_size=16).hexdigest()


##  @}
