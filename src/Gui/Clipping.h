/***************************************************************************
 *   Copyright (c) 2013 Werner Mayer <wmayer[at]users.sourceforge.net>     *
 *                                                                         *
 *   This file is part of the FreeCAD CAx development system.              *
 *                                                                         *
 *   This library is free software; you can redistribute it and/or         *
 *   modify it under the terms of the GNU Library General Public           *
 *   License as published by the Free Software Foundation; either          *
 *   version 2 of the License, or (at your option) any later version.      *
 *                                                                         *
 *   This library  is distributed in the hope that it will be useful,      *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
 *   GNU Library General Public License for more details.                  *
 *                                                                         *
 *   You should have received a copy of the GNU Library General Public     *
 *   License along with this library; see the file COPYING.LIB. If not,    *
 *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
 *   Suite 330, Boston, MA  02111-1307, USA                                *
 *                                                                         *
 ***************************************************************************/


#pragma once

#include <QDialog>
#include <FCGlobal.h>

namespace App
{
class Document;
}

namespace Gui
{
class View3DInventor;
namespace Dialog
{

/**
 * Editor for the clipping of one 3D view.
 *
 * The planes themselves live in the viewer (Gui::ViewClipping), not here, so
 * they outlive this panel and can be saved with the document.  This class is a
 * pure editor over that state: every handler writes one field through
 * ViewClipping::setPlane(), and syncFromState() pushes the result back into the
 * widgets when ViewClipping::signalChanged fires.  Reopening the panel
 * therefore shows the clipping that is already active instead of resetting it.
 *
 * The one thing the panel edits beyond that state is the document's list of
 * saved views (Gui::NamedClippingView), which the row at the top of the panel
 * recalls, adds to and deletes from.
 *
 * @author Werner Mayer
 */
class GuiExport Clipping: public QDialog
{
    Q_OBJECT

public:
    static Clipping* makeDockWidget(Gui::View3DInventor*, App::Document* showOn);
    Clipping(Gui::View3DInventor* view, App::Document* showOn, QWidget* parent = nullptr);
    ~Clipping() override;

protected:
    /// Middle click dismisses the panel, matching the button's use elsewhere as
    /// "I am done here". Only over the panel's own background: clicks that land
    /// on a child widget belong to that widget.
    void mousePressEvent(QMouseEvent* event) override;

    void setupConnections();
    void onActiveDocument(const App::Document& doc);
    void onSavedViewsChanged(int);
    void onSaveViewClicked();
    void onDeleteViewClicked();
    void onGroupBoxXToggled(bool);
    void onGroupBoxYToggled(bool);
    void onGroupBoxZToggled(bool);
    void onClipXValueChanged(double);
    void onClipYValueChanged(double);
    void onClipZValueChanged(double);
    void onFlipClipXClicked();
    void onFlipClipYClicked();
    void onFlipClipZClicked();
    void onGroupBoxViewToggled(bool);
    void onClipViewValueChanged(double);
    void onFromViewClicked();
    void onAdjustViewdirectionToggled(bool);
    void onDirXValueChanged(double);
    void onDirYValueChanged(double);
    void onDirZValueChanged(double);
    void onClearAllClicked();
    void onCapXToggled(bool);
    void onCapYToggled(bool);
    void onCapZToggled(bool);
    void onCapViewToggled(bool);
    void onCapModeXCurrentIndexChanged(int);
    void onCapModeYCurrentIndexChanged(int);
    void onCapModeZCurrentIndexChanged(int);
    void onCapModeViewCurrentIndexChanged(int);
    void onCapColorXChanged();
    void onCapColorYChanged();
    void onCapColorZChanged();
    void onCapColorViewChanged();
    void onCapTransparencyXValueChanged(int);
    void onCapTransparencyYValueChanged(int);
    void onCapTransparencyZValueChanged(int);
    void onCapTransparencyViewValueChanged(int);

public:
    void reject() override;

private:
    /// Pushes the viewer's clipping state into every widget, without echoing back.
    void syncFromState();

    class Private;
    Private* d;
};

}  // namespace Dialog
}  // namespace Gui
