// SPDX-License-Identifier: LGPL-2.1-or-later
/***************************************************************************
 *   Copyright (c) 2026 The FreeCAD Project Association AISBL              *
 *                                                                         *
 *   This file is part of FreeCAD.                                         *
 *                                                                         *
 *   FreeCAD is free software: you can redistribute it and/or modify it    *
 *   under the terms of the GNU Lesser General Public License as           *
 *   published by the Free Software Foundation, either version 2.1 of the  *
 *   License, or (at your option) any later version.                       *
 *                                                                         *
 *   FreeCAD is distributed in the hope that it will be useful, but        *
 *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU      *
 *   Lesser General Public License for more details.                       *
 *                                                                         *
 *   You should have received a copy of the GNU Lesser General Public      *
 *   License along with FreeCAD. If not, see                               *
 *   <https://www.gnu.org/licenses/>.                                      *
 *                                                                         *
 **************************************************************************/

#ifndef GUI_MIDDLECLICKCONFIRM_H
#define GUI_MIDDLECLICKCONFIRM_H

#include <QObject>
#include <FCGlobal.h>

class QAbstractButton;
class QWidget;

namespace Gui
{

/** Middle click means "I am done here".
 *
 * Qt offers a mouse press to the widget under the pointer and then, for as
 * long as it stays ignored, to each parent in turn.  This filter watches every
 * step and acts only once the press reaches a dialog or the task panel, so
 * each widget on the way had its chance to claim the button first: text fields
 * paste the primary selection with it, item views select, and none of those
 * ever let the click through.
 *
 * On a dialog the click does what Enter would: the default button, else the
 * button box's OK, Yes or Close, else QDialog::accept() on a dialog with no
 * buttons at all.  On the task panel it accepts the running task dialog.
 *
 * NavigationStyle::confirmActiveDialog covers the 3D view, where a tap has to
 * be told from a pan.  Both honour the same preference.
 */
class GuiExport MiddleClickConfirm: public QObject
{
    Q_OBJECT

public:
    /// Installs the application-wide filter.  Safe to call more than once.
    static void install();
    /// Preferences/View MiddleClickConfirm, on by default.
    static bool enabled();
    /// The button a middle click on @a window stands in for, or nullptr.
    static QAbstractButton* confirmButton(QWidget* window);
    /// Confirms @a window once the current event is off the stack.
    /// @return whether there was anything to confirm.
    static bool confirm(QWidget* window);

protected:
    bool eventFilter(QObject* watched, QEvent* event) override;

private:
    explicit MiddleClickConfirm(QObject* parent);
};

}  // namespace Gui

#endif  // GUI_MIDDLECLICKCONFIRM_H
