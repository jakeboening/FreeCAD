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

#include <array>

#include <fastsignals/signal.h>

#include <FCGlobal.h>

#include "ClippingState.h"

class SoClipPlane;
class SoSensor;
class SoTimerSensor;

namespace Gui
{

class View3DInventorViewer;

/**
 * The clipping of one 3D view: the SoClipPlane nodes, and the state they are
 * driven from.
 *
 * The viewer owns this, not the Clipping panel.  That is what lets the clipping
 * outlive the panel - so closing the panel no longer throws the clipping away,
 * and Gui::Document can serialise it into GuiDocument.xml the way it already
 * serialises the camera.  The panel becomes a pure editor: it reads state(),
 * writes setPlane(), and re-syncs itself from signalChanged.
 *
 * The stored normal and offset are always the ones the user typed, before
 * `flipped` is taken into account; the flip is applied when the plane reaches
 * Coin.  Likewise, while `followViewDir` is on, the sensor re-aims the Coin
 * node at the camera without disturbing the typed direction, so turning it off
 * gives the user their own direction back and a saved document keeps it.
 */
class GuiExport ViewClipping
{
public:
    explicit ViewClipping(View3DInventorViewer& viewer);
    ~ViewClipping();

    ViewClipping(const ViewClipping&) = delete;
    ViewClipping& operator=(const ViewClipping&) = delete;

    const ClippingState& state() const
    {
        return st;
    }

    /// Applies a whole configuration at once, as a document restore does.
    void setState(const ClippingState& state);
    /// Applies one plane, as the panel does on every widget change.
    void setPlane(int slot, const ClipPlaneState& plane);
    /// Turns everything off, leaving the nodes in place.
    void clear();

    /// Emitted after any change, so an open panel can re-sync its widgets.
    fastsignals::signal<void()> signalChanged;

private:
    /// Pushes planes[slot] into its SoClipPlane, applying the flip.
    void applyToNode(int slot);
    /// Starts or stops the sensor that keeps the custom plane facing the camera.
    void updateViewDirSensor();
    static void viewDirCallback(void* data, SoSensor* sensor);

    View3DInventorViewer& viewer;
    ClippingState st;
    std::array<SoClipPlane*, ClipPlaneState::SlotCount> nodes {};
    SoTimerSensor* viewDirSensor {nullptr};
};

}  // namespace Gui
