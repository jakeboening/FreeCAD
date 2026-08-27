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

#include <Inventor/SbPlane.h>
#include <Inventor/SbVec3f.h>
#include <Inventor/nodes/SoClipPlane.h>
#include <Inventor/nodes/SoGroup.h>
#include <Inventor/sensors/SoTimerSensor.h>

#include "ViewClipping.h"
#include "View3DInventorViewer.h"

using namespace Gui;

namespace
{

bool isValidSlot(int slot)
{
    return slot >= 0 && slot < ClipPlaneState::SlotCount;
}

/**
 * The normal a slot starts out with: the axis it is named after, and +Z for the
 * custom plane, which is what the panel's direction widgets show by default.
 */
SbVec3f defaultNormal(int slot)
{
    switch (slot) {
        case ClipPlaneState::SlotX:
            return {1.0F, 0.0F, 0.0F};
        case ClipPlaneState::SlotY:
            return {0.0F, 1.0F, 0.0F};
        default:
            // SlotZ and SlotCustom both face +Z.
            return {0.0F, 0.0F, 1.0F};
    }
}

/**
 * Everything off, every plane through the origin and facing its own axis.
 *
 * ClippingState's own defaults cannot express this - it is plain data and has
 * one set of member initialisers for all four slots - so the per-slot normals
 * are seeded here, and this is what both construction and clear() start from.
 * Without it a freshly cleared Y plane would come back facing X.
 */
ClippingState defaultState()
{
    ClippingState state;

    for (int slot = 0; slot < ClipPlaneState::SlotCount; ++slot) {
        const SbVec3f normal = defaultNormal(slot);
        for (int i = 0; i < 3; ++i) {
            state.planes[slot].normal[i] = normal[i];
        }
    }

    return state;
}

}  // namespace

ViewClipping::ViewClipping(View3DInventorViewer& viewer)
    : viewer(viewer)
    , st(defaultState())
{
    for (int slot = 0; slot < ClipPlaneState::SlotCount; ++slot) {
        auto* node = new SoClipPlane();
        node->ref();
        nodes[slot] = node;
        // Pushes the seeded defaults - and, in particular, on = false.
        applyToNode(slot);
    }

    // The viewer owns this group and has already placed it just ahead of the
    // model geometry, so the planes clip the model and leave the editing root,
    // the dimensions and the on-top selection alone.
    if (SoGroup* group = viewer.getClipPlaneGroup()) {
        for (SoClipPlane* node : nodes) {
            group->addChild(node);
        }
    }

    // Left unscheduled: it only runs while the custom plane follows the camera.
    viewDirSensor = new SoTimerSensor(viewDirCallback, this);
}

ViewClipping::~ViewClipping()
{
    if (viewDirSensor) {
        // A scheduled sensor stays in the timer queue and would fire into freed
        // memory, so it has to come off the queue before it is destroyed.
        if (viewDirSensor->isScheduled()) {
            viewDirSensor->unschedule();
        }
        delete viewDirSensor;
        viewDirSensor = nullptr;
    }

    // This runs while the viewer is being torn down; the group getter is a
    // plain accessor, and the viewer destroys us before it drops the group.
    SoGroup* group = viewer.getClipPlaneGroup();

    for (SoClipPlane*& node : nodes) {
        if (!node) {
            continue;
        }
        if (group) {
            // findChild first: removeChild() of a node that is not there warns.
            const int index = group->findChild(node);
            if (index >= 0) {
                group->removeChild(index);
            }
        }
        node->unref();
        node = nullptr;
    }
}

void ViewClipping::setState(const ClippingState& state)
{
    st = state;

    for (int slot = 0; slot < ClipPlaneState::SlotCount; ++slot) {
        applyToNode(slot);
    }

    updateViewDirSensor();
    // One signal for the whole configuration: a restore must not make the panel
    // rebuild its widgets four times.
    signalChanged();
}

void ViewClipping::setPlane(int slot, const ClipPlaneState& plane)
{
    if (!isValidSlot(slot)) {
        return;
    }

    st.planes[slot] = plane;
    applyToNode(slot);

    if (slot == ClipPlaneState::SlotCustom) {
        // The only slot the sensor depends on, through followViewDir/enabled.
        updateViewDirSensor();
    }

    signalChanged();
}

void ViewClipping::clear()
{
    setState(defaultState());
}

void ViewClipping::applyToNode(int slot)
{
    if (!isValidSlot(slot) || !nodes[slot]) {
        return;
    }

    SoClipPlane* node = nodes[slot];
    const ClipPlaneState& plane = st.planes[slot];

    SbVec3f normal(plane.normal[0], plane.normal[1], plane.normal[2]);
    auto offset = static_cast<float>(plane.offset);

    // The stored normal and offset are the ones the user typed; flipping turns
    // the plane around without moving it, which means negating both - negating
    // the normal alone would mirror the plane through the origin instead.
    if (plane.flipped) {
        normal = -normal;
        offset = -offset;
    }

    // SbPlane normalises what it is given, so a zero-length normal would divide
    // by zero. Leave the node's current plane alone rather than degrade it; the
    // panel can pass through (0, 0, 0) while the user is typing a direction.
    if (normal.sqrLength() > 0.0F) {
        node->plane.setValue(SbPlane(normal, offset));
    }

    node->on.setValue(plane.enabled);
}

void ViewClipping::updateViewDirSensor()
{
    if (!viewDirSensor) {
        return;
    }

    const ClipPlaneState& custom = st.planes[ClipPlaneState::SlotCustom];
    const bool follow = custom.enabled && custom.followViewDir;

    // schedule()/unschedule() warn when they are a no-op, hence the isScheduled
    // test rather than calling them unconditionally.
    if (follow && !viewDirSensor->isScheduled()) {
        viewDirSensor->schedule();
    }
    else if (!follow && viewDirSensor->isScheduled()) {
        viewDirSensor->unschedule();
    }
}

void ViewClipping::viewDirCallback(void* data, SoSensor* /*sensor*/)
{
    auto* self = static_cast<ViewClipping*>(data);
    SoClipPlane* node = self->nodes[ClipPlaneState::SlotCustom];
    if (!node) {
        return;
    }

    SbVec3f direction = self->viewer.getViewDirection();
    if (direction.sqrLength() <= 0.0F) {
        return;
    }

    if (self->st.planes[ClipPlaneState::SlotCustom].flipped) {
        direction = -direction;
    }

    // Only the Coin node is touched, never st: the direction the user typed has
    // to survive, so that turning the follow off gives it back and a saved
    // document keeps it. Reusing the node's own distance keeps the plane where
    // it is and only re-aims it.
    const SbPlane current = node->plane.getValue();
    node->plane.setValue(SbPlane(direction, current.getDistanceFromOrigin()));
}
