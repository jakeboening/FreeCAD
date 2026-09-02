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

// Must precede SoFCClippingCap.h: that header uses SO_NODE_HEADER but includes
// only <Inventor/nodes/SoNode.h>, which does not define the macro, so it does not
// compile on its own. One line in the header fixes this properly - see the report;
// SoFCBackgroundGradient.h, the node this one is modelled on, includes SoSubNode.h
// itself. Until then this include order is load-bearing.
#include <Inventor/nodes/SoSubNode.h>

#include <Inventor/actions/SoGLRenderAction.h>
#include <Inventor/elements/SoCacheElement.h>
#include <Inventor/elements/SoGLLazyElement.h>
#include <Inventor/elements/SoLazyElement.h>
#include <Inventor/misc/SoState.h>

#include "SoFCClippingCap.h"
#include "../View3DInventorViewer.h"

using namespace Gui;

SO_NODE_SOURCE(SoFCClippingCap)

void SoFCClippingCap::finish()
{
    atexit_cleanup();
}

SoFCClippingCap::SoFCClippingCap()
{
    SO_NODE_CONSTRUCTOR(SoFCClippingCap);
}

SoFCClippingCap::~SoFCClippingCap() = default;

void SoFCClippingCap::initClass()
{
    SO_NODE_INIT_CLASS(SoFCClippingCap, SoNode, "Node");
}

void SoFCClippingCap::setViewer(View3DInventorViewer* newViewer)
{
    this->viewer = newViewer;
}

/**
 * Paints the caps, then hands the traversal back in the state it was lent in.
 *
 * The work itself is ClippingCapRenderer's; what belongs here is everything that
 * follows from being a node rather than a top-level pass. The renderer drives a
 * render action of its own, so this is the SoShadowGroup situation: a complete
 * nested traversal in the middle of somebody else's, sharing one OpenGL context
 * with two independent sets of Coin bookkeeping.
 */
void SoFCClippingCap::GLRender(SoGLRenderAction* action)
{
    SoState* state = action->getState();

    // Unconditionally, and before any early return. Whether there is a cap to
    // draw depends on the view's clipping state, which the user changes without
    // ever touching the scene graph - so a render cache taken across this node
    // would happily replay a frame from before the cap was switched on. The
    // painting is raw OpenGL besides, and a nested traversal cannot be recorded
    // into an enclosing display list in the first place.
    SoCacheElement::invalidate(state);

    if (!viewer) {
        return;
    }

    // Coin walks the graph again for the paths it put off - transparent geometry,
    // and the annotations that are the whole reason the caps moved in here.
    //
    // This is not belt and braces: it is load-bearing. Path traversal still visits
    // the children that are not on the path when they answer true to
    // affectsState(), and SoNode says true by default, so an annotation's delayed
    // path reaches this node off-path and Coin calls GLRender through
    // GLRenderOffPath. Painting the caps there would put them straight back on top
    // of the annotations this whole arrangement exists to keep them clear of - the
    // original bug, restored one pass later.
    if (action->isRenderingDelayedPaths()) {
        return;
    }

    // Multi-pass antialiasing traverses us once per pass, and that is not waste:
    // Coin clears and re-renders for each pass and accumulates the results at
    // 1/numPasses each, so a cap skipped in even one pass comes out that much
    // more transparent. Every pass has to draw it. (The nested action runs at
    // numPasses 1, so the cap itself is not jittered - see the note in the
    // report; the edge is a touch harder than the model's, nothing more.)

    viewer->renderClippingCaps();

    // The nested action pushed its own materials, light model and texture state
    // into the shared context. SoGLLazyElement sends that state lazily: the outer
    // traversal's instance still believes OpenGL holds whatever it last sent, and
    // will skip re-sending it. Nothing we could restore by hand fixes that, since
    // the mismatch is in Coin's bookkeeping rather than in OpenGL - so tell it to
    // assume nothing and re-send everything. The fixed-function state the renderer
    // touches is the renderer's own problem, and its guard puts that back itself.
    SoGLLazyElement::getInstance(state)->reset(state, SoLazyElement::ALL_MASK);
}

void SoFCClippingCap::getBoundingBox(SoGetBoundingBoxAction* action)
{
    // Deliberately empty. The caps fill in a section of geometry that is already
    // in the scene, so they can only ever add extent that is already accounted
    // for - and the renderer asks the viewer for the scene's bounding box while
    // it works, which would recurse straight back into here.
    (void)action;
}

void SoFCClippingCap::rayPick(SoRayPickAction* action)
{
    // Deliberately empty: the cap is pixels, not a surface. Picking it would hand
    // the caller a hit on something that has no geometry, no sub-element and no
    // view provider behind it.
    (void)action;
}
