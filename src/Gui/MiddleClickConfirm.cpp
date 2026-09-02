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

#include "PreCompiled.h"
#ifndef _PreComp_
#include <QAbstractButton>
#include <QApplication>
#include <QDialog>
#include <QDialogButtonBox>
#include <QEvent>
#include <QMouseEvent>
#include <QPushButton>
#include <QTimer>
#endif

#include <App/Application.h>
#include <Base/Parameter.h>

#include "MiddleClickConfirm.h"
#include "Control.h"
#include "TaskView/TaskView.h"


using namespace Gui;

MiddleClickConfirm::MiddleClickConfirm(QObject* parent)
    : QObject(parent)
{}

void MiddleClickConfirm::install()
{
    static MiddleClickConfirm* filter = nullptr;
    if (!filter) {
        filter = new MiddleClickConfirm(qApp);
        qApp->installEventFilter(filter);
    }
}

bool MiddleClickConfirm::enabled()
{
    ParameterGrp::handle hGrp =
        App::GetApplication().GetParameterGroupByPath("User parameter:BaseApp/Preferences/View");
    return hGrp->GetBool("MiddleClickConfirm", true);
}

QAbstractButton* MiddleClickConfirm::confirmButton(QWidget* window)
{
    const auto usable = [](QAbstractButton* button) {
        return button && button->isVisible() && button->isEnabled();
    };

    // A way out rather than a way through: Cancel, Abort, Discard.  Close is
    // the exception, being the "done" button of a panel with nothing to accept.
    const auto dismisses = [](QAbstractButton* button) {
        auto* box = qobject_cast<QDialogButtonBox*>(button->parentWidget());
        if (!box || box->standardButton(button) == QDialogButtonBox::Close) {
            return false;
        }
        const auto role = box->buttonRole(button);
        return role == QDialogButtonBox::RejectRole || role == QDialogButtonBox::DestructiveRole;
    };

    // What Enter would press.  QDialog promotes the first auto-default button
    // when it is shown, so a button box's OK normally lands here - and so does
    // a lone Cancel, which is where Enter and the middle button part ways.
    for (QPushButton* button : window->findChildren<QPushButton*>()) {
        if (button->isDefault() && usable(button) && !dismisses(button)) {
            return button;
        }
    }

    for (QDialogButtonBox* box : window->findChildren<QDialogButtonBox*>()) {
        for (auto role : {QDialogButtonBox::AcceptRole, QDialogButtonBox::YesRole}) {
            for (QAbstractButton* button : box->buttons()) {
                if (box->buttonRole(button) == role && usable(button)) {
                    return button;
                }
            }
        }
        // Close carries the reject role, but on a panel with nothing to accept
        // it is the "done" button.
        if (QPushButton* close = box->button(QDialogButtonBox::Close); usable(close)) {
            return close;
        }
    }

    return nullptr;
}

bool MiddleClickConfirm::confirm(QWidget* window)
{
    // Deferred: confirming tears the window down, and with it whatever is
    // being edited, so it happens once this event is off the stack rather
    // than underneath it.  The context object cancels the call if the window
    // goes first.
    if (QAbstractButton* button = confirmButton(window)) {
        QTimer::singleShot(0, button, [button]() {
            button->click();
        });
        return true;
    }

    auto* dialog = qobject_cast<QDialog*>(window);
    if (!dialog) {
        return false;
    }

    // Buttons, but none of them a way to finish - Cancel on its own, say - is
    // a dialog that does not want finishing this way.  One with no buttons at
    // all has nothing else to offer.
    if (!window->findChildren<QPushButton*>().isEmpty()) {
        return false;
    }

    QTimer::singleShot(0, dialog, [dialog]() {
        dialog->accept();
    });
    return true;
}

bool MiddleClickConfirm::eventFilter(QObject* watched, QEvent* event)
{
    if (event->type() != QEvent::MouseButtonPress || !watched->isWidgetType()) {
        return false;
    }
    if (static_cast<QMouseEvent*>(event)->button() != Qt::MiddleButton) {
        return false;
    }

    auto* widget = static_cast<QWidget*>(watched);

    // The press has climbed to the task panel: nothing in it wanted the button.
    // Matched by class rather than through Control().taskPanel(), which only
    // knows the "Tasks" dock window and not the one inside the combo view.
    if (qobject_cast<TaskView::TaskView*>(widget)) {
        if (!Control().activeDialog() || !enabled()) {
            return false;
        }
        QTimer::singleShot(0, qApp, []() {
            if (Control().activeDialog()) {
                Control().accept();
            }
        });
        return true;
    }

    // Likewise a dialog, whether it is a window of its own or sits in a dock.
    if (!qobject_cast<QDialog*>(widget) || !enabled()) {
        return false;
    }
    return confirm(widget);
}
