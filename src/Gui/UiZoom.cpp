// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2026 FreeCAD Contributors                               *
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
#include <QRegularExpression>
#include <QTimer>
#include <QVariant>
#include <QWidget>
#endif

#include <algorithm>
#include <cmath>

#include <Base/Parameter.h>

#include "UiZoom.h"
#include "Application.h"
#include "MainWindow.h"
#include "ParamHandler.h"
#include "ToolBarManager.h"
#include "Tree.h"
#include "TreeParams.h"
#include "Window.h"


using namespace Gui;

namespace
{

/// Properties whose px values are hairlines or image geometry, and so are left
/// unscaled.  Matched against the exact property name, which is why
/// "border-radius" and "border-top-left-radius" are absent - those do scale.
bool isUnscalableProperty(const QString& property)
{
    static const QStringList exact {
        QStringLiteral("border"),
        QStringLiteral("border-width"),
        QStringLiteral("border-top-width"),
        QStringLiteral("border-right-width"),
        QStringLiteral("border-bottom-width"),
        QStringLiteral("border-left-width"),
        QStringLiteral("border-style"),
        QStringLiteral("border-image"),
        QStringLiteral("outline"),
        QStringLiteral("outline-width"),
        QStringLiteral("image"),
    };

    return exact.contains(property);
}

ParameterGrp::handle mainWindowGroup()
{
    return WindowParameter::getDefaultParameter()->GetGroup("MainWindow");
}

}  // namespace


UiZoom& UiZoom::instance()
{
    static UiZoom inst;
    return inst;
}

const std::vector<int>& UiZoom::steps()
{
    static const std::vector<int> ladder {50, 67, 75, 80, 90, 100, 110, 125, 150, 175, 200, 250, 300};
    return ladder;
}

void UiZoom::initialize()
{
    UiZoom& self = instance();
    if (self.initialized) {
        return;
    }

    // Captured before anything scales it, so every later apply() derives from
    // the untouched system font instead of compounding on the previous one.
    self.baseFont = QApplication::font();
    self.currentPercent =
        std::clamp(int(mainWindowGroup()->GetInt("UiZoom", DefaultPercent)), MinPercent, MaxPercent);
    self.initialized = true;

    static ParamHandlers handlers;
    handlers.addDelayedHandler(mainWindowGroup(), "UiZoom", [](ParameterGrp::handle hGrp) {
        UiZoom& zoom = instance();
        zoom.currentPercent =
            std::clamp(int(hGrp->GetInt("UiZoom", DefaultPercent)), MinPercent, MaxPercent);
        zoom.apply();
    });

    // The font must be in place before the first stylesheet load; the
    // stylesheet itself is applied by the caller right afterwards, so there is
    // nothing to reload yet.
    self.applyFont();

    // The toolbars and the tree do not exist yet, so their icon sizes have to
    // wait for the event loop.  Without this a zoom restored from the
    // parameters would reach them only on the user's first zoom change, and the
    // window would come up with unscaled tree icons.
    if (self.currentPercent != DefaultPercent) {
        QTimer::singleShot(0, qApp, []() {
            instance().applyIconSizes();
        });
    }
}

void UiZoom::setPercent(int percent)
{
    const int clamped = std::clamp(percent, MinPercent, MaxPercent);
    if (clamped == currentPercent) {
        return;
    }

    // Writing the parameter is what drives everything - the observer calls
    // apply().  This keeps the commands, the preferences page and a hand edit
    // in the parameter editor on exactly the same path.
    mainWindowGroup()->SetInt("UiZoom", clamped);
}

bool UiZoom::canZoomIn() const
{
    return currentPercent < steps().back();
}

bool UiZoom::canZoomOut() const
{
    return currentPercent > steps().front();
}

void UiZoom::zoomIn()
{
    const auto& ladder = steps();
    // Strictly above, so a hand-typed value between two steps still moves.
    auto it = std::upper_bound(ladder.begin(), ladder.end(), currentPercent);
    if (it != ladder.end()) {
        setPercent(*it);
    }
}

void UiZoom::zoomOut()
{
    const auto& ladder = steps();
    auto it = std::lower_bound(ladder.begin(), ladder.end(), currentPercent);
    if (it != ladder.begin()) {
        setPercent(*std::prev(it));
    }
}

void UiZoom::reset()
{
    setPercent(DefaultPercent);
}

int UiZoom::scaled(int px) const
{
    if (currentPercent == DefaultPercent) {
        return px;
    }
    return std::max(1, int(std::lround(px * factor())));
}

QString UiZoom::scaleStyleSheet(const QString& qss) const
{
    if (currentPercent == DefaultPercent) {
        return qss;
    }

    // One declaration at a time: the property name decides whether its px
    // values may be touched at all.
    static const QRegularExpression declaration(
        QStringLiteral("([A-Za-z-]+)(\\s*:\\s*)([^;{}]*)")
    );
    static const QRegularExpression pixels(QStringLiteral("(\\d+(?:\\.\\d+)?)px"));

    QString result;
    result.reserve(qss.size());
    qsizetype last = 0;

    auto it = declaration.globalMatch(qss);
    while (it.hasNext()) {
        const QRegularExpressionMatch match = it.next();
        const QString property = match.captured(1).toLower();

        result += qss.mid(last, match.capturedStart() - last);
        last = match.capturedEnd();

        QString value = match.captured(3);
        if (!isUnscalableProperty(property)) {
            QString scaledValue;
            qsizetype valueLast = 0;

            auto pit = pixels.globalMatch(value);
            while (pit.hasNext()) {
                const QRegularExpressionMatch px = pit.next();
                scaledValue += value.mid(valueLast, px.capturedStart() - valueLast);
                valueLast = px.capturedEnd();

                const double original = px.captured(1).toDouble();
                if (original < MinScaledPx) {
                    // A hairline. Scaling it only blurs it.
                    scaledValue += px.captured(0);
                }
                else {
                    scaledValue += QStringLiteral("%1px").arg(std::max(1L, std::lround(original * factor())));
                }
            }
            scaledValue += value.mid(valueLast);
            value = scaledValue;
        }

        result += match.captured(1) + match.captured(2) + value;
    }
    result += qss.mid(last);

    return result;
}

void UiZoom::applyFont() const
{
    QFont font = baseFont;
    if (baseFont.pointSizeF() > 0) {
        font.setPointSizeF(baseFont.pointSizeF() * factor());
    }
    else if (baseFont.pixelSize() > 0) {
        font.setPixelSize(std::max(1, int(std::lround(baseFont.pixelSize() * factor()))));
    }

    // Qt posts QEvent::ApplicationFontChange to every widget from here, which
    // is what makes the whole chrome follow without any plumbing of our own.
    QApplication::setFont(font);
}

void UiZoom::applyIconSizes() const
{
    // ToolBarManager::toolBarIconSize() applies the factor itself; this just
    // makes it re-read and push the new sizes out to every toolbar, plus the
    // menu bar and status bar areas.
    if (auto* manager = ToolBarManager::getInstance()) {
        manager->setupToolBarIconSize();
    }

    // The tree needs telling explicitly: TreeWidget::getIconSize() caches the
    // style's decoration size in a function-local static, so it does not
    // re-derive after a font change.
    if (auto* tree = TreeWidget::instance()) {
        const int base = TreeParams::getIconSize() > 0 ? int(TreeParams::getIconSize())
                                                       : TreeWidget::getIconSize();
        tree->setIconHeight(scaled(base));
    }
}

void UiZoom::applyWidgetProperties() const
{
    auto* mw = getMainWindow();
    if (!mw) {
        return;
    }

    const auto widgets = mw->findChildren<QWidget*>();
    for (auto* widget : widgets) {
        const QVariant minimumWidth = widget->property("fc_baseMinimumWidth");
        if (minimumWidth.isValid()) {
            widget->setMinimumWidth(scaled(minimumWidth.toInt()));
        }

        const QVariant iconSize = widget->property("fc_baseIconSize");
        if (iconSize.isValid()) {
            const int size = scaled(iconSize.toInt());
            if (auto* button = qobject_cast<QAbstractButton*>(widget)) {
                button->setIconSize(QSize(size, size));
            }
            else {
                // QComboBox, QTabBar and friends each declare their own
                // setIconSize; go through the meta-object rather than listing
                // every type here.
                widget->setProperty("iconSize", QSize(size, size));
            }
        }
    }
}

void UiZoom::apply()
{
    applyFont();
    applyIconSizes();
    applyWidgetProperties();

    // Re-reads the current .qss through replaceVariablesInQss(), which is where
    // the px scaling lives, and refreshes the dock overlay with it.
    if (Application::Instance && getMainWindow()) {
        Application::Instance->reloadStyleSheet();
    }
}
