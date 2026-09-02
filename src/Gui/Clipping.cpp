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

#include <algorithm>
#include <limits>
#include <functional>

#include <Inventor/SbVec3f.h>
#include <Inventor/actions/SoGetBoundingBoxAction.h>
#include <QCheckBox>
#include <QComboBox>
#include <QDialogButtonBox>
#include <QDockWidget>
#include <QDoubleSpinBox>
#include <QInputDialog>
#include <QLabel>
#include <QLineEdit>
#include <QMessageBox>
#include <QPointer>
#include <QPushButton>
#include <QSpinBox>

#include <App/Application.h>
#include <Base/Tools.h>

#include "Clipping.h"
#include "ui_Clipping.h"
#include "ClippingState.h"
#include "DockWindowManager.h"
#include "Document.h"
#include "View3DInventor.h"
#include "View3DInventorViewer.h"
#include "ViewClipping.h"
#include "Widgets.h"

using namespace Gui::Dialog;

namespace
{

/**
 * Writes a spin box only when it would actually change.
 *
 * Every widget edit round-trips through the viewer and comes back as
 * signalChanged, so a blind setValue() would re-format the text of the box the
 * user is typing into and move the caret. Comparing first turns that round trip
 * into a no-op.
 */
void setSpinBoxValue(QDoubleSpinBox* box, double value)
{
    if (box->value() != value) {
        box->setValue(value);
    }
}

/**
 * Same, for a field the state keeps as float.
 *
 * The comparison has to happen in float, or the double -> float -> double round
 * trip of what the user typed would never compare equal and the box would be
 * rewritten on every keystroke.
 */
void setSpinBoxValue(QDoubleSpinBox* box, float value)
{
    if (static_cast<float>(box->value()) != value) {
        box->setValue(value);
    }
}

/**
 * Same again for the transparency boxes, which the state keeps as a fraction.
 *
 * The caller rounds to whole percent before getting here, so the comparison is
 * exact and a value the user typed is never re-written from the rounded float
 * it became.
 */
void setSpinBoxValue(QSpinBox* box, int value)
{
    if (box->value() != value) {
        box->setValue(value);
    }
}

}  // namespace

class Clipping::Private
{
public:
    Ui_Clipping ui;
    QPointer<Gui::View3DInventor> view;
    App::Document* shownOn {nullptr};
    QDockWidget* dockWidget {nullptr};
    QPushButton* clearButton {nullptr};
    /// Set while syncFromState() drives the widgets, so their signals do not
    /// bounce straight back into the viewer as edits.
    bool updating {false};
    fastsignals::scoped_connection activeDocConnection;
    fastsignals::scoped_connection clippingConnection;

    /// The clipping this panel edits, or nullptr once its view has gone away.
    Gui::ViewClipping* clipping() const
    {
        return view ? &view->getViewer()->clipping() : nullptr;
    }

    /// The document the saved views live in, or nullptr once the view has gone.
    Gui::Document* guiDocument() const
    {
        return view ? view->getGuiDocument() : nullptr;
    }

    /**
     * The saved view a combo row stands for, or nullptr for the placeholder.
     *
     * Row 0 is the "(none)" placeholder, so the document's list is offset by
     * one. Nothing else in this file may assume that: go through here.
     */
    const Gui::NamedClippingView* viewAt(int row) const
    {
        Gui::Document* doc = guiDocument();
        if (!doc || row < 1) {
            return nullptr;
        }

        const Gui::NamedClippingViews& views = doc->getClippingViews();
        const auto index = static_cast<std::size_t>(row - 1);
        return index < views.size() ? &views[index] : nullptr;
    }

    /**
     * Refills the combo from the document and selects @a selectName.
     *
     * Held under `updating` for the same reason syncFromState() is: clearing
     * and refilling a combo box emits currentIndexChanged for every row that
     * passes through the current slot, and each of those echoes would reach
     * onSavedViewsChanged() and apply a view the user never picked - including,
     * on the last one, a view other than the one just saved.
     */
    void populateSavedViews(const QString& selectName)
    {
        const bool wasUpdating = updating;
        updating = true;

        ui.savedViews->clear();
        ui.savedViews->addItem(Clipping::tr("(none)"));

        Gui::Document* doc = guiDocument();
        int selected = 0;
        if (doc) {
            int row = 1;
            for (const Gui::NamedClippingView& saved : doc->getClippingViews()) {
                const QString name = QString::fromStdString(saved.name);
                ui.savedViews->addItem(name);
                if (name == selectName) {
                    selected = row;
                }
                ++row;
            }
        }
        ui.savedViews->setCurrentIndex(selected);

        updating = wasUpdating;

        // Both buttons need somewhere to read from and write to. Deleting also
        // needs something actually selected, which the placeholder is not.
        const bool editable = doc && clipping();
        ui.savedViews->setEnabled(editable);
        ui.saveView->setEnabled(editable);
        ui.deleteView->setEnabled(editable && selected > 0);
    }

    /// Reads one plane out of the state, hands it to @a mutate, and writes it back.
    template<class Mutator>
    void editPlane(int slot, Mutator mutate)
    {
        Gui::ViewClipping* clip = clipping();
        if (!clip) {
            return;
        }

        Gui::ClipPlaneState plane = clip->state().planes[slot];
        mutate(plane);
        clip->setPlane(slot, plane);
    }

    /**
     * Switches a slot off, but only when it is on.
     *
     * A redundant setPlane() would still fire signalChanged, and syncFromState()
     * would then walk the widgets back over the click that is still being
     * handled - correct in the end, but for no reason.
     */
    void disablePlane(int slot)
    {
        Gui::ViewClipping* clip = clipping();
        if (!clip || !clip->state().planes[slot].enabled) {
            return;
        }

        editPlane(slot, [](Gui::ClipPlaneState& plane) {
            plane.enabled = false;
        });
    }

    /**
     * The three direction boxes are one vector, so each of them writes all three.
     *
     * The vector is stored as typed rather than normalised: normalising here
     * would push a different number back into the very box the user is typing
     * into. ViewClipping normalises on the way into Coin, which is where it
     * matters.
     */
    void applyViewDirection()
    {
        const SbVec3f normal(ui.dirX->value(), ui.dirY->value(), ui.dirZ->value());
        if (normal.sqrLength() <= 0.0F) {
            // A vector that is still being typed can pass through zero. Keep the
            // last usable direction rather than storing one that means nothing.
            return;
        }

        editPlane(Gui::ClipPlaneState::SlotCustom, [&normal](Gui::ClipPlaneState& plane) {
            plane.normal[0] = normal[0];
            plane.normal[1] = normal[1];
            plane.normal[2] = normal[2];
        });
    }

    /// dirX/Y/Z and "From view" mean nothing while the plane tracks the camera.
    void setViewDirWidgetsEnabled(bool followViewDir)
    {
        ui.dirX->setDisabled(followViewDir);
        ui.dirY->setDisabled(followViewDir);
        ui.dirZ->setDisabled(followViewDir);
        ui.fromView->setDisabled(followViewDir);
    }

    /// The four widgets of one group's cap row.
    struct CapWidgets
    {
        QCheckBox* cap;
        QComboBox* mode;
        Gui::ColorButton* color;
        QSpinBox* transparency;
    };

    /**
     * The cap row of one slot.
     *
     * Looked up rather than cached, so nothing has to be wired up after
     * setupUi(); one helper can then serve all four groups instead of the four
     * near-identical copies the rest of this panel still carries.
     */
    CapWidgets capWidgets(int slot) const
    {
        switch (slot) {
            case Gui::ClipPlaneState::SlotX:
                return {ui.capX, ui.capModeX, ui.capColorX, ui.capTransparencyX};
            case Gui::ClipPlaneState::SlotY:
                return {ui.capY, ui.capModeY, ui.capColorY, ui.capTransparencyY};
            case Gui::ClipPlaneState::SlotZ:
                return {ui.capZ, ui.capModeZ, ui.capColorZ, ui.capTransparencyZ};
            default:
                return {ui.capView, ui.capModeView, ui.capColorView, ui.capTransparencyView};
        }
    }

    /**
     * Whether this view can draw caps at all.
     *
     * Capping needs a stencil buffer, and stencilBits() only knows the answer
     * once a frame has been drawn - it reports -1 until then. Treat that as
     * "assume fine": the controls are re-checked on every syncFromState(), so a
     * context that really has no stencil buffer still greys them out long
     * before the user can act on them, whereas disabling on -1 would leave the
     * panel dead on a perfectly capable view.
     */
    bool capsSupported() const
    {
        return !view || view->getViewer()->stencilBits() != 0;
    }

    /**
     * Applies one cap row's enable rules, reading the widgets themselves.
     *
     * Called from the handlers so the row follows the click at once, and from
     * syncFromState() so a change made anywhere else lands too. It must not run
     * while its group box is unchecked: an unchecked QGroupBox disables its
     * children wholesale, and re-enabling them here would undo that.
     */
    void updateCapRowEnabled(int slot)
    {
        const CapWidgets widgets = capWidgets(slot);
        const bool supported = capsSupported();
        const bool row = supported && widgets.cap->isChecked();

        widgets.cap->setEnabled(supported);
        widgets.mode->setEnabled(row);
        widgets.transparency->setEnabled(row);
        // Only a cap that is not taking the cut object's own colour has a
        // colour of its own to pick.
        const bool custom =
            widgets.mode->currentIndex() == static_cast<int>(Gui::CapColorMode::CustomColor);
        widgets.color->setEnabled(row && custom);
    }

    /// Pushes one plane's cap settings into its row. The caller holds `updating`.
    void syncCapWidgets(int slot, const Gui::ClipPlaneState& plane)
    {
        const CapWidgets widgets = capWidgets(slot);

        widgets.cap->setChecked(plane.capEnabled);
        const int mode = static_cast<int>(plane.capColorMode);
        if (widgets.mode->currentIndex() != mode) {
            widgets.mode->setCurrentIndex(mode);
        }
        if (widgets.color->packedColor() != plane.capColor) {
            widgets.color->setPackedColor(plane.capColor);
        }
        setSpinBoxValue(widgets.transparency,
                        static_cast<int>(Base::toPercent(plane.capTransparency)));

        // After the group box's own setChecked(), which enables and disables
        // its children wholesale and would otherwise undo this.
        if (plane.enabled) {
            updateCapRowEnabled(slot);
        }
    }

    void applyCapEnabled(int slot, bool on)
    {
        updateCapRowEnabled(slot);
        editPlane(slot, [on](Gui::ClipPlaneState& plane) {
            plane.capEnabled = on;
        });
    }

    void applyCapColorMode(int slot, int index)
    {
        updateCapRowEnabled(slot);

        const auto mode = index == static_cast<int>(Gui::CapColorMode::CustomColor)
            ? Gui::CapColorMode::CustomColor
            : Gui::CapColorMode::BodyColor;
        editPlane(slot, [mode](Gui::ClipPlaneState& plane) {
            plane.capColorMode = mode;
        });
    }

    void applyCapColor(int slot)
    {
        // ColorButton has no colour in its signal, so read it back off the button.
        const uint32_t color = capWidgets(slot).color->packedColor();
        editPlane(slot, [color](Gui::ClipPlaneState& plane) {
            plane.capColor = color;
        });
    }

    /// The box shows whole percent; the state keeps a 0..1 fraction.
    void applyCapTransparency(int slot, int percent)
    {
        const float transparency = Base::fromPercent(percent);
        editPlane(slot, [transparency](Gui::ClipPlaneState& plane) {
            plane.capTransparency = transparency;
        });
    }
};

/* TRANSLATOR Gui::Dialog::Clipping */

Clipping::Clipping(Gui::View3DInventor* view, App::Document* showOn, QWidget* parent)
    : QDialog(parent)
    , d(new Private)
{
    // create widgets
    d->ui.setupUi(this);

    // Closing the panel no longer throws the clipping away - the viewer owns it
    // now - so the user needs somewhere to say "off". Added from code rather
    // than from the .ui because the button wants a verb that the standard Reset
    // text does not carry.
    d->clearButton = d->ui.buttonBox->addButton(QDialogButtonBox::Reset);
    d->clearButton->setText(tr("Clear all"));
    d->clearButton->setToolTip(tr("Switches off every clipping plane of this view"));

    d->shownOn = showOn;
    d->view = view;
    setupConnections();

    constexpr int max = std::numeric_limits<int>::max();
    d->ui.clipView->setRange(-max, max);
    d->ui.clipView->setSingleStep(0.1f);
    d->ui.clipX->setRange(-max, max);
    d->ui.clipX->setSingleStep(0.1f);
    d->ui.clipY->setRange(-max, max);
    d->ui.clipY->setSingleStep(0.1f);
    d->ui.clipZ->setRange(-max, max);
    d->ui.clipZ->setSingleStep(0.1f);

    d->ui.dirX->setRange(-max, max);
    d->ui.dirX->setSingleStep(0.1f);
    d->ui.dirY->setRange(-max, max);
    d->ui.dirY->setSingleStep(0.1f);
    d->ui.dirZ->setRange(-max, max);
    d->ui.dirZ->setSingleStep(0.1f);

    View3DInventorViewer* viewer = view->getViewer();
    ViewClipping& clipping = viewer->clipping();

    SoGetBoundingBoxAction action(viewer->getSoRenderManager()->getViewportRegion());
    action.apply(viewer->getSceneGraph());
    SbBox3f box = action.getBoundingBox();

    if (!box.isEmpty()) {
        // The offsets are state, so they are only seeded while there is nothing
        // to preserve: the panel now reopens onto the clipping that is already
        // active, and dragging every plane back to the centre of the scene each
        // time would defeat the point. The steps and decimals below are
        // presentation, not state, and always follow the scene.
        if (clipping.state().isDefault()) {
            SbVec3f cnt = box.getCenter();
            d->ui.clipView->setValue(cnt[2]);
            d->ui.clipX->setValue(cnt[0]);
            d->ui.clipY->setValue(cnt[1]);
            d->ui.clipZ->setValue(cnt[2]);
        }

        int minDecimals = 2;
        float lenx, leny, lenz;
        box.getSize(lenx, leny, lenz);
        int steps = 100;
        float minlen = std::min<float>(lenx, std::min<float>(leny, lenz));

        // determine the single step values
        {
            minlen = minlen / steps;
            int dim = static_cast<int>(log10(minlen));
            double singleStep = pow(10.0, dim);
            d->ui.clipView->setSingleStep(singleStep);
            minDecimals = std::max(minDecimals, -dim);
        }
        {
            lenx = lenx / steps;
            int dim = static_cast<int>(log10(lenx));
            double singleStep = pow(10.0, dim);
            d->ui.clipX->setSingleStep(singleStep);
        }
        {
            leny = leny / steps;
            int dim = static_cast<int>(log10(leny));
            double singleStep = pow(10.0, dim);
            d->ui.clipY->setSingleStep(singleStep);
        }
        {
            lenz = lenz / steps;
            int dim = static_cast<int>(log10(lenz));
            double singleStep = pow(10.0, dim);
            d->ui.clipZ->setSingleStep(singleStep);
        }

        // set decimals
        d->ui.clipView->setDecimals(minDecimals);
        d->ui.clipX->setDecimals(minDecimals);
        d->ui.clipY->setDecimals(minDecimals);
        d->ui.clipZ->setDecimals(minDecimals);
    }

    // Nothing is selected to begin with: what the view is clipped by may well
    // match a saved view, but it may equally have been edited since, and the
    // panel has no way to tell the two apart that is worth misleading the user
    // over.
    d->populateSavedViews(QString());

    // Show whatever the view is already clipped by. This also supplies the
    // custom plane's default +Z direction, which the viewer seeds.
    syncFromState();
}

Clipping* Clipping::makeDockWidget(Gui::View3DInventor* view, App::Document* showOn)
{
    // embed this dialog into a QDockWidget
    auto clipping = new Clipping(view, showOn);
    Gui::DockWindowManager* pDockMgr = Gui::DockWindowManager::instance();
    QDockWidget* dw = pDockMgr->addDockWindow("Clipping", clipping, Qt::LeftDockWidgetArea);
    dw->setFeatures(QDockWidget::DockWidgetMovable | QDockWidget::DockWidgetFloatable);
    dw->show();
    clipping->d->dockWidget = dw;

    return clipping;
}

/** Destroys the object and frees any allocated resources */
Clipping::~Clipping()
{
    // The planes belong to the viewer and stay behind; only the two
    // subscriptions are ours to give up.
    d->activeDocConnection.disconnect();
    d->clippingConnection.disconnect();
    delete d;
}

void Clipping::setupConnections()
{
    // Re-read the state whenever it changes, wherever the change came from:
    // this panel, the Clear all button, or a document restore.
    if (ViewClipping* clipping = d->clipping()) {
        d->clippingConnection =
            clipping->signalChanged.connect(std::bind(&Clipping::syncFromState, this));
    }

    // clang-format off
    d->activeDocConnection = App::GetApplication().signalActiveDocument.connect(
            std::bind(&Clipping::onActiveDocument, this, std::placeholders::_1));
    connect(d->ui.savedViews, qOverload<int>(&QComboBox::currentIndexChanged),
            this, &Clipping::onSavedViewsChanged);
    connect(d->ui.saveView, &QPushButton::clicked,
            this, &Clipping::onSaveViewClicked);
    connect(d->ui.deleteView, &QPushButton::clicked,
            this, &Clipping::onDeleteViewClicked);
    connect(d->ui.groupBoxX, &QGroupBox::toggled,
            this, &Clipping::onGroupBoxXToggled);
    connect(d->ui.groupBoxY, &QGroupBox::toggled,
            this, &Clipping::onGroupBoxYToggled);
    connect(d->ui.groupBoxZ, &QGroupBox::toggled,
            this, &Clipping::onGroupBoxZToggled);
    connect(d->ui.clipX, qOverload<double>(&QDoubleSpinBox::valueChanged),
            this, &Clipping::onClipXValueChanged);
    connect(d->ui.clipY, qOverload<double>(&QDoubleSpinBox::valueChanged),
            this, &Clipping::onClipYValueChanged);
    connect(d->ui.clipZ, qOverload<double>(&QDoubleSpinBox::valueChanged),
            this, &Clipping::onClipZValueChanged);
    connect(d->ui.flipClipX, &QPushButton::clicked,
            this, &Clipping::onFlipClipXClicked);
    connect(d->ui.flipClipY, &QPushButton::clicked,
            this, &Clipping::onFlipClipYClicked);
    connect(d->ui.flipClipZ, &QPushButton::clicked,
            this, &Clipping::onFlipClipZClicked);
    connect(d->ui.groupBoxView, &QGroupBox::toggled,
            this, &Clipping::onGroupBoxViewToggled);
    connect(d->ui.clipView, qOverload<double>(&QDoubleSpinBox::valueChanged),
            this, &Clipping::onClipViewValueChanged);
    connect(d->ui.fromView, &QPushButton::clicked,
            this, &Clipping::onFromViewClicked);
    connect(d->ui.adjustViewdirection, &QCheckBox::toggled,
            this, &Clipping::onAdjustViewdirectionToggled);
    connect(d->ui.dirX, qOverload<double>(&QDoubleSpinBox::valueChanged),
            this, &Clipping::onDirXValueChanged);
    connect(d->ui.dirY, qOverload<double>(&QDoubleSpinBox::valueChanged),
            this, &Clipping::onDirYValueChanged);
    connect(d->ui.dirZ, qOverload<double>(&QDoubleSpinBox::valueChanged),
            this, &Clipping::onDirZValueChanged);
    connect(d->clearButton, &QPushButton::clicked,
            this, &Clipping::onClearAllClicked);
    connect(d->ui.capX, &QCheckBox::toggled,
            this, &Clipping::onCapXToggled);
    connect(d->ui.capY, &QCheckBox::toggled,
            this, &Clipping::onCapYToggled);
    connect(d->ui.capZ, &QCheckBox::toggled,
            this, &Clipping::onCapZToggled);
    connect(d->ui.capView, &QCheckBox::toggled,
            this, &Clipping::onCapViewToggled);
    connect(d->ui.capModeX, qOverload<int>(&QComboBox::currentIndexChanged),
            this, &Clipping::onCapModeXCurrentIndexChanged);
    connect(d->ui.capModeY, qOverload<int>(&QComboBox::currentIndexChanged),
            this, &Clipping::onCapModeYCurrentIndexChanged);
    connect(d->ui.capModeZ, qOverload<int>(&QComboBox::currentIndexChanged),
            this, &Clipping::onCapModeZCurrentIndexChanged);
    connect(d->ui.capModeView, qOverload<int>(&QComboBox::currentIndexChanged),
            this, &Clipping::onCapModeViewCurrentIndexChanged);
    connect(d->ui.capColorX, &Gui::ColorButton::changed,
            this, &Clipping::onCapColorXChanged);
    connect(d->ui.capColorY, &Gui::ColorButton::changed,
            this, &Clipping::onCapColorYChanged);
    connect(d->ui.capColorZ, &Gui::ColorButton::changed,
            this, &Clipping::onCapColorZChanged);
    connect(d->ui.capColorView, &Gui::ColorButton::changed,
            this, &Clipping::onCapColorViewChanged);
    connect(d->ui.capTransparencyX, qOverload<int>(&QSpinBox::valueChanged),
            this, &Clipping::onCapTransparencyXValueChanged);
    connect(d->ui.capTransparencyY, qOverload<int>(&QSpinBox::valueChanged),
            this, &Clipping::onCapTransparencyYValueChanged);
    connect(d->ui.capTransparencyZ, qOverload<int>(&QSpinBox::valueChanged),
            this, &Clipping::onCapTransparencyZValueChanged);
    connect(d->ui.capTransparencyView, qOverload<int>(&QSpinBox::valueChanged),
            this, &Clipping::onCapTransparencyViewValueChanged);
    // clang-format on
}

void Clipping::syncFromState()
{
    ViewClipping* clipping = d->clipping();
    if (!clipping) {
        return;
    }

    const ClippingState& state = clipping->state();

    // The writes below emit exactly the signals the handlers listen to. The
    // guard makes those echoes no-ops, which leaves the viewer's state as the
    // one source of truth and keeps the two sides from driving each other.
    d->updating = true;

    const ClipPlaneState& planeX = state.planes[ClipPlaneState::SlotX];
    d->ui.groupBoxX->setChecked(planeX.enabled);
    setSpinBoxValue(d->ui.clipX, planeX.offset);
    d->syncCapWidgets(ClipPlaneState::SlotX, planeX);

    const ClipPlaneState& planeY = state.planes[ClipPlaneState::SlotY];
    d->ui.groupBoxY->setChecked(planeY.enabled);
    setSpinBoxValue(d->ui.clipY, planeY.offset);
    d->syncCapWidgets(ClipPlaneState::SlotY, planeY);

    const ClipPlaneState& planeZ = state.planes[ClipPlaneState::SlotZ];
    d->ui.groupBoxZ->setChecked(planeZ.enabled);
    setSpinBoxValue(d->ui.clipZ, planeZ.offset);
    d->syncCapWidgets(ClipPlaneState::SlotZ, planeZ);

    const ClipPlaneState& custom = state.planes[ClipPlaneState::SlotCustom];
    d->ui.groupBoxView->setChecked(custom.enabled);
    setSpinBoxValue(d->ui.clipView, custom.offset);
    setSpinBoxValue(d->ui.dirX, custom.normal[0]);
    setSpinBoxValue(d->ui.dirY, custom.normal[1]);
    setSpinBoxValue(d->ui.dirZ, custom.normal[2]);
    d->ui.adjustViewdirection->setChecked(custom.followViewDir);
    // After groupBoxView, whose own checked state enables and disables its
    // children wholesale and would otherwise undo this - and only while it is
    // checked, since re-enabling "From view" under an unchecked group box would
    // offer the user a button that edits a plane that is switched off.
    if (custom.enabled) {
        d->setViewDirWidgetsEnabled(custom.followViewDir);
    }
    d->syncCapWidgets(ClipPlaneState::SlotCustom, custom);

    // Asked again on every change rather than once at startup: the viewer only
    // learns how many stencil bits it got when it draws its first frame, which
    // may well be after this panel was built.
    d->ui.stencilWarning->setVisible(!d->capsSupported());

    d->updating = false;
}

void Clipping::reject()
{
    QDialog::reject();
    auto dw = qobject_cast<QDockWidget*>(parent());
    if (dw) {
        dw->deleteLater();
    }
}
void Clipping::onActiveDocument(const App::Document& doc)
{
    if (!d || !d->dockWidget) {
        return;
    }
    if (&doc == d->shownOn) {
        d->dockWidget->show();
    }
    else {
        d->dockWidget->hide();
    }
}

/**
 * Applies the picked view, whole.
 *
 * setState() emits signalChanged, and syncFromState() is connected to it, so
 * every plane, cap and direction widget is refreshed from there; there is
 * deliberately nothing to repeat here.
 */
void Clipping::onSavedViewsChanged(int index)
{
    if (d->updating) {
        return;
    }

    const NamedClippingView* saved = d->viewAt(index);

    // The placeholder is a way back to "none of these", not a view of its own:
    // picking it leaves the clipping alone and only greys out Delete.
    d->ui.deleteView->setEnabled(saved != nullptr);

    if (!saved) {
        return;
    }

    if (ViewClipping* clipping = d->clipping()) {
        clipping->setState(saved->state);
    }
}

/**
 * Stores the current clipping in the document under a name.
 *
 * The document owns the list, so this reads it, edits the copy, and hands the
 * whole thing back - setClippingViews() is what marks the document modified.
 */
void Clipping::onSaveViewClicked()
{
    Gui::Document* doc = d->guiDocument();
    ViewClipping* clipping = d->clipping();
    if (!doc || !clipping) {
        return;
    }

    // Pre-filled with the selection, so that adjusting a view and saving it
    // back over itself is two confirmations rather than retyping its name.
    const NamedClippingView* selected = d->viewAt(d->ui.savedViews->currentIndex());
    const QString suggestion = selected ? QString::fromStdString(selected->name) : QString();

    bool accepted = false;
    const QString entered = QInputDialog::getText(this,
                                                  tr("Save view"),
                                                  tr("Name:"),
                                                  QLineEdit::Normal,
                                                  suggestion,
                                                  &accepted);
    if (!accepted) {
        return;
    }

    // Trimmed before it is judged, so that a name of nothing but spaces cannot
    // become a row the user has no way of telling from any other.
    const QString name = entered.trimmed();
    if (name.isEmpty()) {
        QMessageBox::warning(this, tr("Save view"), tr("Please enter a name for the view."));
        return;
    }

    NamedClippingViews views = doc->getClippingViews();
    const auto existing =
        std::find_if(views.begin(), views.end(), [&name](const NamedClippingView& saved) {
            return QString::fromStdString(saved.name) == name;
        });

    if (existing != views.end()) {
        const auto answer =
            QMessageBox::question(this,
                                  tr("Save view"),
                                  tr("A saved view named '%1' already exists.\n\n"
                                     "Do you want to overwrite it?")
                                      .arg(name),
                                  QMessageBox::Yes | QMessageBox::No,
                                  QMessageBox::No);
        if (answer != QMessageBox::Yes) {
            return;
        }

        // Overwritten in place: the list is the user's own order, and a save
        // that shuffled a view to the end would quietly rearrange it.
        existing->state = clipping->state();
    }
    else {
        views.push_back({name.toStdString(), clipping->state()});
    }

    doc->setClippingViews(views);
    d->populateSavedViews(name);
}

/**
 * Forgets a saved view.
 *
 * Only the name goes: the clipping the user is looking at is left exactly as
 * it is, since deleting a bookmark is not the same as asking for the view to
 * be reset.
 */
void Clipping::onDeleteViewClicked()
{
    Gui::Document* doc = d->guiDocument();
    const int index = d->ui.savedViews->currentIndex();
    const NamedClippingView* selected = d->viewAt(index);
    if (!doc || !selected) {
        return;
    }

    const QString name = QString::fromStdString(selected->name);
    const auto answer = QMessageBox::question(this,
                                              tr("Delete view"),
                                              tr("Delete the saved view '%1'?").arg(name),
                                              QMessageBox::Yes | QMessageBox::No,
                                              QMessageBox::No);
    if (answer != QMessageBox::Yes) {
        return;
    }

    NamedClippingViews views = doc->getClippingViews();
    views.erase(views.begin() + (index - 1));
    doc->setClippingViews(views);

    // Nothing is left selected: the placeholder is what "the clipping on
    // screen is not one of the saved views" looks like.
    d->populateSavedViews(QString());
}

/**
 * The three axis planes and the custom-direction plane are mutually exclusive,
 * so switching one side on switches the other off.
 *
 * This goes through the state rather than through the other group boxes'
 * setChecked(), so that the widgets only ever move in one direction - state to
 * widget, in syncFromState() - and the exclusion cannot depend on the order in
 * which nested toggled() signals happen to arrive.
 */
void Clipping::onGroupBoxXToggled(bool on)
{
    if (d->updating) {
        return;
    }

    if (on) {
        d->disablePlane(ClipPlaneState::SlotCustom);
    }

    d->editPlane(ClipPlaneState::SlotX, [on](ClipPlaneState& plane) {
        plane.enabled = on;
    });
}

void Clipping::onGroupBoxYToggled(bool on)
{
    if (d->updating) {
        return;
    }

    if (on) {
        d->disablePlane(ClipPlaneState::SlotCustom);
    }

    d->editPlane(ClipPlaneState::SlotY, [on](ClipPlaneState& plane) {
        plane.enabled = on;
    });
}

void Clipping::onGroupBoxZToggled(bool on)
{
    if (d->updating) {
        return;
    }

    if (on) {
        d->disablePlane(ClipPlaneState::SlotCustom);
    }

    d->editPlane(ClipPlaneState::SlotZ, [on](ClipPlaneState& plane) {
        plane.enabled = on;
    });
}

void Clipping::onClipXValueChanged(double val)
{
    if (d->updating) {
        return;
    }

    // The offset is stored as the user typed it; the flip is applied further
    // down, where the plane reaches Coin.
    d->editPlane(ClipPlaneState::SlotX, [val](ClipPlaneState& plane) {
        plane.offset = val;
    });
}

void Clipping::onClipYValueChanged(double val)
{
    if (d->updating) {
        return;
    }

    d->editPlane(ClipPlaneState::SlotY, [val](ClipPlaneState& plane) {
        plane.offset = val;
    });
}

void Clipping::onClipZValueChanged(double val)
{
    if (d->updating) {
        return;
    }

    d->editPlane(ClipPlaneState::SlotZ, [val](ClipPlaneState& plane) {
        plane.offset = val;
    });
}

void Clipping::onFlipClipXClicked()
{
    if (d->updating) {
        return;
    }

    d->editPlane(ClipPlaneState::SlotX, [](ClipPlaneState& plane) {
        plane.flipped = !plane.flipped;
    });
}

void Clipping::onFlipClipYClicked()
{
    if (d->updating) {
        return;
    }

    d->editPlane(ClipPlaneState::SlotY, [](ClipPlaneState& plane) {
        plane.flipped = !plane.flipped;
    });
}

void Clipping::onFlipClipZClicked()
{
    if (d->updating) {
        return;
    }

    d->editPlane(ClipPlaneState::SlotZ, [](ClipPlaneState& plane) {
        plane.flipped = !plane.flipped;
    });
}

void Clipping::onGroupBoxViewToggled(bool on)
{
    if (d->updating) {
        return;
    }

    if (on) {
        for (int slot : {ClipPlaneState::SlotX, ClipPlaneState::SlotY, ClipPlaneState::SlotZ}) {
            d->disablePlane(slot);
        }
    }

    d->editPlane(ClipPlaneState::SlotCustom, [on](ClipPlaneState& plane) {
        plane.enabled = on;
    });
}

void Clipping::onClipViewValueChanged(double val)
{
    if (d->updating) {
        return;
    }

    d->editPlane(ClipPlaneState::SlotCustom, [val](ClipPlaneState& plane) {
        plane.offset = val;
    });
}

void Clipping::onFromViewClicked()
{
    if (d->updating || !d->view) {
        return;
    }

    // Snap the custom normal to where the camera is looking right now, once.
    // Unlike "Adjust to view direction" this is a one-off, so it writes the
    // direction into the state and the direction widgets follow.
    const SbVec3f dir = d->view->getViewer()->getViewDirection();
    d->editPlane(ClipPlaneState::SlotCustom, [&dir](ClipPlaneState& plane) {
        plane.normal[0] = dir[0];
        plane.normal[1] = dir[1];
        plane.normal[2] = dir[2];
    });
}

void Clipping::onAdjustViewdirectionToggled(bool on)
{
    if (d->updating) {
        return;
    }

    // Also done in syncFromState(), which is what keeps the two boxes right
    // when the state changes from anywhere else.
    d->setViewDirWidgetsEnabled(on);

    d->editPlane(ClipPlaneState::SlotCustom, [on](ClipPlaneState& plane) {
        plane.followViewDir = on;
    });
}

void Clipping::onDirXValueChanged(double)
{
    if (d->updating) {
        return;
    }

    d->applyViewDirection();
}

void Clipping::onDirYValueChanged(double)
{
    if (d->updating) {
        return;
    }

    d->applyViewDirection();
}

void Clipping::onDirZValueChanged(double)
{
    if (d->updating) {
        return;
    }

    d->applyViewDirection();
}

void Clipping::onClearAllClicked()
{
    if (ViewClipping* clipping = d->clipping()) {
        clipping->clear();
    }
}

/**
 * Turning a cap on or off.
 *
 * Nothing here decides whether the cap can be drawn - that is the viewer's
 * business - so this only records the wish and moves the rest of the row with
 * it.
 */
void Clipping::onCapXToggled(bool on)
{
    if (d->updating) {
        return;
    }

    d->applyCapEnabled(ClipPlaneState::SlotX, on);
}

void Clipping::onCapYToggled(bool on)
{
    if (d->updating) {
        return;
    }

    d->applyCapEnabled(ClipPlaneState::SlotY, on);
}

void Clipping::onCapZToggled(bool on)
{
    if (d->updating) {
        return;
    }

    d->applyCapEnabled(ClipPlaneState::SlotZ, on);
}

void Clipping::onCapViewToggled(bool on)
{
    if (d->updating) {
        return;
    }

    d->applyCapEnabled(ClipPlaneState::SlotCustom, on);
}

/// The combo box is filled in the .ui in enum order, so the index is the mode.
void Clipping::onCapModeXCurrentIndexChanged(int index)
{
    if (d->updating) {
        return;
    }

    d->applyCapColorMode(ClipPlaneState::SlotX, index);
}

void Clipping::onCapModeYCurrentIndexChanged(int index)
{
    if (d->updating) {
        return;
    }

    d->applyCapColorMode(ClipPlaneState::SlotY, index);
}

void Clipping::onCapModeZCurrentIndexChanged(int index)
{
    if (d->updating) {
        return;
    }

    d->applyCapColorMode(ClipPlaneState::SlotZ, index);
}

void Clipping::onCapModeViewCurrentIndexChanged(int index)
{
    if (d->updating) {
        return;
    }

    d->applyCapColorMode(ClipPlaneState::SlotCustom, index);
}

void Clipping::onCapColorXChanged()
{
    if (d->updating) {
        return;
    }

    d->applyCapColor(ClipPlaneState::SlotX);
}

void Clipping::onCapColorYChanged()
{
    if (d->updating) {
        return;
    }

    d->applyCapColor(ClipPlaneState::SlotY);
}

void Clipping::onCapColorZChanged()
{
    if (d->updating) {
        return;
    }

    d->applyCapColor(ClipPlaneState::SlotZ);
}

void Clipping::onCapColorViewChanged()
{
    if (d->updating) {
        return;
    }

    d->applyCapColor(ClipPlaneState::SlotCustom);
}

/**
 * Transparency applies in both colour modes.
 *
 * It is the cut face's own transparency, not the picked colour's alpha, which
 * is why the colour button is left without an alpha channel and this box sits
 * outside the mode it belongs to.
 */
void Clipping::onCapTransparencyXValueChanged(int percent)
{
    if (d->updating) {
        return;
    }

    d->applyCapTransparency(ClipPlaneState::SlotX, percent);
}

void Clipping::onCapTransparencyYValueChanged(int percent)
{
    if (d->updating) {
        return;
    }

    d->applyCapTransparency(ClipPlaneState::SlotY, percent);
}

void Clipping::onCapTransparencyZValueChanged(int percent)
{
    if (d->updating) {
        return;
    }

    d->applyCapTransparency(ClipPlaneState::SlotZ, percent);
}

void Clipping::onCapTransparencyViewValueChanged(int percent)
{
    if (d->updating) {
        return;
    }

    d->applyCapTransparency(ClipPlaneState::SlotCustom, percent);
}

#include "moc_Clipping.cpp"
