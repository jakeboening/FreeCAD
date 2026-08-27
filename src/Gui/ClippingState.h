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
#include <cstdint>

#include <FCGlobal.h>

namespace Base
{
class Writer;
class XMLReader;
}  // namespace Base

namespace Gui
{

/// Where the colour of a clipping cap comes from.
enum class CapColorMode
{
    /// Take the colour of the object being cut, so the cap reads as its material.
    BodyColor = 0,
    /// Use the colour picked in the Clipping panel.
    CustomColor = 1
};

/**
 * One clipping plane of the Clipping panel, plus its cap settings.
 *
 * This is plain data on purpose: no Coin, no Qt.  The panel edits it, the
 * viewer applies it to an SoClipPlane, and Gui::Document serialises it, and
 * none of those three need to know about the others.
 */
struct GuiExport ClipPlaneState
{
    enum Slot
    {
        SlotX = 0,
        SlotY,
        SlotZ,
        SlotCustom,
        SlotCount
    };

    bool enabled {false};
    bool flipped {false};
    /** Normal in world space, as shown in the panel and before `flipped`.
     *
     * Stored as the user typed it rather than normalised, so that editing one
     * of the three direction boxes does not rewrite the other two under the
     * cursor. Only the direction matters downstream - SbPlane normalises what
     * it is given, and the offset is independent of the length - but a restore
     * does normalise, so do not rely on the two being bit-identical.
     */
    float normal[3] {1.0F, 0.0F, 0.0F};
    /// Offset along the normal, as typed in the panel (before `flipped`).
    double offset {0.0};
    /// SlotCustom only: keep the normal aimed at the current view direction.
    bool followViewDir {false};

    bool capEnabled {false};
    CapColorMode capColorMode {CapColorMode::BodyColor};
    /// Base::Color packed as 0xRRGGBBAA. Only used when capColorMode is CustomColor.
    uint32_t capColor {0xccccccffU};
    /// 0..1. Overrides the cut object's own transparency rather than adding to it.
    float capTransparency {0.0F};

    bool operator==(const ClipPlaneState&) const = default;
};

/**
 * The clipping configuration of one 3D view.
 *
 * Saved into GuiDocument.xml next to the camera, so a document reopens clipped
 * the way it was left.  The element is written after `<Camera/>` and only when
 * something is actually enabled, which keeps untouched documents byte-identical
 * and lets an older FreeCAD ignore it: its reader stops once it has found the
 * camera.
 */
struct GuiExport ClippingState
{
    std::array<ClipPlaneState, ClipPlaneState::SlotCount> planes {};

    /// True when nothing is enabled anywhere, in which case nothing is written.
    bool isDefault() const;
    /// True when at least one enabled plane also wants a cap.
    bool anyCapEnabled() const;
    /// True when at least one plane is enabled.
    bool anyPlaneEnabled() const;

    void save(Base::Writer& writer) const;
    /// The reader must already be positioned on the `<Clipping>` element.
    void restore(Base::XMLReader& reader);

    bool operator==(const ClippingState&) const = default;
};

}  // namespace Gui
