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

#pragma once

#include <vector>

#include <QFont>
#include <QString>

#include <FCGlobal.h>

class QWidget;

namespace Gui
{

/**
 * Scales the whole Qt interface on the fly, in the manner of VS Code: one
 * command shrinks or grows text, icons and spacing together, trading text size
 * for screen real estate.  Nothing needs a restart.
 *
 * Everything is driven from a single parameter, so the commands, the
 * preferences page, the parameter editor, a preference pack and a Python script
 * all take the same path:
 * @code
 * User parameter:BaseApp/Preferences/MainWindow
 *     UiZoom  (int)  scale in percent, 100 = unscaled, clamped to [50, 300]
 * @endcode
 *
 * Five mechanisms make up the scaling, in decreasing order of how much they
 * move:
 *
 * 1. The application font.  Qt styles derive most widget geometry from font
 *    metrics, so a scaled `qApp->setFont()` moves menus, toolbar buttons, tree
 *    rows, tab bars, headers and dialogs in one step, and Qt propagates it for
 *    us via QEvent::ApplicationFontChange.
 * 2. Icon sizes, multiplied at the existing choke points -
 *    ToolBarManager::toolBarIconSize() for the toolbars, menu bar and status
 *    bar, TreeWidget::setIconHeight() for the tree.
 * 3. `px` literals in every stylesheet, rewritten as the .qss is loaded.  This
 *    runs inside Application::replaceVariablesInQss(), so it covers
 *    defaults.qss, the theme sheet and the dock overlay sheet alike, and works
 *    on third-party themes with no edits to them.
 * 4. Hardcoded pixel sizes that fall through the other three.  Those opt in by
 *    carrying an `fc_baseMinimumWidth` or `fc_baseIconSize` dynamic property
 *    holding the unzoomed value; apply() sweeps the main window and re-applies
 *    the scaled value to whatever carries one.  A new call site needs the
 *    property and nothing here.
 * 5. `@UiZoom`, exposed to the stylesheet expression language so a theme can
 *    scale something explicitly with `@{12px * @UiZoom}`.
 *
 * Deliberately out of scope: the 3D viewport.  Its camera zoom, the navigation
 * cube (which has its own NaviCube/CubeSize parameter) and the Coin3D overlay
 * text are left alone, exactly as Ctrl+= in VS Code leaves the editor's own
 * zoom alone.
 *
 * Known limits, none of which have a cheap fix:
 * - `px` values inside a theme's YAML parameter file are resolved during
 *   placeholder substitution rather than read from the .qss text, so they are
 *   not auto-scaled.  Theme authors write `@{3px * @UiZoom}` for those.
 * - Dock panel *widths* come from the saved window state and are in pixels, so
 *   a panel keeps its width across a zoom change; its minimum width and its
 *   contents scale.  VS Code behaves the same way.
 * - Gui::FontScaledSVG recomputes only on resizeEvent, so it re-scales on the
 *   next resize rather than instantly.
 * - InputHintWidget bakes a fixed 22px into its generated key-glyph pixmaps,
 *   so the status bar hints stay one size.
 */
class GuiExport UiZoom
{
public:
    static UiZoom& instance();

    /**
     * Captures the base application font and attaches the parameter observer.
     * Must run before the first stylesheet load, so that the window is drawn at
     * the right size rather than resized after the first paint.
     */
    static void initialize();

    /// The zoom ladder that zoomIn()/zoomOut() step through, in percent.
    static const std::vector<int>& steps();

    static constexpr int MinPercent = 50;
    static constexpr int MaxPercent = 300;
    static constexpr int DefaultPercent = 100;

    int percent() const
    {
        return currentPercent;
    }
    double factor() const
    {
        return currentPercent / 100.0;
    }

    /// Clamps to [MinPercent, MaxPercent] and writes the parameter, which is
    /// what actually triggers apply().
    void setPercent(int percent);

    void zoomIn();
    void zoomOut();
    void reset();
    bool canZoomIn() const;
    bool canZoomOut() const;

    /// Scales an unzoomed pixel quantity, with a floor of 1 so nothing vanishes.
    int scaled(int px) const;

    /**
     * Rewrites `px` literals in raw .qss text.  Values below MinScaledPx and a
     * deny-list of border and image properties are left alone - scaling a 1px
     * hairline only blurs it.  Runs before placeholder substitution, so the
     * result of a `@{...}` expression is never scaled a second time.
     */
    QString scaleStyleSheet(const QString& qss) const;

    /// Re-applies every mechanism.  Called by the parameter observer.
    void apply();

private:
    UiZoom() = default;

    /// Smallest px literal in a stylesheet that is worth scaling.
    static constexpr int MinScaledPx = 4;

    void applyFont() const;
    void applyIconSizes() const;
    /// Mechanism 4: re-applies scaled values to widgets carrying a base-size property.
    void applyWidgetProperties() const;

    int currentPercent = DefaultPercent;
    QFont baseFont;
    bool initialized = false;
};

}  // namespace Gui
