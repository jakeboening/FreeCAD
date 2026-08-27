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

#include <Inventor/actions/SoGLRenderAction.h>

#include <FCGlobal.h>

class SoCamera;
class SoClipPlane;
class SoCoordinate3;
class SoGroup;
class SoMaterial;
class SoSeparator;

namespace Gui
{

class View3DInventorViewer;
struct ClippingState;

/**
 * Fills in the cross-section a clipping plane exposes, so a clipped solid reads
 * as solid instead of hollow.
 *
 * A clip plane only discards fragments; it does not close the shape, so without
 * this you see the inside of the far wall through the cut. The fill is drawn
 * per frame with the stencil buffer rather than computed as geometry: it costs
 * nothing when the user drags the offset, and it works for anything that
 * rasterises - Part, Mesh, points - rather than only for BRep.
 *
 * For each capped plane, and each group of objects sharing a cap colour:
 *
 *  1. Clear the stencil, switch colour and depth writes off, and render the
 *     geometry with the front and back faces counting in opposite directions.
 *     Where the count ends up non-zero, the plane is passing through material.
 *  2. Switch colour writes back on and draw a quad lying on the plane, letting
 *     through only the pixels the count marked.
 *
 * The test is "not zero" rather than "exactly one", which makes the result
 * independent of winding order and unions overlapping or nested solids instead
 * of cancelling them into holes.
 *
 * Known limits, none of them cheap to fix:
 * - The fill is pixels, not geometry: it cannot be selected, measured, snapped
 *   to or hatched.
 * - A shape that is not a closed manifold has no inside, so its cap is
 *   undefined - open shells and meshes with holes speckle.
 * - Transparent geometry writes no depth, so a cap behind a transparent body
 *   draws over it.
 * - Vector export has no stencil buffer, so it has no caps.
 */
class GuiExport ClippingCapRenderer
{
public:
    explicit ClippingCapRenderer(View3DInventorViewer& viewer);
    ~ClippingCapRenderer();

    ClippingCapRenderer(const ClippingCapRenderer&) = delete;
    ClippingCapRenderer& operator=(const ClippingCapRenderer&) = delete;

    /**
     * Draws the caps for every enabled plane whose cap is switched on.
     *
     * Must be called with the view's context current and the main scene already
     * drawn, so that the depth buffer is complete - but before anything clears
     * the depth buffer again. It is a no-op when nothing wants a cap or the
     * context has no stencil buffer.
     */
    void render(const ClippingState& state);

    /// Drops the cached object grouping. Call when the scene graph changes.
    void invalidate();

private:
    /// Whitelist test: does this shape type contribute a filled surface?
    static bool isFilledSurfaceType(const SoType& type);
    /// Prunes edges, points and text from the stencil pass.
    static SoGLRenderAction::AbortCode abortCallback(void* userData);

    View3DInventorViewer& viewer;

    /// Its own action: the viewer's one is mid-flight when we run, and its
    /// transparency mode can render some geometry twice, which would break the
    /// face count. Ours renders everything once, opaque.
    SoGLRenderAction* capAction {nullptr};
    bool stencilPassActive {false};

    SoSeparator* stencilRoot {nullptr};
    SoGroup* stencilClips {nullptr};
    SoGroup* stencilTarget {nullptr};

    SoSeparator* quadRoot {nullptr};
    SoGroup* quadClips {nullptr};
    SoMaterial* quadMaterial {nullptr};
    SoCoordinate3* quadCoords {nullptr};

    SoCamera* attachedCamera {nullptr};
};

}  // namespace Gui
