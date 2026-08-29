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

"""Statistics over a design point table.

Everything a correlation matrix or a sensitivity chart needs, and nothing
that needs FreeCAD or Qt: rows in, numpy arrays and small result records out.
Only completed points count; a failed design has no outputs, and treating a
missing number as zero is how a sensitivity chart ends up pointing at the
wrong variable.
"""

__title__ = "FreeCAD FEM design exploration statistics"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

from .model import COMPLETED


def _scalar(value):
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def table_arrays(rows, input_keys, output_keys):
    """Completed rows as two float arrays, X (n, k) and Y (n, m).

    Rows missing any requested value are left out entirely rather than
    imputed; the returned index list says which rows survived.
    """
    import numpy as np

    xs, ys, kept = [], [], []
    for i, row in enumerate(rows):
        state = row.get("state") if isinstance(row, dict) else getattr(row, "state", None)
        if state != COMPLETED:
            continue
        inputs = row.get("inputs", {}) if isinstance(row, dict) else row.inputs
        outputs = row.get("outputs", {}) if isinstance(row, dict) else row.outputs
        try:
            x = [float(_scalar(inputs[k])) for k in input_keys]
            y = [float(_scalar(outputs[k])) for k in output_keys]
        except (KeyError, TypeError, ValueError):
            continue
        xs.append(x)
        ys.append(y)
        kept.append(i)
    return (
        np.array(xs, dtype=float).reshape(len(xs), len(input_keys)),
        np.array(ys, dtype=float).reshape(len(ys), len(output_keys)),
        kept,
    )


def pearson(matrix):
    """Pearson correlation between the columns of *matrix*; NaN-safe.

    A constant column has no correlation with anything, and numpy would
    print a warning and give NaN; here it is 0 and stays quiet.
    """
    import numpy as np

    m = np.asarray(matrix, dtype=float)
    if m.ndim != 2 or m.shape[0] < 2:
        return np.zeros((m.shape[-1], m.shape[-1])) if m.ndim == 2 else np.zeros((0, 0))
    centred = m - m.mean(axis=0)
    norms = np.sqrt((centred * centred).sum(axis=0))
    safe = np.where(norms > 0, norms, 1.0)
    unit = centred / safe
    corr = unit.T @ unit
    corr[norms == 0, :] = 0.0
    corr[:, norms == 0] = 0.0
    np.fill_diagonal(corr, 1.0)
    return corr


def spearman(matrix):
    """Rank correlation: monotone rather than linear association."""
    import numpy as np

    m = np.asarray(matrix, dtype=float)
    if m.ndim != 2 or m.shape[0] < 2:
        return pearson(m)
    ranks = np.empty_like(m)
    for j in range(m.shape[1]):
        order = m[:, j].argsort(kind="stable")
        r = np.empty(m.shape[0])
        r[order] = np.arange(m.shape[0], dtype=float)
        # average ties, or a discrete variable's correlation is exaggerated
        values = m[order, j]
        start = 0
        while start < len(values):
            end = start
            while end + 1 < len(values) and values[end + 1] == values[start]:
                end += 1
            if end > start:
                r[order[start : end + 1]] = 0.5 * (start + end)
            start = end + 1
        ranks[:, j] = r
    return pearson(ranks)


def correlation_matrix(rows, input_keys, output_keys, method="pearson"):
    """Correlation among inputs and outputs, as (labels_in_order, matrix)."""
    import numpy as np

    x, y, _kept = table_arrays(rows, input_keys, output_keys)
    keys = list(input_keys) + list(output_keys)
    if x.shape[0] < 2:
        return keys, np.zeros((len(keys), len(keys)))
    data = np.hstack([x, y])
    return keys, (spearman(data) if method == "spearman" else pearson(data))


def linear_sensitivity(x, y):
    """Standardised regression coefficients of y on the columns of x.

    Each input is scaled to unit variance before the least squares fit, so
    a coefficient reads as "standard deviations of output per standard
    deviation of input" and inputs with different units compare directly.
    These are the numbers behind the tornado bars every commercial tool
    shows.  Returns ``(coefficients, r_squared)``.
    """
    import numpy as np

    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float).reshape(-1)
    n, k = x.shape
    if n < 2 or k == 0:
        return np.zeros(k), 0.0
    sx = x.std(axis=0)
    sy = y.std()
    if sy == 0:
        return np.zeros(k), 1.0
    scale = np.where(sx > 0, sx, 1.0)
    xs = (x - x.mean(axis=0)) / scale
    ys = (y - y.mean()) / sy
    design = np.hstack([xs, np.ones((n, 1))])
    coeffs, _res, _rank, _sv = np.linalg.lstsq(design, ys, rcond=None)
    beta = coeffs[:k]
    beta[sx == 0] = 0.0
    fitted = design @ coeffs
    ss_res = float(((ys - fitted) ** 2).sum())
    ss_tot = float((ys**2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
    return beta, max(0.0, min(1.0, r2))


def sensitivity_table(rows, input_keys, output_keys):
    """Per output, the standardised effect of each input.

    Returns ``{output_key: {"coefficients": {input_key: beta}, "r2": r2,
    "points": n}}``.  ``r2`` well below one means the linear picture is
    incomplete - the chart should say so rather than let bars imply more
    than they know.
    """
    x, y, kept = table_arrays(rows, input_keys, output_keys)
    table = {}
    for j, key in enumerate(output_keys):
        beta, r2 = linear_sensitivity(x, y[:, j]) if len(kept) else ([0.0] * len(input_keys), 0.0)
        table[key] = {
            "coefficients": {name: float(b) for name, b in zip(input_keys, beta)},
            "r2": float(r2),
            "points": len(kept),
        }
    return table


def mesh_convergence_warning(rows, output_keys, threshold=0.25):
    """Outputs whose variation tracks the mesh size suspiciously closely.

    Every point records its node count.  If an output correlates with the
    node count about as strongly as with any design variable, some of what
    the chart calls a design trend is discretisation noise.  Returns the
    outputs to flag, with the offending correlation.
    """
    from .model import MESH_NODES

    # the node count is recorded as an implicit output of every point
    _x, y, kept = table_arrays(rows, [], [MESH_NODES] + list(output_keys))
    if len(kept) < 3:
        return {}
    nodes = y[:, 0]
    if nodes.std() == 0:
        return {}
    flagged = {}
    corr = pearson(y)
    for j, key in enumerate(output_keys):
        value = float(corr[0, 1 + j])
        if abs(value) >= threshold:
            flagged[key] = value
    return flagged


##  @}
