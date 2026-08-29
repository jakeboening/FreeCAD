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

"""Promoting a model property to a design variable.

One function, :func:`promote`, behind every entry point - the toolbar command,
the parameter set panel, a macro, and the MCP bridge - so the rule about what
may be promoted, and the unit captured when it is, exists exactly once.

The mechanism is stock FreeCAD.  A property of the same type is added to an
``App::VarSet``, seeded with the current value, and the original property is
bound to it by expression.  From then on FreeCAD's dependency graph does the
propagation: a sweep writes one number to the variable set and everything
downstream - geometry, mesh input, constraint magnitudes - recomputes on its
own.  There is no bespoke "update what depends on this" code in this feature,
which is precisely the trap the reference implementation fell into.

No Gui imports here; the dialog lives in ``femguiutils``.
"""

__title__ = "FreeCAD FEM design variable promotion"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import FreeCAD

from femexploration import model
from femtools import explorationutils


#: Property types that can be driven by a sweep.  Quantity-valued types come
#: first because they are the common case and the ones that carry a unit.
PROMOTABLE_TYPES = (
    "App::PropertyLength",
    "App::PropertyDistance",
    "App::PropertyAngle",
    "App::PropertyArea",
    "App::PropertyVolume",
    "App::PropertyPressure",
    "App::PropertyForce",
    "App::PropertyTemperature",
    "App::PropertyMass",
    "App::PropertySpeed",
    "App::PropertyTime",
    "App::PropertyFrequency",
    "App::PropertyQuantity",
    "App::PropertyQuantityConstraint",
    "App::PropertyFloat",
    "App::PropertyFloatConstraint",
    "App::PropertyPercent",
    "App::PropertyInteger",
    "App::PropertyIntegerConstraint",
)

#: Types whose value is a ``Base.Quantity`` rather than a plain number.
QUANTITY_TYPES = frozenset(
    t
    for t in PROMOTABLE_TYPES
    if t
    not in (
        "App::PropertyFloat",
        "App::PropertyFloatConstraint",
        "App::PropertyPercent",
        "App::PropertyInteger",
        "App::PropertyIntegerConstraint",
    )
)

INTEGER_TYPES = frozenset({"App::PropertyInteger", "App::PropertyIntegerConstraint", "App::PropertyPercent"})


class PromotionError(Exception):
    """Raised when a property cannot be made into a design variable."""


# ************************************************************************************************
# inspection


def is_promotable(obj, prop):
    """Return ``(True, "")`` if *prop* on *obj* can become a design variable.

    Otherwise ``(False, reason)``.  The reason is written to be shown to the
    user directly, because "nothing happened" is a miserable failure mode for
    this particular action.
    """
    try:
        type_id = obj.getTypeIdOfProperty(prop)
    except Exception:
        return False, f"{obj.Name} has no property {prop!r}"

    if type_id not in PROMOTABLE_TYPES:
        return False, f"{prop} is a {type_id}, which is not a numeric property"

    status = obj.getTypeOfProperty(prop)
    for blocked in ("ReadOnly", "Output", "Hidden"):
        if blocked in status:
            return False, f"{prop} is {blocked.lower()} and cannot be driven"

    if "ReadOnly" in obj.getEditorMode(prop):
        return False, f"{prop} is read only and cannot be driven"

    if bound_expression(obj, prop) is not None:
        return False, (
            f"{prop} is already driven by an expression. Writing to it would be "
            "silently overwritten on the next recompute; promote the property "
            "that expression refers to instead"
        )

    return True, ""


def bound_expression(obj, prop):
    """Return the expression driving *prop*, or None.

    Matches on the leading path component, since a binding may target a
    sub-element such as ``Placement.Base.x``.
    """
    for path, expression in obj.ExpressionEngine:
        if path == prop or path.startswith(prop + "."):
            return expression
    return None


def promotable_properties(obj):
    """Yield ``(name, type_id, value_string)`` for every promotable property."""
    out = []
    for name in obj.PropertiesList:
        ok, _ = is_promotable(obj, name)
        if not ok:
            continue
        try:
            value = obj.getPropertyByName(name)
        except Exception:
            continue
        out.append((name, obj.getTypeIdOfProperty(name), str(value)))
    return out


def capture_unit(obj, prop):
    """Return the unit string a value of *prop* should be expressed in.

    For quantity-valued properties this is the user's preferred display unit,
    so bounds read the way the engineer thinks - ``2 mm`` rather than an
    internal number that happens to mean millimetres.  Dimensionless
    properties return an empty string.
    """
    if obj.getTypeIdOfProperty(prop) not in QUANTITY_TYPES:
        return ""
    try:
        # getUserPreferred() -> (display_string, factor, unit_string)
        return obj.getPropertyByName(prop).getUserPreferred()[2]
    except Exception:
        return ""


def value_in_unit(obj, prop, unit):
    """Read *prop* as a plain float expressed in *unit*."""
    value = obj.getPropertyByName(prop)
    if unit:
        return float(value.getValueAs(unit))
    return float(value)


def default_bounds(obj, prop, value):
    """Suggest ``(lower, upper)`` for a newly promoted variable.

    A constrained property already states its own limits, so use them; that is
    free and it stops the classic "swept a wall thickness to minus three
    millimetres" mistake before it happens.  Otherwise fall back to a band
    around the current value.
    """
    try:
        constraints = obj.getPropertyByName(prop)
        low = getattr(constraints, "Minimum", None)
        high = getattr(constraints, "Maximum", None)
        if low is not None and high is not None:
            low, high = float(low), float(high)
            if low > -1e300 and high < 1e300 and high > low:
                return low, high
    except Exception:
        pass

    if value > 0.0:
        return 0.5 * value, 1.5 * value
    if value < 0.0:
        return 1.5 * value, 0.5 * value
    return -1.0, 1.0


# ************************************************************************************************
# promotion


def _unique_property_name(container, base):
    """Return a property name not already used on *container*."""
    name = "".join(c if c.isalnum() or c == "_" else "_" for c in base)
    if name and name[0].isdigit():
        name = "_" + name
    if name not in container.PropertiesList:
        return name
    index = 2
    while f"{name}{index}" in container.PropertiesList:
        index += 1
    return f"{name}{index}"


def promote(obj, prop, analysis, param_set=None, varset=None, label=None):
    """Make *prop* of *obj* a design variable of *analysis*.

    Returns the new ``Fem::ExplorationInput`` document object.

    Raises :class:`PromotionError` without touching the document if the
    property cannot be driven.
    """
    ok, reason = is_promotable(obj, prop)
    if not ok:
        raise PromotionError(reason)

    doc = obj.Document
    type_id = obj.getTypeIdOfProperty(prop)
    unit = capture_unit(obj, prop)
    value = value_in_unit(obj, prop, unit)

    if varset is None:
        varset = explorationutils.get_varset(analysis, create=True, doc=doc)
    if param_set is None:
        param_set = explorationutils.get_parameter_set(analysis)
        if param_set is None:
            import ObjectsFem

            param_set = ObjectsFem.makeExplorationParameters(doc)
            param_set.VarSet = varset
            if analysis is not None:
                analysis.addObject(param_set)

    # the variable itself, of the same type so it carries the same unit
    var_name = _unique_property_name(varset, label or f"{obj.Name}_{prop}")
    varset.addProperty(
        type_id,
        var_name,
        "Design Variables",
        f"Drives {obj.Label}.{prop}",
    )
    setattr(varset, var_name, obj.getPropertyByName(prop))

    # bind the original property to it; from here the dependency graph propagates
    obj.setExpression(prop, f"{varset.Name}.{var_name}")

    inp = _make_input(doc, obj, prop, type_id, unit, value, var_name, varset, label)
    param_set.addObject(inp)
    return inp


def _make_input(doc, obj, prop, type_id, unit, value, var_name, varset, label):
    """Create and populate the ExplorationInput describing the new variable."""
    import ObjectsFem

    inp = ObjectsFem.makeExplorationInput(doc, "Input")
    inp.Label = label or f"{obj.Label} {prop}"

    inp.Target = varset
    inp.TargetProperty = var_name
    inp.TargetTypeId = type_id
    inp.Unit = unit

    lower, upper = default_bounds(obj, prop, value)
    inp.Nominal = value
    inp.Lower = lower
    inp.Upper = upper
    inp.VarType = model.INTEGER if type_id in INTEGER_TYPES else model.CONTINUOUS
    inp.GeometryAffecting = affects_geometry(obj, varset, var_name)

    return inp


def affects_geometry(obj, varset, var_name):
    """Guess whether driving this variable changes the meshed shape.

    Only geometry-affecting variables force a remesh per design point, and
    remeshing usually dominates the cost of a sweep, so getting this right is
    worth real time.  The user can override it.

    The test is structural: does anything downstream of the driven object carry
    a ``Shape``?  A load magnitude or a material property does not; a sketch
    constraint or a pad length does.
    """
    try:
        for dependent in [obj] + obj.OutListRecursive:
            if dependent.isDerivedFrom("Part::Feature"):
                return True
        # the driven object may itself be consumed by geometry further up
        for parent in obj.InListRecursive:
            if parent.isDerivedFrom("Part::Feature"):
                return True
    except Exception:
        # never let detection failure block a promotion; assume the safe answer
        return True
    return False


##  @}
