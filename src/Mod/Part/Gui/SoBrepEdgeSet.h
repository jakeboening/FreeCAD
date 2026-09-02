// SPDX-License-Identifier: LGPL-2.1-or-later
// SPDX-FileCopyrightText: 2011 Werner Mayer <wmayer[at]users.sourceforge.net>
// SPDX-FileCopyrightText: 2026 Joao Matos
// SPDX-FileNotice: Part of the FreeCAD project.

/******************************************************************************
 *                                                                            *
 *   FreeCAD is free software: you can redistribute it and/or modify          *
 *   it under the terms of the GNU Lesser General Public License as           *
 *   published by the Free Software Foundation, either version 2.1 of the     *
 *   License, or (at your option) any later version.                          *
 *                                                                            *
 *   FreeCAD is distributed in the hope that it will be useful, but           *
 *   WITHOUT ANY WARRANTY; without even the implied warranty of               *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the            *
 *   GNU Lesser General Public License for more details.                      *
 *                                                                            *
 *   You should have received a copy of the GNU Lesser General Public         *
 *   License along with FreeCAD.  If not, see                                *
 *   <https://www.gnu.org/licenses/>.                                         *
 *                                                                            *
 ******************************************************************************/

#pragma once

#include <boost/algorithm/string/predicate.hpp>
#include <Inventor/fields/SoMFInt32.h>
#include <Inventor/fields/SoSFColor.h>
#include <Inventor/nodes/SoIndexedLineSet.h>
#include <Inventor/nodes/SoNode.h>
#include <memory>
#include <vector>
#include <Gui/Selection/SoFCSelectionContext.h>
#include <Mod/Part/PartGlobal.h>


class SoCoordinateElement;

namespace PartGui
{

class ViewProviderPartExt;

/** Hides the geometry that follows it, without making it unpickable.
 *
 * The "Shaded" display mode has to traverse the edges even though it does not
 * draw them: an SoSwitch skips its non-selected children for *every* action,
 * ray picking included, so geometry left out of the active display mask mode
 * produces no SoLineDetail and cannot be selected at all.  Hiding therefore has
 * to happen during rendering rather than by omission from the graph.
 *
 * Raising SoShapeStyleElement's INVISIBLE flag is all it takes:
 * SoShape::shouldGLRender() returns early on it, while nothing on the pick path
 * consults it - SoShape::shouldRayPick() looks at SoPickStyleElement alone.
 *
 * An SoDrawStyle set to INVISIBLE reaches the same flag, but it can be
 * overruled: SoDrawStyle::doAction() skips the element whenever an ancestor has
 * set SoOverrideElement's draw style override.  SoRenderManager's HIDDEN_LINE
 * mode does exactly that on its second pass, and FreeCAD's "Hidden Line" draw
 * style renders through the "Shaded" mask mode - so the edges would reappear
 * there and change a display mode that is meant to be left alone.  This node
 * writes the flag unconditionally, so the hiding holds in every render mode.
 *
 * Only GLRender() is implemented, deliberately: no other action should see any
 * difference.
 */
class PartGuiExport SoBrepEdgeHider: public SoNode
{
    using inherited = SoNode;

    SO_NODE_HEADER(SoBrepEdgeHider);

public:
    static void initClass();
    SoBrepEdgeHider();

    void GLRender(SoGLRenderAction* action) override;

protected:
    ~SoBrepEdgeHider() override = default;
};

class PartGuiExport SoBrepEdgeSet: public SoIndexedLineSet
{
    using inherited = SoIndexedLineSet;

    SO_NODE_HEADER(SoBrepEdgeSet);

public:
    static void initClass();
    SoBrepEdgeSet();

    void setViewProvider(ViewProviderPartExt* vp)
    {
        viewProvider = vp;
    }

    SoMFInt32 highlightCoordIndex;
    SoMFInt32 selectionCoordIndex;
    SoSFColor highlightColor;
    SoSFColor selectionColor;

protected:
    ~SoBrepEdgeSet() override;
    void GLRender(SoGLRenderAction* action) override;
    void GLRenderBelowPath(SoGLRenderAction* action) override;
    void doAction(SoAction* action) override;
    SoDetail* createLineSegmentDetail(
        SoRayPickAction* action,
        const SoPrimitiveVertex* v1,
        const SoPrimitiveVertex* v2,
        SoPickedPoint* pp
    ) override;

    void getBoundingBox(SoGetBoundingBoxAction* action) override;

private:
    struct SelContext;
    using SelContextPtr = std::shared_ptr<SelContext>;

    void renderHighlight(SoGLRenderAction* action, SelContextPtr);
    void renderSelection(SoGLRenderAction* action, SelContextPtr, bool push = true);
    bool validIndexes(const SoCoordinateElement*, const std::vector<int32_t>&) const;


private:
    SelContextPtr selContext;
    SelContextPtr selContext2;
    Gui::SoFCSelectionCounter selCounter;
    SoIndexedLineSet* overlayLineSet {nullptr};

    // backreference to viewprovider that owns this node
    ViewProviderPartExt* viewProvider = nullptr;
};

}  // namespace PartGui
