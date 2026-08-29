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

"""Task panel for one output parameter.

The source combo drives a stacked widget so only the fields belonging to the
chosen source are on screen.  Showing all three sets at once turns the panel
into a guessing game about which ones matter.
"""

__title__ = "FreeCAD FEM exploration output parameter task panel"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

from PySide import QtGui

from femexploration import model

from . import base_femtaskpanel


class _TaskPanel(base_femtaskpanel._BaseTaskPanel):
    """Edit how one response is extracted from a solved model."""

    def __init__(self, obj):
        super().__init__(obj)

        self.form = QtGui.QWidget()
        self.form.setWindowTitle("Output Parameter")
        layout = QtGui.QVBoxLayout(self.form)

        form = QtGui.QFormLayout()
        self.source = QtGui.QComboBox()
        self.source.addItems(list(model.RESPONSE_SOURCES))
        self.source.setCurrentText(obj.Source)
        form.addRow("Source", self.source)

        self.unit = QtGui.QLineEdit(obj.Unit)
        form.addRow("Unit", self.unit)

        self.load_step = QtGui.QSpinBox()
        self.load_step.setRange(-1, 10000)
        self.load_step.setValue(int(obj.LoadStep))
        self.load_step.setToolTip("Which solution stage to read; -1 is the last")
        form.addRow("Load step", self.load_step)
        layout.addLayout(form)

        self.stack = QtGui.QStackedWidget()
        self.stack.addWidget(self._result_page(obj))
        self.stack.addWidget(self._expression_page(obj))
        self.stack.addWidget(self._extractor_page(obj))
        layout.addWidget(self.stack)
        layout.addStretch(1)

        self.source.currentIndexChanged.connect(self.stack.setCurrentIndex)
        self.stack.setCurrentIndex(self.source.currentIndex())

    def _result_page(self, obj):
        page = QtGui.QWidget()
        form = QtGui.QFormLayout(page)
        self.quantity = QtGui.QComboBox()
        self.quantity.addItems(list(model.RESULT_QUANTITIES))
        self.quantity.setCurrentText(obj.Quantity)
        form.addRow("Quantity", self.quantity)
        self.aggregate = QtGui.QComboBox()
        self.aggregate.addItems(list(model.AGGREGATES))
        self.aggregate.setCurrentText(obj.Aggregate)
        form.addRow("Aggregate", self.aggregate)
        return page

    def _expression_page(self, obj):
        page = QtGui.QWidget()
        form = QtGui.QFormLayout(page)
        self.expression = QtGui.QLineEdit(obj.Expression)
        self.expression.setPlaceholderText("Box.Shape.Volume * 7.85e-6")
        form.addRow("Expression", self.expression)
        return page

    def _extractor_page(self, obj):
        page = QtGui.QWidget()
        form = QtGui.QFormLayout(page)
        self.column = QtGui.QLineEdit(obj.Column)
        form.addRow("Column", self.column)
        self.reduction = QtGui.QComboBox()
        self.reduction.addItems(list(model.REDUCTIONS))
        self.reduction.setCurrentText(obj.Reduction)
        form.addRow("Reduction", self.reduction)
        return page

    def accept(self):
        obj = self.obj
        obj.Source = self.source.currentText()
        obj.Unit = self.unit.text()
        obj.LoadStep = self.load_step.value()
        obj.Quantity = self.quantity.currentText()
        obj.Aggregate = self.aggregate.currentText()
        obj.Expression = self.expression.text()
        obj.Column = self.column.text()
        obj.Reduction = self.reduction.currentText()
        return super().accept()


##  @}
