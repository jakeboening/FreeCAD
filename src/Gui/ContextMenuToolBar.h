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

#include <QPoint>

#include <FCGlobal.h>

class QMenu;
class QWidget;

namespace Gui
{

/**
 * Prepends a horizontal strip of command icons to a context menu, in the manner
 * of Siemens NX: the familiar vertical list of labelled entries is left alone,
 * and a compact icon-only toolbar of frequently used commands is placed next to
 * the cursor.
 *
 * The strip is a QWidgetAction holding a QToolBar, so it participates in the
 * menu like any other action - it is laid out by the menu, follows the menu
 * palette, and disappears with the menu.
 *
 * The menu is anchored by the strip rather than by its own top left corner: the
 * bottom left corner of the icons lands on the cursor, so the first icon is one
 * short move up and to the right.  A long list would push that anchor away from
 * the click, so past MaxItems entries the tail of the list folds into a "More"
 * submenu and the menu stays short enough to place honestly.  It is free to
 * overhang the main window - a menu is a popup, and only the screen bounds it.  That first icon is the menu's default action
 * - "Edit sketch", "Edit pad", whatever the view provider offers for the object
 * that was clicked - followed by the configured commands.
 *
 * Which commands appear is read from
 * @code
 * User parameter:BaseApp/Preferences/ContextMenu
 *     ShowToolBar   (bool)   master switch, default true
 *     ViewCommands  (string) comma separated command names for the 3D view
 *     TreeCommands  (string) comma separated command names for the tree
 *     IconSize      (int)    icon pixel size, 0 = follow ToolbarIconSize
 *     MaxItems      (int)    entries to show before the rest folds into a
 *                            "More" submenu, default 12, 0 = never fold
 *     QuickCommands (string) comma separated command names for the quick menu
 *     LeftClickQuickMenu   (bool)   raise the quick menu on a left click in an
 *                                   empty part of the 3D view, default true
 *     LeftClickWorkbenches (string) comma separated workbench names it applies
 *                                   in, default "PartDesignWorkbench", "*" = all
 * @endcode
 * "Separator" may appear in a command list to insert a gap.  Commands that are
 * not registered, or that carry no icon, are skipped silently - an icon strip
 * has nothing to show for a command without a pixmap.
 */
class GuiExport ContextMenuToolBar
{
public:
    /**
     * Insert the icon strip into @a menu and work out where the menu should
     * appear.  The caller pops the menu up at the returned position, with
     * popup() or exec() as it sees fit.
     *
     * The menu must already be fully populated, and its default action set, as
     * both feed into the result.  If the strip is switched off, or none of the
     * configured commands can be shown, @a at is returned unchanged and the
     * menu is left alone.
     *
     * @param menu       the context menu, already populated
     * @param recipient  "View" or "Tree" - the same string workbenches receive
     *                   in setupContextMenu(), used to pick the command list
     * @param at         where the user clicked, in global coordinates
     * @return           top left corner for the menu, in global coordinates
     */
    static QPoint prepare(QMenu& menu, const char* recipient, const QPoint& at);

    /**
     * Show the strip on its own, with no list under it, anchored the same way.
     *
     * This is the quick menu a left click in an empty part of the 3D view
     * raises.  It reads its own command list, headed by the ruler, because
     * there is no clicked object to offer an edit action for.
     */
    static void popupQuickMenu(QWidget* parent, const QPoint& at);

    /// Whether a left click should raise the quick menu in this workbench.
    static bool quickMenuWantedFor(const char* workbench);

    /// Whether the strip is switched on at all.
    static bool isEnabled();
};

}  // namespace Gui
