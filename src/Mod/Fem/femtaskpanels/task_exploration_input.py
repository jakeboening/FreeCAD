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

"""Task panel for one design variable.

Shows the sweep grid the current settings produce, live.  Seeing the actual
values before anything is solved is the cheapest way to catch a bound entered
in the wrong unit or a level count that will take all night.
"""

__title__ = "FreeCAD FEM exploration input parameter task panel"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

from PySide import QtGui

from femexploration import model
from femexploration.model import Variable

from . import base_femtaskpanel


class _TaskPanel(base_femtaskpanel._BaseTaskPanel):
    """Edit the domain of one design variable."""

    def __init__(self, obj):
        super().__init__(obj)

        self.form = QtGui.QWidget()
        self.form.setWindowTitle("Design Variable")
        layout = QtGui.QVBoxLayout(self.form)

        target = QtGui.QLabel()
        target.setWordWrap(True)
        driven = f"{obj.Target.Label}.{obj.TargetProperty}" if obj.Target else "(unbound)"
        target.setText(f"<b>Drives:</b> {driven}")
        layout.addWidget(target)

        form = QtGui.QFormLayout()

        self.var_type = QtGui.QComboBox()
        self.var_type.addItems(list(model.VAR_TYPES))
        self.var_type.setCurrentText(obj.VarType)
        form.addRow("Type", self.var_type)

        unit = f" [{obj.Unit}]" if obj.Unit else ""
        self.lower = self._spin(obj.Lower)
        form.addRow("Lower" + unit, self.lower)
        self.upper = self._spin(obj.Upper)
        form.addRow("Upper" + unit, self.upper)
        self.nominal = self._spin(obj.Nominal)
        form.addRow("Nominal" + unit, self.nominal)

        self.scale = QtGui.QComboBox()
        self.scale.addItems(list(model.SCALES))
        self.scale.setCurrentText(obj.Scale)
        form.addRow("Spacing", self.scale)

        self.levels = QtGui.QSpinBox()
        self.levels.setRange(2, 1000)
        self.levels.setValue(int(obj.LevelCount))
        form.addRow("Levels", self.levels)

        self.geometry = QtGui.QCheckBox("Changes the geometry (forces a re-mesh)")
        self.geometry.setChecked(bool(obj.GeometryAffecting))
        form.addRow("", self.geometry)

        self.active = QtGui.QCheckBox("Vary this parameter")
        self.active.setChecked(bool(obj.Active))
        form.addRow("", self.active)

        layout.addLayout(form)

        layout.addWidget(QtGui.QLabel("Values this variable will take:"))
        self.preview = QtGui.QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(90)
        layout.addWidget(self.preview)

        for widget in (self.lower, self.upper):
            widget.valueChanged.connect(self.update_preview)
        self.levels.valueChanged.connect(self.update_preview)
        self.scale.currentTextChanged.connect(self.update_preview)
        self.var_type.currentTextChanged.connect(self.update_preview)
        self.update_preview()

    @staticmethod
    def _spin(value):
        box = QtGui.QDoubleSpinBox()
        box.setDecimals(6)
        box.setRange(-1e12, 1e12)
        box.setValue(float(value))
        return box

    def update_preview(self):
        """Show the grid without touching the document."""
        probe = Variable(
            name=self.obj.Name,
            unit=self.obj.Unit,
            var_type=self.var_type.currentText(),
            lower=self.lower.value(),
            upper=self.upper.value(),
            scale=self.scale.currentText(),
            level_count=self.levels.value(),
            levels=list(self.obj.Levels),
            enum_levels=list(self.obj.EnumLevels),
        )
        try:
            probe.validate()
            values = probe.grid()
        except model.SchemaError as exc:
            self.preview.setPlainText(str(exc))
            return

        unit = f" {self.obj.Unit}" if self.obj.Unit else ""
        self.preview.setPlainText(
            f"{len(values)} values:  " + ",  ".join(f"{v:g}{unit}" for v in values)
        )

    def accept(self):
        obj = self.obj
        obj.VarType = self.var_type.currentText()
        obj.Lower = self.lower.value()
        obj.Upper = self.upper.value()
        obj.Nominal = self.nominal.value()
        obj.Scale = self.scale.currentText()
        obj.LevelCount = self.levels.value()
        obj.GeometryAffecting = self.geometry.isChecked()
        obj.Active = self.active.isChecked()
        return super().accept()


##  @}
