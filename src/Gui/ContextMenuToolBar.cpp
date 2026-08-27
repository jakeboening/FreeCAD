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

#include <QAction>
#include <QCoreApplication>
#include <QGuiApplication>
#include <QMouseEvent>
#include <QEvent>
#include <QHBoxLayout>
#include <QMenu>
#include <QProxyStyle>
#include <QScreen>
#include <QToolBar>
#include <QWidgetAction>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <string>

#include "ContextMenuToolBar.h"
#include "Action.h"
#include "Application.h"
#include "BitmapFactory.h"
#include "Command.h"

#include <App/Application.h>
#include <Base/Parameter.h>


using namespace Gui;

namespace
{

// Padding between the icons and the edges of the menu.  Kept as constants
// because the anchor is measured from the icons, not from the menu row that
// holds them, and the row is not laid out until the menu is shown.
constexpr int StripMarginH = 6;
constexpr int StripMarginV = 3;

ParameterGrp::handle contextMenuParams()
{
    return App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/ContextMenu"
    );
}

bool isRecipient(const char* recipient, const char* name)
{
    return recipient && std::strcmp(recipient, name) == 0;
}

/// The parameter prefix a recipient reads its command list from.
const char* group(const char* recipient)
{
    if (isRecipient(recipient, "Tree")) {
        return "Tree";
    }
    if (isRecipient(recipient, "Quick")) {
        return "Quick";
    }
    return "View";
}

const char* defaultCommands(const char* recipient)
{
    if (isRecipient(recipient, "Tree")) {
        return "Std_ToggleVisibility,Std_SetAppearance,Std_RandomColor,Separator,"
               "Std_TreeSelectAllInstances,Std_Properties,Separator,Std_Delete";
    }
    if (isRecipient(recipient, "Quick")) {
        // The same icons as the 3D view's strip, with the ruler moved to the
        // front - the quick menu has no clicked object to offer an edit for, so
        // measuring is what belongs nearest the pointer.
        return "Std_Measure,Separator,Std_ViewFitAll,Std_ViewFitSelection,Std_ViewIsometric,"
               "Separator,Std_ToggleVisibility,Std_SetAppearance,Std_RandomColor";
    }
    return "Std_ViewFitAll,Std_ViewFitSelection,Std_ViewIsometric,Separator,"
           "Std_ToggleVisibility,Std_SetAppearance,Std_RandomColor,Separator,Std_Measure";
}

int stripIconSize()
{
    int size = static_cast<int>(contextMenuParams()->GetInt("IconSize", 0));
    if (size <= 0) {
        ParameterGrp::handle hGrp = App::GetApplication().GetParameterGroupByPath(
            "User parameter:BaseApp/Preferences/General"
        );
        size = static_cast<int>(hGrp->GetInt("ToolbarIconSize", 24));
    }
    return std::clamp(size, 16, 32);
}

/// Lets a menu scroll instead of spilling into extra columns.
class ScrollableMenuStyle: public QProxyStyle
{
public:
    // The default constructor leaves baseStyle() following the application
    // style without taking ownership of it.
    ScrollableMenuStyle() = default;

    int styleHint(
        StyleHint hint,
        const QStyleOption* option,
        const QWidget* widget,
        QStyleHintReturn* returnData
    ) const override
    {
        if (hint == SH_Menu_Scrollable) {
            return 1;
        }
        return QProxyStyle::styleHint(hint, option, widget, returnData);
    }
};

/**
 * Build a stand-in for an action of the menu.
 *
 * The real action is not put into the strip directly: a QToolBar emits
 * actionTriggered only after the action's own handlers have run, so the command
 * would start while the menu still holds the mouse grab.  The proxy mirrors the
 * real action's appearance and state, hides the menu, and only then triggers the
 * real thing.
 *
 * The trigger is deliberately synchronous.  Both call sites tear down state the
 * command needs the moment the menu is done - TreeWidget clears contextItem, and
 * the 3D view's menu owns the receiver its edit actions are connected to - so
 * anything posted for later would fire into the wreckage, or not at all.
 * Hiding the menu already releases the grab, and Qt holds back the menu's
 * deferred deletion until the command's own event loop, if it opens one, has
 * finished.
 */
QAction* proxyFor(QAction* real, const QIcon& icon, const QString& label, QMenu* menu, QWidget* parent)
{
    auto* proxy = new QAction(icon, label, parent);
    proxy->setToolTip(real->toolTip().isEmpty() ? label : real->toolTip());
    proxy->setStatusTip(real->statusTip());
    proxy->setEnabled(real->isEnabled());
    proxy->setCheckable(real->isCheckable());
    proxy->setChecked(real->isChecked());

    QObject::connect(proxy, &QAction::triggered, menu, [menu, real]() {
        menu->close();
        real->trigger();
    });

    return proxy;
}

/// The command's icon strip entry, or nullptr if it cannot have one.
QAction* proxyForCommand(Command* cmd, QMenu* menu, QWidget* parent)
{
    cmd->initAction();
    Action* wrapper = cmd->getAction();
    if (!wrapper || !wrapper->action()) {
        return nullptr;
    }

    QAction* real = wrapper->action();
    if (real->icon().isNull()) {
        // An icon-only strip has nothing to show for a command without a pixmap.
        return nullptr;
    }

    cmd->testActive();

    // commandMenuText() strips the mnemonic '&', which a menu needs and an
    // icon-only button would otherwise leak into its tooltip.
    return proxyFor(real, real->icon(), Action::commandMenuText(cmd), menu, parent);
}

/**
 * The strip entry for whatever the menu considers its default action.
 *
 * Both call sites set that to the first thing the view provider offered for the
 * object under the cursor - "Edit sketch" on a sketch, "Edit pad" on a pad - so
 * it is what belongs closest to the pointer.  Edit entries usually carry no
 * pixmap of their own, hence the generic fallback.
 */
QAction* proxyForDefaultAction(QMenu& menu, QWidget* parent)
{
    QAction* real = menu.defaultAction();
    if (!real || real->isSeparator() || real->text().isEmpty()) {
        return nullptr;
    }

    QIcon icon = real->icon();
    if (icon.isNull()) {
        icon = BitmapFactory().iconFromTheme("edit-edit");
    }
    if (icon.isNull()) {
        return nullptr;
    }

    QString label = real->text();
    label.remove(QLatin1Char('&'));

    return proxyFor(real, icon, label, &menu, parent);
}

/// Move the strip, and the separator that divides it from the list, to one end.
void placeStrip(QMenu& menu, QAction* strip, QAction* separator, bool atTop)
{
    menu.removeAction(strip);
    menu.removeAction(separator);

    if (atTop) {
        QAction* first = menu.actions().value(0);
        if (first) {
            menu.insertAction(first, strip);
            menu.insertAction(first, separator);
            return;
        }
    }
    else {
        menu.addAction(separator);
    }
    menu.addAction(strip);
}

/**
 * Where the menu has to sit for the strip's bottom left corner to land on @a at.
 * Costs a layout pass, so the caller gets the resulting size back as well.
 *
 * This is an estimate: a menu that has never been shown reports a taller row for
 * the strip than it ends up using, because the widget inside it is not styled
 * until it is polished.  Close enough to decide which way the menu should hang;
 * StripAnchor puts it exactly where it belongs once the real geometry exists.
 */
/// Entries a user would call items - the rules between groups are not items.
int listItems(const QMenu& menu)
{
    const QList<QAction*> actions = menu.actions();
    return static_cast<int>(std::count_if(actions.cbegin(), actions.cend(), [](QAction* action) {
        return !action->isSeparator();
    }));
}

/**
 * Move everything past @a maxItems entries into a submenu at the end.
 *
 * The obvious alternative - cap the height and let the menu scroll - does not
 * work: QMenu only builds a scroller when it outgrows the whole screen, and a
 * maximumHeight below that just clips the rows off.  Folding the tail into a
 * submenu keeps every entry reachable and, unlike scrolling, reads the same
 * whether the list hangs below the strip or stands above it.
 */
void foldOverflow(QMenu& menu, int maxItems)
{
    const QList<QAction*> actions = menu.actions();

    int items = 0;
    int cut = -1;
    for (int i = 0; i < actions.size(); ++i) {
        if (actions.at(i)->isSeparator()) {
            continue;
        }
        if (++items > maxItems) {
            cut = i;
            break;
        }
    }
    if (cut < 0) {
        return;
    }

    // Leave no rule dangling at the end of what stays behind.
    while (cut > 0 && actions.at(cut - 1)->isSeparator()) {
        --cut;
    }

    auto* more = new QMenu(QCoreApplication::translate("Gui::ContextMenuToolBar", "More"), &menu);
    for (int i = cut; i < actions.size(); ++i) {
        QAction* action = actions.at(i);
        menu.removeAction(action);
        if (action->isSeparator() && more->actions().isEmpty()) {
            continue;
        }
        more->addAction(action);
    }

    if (more->actions().isEmpty()) {
        delete more;
        return;
    }

    menu.addSeparator();
    menu.addMenu(more);
}

/// Ask a menu to scroll rather than spill into a second column.
void makeScrollable(QMenu& menu)
{
    auto* style = new ScrollableMenuStyle;
    style->setParent(&menu);
    menu.setStyle(style);
}

QPoint anchorFor(QMenu& menu, QAction* strip, const QPoint& at, QSize& size)
{
    menu.ensurePolished();
    size = menu.sizeHint();
    size.setHeight(std::min(size.height(), menu.maximumHeight()));

    const QRect row = menu.actionGeometry(strip);
    return {at.x() - row.left() - StripMarginH, at.y() - row.bottom() + StripMarginV};
}

/// Whether the menu has given up on one column and started another.
bool spansColumns(const QMenu& menu)
{
    const QList<QAction*> actions = menu.actions();
    if (actions.isEmpty()) {
        return false;
    }

    const int left = menu.actionGeometry(actions.constFirst()).x();
    return std::any_of(actions.cbegin(), actions.cend(), [&](QAction* action) {
        return menu.actionGeometry(action).x() != left;
    });
}

QRect screenFor(const QPoint& at)
{
    const QScreen* screen = QGuiApplication::screenAt(at);
    if (!screen) {
        screen = QGuiApplication::primaryScreen();
    }
    return screen ? screen->availableGeometry() : QRect(0, 0, 1024, 768);
}

/// Keep the menu on the screen sideways; the vertical fit is decided separately.
QPoint clampHorizontally(QPoint pos, const QSize& size, const QRect& screen)
{
    pos.setX(std::min(pos.x(), screen.right() - size.width() + 1));
    pos.setX(std::max(pos.x(), screen.left()));
    return pos;
}

/**
 * Nudges the menu into place the moment it is shown.
 *
 * By then the strip has a real geometry - before that the menu can only guess at
 * one - so this is where the bottom left corner of the icons is finally laid on
 * the cursor.  Qt has already positioned the menu by then, and moving it during
 * the show event beats it to the first paint.
 */
class StripAnchor: public QObject
{
public:
    StripAnchor(QMenu* menu, QWidget* bar, const QPoint& at)
        : QObject(menu)
        , menu(menu)
        , bar(bar)
        , at(at)
    {
        menu->installEventFilter(this);
    }

    bool eventFilter(QObject* watched, QEvent* event) override
    {
        if (watched == menu && event->type() == QEvent::Show) {
            menu->removeEventFilter(this);
            anchor();
        }
        return QObject::eventFilter(watched, event);
    }

private:
    void anchor()
    {
        if (QWidget* holder = bar->parentWidget()) {
            if (QLayout* layout = holder->layout()) {
                layout->activate();
            }
        }

        const QPoint corner = bar->mapTo(menu, QPoint(0, bar->height()));
        const QRect screen = screenFor(at);
        QPoint pos(at.x() - corner.x(), at.y() - corner.y());

        pos.setX(std::clamp(pos.x(), screen.left(),
                            std::max(screen.left(), screen.right() - menu->width() + 1)));
        pos.setY(std::clamp(pos.y(), screen.top(),
                            std::max(screen.top(), screen.bottom() - menu->height() + 1)));

        menu->move(pos);
    }

    QMenu* menu;
    QWidget* bar;
    QPoint at;
};


/**
 * Fades the quick menu out as the pointer wanders off from the click.
 *
 * A menu raised by a plain left click is easy to raise by accident, so it gives
 * way to a pointer that is evidently going somewhere else, and lets go entirely
 * once it is far enough gone.  Wandering onto the menu itself is not wandering
 * off: while the pointer is over it, it stays whole.
 *
 * The menu holds the mouse grab, so it sees the pointer wherever it goes.
 */
class QuickMenuFade: public QObject
{
public:
    QuickMenuFade(QMenu* menu, const QPoint& at, int solidRadius, int goneRadius)
        : QObject(menu)
        , menu(menu)
        , origin(at)
        , solidRadius(solidRadius)
        , goneRadius(std::max(goneRadius, solidRadius + 1))
    {
        menu->installEventFilter(this);
    }

    bool eventFilter(QObject* watched, QEvent* event) override
    {
        if (watched == menu && event->type() == QEvent::MouseMove) {
            apply(static_cast<QMouseEvent*>(event)->globalPosition().toPoint());
        }
        return QObject::eventFilter(watched, event);
    }

private:
    void apply(const QPoint& pos)
    {
        if (menu->geometry().contains(pos)) {
            menu->setWindowOpacity(1.0);
            return;
        }

        const double distance = std::hypot(pos.x() - origin.x(), pos.y() - origin.y());
        if (distance <= solidRadius) {
            menu->setWindowOpacity(1.0);
            return;
        }
        if (distance >= goneRadius) {
            menu->close();
            return;
        }

        const double gone = (distance - solidRadius) / (goneRadius - solidRadius);
        menu->setWindowOpacity(1.0 - gone);
    }

    QMenu* menu;
    QPoint origin;
    int solidRadius;
    int goneRadius;
};

}  // namespace


bool ContextMenuToolBar::isEnabled()
{
    return contextMenuParams()->GetBool("ShowToolBar", true);
}

QPoint ContextMenuToolBar::prepare(QMenu& menu, const char* recipient, const QPoint& at)
{
    if (!isEnabled()) {
        return at;
    }

    const std::string key = std::string(group(recipient)) + "Commands";
    const QString spec = QString::fromStdString(
        contextMenuParams()->GetASCII(key.c_str(), defaultCommands(recipient))
    );

    const int size = stripIconSize();
    auto* bar = new QToolBar(&menu);
    bar->setToolButtonStyle(Qt::ToolButtonIconOnly);
    bar->setIconSize(QSize(size, size));
    bar->setMovable(false);
    bar->setFloatable(false);
    // The bar lives inside a menu, so it must not paint a frame or a background
    // of its own over the menu's.
    bar->setStyleSheet(QStringLiteral("QToolBar { border: 0; background: transparent; }"));

    int buttons = 0;

    // Whatever the object under the cursor offers goes first, nearest the pointer.
    if (QAction* edit = proxyForDefaultAction(menu, bar)) {
        bar->addAction(edit);
        bar->addSeparator();
        ++buttons;
    }

    CommandManager& mgr = Application::Instance->commandManager();
    for (const QString& entry : spec.split(QLatin1Char(','), Qt::SkipEmptyParts)) {
        const QString name = entry.trimmed();
        if (name.isEmpty()) {
            continue;
        }
        if (name == QLatin1String("Separator")) {
            if (buttons > 0) {
                bar->addSeparator();
            }
            continue;
        }

        const QByteArray latin = name.toLatin1();
        Command* cmd = mgr.getCommandByName(latin.constData());
        if (!cmd) {
            // Commands from workbenches that were never loaded simply do not exist.
            continue;
        }
        if (QAction* proxy = proxyForCommand(cmd, &menu, bar)) {
            bar->addAction(proxy);
            ++buttons;
        }
    }

    if (buttons == 0) {
        delete bar;
        return at;
    }

    // A separator that ended up last has nothing to separate.
    const QList<QAction*> placed = bar->actions();
    if (!placed.isEmpty() && placed.back()->isSeparator()) {
        bar->removeAction(placed.back());
    }

    auto* holder = new QWidget(&menu);
    auto* layout = new QHBoxLayout(holder);
    layout->setContentsMargins(StripMarginH, StripMarginV, StripMarginH, StripMarginV);
    layout->addWidget(bar);
    layout->addStretch(1);

    auto* strip = new QWidgetAction(&menu);
    strip->setDefaultWidget(holder);
    new StripAnchor(&menu, bar, at);
    auto* divider = new QAction(&menu);
    divider->setSeparator(true);

    const QRect screen = screenFor(at);
    const int maxItems = static_cast<int>(contextMenuParams()->GetInt("MaxItems", 12));
    if (maxItems > 0 && listItems(menu) > maxItems) {
        // A list long enough to shove the strip off the click is the thing being
        // fixed here, so the tail of it moves into a submenu.
        foldOverflow(menu, maxItems);
    }

    QSize hint;
    auto arrange = [&](bool top) {
        placeStrip(menu, strip, divider, top);
        return anchorFor(menu, strip, at, hint);
    };

    auto fits = [&](const QPoint& p) {
        return p.y() >= screen.top() && p.y() + hint.height() <= screen.bottom() + 1;
    };

    // First choice: strip on top, list hanging below it.  The menu is welcome to
    // overhang the main window - it is a popup, the desktop is its limit.
    QPoint pos = arrange(true);
    if (fits(pos)) {
        return clampHorizontally(pos, hint, screen);
    }

    // Too long to hang below: stand the list up above the strip instead, which
    // keeps the icons on the cursor either way.
    pos = arrange(false);
    if (fits(pos)) {
        return clampHorizontally(pos, hint, screen);
    }

    // Fits neither way round.  The list takes whichever side has more room and
    // the anchor gives way - StripAnchor slides the menu the few pixels needed
    // to keep it on the screen.
    const bool below = (screen.bottom() - at.y()) >= (at.y() - screen.top());
    pos = arrange(below);

    if (spansColumns(menu)) {
        // Still long enough that Qt has started a second column - a shape nobody
        // reads top to bottom.  Now the menu really is taller than the screen,
        // which is the one case QMenu will scroll for.
        makeScrollable(menu);
        pos = anchorFor(menu, strip, at, hint);

        const int room = below ? screen.bottom() - pos.y() + 1
                               : pos.y() + hint.height() - screen.top();
        menu.setMaximumHeight(std::max(1, std::min(room, screen.height())));
        hint.setHeight(std::min(hint.height(), menu.maximumHeight()));
    }

    pos.setY(std::clamp(pos.y(), screen.top(),
                        std::max(screen.top(), screen.bottom() - hint.height() + 1)));
    return clampHorizontally(pos, hint, screen);
}


void ContextMenuToolBar::popupQuickMenu(QWidget* parent, const QPoint& at)
{
    auto* menu = new QMenu(parent);
    menu->setAttribute(Qt::WA_DeleteOnClose);

    const QPoint pos = prepare(*menu, "Quick", at);
    if (menu->actions().isEmpty()) {
        // Switched off, or not one command in the list could be shown.
        delete menu;
        return;
    }

    const int solidRadius = static_cast<int>(contextMenuParams()->GetInt("QuickMenuFadeNear", 42));
    const int goneRadius = static_cast<int>(contextMenuParams()->GetInt("QuickMenuFadeFar", 154));
    if (goneRadius > 0) {
        new QuickMenuFade(menu, at, solidRadius, goneRadius);
    }

    menu->popup(pos);
}

bool ContextMenuToolBar::quickMenuWantedFor(const char* workbench)
{
    if (!contextMenuParams()->GetBool("LeftClickQuickMenu", true)) {
        return false;
    }

    const QString wanted = QString::fromStdString(
        contextMenuParams()->GetASCII("LeftClickWorkbenches", "PartDesignWorkbench")
    );
    if (wanted.trimmed() == QLatin1String("*")) {
        return true;
    }

    const QString name = QString::fromLatin1(workbench ? workbench : "");
    const QStringList entries = wanted.split(QLatin1Char(','), Qt::SkipEmptyParts);
    return std::any_of(entries.cbegin(), entries.cend(), [&](const QString& entry) {
        return entry.trimmed() == name;
    });
}
