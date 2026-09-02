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

#include <Inventor/nodes/SoNode.h>
#include <Inventor/nodes/SoSubNode.h>

#include <FCGlobal.h>

namespace Gui
{

class View3DInventorViewer;

/**
 * Draws the clipping caps from inside the scene traversal.
 *
 * The caps used to be painted after the whole scene had been rendered, which
 * put them on top of the measurements: an annotation builds its root as an
 * SoAnnotation, which Coin defers to a pass at the end of the traversal exactly
 * so that it ends up in front of the geometry it describes, and a cap drawn
 * after that traversal landed on top of work that had already placed itself
 * last. Rendering from a node inside the traversal restores the order without
 * anyone having to special-case anything: the model is drawn, then this, then
 * Coin's delayed annotations.
 *
 * Placement matters twice over. The node goes after the group that holds the
 * clipping planes and the geometry they cut, so the model is already in the
 * depth buffer by the time it runs; and it goes *outside* that group, because
 * inside it the traversal still has the planes enabled in OpenGL and they would
 * clip the very quad the cap is made of.
 *
 * The node has no geometry of its own as far as the rest of FreeCAD is
 * concerned: it contributes no bounding box and cannot be picked.
 */
class GuiExport SoFCClippingCap: public SoNode
{
    using inherited = SoNode;

    SO_NODE_HEADER(Gui::SoFCClippingCap);

public:
    static void initClass();
    static void finish();

    SoFCClippingCap();

    /// The viewer whose clipping this fills in. Not owned; may be nullptr.
    void setViewer(View3DInventorViewer* viewer);

    void GLRender(SoGLRenderAction* action) override;

    /// Contributes nothing to the scene's extent - it only paints what is there.
    void getBoundingBox(SoGetBoundingBoxAction* action) override;
    /// Not pickable: the cap is a fill, not a surface you can select or snap to.
    void rayPick(SoRayPickAction* action) override;

protected:
    ~SoFCClippingCap() override;

private:
    View3DInventorViewer* viewer {nullptr};
};

}  // namespace Gui
