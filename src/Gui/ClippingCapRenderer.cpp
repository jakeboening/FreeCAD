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

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <iterator>
#include <map>
#include <string>
#include <utility>
#include <vector>

#include <QOpenGLContext>
#include <QOpenGLFunctions>
#include <QOpenGLWidget>

#include <Inventor/SbBox3f.h>
#include <Inventor/SbMatrix.h>
#include <Inventor/SbPlane.h>
#include <Inventor/SbVec3f.h>
#include <Inventor/SbVec4f.h>
#include <Inventor/SbViewVolume.h>
#include <Inventor/SbViewportRegion.h>
#include <Inventor/SoType.h>
#include <Inventor/actions/SoGLRenderAction.h>
#include <Inventor/nodes/SoAnnotation.h>
#include <Inventor/nodes/SoCallback.h>
#include <Inventor/nodes/SoCamera.h>
#include <Inventor/nodes/SoClipPlane.h>
#include <Inventor/nodes/SoCoordinate3.h>
#include <Inventor/nodes/SoDepthBuffer.h>
#include <Inventor/nodes/SoDrawStyle.h>
#include <Inventor/nodes/SoFaceSet.h>
#include <Inventor/nodes/SoGroup.h>
#include <Inventor/nodes/SoLightModel.h>
#include <Inventor/nodes/SoMaterial.h>
#include <Inventor/nodes/SoPolygonOffset.h>
#include <Inventor/nodes/SoSeparator.h>
#include <Inventor/nodes/SoShape.h>
#include <Inventor/nodes/SoShapeHints.h>
#include <Inventor/SoRenderManager.h>
#include <Inventor/system/gl.h>

#include <App/Application.h>
#include <Base/BoundBox.h>
#include <Base/Color.h>
#include <Base/Console.h>
#include <Base/Parameter.h>

#include "ClippingCapRenderer.h"
#include "ClippingState.h"
#include "Document.h"
#include "SoDevicePixelRatioElement.h"
#include "SoFCInteractiveElement.h"
#include "View3DInventorViewer.h"
#include "ViewParams.h"
#include "ViewProvider.h"
#include "ViewProviderGeometryObject.h"

using namespace Gui;

namespace
{

/// How far the cap quad reaches, as a fraction of the scene bounding box diagonal.
/// Taken from the diagonal rather than per-axis extents so that one radius covers
/// the section whatever way the plane is turned.
constexpr float capQuadRadiusFactor = 0.55F;

/// Ambient floor of the fake headlight folded into the cap colour; see shadeFactor().
constexpr float capShadeAmbient = 0.30F;
constexpr float capShadeDiffuse = 0.70F;

/// Falls back to this when the user has not set View/ClipCapMaxPasses.
constexpr long defaultMaxCapPasses = 16;

/**
 * One colour's worth of a plane's cap: the view provider roots to stencil, and
 * the world box they jointly occupy, which is what the pass gets scissored to.
 */
struct CapPass
{
    SbColor color;
    std::vector<SoNode*> roots;
    Base::BoundBox3d box;
};

/// A candidate object: its root in the viewer's object group, its world box and
/// the colour its cap would take. None of this depends on the clipping planes.
struct CapObject
{
    SoNode* root {nullptr};
    Base::BoundBox3d box;
    SbColor color {0.8F, 0.8F, 0.8F};
};

/**
 * The per-viewer object table, kept outside the class.
 *
 * ClippingCapRenderer.h has no member to hang this on and is owned elsewhere, so
 * the cache lives in a side table keyed on the renderer. It should move into the
 * class (a cache member, or a pimpl) the next time the header is opened - this
 * is the one thing in here that is shaped by the header rather than by the
 * algorithm.
 */
struct ObjectCache
{
    bool valid {false};
    SbUniqueId groupId {0};
    SbVec3f sceneMin;
    SbVec3f sceneMax;
    std::vector<CapObject> objects;
};

std::map<const ClippingCapRenderer*, ObjectCache>& objectCaches()
{
    static std::map<const ClippingCapRenderer*, ObjectCache> caches;
    return caches;
}

/// Shape types that rasterise a filled surface, and so may be counted.
/// Deliberately built on first use: at static-init time Coin's types do not exist
/// yet and every fromName() would come back bad.
std::vector<SoType> buildFilledSurfaceTypes()
{
    static const char* const names[] = {
        // SoBrepFaceSet and Mesh's SoFCIndexedFaceSet both derive from this one.
        "SoIndexedFaceSet",
        "SoFaceSet",
        "SoIndexedTriangleStripSet",
        "SoTriangleStripSet",
        "SoQuadMesh",
        "SoCube",
        "SoCylinder",
        "SoCone",
        "SoSphere",
        "SoNurbsSurface",
        "SoIndexedNurbsSurface",
        "SoVRMLIndexedFaceSet"
    };

    std::vector<SoType> types;
    types.reserve(std::size(names));
    for (const char* name : names) {
        // A Coin build without VRML, say, simply has no such type; skipping it
        // beats handing SoType::isDerivedFrom() a bad type.
        const SoType type = SoType::fromName(SbName(name));
        if (!type.isBad()) {
            types.push_back(type);
        }
    }

    return types;
}

/**
 * Puts back the elements renderGLActionScene() establishes before it renders.
 *
 * We drive our own action over our own root, so nothing has set them up for us;
 * without them the shapes that consult them - VBO use, the widget behind the
 * context, the device pixel ratio - would see defaults rather than the values
 * the frame around us was drawn with. Mirrors View3DInventorViewer.cpp.
 */
void setupRenderElements(void* userData, SoAction* action)
{
    if (!action->isOfType(SoGLRenderAction::getClassTypeId())) {
        return;
    }

    auto* viewer = static_cast<View3DInventorViewer*>(userData);
    SoState* state = action->getState();

    SoDevicePixelRatioElement::set(state, static_cast<float>(viewer->devicePixelRatio()));
    SoGLWidgetElement::set(state, qobject_cast<QOpenGLWidget*>(viewer->getGLWidget()));
    SoGLRenderActionElement::set(state, static_cast<SoGLRenderAction*>(action));
    SoGLVBOActivatedElement::set(state, viewer->isEnabledVBO());
}

/// Unit vector orthogonal to `normal`, taken from whichever world axis is least
/// parallel to it - Gram-Schmidt against the closest axis would cancel.
SbVec3f orthogonalTo(const SbVec3f& normal)
{
    const float ax = std::fabs(normal[0]);
    const float ay = std::fabs(normal[1]);
    const float az = std::fabs(normal[2]);

    SbVec3f axis(1.0F, 0.0F, 0.0F);
    if (ay <= ax && ay <= az) {
        axis = SbVec3f(0.0F, 1.0F, 0.0F);
    }
    else if (az <= ax && az <= ay) {
        axis = SbVec3f(0.0F, 0.0F, 1.0F);
    }

    SbVec3f result = axis - normal * axis.dot(normal);
    if (result.normalize() == 0.0F) {
        // Cannot happen for a normalised normal and the least parallel axis, but
        // a degenerate normal must not produce NaN coordinates.
        return SbVec3f(1.0F, 0.0F, 0.0F);
    }

    return result;
}

/**
 * Does the plane pass through this box?
 *
 * The signed distance of the box centre against the box's own extent projected
 * onto the normal - the standard box/plane separation test. False means the box
 * is wholly kept or wholly clipped, and either way it can contribute no cap.
 */
/**
 * Stencil bits of the framebuffer currently bound for drawing.
 *
 * GL_STENCIL_BITS answers for the draw framebuffer on a compatibility profile,
 * but some drivers report zero for a framebuffer object and only answer the
 * attachment query, so fall back to that before believing there is none.
 */
GLint boundStencilBits(QOpenGLFunctions* gl)
{
    GLint bits = 0;
    glGetIntegerv(GL_STENCIL_BITS, &bits);
    if (bits > 0) {
        return bits;
    }

    GLint type = GL_NONE;
    gl->glGetFramebufferAttachmentParameteriv(
        GL_FRAMEBUFFER,
        GL_STENCIL_ATTACHMENT,
        GL_FRAMEBUFFER_ATTACHMENT_OBJECT_TYPE,
        &type
    );
    if (type == GL_NONE) {
        return 0;
    }

    gl->glGetFramebufferAttachmentParameteriv(
        GL_FRAMEBUFFER,
        GL_STENCIL_ATTACHMENT,
        GL_FRAMEBUFFER_ATTACHMENT_STENCIL_SIZE,
        &bits
    );
    return bits;
}

/// One enabled clipping plane, reduced to what the box tests below need.
struct ActivePlane
{
    SbVec3f normal;
    float distance {0.0F};
};

/**
 * Signed distance of the box centre from the plane, and the box's extent along
 * the normal. Together they place the whole box relative to the plane without
 * touching its eight corners.
 */
void boxAgainstPlane(
    const Base::BoundBox3d& box,
    const SbVec3f& normal,
    float distance,
    float& centreDistance,
    float& radius
)
{
    const auto cx = static_cast<float>((box.MinX + box.MaxX) * 0.5);
    const auto cy = static_cast<float>((box.MinY + box.MaxY) * 0.5);
    const auto cz = static_cast<float>((box.MinZ + box.MaxZ) * 0.5);
    const auto hx = static_cast<float>((box.MaxX - box.MinX) * 0.5);
    const auto hy = static_cast<float>((box.MaxY - box.MinY) * 0.5);
    const auto hz = static_cast<float>((box.MaxZ - box.MinZ) * 0.5);

    centreDistance = normal[0] * cx + normal[1] * cy + normal[2] * cz - distance;
    radius = std::fabs(normal[0]) * hx + std::fabs(normal[1]) * hy + std::fabs(normal[2]) * hz;
}

bool straddlesPlane(const Base::BoundBox3d& box, const SbVec3f& normal, float distance)
{
    if (!box.IsValid()) {
        return false;
    }

    float centreDistance = 0.0F;
    float radius = 0.0F;
    boxAgainstPlane(box, normal, distance, centreDistance, radius);

    return std::fabs(centreDistance) <= radius;
}

/// True when the whole box is on the discarded side of the plane, so that no part
/// of it survives to be capped.
bool whollyClippedBy(const Base::BoundBox3d& box, const ActivePlane& plane)
{
    if (!box.IsValid()) {
        return false;
    }

    float centreDistance = 0.0F;
    float radius = 0.0F;
    boxAgainstPlane(box, plane.normal, plane.distance, centreDistance, radius);

    // A clip plane keeps the half-space its normal points into.
    return centreDistance + radius < 0.0F;
}

/**
 * Screen-space AABB of a world box, in window pixels.
 *
 * Returns false when any corner is at or behind the eye, where the perspective
 * divide is meaningless; the caller then falls back to the whole viewport, which
 * is correct but slower.
 */
bool projectBox(
    const Base::BoundBox3d& box,
    const SbMatrix& viewProjection,
    const SbVec2s& origin,
    const SbVec2s& size,
    std::array<GLint, 4>& out
)
{
    if (!box.IsValid() || size[0] <= 0 || size[1] <= 0) {
        return false;
    }

    float minX = 1.0F;
    float minY = 1.0F;
    float maxX = -1.0F;
    float maxY = -1.0F;

    for (int corner = 0; corner < 8; ++corner) {
        const SbVec4f world(
            static_cast<float>((corner & 1) != 0 ? box.MaxX : box.MinX),
            static_cast<float>((corner & 2) != 0 ? box.MaxY : box.MinY),
            static_cast<float>((corner & 4) != 0 ? box.MaxZ : box.MinZ),
            1.0F
        );

        SbVec4f clip;
        viewProjection.multVecMatrix(world, clip);
        if (clip[3] <= 0.0F) {
            return false;
        }

        const float ndcX = clip[0] / clip[3];
        const float ndcY = clip[1] / clip[3];
        minX = std::min(minX, ndcX);
        minY = std::min(minY, ndcY);
        maxX = std::max(maxX, ndcX);
        maxY = std::max(maxY, ndcY);
    }

    const auto width = static_cast<float>(size[0]);
    const auto height = static_cast<float>(size[1]);

    // One pixel of slack each way: the box is a bound on the geometry, not on the
    // pixels its edges touch once rasterised.
    auto left = static_cast<GLint>(std::floor((minX * 0.5F + 0.5F) * width)) - 1;
    auto bottom = static_cast<GLint>(std::floor((minY * 0.5F + 0.5F) * height)) - 1;
    auto right = static_cast<GLint>(std::ceil((maxX * 0.5F + 0.5F) * width)) + 1;
    auto top = static_cast<GLint>(std::ceil((maxY * 0.5F + 0.5F) * height)) + 1;

    left = std::clamp(left, GLint {0}, static_cast<GLint>(size[0]));
    bottom = std::clamp(bottom, GLint {0}, static_cast<GLint>(size[1]));
    right = std::clamp(right, GLint {0}, static_cast<GLint>(size[0]));
    top = std::clamp(top, GLint {0}, static_cast<GLint>(size[1]));

    if (right <= left || top <= bottom) {
        return false;
    }

    out = {origin[0] + left, origin[1] + bottom, right - left, top - bottom};
    return true;
}

/// The colour a cut through this object should read as.
SbColor bodyColorOf(ViewProvider* provider)
{
    if (auto* geometry = freecad_cast<ViewProviderGeometryObject*>(provider)) {
        if (geometry->ShapeAppearance.getSize() > 0) {
            const Base::Color& color = geometry->ShapeAppearance.getDiffuseColor();
            return {color.r, color.g, color.b};
        }
    }

    // Anything else that is drawn but is not a geometry object - a Mesh or Points
    // view provider, a custom one - still answers the generic colour query.
    const std::map<std::string, Base::Color> colors = provider->getElementColors("Face");
    const auto it = colors.find("Face");
    if (it != colors.end()) {
        return {it->second.r, it->second.g, it->second.b};
    }

    const Base::Color fallback(
        static_cast<uint32_t>(ViewParams::instance()->getDefaultShapeColor())
    );
    return {fallback.r, fallback.g, fallback.b};
}

/// Packs a colour into a grouping key, so objects that look the same share a pass.
uint32_t colorKey(const SbColor& color)
{
    const auto quantise = [](float value) {
        return static_cast<uint32_t>(std::clamp(value, 0.0F, 1.0F) * 255.0F + 0.5F);
    };
    return (quantise(color[0]) << 16U) | (quantise(color[1]) << 8U) | quantise(color[2]);
}

/**
 * The candidate objects of one viewer, rebuilt only when they can have changed.
 *
 * Nothing here depends on the clipping planes, so the key is the object group and
 * the scene extent alone: dragging a plane offset - the case that has to stay
 * smooth - reuses the table frame after frame. The expensive part is
 * ViewProvider::getBoundingBox(), which runs a bounding box traversal per object.
 */
const std::vector<CapObject>& collectObjects(
    const ClippingCapRenderer* owner,
    View3DInventorViewer& viewer,
    const SbBox3f& sceneBox
)
{
    ObjectCache& cache = objectCaches()[owner];

    SoGroup* objectGroup = viewer.getObjectGroup();
    const SbUniqueId groupId = objectGroup->getNodeId();

    SbVec3f sceneMin;
    SbVec3f sceneMax;
    sceneBox.getBounds(sceneMin, sceneMax);

    // The node id catches objects appearing and disappearing; the scene extent
    // catches one being moved or reshaped, which does not touch the group node
    // itself. An edit that stays strictly inside the existing bounds is caught by
    // neither - invalidate() is what covers that.
    if (cache.valid && cache.groupId == groupId && cache.sceneMin == sceneMin
        && cache.sceneMax == sceneMax) {
        return cache.objects;
    }

    cache.valid = true;
    cache.groupId = groupId;
    cache.sceneMin = sceneMin;
    cache.sceneMax = sceneMax;
    cache.objects.clear();

    Document* document = viewer.getDocument();
    if (!document) {
        return cache.objects;
    }

    const int count = objectGroup->getNumChildren();
    cache.objects.reserve(static_cast<std::size_t>(count));

    for (int i = 0; i < count; ++i) {
        SoNode* root = objectGroup->getChild(i);

        // addViewProvider() puts every physical view provider in here flat, with its
        // global placement already baked in, so the direct children are exactly the
        // provider roots: no SoSearchAction, and no walking into them.
        ViewProvider* provider = document->getViewProvider(root);
        if (!provider || !provider->isShow()) {
            continue;
        }

        CapObject object;
        object.root = root;
        object.box = provider->getBoundingBox(nullptr, nullptr, true, &viewer);
        if (!object.box.IsValid()) {
            continue;
        }
        object.color = bodyColorOf(provider);

        cache.objects.push_back(object);
    }

    return cache.objects;
}

/**
 * Splits the objects this plane actually cuts into one pass per cap colour.
 *
 * The bounding box rejection is the single biggest saving: in a typical assembly
 * a plane passes through a handful of parts and misses everything else, and a
 * missed part costs nothing at all rather than a full stencil traversal.
 */
std::vector<CapPass> buildPasses(
    const std::vector<CapObject>& objects,
    const ClipPlaneState& plane,
    const SbVec3f& normal,
    float distance,
    const std::vector<ActivePlane>& otherPlanes,
    long maxPasses
)
{
    std::vector<CapPass> passes;

    // Cut by this plane, and not already removed in its entirety by one of the
    // others - the quad would be clipped away everywhere such an object marked
    // the stencil, so stencilling it is pure cost.
    const auto contributes = [&](const CapObject& object) {
        if (!straddlesPlane(object.box, normal, distance)) {
            return false;
        }
        return std::none_of(
            otherPlanes.begin(),
            otherPlanes.end(),
            [&object](const ActivePlane& other) {
                return whollyClippedBy(object.box, other);
            }
        );
    };

    if (plane.capColorMode == CapColorMode::CustomColor) {
        // One colour for everything, so one pass over the whole object group.
        const Base::Color custom(plane.capColor);
        CapPass single;
        single.color = SbColor(custom.r, custom.g, custom.b);

        for (const CapObject& object : objects) {
            if (contributes(object)) {
                single.roots.push_back(object.root);
                single.box.Add(object.box);
            }
        }

        if (!single.roots.empty()) {
            passes.push_back(std::move(single));
        }
        return passes;
    }

    std::map<uint32_t, std::size_t> byColor;
    for (const CapObject& object : objects) {
        if (!contributes(object)) {
            continue;
        }

        const uint32_t key = colorKey(object.color);
        auto it = byColor.find(key);
        if (it == byColor.end()) {
            it = byColor.emplace(key, passes.size()).first;
            CapPass pass;
            pass.color = object.color;
            passes.push_back(std::move(pass));
        }

        CapPass& pass = passes[it->second];
        pass.roots.push_back(object.root);
        pass.box.Add(object.box);
    }

    if (static_cast<long>(passes.size()) > maxPasses) {
        // Budget blown. The biggest groups keep their own colour and the tail is
        // merged into a single extra pass: one wrongly coloured cap beats a
        // rainbow scene that takes a second a frame.
        std::sort(passes.begin(), passes.end(), [](const CapPass& lhs, const CapPass& rhs) {
            return lhs.roots.size() > rhs.roots.size();
        });

        const auto keep = static_cast<std::size_t>(maxPasses) - 1;
        CapPass merged = passes[keep];
        for (std::size_t i = keep + 1; i < passes.size(); ++i) {
            merged.roots.insert(
                merged.roots.end(),
                passes[i].roots.begin(),
                passes[i].roots.end()
            );
            merged.box.Add(passes[i].box);
        }
        passes.resize(keep);
        passes.push_back(std::move(merged));

        // Once for the life of the process: this is per frame, per plane.
        static bool warned = false;
        if (!warned) {
            warned = true;
            Base::Console().log(
                "Clipping caps: more cap colours than the ClipCapMaxPasses budget of "
                "%ld; the remainder share one colour.\n",
                maxPasses
            );
        }
    }

    return passes;
}

/**
 * Lays the cap quad on the plane, big enough to cover the section at any angle.
 *
 * Sized from the scene box diagonal rather than its per-axis extents, because a
 * plane cutting the box corner to corner exposes a section wider than any single
 * extent. It is left exactly on the plane - a geometric nudge along -n would need
 * a scene-scale epsilon and would make the cap float visibly when zoomed in - and
 * the z-fighting against the cut edge is handled by LEQUAL plus polygon offset.
 */
void setQuadGeometry(
    SoCoordinate3* coords,
    const SbVec3f& sceneCentre,
    float radius,
    const SbVec3f& normal,
    float distance
)
{
    const SbVec3f centre = sceneCentre - normal * (sceneCentre.dot(normal) - distance);
    const SbVec3f u = orthogonalTo(normal);
    const SbVec3f v = normal.cross(u);

    // Wound so that the right-hand normal of the quad is the plane normal, that is,
    // counter-clockwise seen from the half-space the clip plane keeps.
    coords->point.set1Value(0, centre + (u + v) * radius);
    coords->point.set1Value(1, centre + (v - u) * radius);
    coords->point.set1Value(2, centre - (u + v) * radius);
    coords->point.set1Value(3, centre + (u - v) * radius);
}

/**
 * Everything OpenGL that the cap passes disturb, saved on the way in and put back
 * on the way out - and the baseline they need, installed once.
 *
 * We run in the middle of a frame that other code has already set up and will go
 * on using: the NaviCube does a scissored depth clear right after us, renderScene
 * finishes with an alpha clear that depends on the colour mask, and
 * renderToFramebuffer deliberately runs at GL_LESS where a fresh render action
 * would leave GL_LEQUAL. So the restore has to be exhaustive, not approximate.
 */
class ScopedCapGLState
{
public:
    explicit ScopedCapGLState(QOpenGLFunctions* functions)
        : gl(functions)
    {
        stencilTest = glIsEnabled(GL_STENCIL_TEST);
        depthTest = glIsEnabled(GL_DEPTH_TEST);
        scissorTest = glIsEnabled(GL_SCISSOR_TEST);
        blend = glIsEnabled(GL_BLEND);
        cullFace = glIsEnabled(GL_CULL_FACE);
        polygonOffsetFill = glIsEnabled(GL_POLYGON_OFFSET_FILL);
        lighting = glIsEnabled(GL_LIGHTING);

        glGetIntegerv(GL_STENCIL_WRITEMASK, &stencilWriteMask);
        glGetIntegerv(GL_STENCIL_BACK_WRITEMASK, &stencilBackWriteMask);
        glGetIntegerv(GL_STENCIL_FUNC, &stencilFunc);
        glGetIntegerv(GL_STENCIL_REF, &stencilRef);
        glGetIntegerv(GL_STENCIL_VALUE_MASK, &stencilValueMask);
        glGetIntegerv(GL_STENCIL_BACK_FUNC, &stencilBackFunc);
        glGetIntegerv(GL_STENCIL_BACK_REF, &stencilBackRef);
        glGetIntegerv(GL_STENCIL_BACK_VALUE_MASK, &stencilBackValueMask);
        glGetIntegerv(GL_STENCIL_FAIL, &stencilOp[0]);
        glGetIntegerv(GL_STENCIL_PASS_DEPTH_FAIL, &stencilOp[1]);
        glGetIntegerv(GL_STENCIL_PASS_DEPTH_PASS, &stencilOp[2]);
        glGetIntegerv(GL_STENCIL_BACK_FAIL, &stencilBackOp[0]);
        glGetIntegerv(GL_STENCIL_BACK_PASS_DEPTH_FAIL, &stencilBackOp[1]);
        glGetIntegerv(GL_STENCIL_BACK_PASS_DEPTH_PASS, &stencilBackOp[2]);
        glGetIntegerv(GL_STENCIL_CLEAR_VALUE, &stencilClearValue);

        glGetBooleanv(GL_COLOR_WRITEMASK, colorMask.data());
        glGetBooleanv(GL_DEPTH_WRITEMASK, &depthWriteMask);
        glGetIntegerv(GL_DEPTH_FUNC, &depthFunc);
        glGetIntegerv(GL_SCISSOR_BOX, scissorBox.data());

        glGetIntegerv(GL_BLEND_SRC_RGB, &blendSrcRgb);
        glGetIntegerv(GL_BLEND_DST_RGB, &blendDstRgb);
        glGetIntegerv(GL_BLEND_SRC_ALPHA, &blendSrcAlpha);
        glGetIntegerv(GL_BLEND_DST_ALPHA, &blendDstAlpha);

        glGetIntegerv(GL_FRONT_FACE, &frontFace);
        glGetFloatv(GL_POLYGON_OFFSET_FACTOR, &polygonOffsetFactor);
        glGetFloatv(GL_POLYGON_OFFSET_UNITS, &polygonOffsetUnits);

        // Baseline. Culling off is the belt to the SoShapeHints override's braces:
        // back faces that never rasterise never get counted, and the count is the
        // whole algorithm. The blend setup is needed because transparency type
        // NONE means Coin will not enable blending for the cap quad itself.
        glDisable(GL_CULL_FACE);
        glFrontFace(GL_CCW);

        // Lighting off, by hand rather than by leaving it to the SoLightModel in
        // quadRoot. Coin sends light-model changes lazily, and a fresh render
        // action assumes GL starts in its default state - so when the previous
        // traversal has left GL_LIGHTING enabled, the glDisable() that BASE_COLOR
        // implies is never issued, and the quad is lit instead. There is no light
        // in quadRoot, so all that survives is the ambient term and the cap comes
        // out nearly black. It only showed in the Shaded draw style, because the
        // other styles happen to draw unlit lines last and leave lighting off.
        // The cap is meant to be flat anyway: its shading is folded into the
        // colour before it ever reaches Coin.
        glDisable(GL_LIGHTING);

        glEnable(GL_BLEND);
        gl->glBlendFuncSeparate(
            GL_SRC_ALPHA,
            GL_ONE_MINUS_SRC_ALPHA,
            GL_SRC_ALPHA,
            GL_ONE_MINUS_SRC_ALPHA
        );
        glPolygonOffset(-1.0F, -1.0F);
        glEnable(GL_POLYGON_OFFSET_FILL);
        glClearStencil(0);
        gl->glStencilMaskSeparate(GL_FRONT_AND_BACK, 0xFFU);
    }

    ScopedCapGLState(const ScopedCapGLState&) = delete;
    ScopedCapGLState& operator=(const ScopedCapGLState&) = delete;

    ~ScopedCapGLState() noexcept
    {
        gl->glStencilFuncSeparate(
            GL_FRONT,
            static_cast<GLenum>(stencilFunc),
            stencilRef,
            static_cast<GLuint>(stencilValueMask)
        );
        gl->glStencilFuncSeparate(
            GL_BACK,
            static_cast<GLenum>(stencilBackFunc),
            stencilBackRef,
            static_cast<GLuint>(stencilBackValueMask)
        );
        gl->glStencilOpSeparate(
            GL_FRONT,
            static_cast<GLenum>(stencilOp[0]),
            static_cast<GLenum>(stencilOp[1]),
            static_cast<GLenum>(stencilOp[2])
        );
        gl->glStencilOpSeparate(
            GL_BACK,
            static_cast<GLenum>(stencilBackOp[0]),
            static_cast<GLenum>(stencilBackOp[1]),
            static_cast<GLenum>(stencilBackOp[2])
        );
        gl->glStencilMaskSeparate(GL_FRONT, static_cast<GLuint>(stencilWriteMask));
        gl->glStencilMaskSeparate(GL_BACK, static_cast<GLuint>(stencilBackWriteMask));
        glClearStencil(stencilClearValue);
        setEnabled(GL_STENCIL_TEST, stencilTest);

        glColorMask(colorMask[0], colorMask[1], colorMask[2], colorMask[3]);
        glDepthMask(depthWriteMask);
        glDepthFunc(static_cast<GLenum>(depthFunc));
        setEnabled(GL_DEPTH_TEST, depthTest);

        glScissor(
            scissorBox[0],
            scissorBox[1],
            static_cast<GLsizei>(scissorBox[2]),
            static_cast<GLsizei>(scissorBox[3])
        );
        setEnabled(GL_SCISSOR_TEST, scissorTest);

        gl->glBlendFuncSeparate(
            static_cast<GLenum>(blendSrcRgb),
            static_cast<GLenum>(blendDstRgb),
            static_cast<GLenum>(blendSrcAlpha),
            static_cast<GLenum>(blendDstAlpha)
        );
        setEnabled(GL_BLEND, blend);

        glFrontFace(static_cast<GLenum>(frontFace));
        setEnabled(GL_CULL_FACE, cullFace);
        setEnabled(GL_LIGHTING, lighting);

        glPolygonOffset(polygonOffsetFactor, polygonOffsetUnits);
        setEnabled(GL_POLYGON_OFFSET_FILL, polygonOffsetFill);
    }

private:
    static void setEnabled(GLenum capability, GLboolean wasEnabled)
    {
        if (wasEnabled == GL_TRUE) {
            glEnable(capability);
        }
        else {
            glDisable(capability);
        }
    }

    QOpenGLFunctions* gl;

    GLboolean stencilTest {GL_FALSE};
    GLboolean depthTest {GL_TRUE};
    GLboolean scissorTest {GL_FALSE};
    GLboolean blend {GL_FALSE};
    GLboolean cullFace {GL_FALSE};
    GLboolean lighting {GL_FALSE};
    GLboolean polygonOffsetFill {GL_FALSE};

    GLint stencilWriteMask {~0};
    GLint stencilBackWriteMask {~0};
    GLint stencilFunc {GL_ALWAYS};
    GLint stencilRef {0};
    GLint stencilValueMask {~0};
    GLint stencilBackFunc {GL_ALWAYS};
    GLint stencilBackRef {0};
    GLint stencilBackValueMask {~0};
    std::array<GLint, 3> stencilOp {GL_KEEP, GL_KEEP, GL_KEEP};
    std::array<GLint, 3> stencilBackOp {GL_KEEP, GL_KEEP, GL_KEEP};
    GLint stencilClearValue {0};

    std::array<GLboolean, 4> colorMask {GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE};
    GLboolean depthWriteMask {GL_TRUE};
    GLint depthFunc {GL_LESS};
    std::array<GLint, 4> scissorBox {};

    GLint blendSrcRgb {GL_ONE};
    GLint blendDstRgb {GL_ZERO};
    GLint blendSrcAlpha {GL_ONE};
    GLint blendDstAlpha {GL_ZERO};

    GLint frontFace {GL_CCW};
    GLfloat polygonOffsetFactor {0.0F};
    GLfloat polygonOffsetUnits {0.0F};
};

}  // namespace

ClippingCapRenderer::ClippingCapRenderer(View3DInventorViewer& viewer)
    : viewer(viewer)
{
    capAction = new SoGLRenderAction(viewer.getSoRenderManager()->getViewportRegion());
    // NONE, and not the viewer's mode: FreeCAD offers NONSOLID_SEPARATE_BACKFACE_PASS
    // as a preference, which draws non-solid transparent geometry twice. Two passes
    // over the same face would cancel out in the stencil and punch a hole in the cap.
    capAction->setTransparencyType(SoGLRenderAction::NONE);
    capAction->setTransparentDelayedObjectRenderType(SoGLRenderAction::ONE_PASS);
    // Sharing the cache context is not optional: a second context id means a second
    // set of display lists for the same geometry, and visible artefacts with it.
    // Same reasoning as View3DInventorViewer::init() and renderToFramebuffer().
    capAction->setCacheContext(viewer.getSoRenderManager()->getGLRenderAction()->getCacheContext());
    capAction->setSmoothing(false);
    capAction->setNumPasses(1);
    capAction->setAbortCallback(&ClippingCapRenderer::abortCallback, this);

    auto* elementSetup = new SoCallback();
    // The viewer outlives us - it owns us - so its address is safe as user data,
    // and the callback only reaches it through public accessors.
    elementSetup->setCallback(setupRenderElements, &this->viewer);

    stencilRoot = new SoSeparator();
    stencilRoot->ref();
    stencilRoot->setName("ClipCapStencilRoot");
    // Both graphs change every frame and are traversed once each; a cache would be
    // built and thrown away over and over.
    stencilRoot->renderCaching = SoSeparator::OFF;
    stencilRoot->boundingBoxCaching = SoSeparator::OFF;

    stencilRoot->addChild(elementSetup);
    // A placeholder until the first render() swaps in the live camera. Having the
    // camera inside the graph is what gives a standalone apply() the right view
    // volume and matrices.
    stencilRoot->addChild(new SoGroup());

    auto* stencilDepth = new SoDepthBuffer();
    stencilDepth->test = FALSE;
    stencilDepth->write = FALSE;
    stencilRoot->addChild(stencilDepth);

    // Overridden, so that a view provider further down cannot re-declare its
    // geometry SOLID and have Coin cull the back faces we need to count.
    // ViewProviderFemMesh does exactly that.
    auto* stencilHints = new SoShapeHints();
    stencilHints->shapeType = SoShapeHints::UNKNOWN_SHAPE_TYPE;
    stencilHints->vertexOrdering = SoShapeHints::COUNTERCLOCKWISE;
    stencilHints->faceType = SoShapeHints::UNKNOWN_FACE_TYPE;
    stencilHints->setOverride(TRUE);
    stencilRoot->addChild(stencilHints);

    // Wireframe and Points draw styles must still stencil solid faces, or the cap
    // would vanish the moment the user switched the display mode.
    auto* stencilStyle = new SoDrawStyle();
    stencilStyle->style = SoDrawStyle::FILLED;
    stencilStyle->setOverride(TRUE);
    stencilRoot->addChild(stencilStyle);

    // Colour writes are off in this pass, so lighting is pure cost.
    auto* stencilLight = new SoLightModel();
    stencilLight->model = SoLightModel::BASE_COLOR;
    stencilLight->setOverride(TRUE);
    stencilRoot->addChild(stencilLight);

    stencilClips = new SoGroup();
    stencilRoot->addChild(stencilClips);

    stencilTarget = new SoGroup();
    stencilTarget->setName("ClipCapStencilTarget");
    stencilRoot->addChild(stencilTarget);

    quadRoot = new SoSeparator();
    quadRoot->ref();
    quadRoot->setName("ClipCapQuadRoot");
    quadRoot->renderCaching = SoSeparator::OFF;
    quadRoot->boundingBoxCaching = SoSeparator::OFF;

    auto* quadElementSetup = new SoCallback();
    quadElementSetup->setCallback(setupRenderElements, &this->viewer);
    quadRoot->addChild(quadElementSetup);
    quadRoot->addChild(new SoGroup());

    auto* quadDepth = new SoDepthBuffer();
    quadDepth->test = TRUE;
    quadDepth->write = TRUE;
    // LEQUAL rather than LESS so that a tie against the cut edge goes to the cap.
    quadDepth->function = SoDepthBuffer::LEQUAL;
    quadRoot->addChild(quadDepth);

    auto* quadHints = new SoShapeHints();
    quadHints->shapeType = SoShapeHints::UNKNOWN_SHAPE_TYPE;
    quadHints->vertexOrdering = SoShapeHints::COUNTERCLOCKWISE;
    quadHints->faceType = SoShapeHints::CONVEX;
    quadRoot->addChild(quadHints);

    // Own node as well as the GL call in the guard: a SoPolygonOffset anywhere in
    // the stencil traversal would, on pop, push the element default back to GL and
    // switch the offset off again behind our back.
    auto* quadOffset = new SoPolygonOffset();
    quadOffset->factor = -1.0F;
    quadOffset->units = -1.0F;
    quadOffset->styles = SoPolygonOffset::FILLED;
    quadOffset->on = TRUE;
    quadRoot->addChild(quadOffset);

    // Unlit: the headlight term is folded into the colour instead, see render().
    auto* quadLight = new SoLightModel();
    quadLight->model = SoLightModel::BASE_COLOR;
    quadRoot->addChild(quadLight);

    quadClips = new SoGroup();
    quadRoot->addChild(quadClips);

    quadMaterial = new SoMaterial();
    quadRoot->addChild(quadMaterial);

    quadCoords = new SoCoordinate3();
    quadCoords->point.setNum(4);
    quadRoot->addChild(quadCoords);

    auto* quadFace = new SoFaceSet();
    quadFace->numVertices.setNum(1);
    quadFace->numVertices.set1Value(0, 4);
    quadRoot->addChild(quadFace);

    // One shadow plane per slot in each set, created up front so that render()
    // only ever writes field values and never restructures the graphs.
    for (int slot = 0; slot < ClipPlaneState::SlotCount; ++slot) {
        auto* stencilPlane = new SoClipPlane();
        stencilPlane->on = FALSE;
        stencilClips->addChild(stencilPlane);

        auto* quadPlane = new SoClipPlane();
        quadPlane->on = FALSE;
        quadClips->addChild(quadPlane);
    }
}

ClippingCapRenderer::~ClippingCapRenderer()
{
    objectCaches().erase(this);

    if (stencilTarget) {
        // Drop our references to the view provider roots before anything else, so
        // that they are never left parented to a graph that is going away.
        stencilTarget->removeAllChildren();
    }

    if (stencilRoot) {
        stencilRoot->unref();
        stencilRoot = nullptr;
    }
    if (quadRoot) {
        quadRoot->unref();
        quadRoot = nullptr;
    }

    stencilClips = nullptr;
    stencilTarget = nullptr;
    quadClips = nullptr;
    quadMaterial = nullptr;
    quadCoords = nullptr;
    attachedCamera = nullptr;

    delete capAction;
    capAction = nullptr;
}

void ClippingCapRenderer::invalidate()
{
    objectCaches().erase(this);
}

bool ClippingCapRenderer::isFilledSurfaceType(const SoType& type)
{
    static const std::vector<SoType> filledTypes = buildFilledSurfaceTypes();

    return std::any_of(filledTypes.begin(), filledTypes.end(), [&type](const SoType& filled) {
        return type.isDerivedFrom(filled);
    });
}

/**
 * Keeps everything but filled surfaces out of the stencil pass.
 *
 * Lines and points take the *front* face stencil operation whatever their winding,
 * so glStencilOpSeparate cannot protect us from them: every edge and vertex marker
 * drawn inside the silhouette would decrement the count and eat a hole out of the
 * cap. Coin's abort callback gets no node, so the node is recovered from the
 * action's current path.
 */
SoGLRenderAction::AbortCode ClippingCapRenderer::abortCallback(void* userData)
{
    auto* self = static_cast<ClippingCapRenderer*>(userData);
    if (!self->stencilPassActive) {
        return SoGLRenderAction::CONTINUE;
    }

    SoNode* node = self->capAction->getCurPathTail();
    if (!node) {
        return SoGLRenderAction::CONTINUE;
    }

    const SoType type = node->getTypeId();

    // Annotations are drawn on top of everything and are never part of a solid.
    if (type.isDerivedFrom(SoAnnotation::getClassTypeId())) {
        return SoGLRenderAction::PRUNE;
    }

    if (type.isDerivedFrom(SoShape::getClassTypeId()) && !isFilledSurfaceType(type)) {
        return SoGLRenderAction::PRUNE;
    }

    return SoGLRenderAction::CONTINUE;
}

void ClippingCapRenderer::render(const ClippingState& state)
{
    QOpenGLContext* context = QOpenGLContext::currentContext();
    if (!context) {
        return;
    }
    // Per call, not cached in a member: the context can change under us, and
    // glStencilOpSeparate and GL_INCR_WRAP are OpenGL 2.0 entry points that
    // opengl32.dll does not export on Windows.
    QOpenGLFunctions* gl = context->functions();
    if (!gl) {
        return;
    }

    // The viewer's stencilBits() describes the on-screen framebuffer, but this
    // also runs while an offscreen one is bound for a saved image, and that one
    // may have no stencil attachment. Without a stencil buffer the stencil test
    // silently always passes, so the quad would be painted across the whole
    // plane instead of only where the plane cuts material - a far worse result
    // than simply having no cap. Ask the framebuffer that is actually bound.
    if (boundStencilBits(gl) == 0) {
        static bool warned = false;
        if (!warned) {
            warned = true;
            Base::Console().warning(
                "Clipping caps skipped: the target framebuffer has no stencil buffer.\n"
            );
        }
        return;
    }

    // Defensive: a Base::MemoryException unwinding out of a stencil pass would
    // otherwise leave the abort callback pruning for good.
    stencilPassActive = false;

    SoRenderManager* manager = viewer.getSoRenderManager();
    SoCamera* camera = manager ? manager->getCamera() : nullptr;
    if (!camera) {
        return;
    }

    SoGroup* objectGroup = viewer.getObjectGroup();
    if (!objectGroup || objectGroup->getNumChildren() == 0) {
        return;
    }

    const SbBox3f sceneBox = viewer.getBoundingBox();
    if (sceneBox.isEmpty()) {
        return;
    }

    // Take the viewport from OpenGL, not from the render manager. This also runs
    // while an offscreen buffer is bound for a saved image, and that buffer's size
    // has nothing to do with the on-screen one - renderToFramebuffer() sets the GL
    // viewport itself. Projecting the stencil pass at the window's aspect ratio
    // would put the mark somewhere other than the geometry it is meant to mark,
    // and the cap would be painted across the picture.
    GLint glViewport[4] = {0, 0, 0, 0};
    glGetIntegerv(GL_VIEWPORT, glViewport);

    SbViewportRegion viewport = manager->getViewportRegion();
    if (glViewport[2] > 0 && glViewport[3] > 0) {
        viewport.setWindowSize(
            static_cast<short>(glViewport[0] + glViewport[2]),
            static_cast<short>(glViewport[1] + glViewport[3])
        );
        viewport.setViewportPixels(
            static_cast<short>(glViewport[0]),
            static_cast<short>(glViewport[1]),
            static_cast<short>(glViewport[2]),
            static_cast<short>(glViewport[3])
        );
    }
    capAction->setViewportRegion(viewport);
    // Cheap and idempotent, but setRenderCache() can replace the manager's action,
    // so re-read the id rather than trusting the one taken at construction.
    capAction->setCacheContext(manager->getGLRenderAction()->getCacheContext());

    if (camera != attachedCamera) {
        // Index 1 in both graphs, right after the element-setup callback.
        stencilRoot->replaceChild(1, camera);
        quadRoot->replaceChild(1, camera);
        attachedCamera = camera;
    }

    // Shadow planes, refreshed once for the whole frame. The live nodes in the
    // viewer's clip group are only ever read: writing a field on them twice a
    // frame would thrash the caches of the geometry underneath them.
    SoGroup* liveClips = viewer.getClipPlaneGroup();
    const bool liveUsable = liveClips != nullptr
        && liveClips->getNumChildren() == ClipPlaneState::SlotCount;

    std::array<SbPlane, ClipPlaneState::SlotCount> planes {};
    std::array<bool, ClipPlaneState::SlotCount> planeValid {};

    for (int slot = 0; slot < ClipPlaneState::SlotCount; ++slot) {
        const ClipPlaneState& plane = state.planes[slot];
        planeValid[slot] = false;

        if (plane.enabled) {
            SoNode* live = liveUsable ? liveClips->getChild(slot) : nullptr;
            if (live && live->isOfType(SoClipPlane::getClassTypeId())) {
                // Preferred: the node is the truth. It also carries the custom
                // slot's follow-the-view-direction aiming, which ViewClipping
                // applies to the node without writing it back into the state.
                planes[slot] = static_cast<SoClipPlane*>(live)->plane.getValue();
                planeValid[slot] = true;
            }
            else {
                SbVec3f normal(plane.normal[0], plane.normal[1], plane.normal[2]);
                auto offset = static_cast<float>(plane.offset);
                if (plane.flipped) {
                    normal = -normal;
                    offset = -offset;
                }
                if (normal.sqrLength() > 0.0F) {
                    planes[slot] = SbPlane(normal, offset);
                    planeValid[slot] = true;
                }
            }
        }

        auto* stencilPlane = static_cast<SoClipPlane*>(stencilClips->getChild(slot));
        auto* quadPlane = static_cast<SoClipPlane*>(quadClips->getChild(slot));
        if (planeValid[slot]) {
            stencilPlane->plane.setValue(planes[slot]);
            quadPlane->plane.setValue(planes[slot]);
        }
        // The quad pass runs with every active plane on and switches off just the
        // plane it is capping, in the loop below. The stencil pass is the other way
        // round: it needs exactly one plane, so they all start off here and the loop
        // switches on the single plane it is counting for. See the comment there.
        stencilPlane->on.setValue(FALSE);
        quadPlane->on.setValue(planeValid[slot]);
    }

    const std::vector<CapObject>& objects = collectObjects(this, viewer, sceneBox);
    if (objects.empty()) {
        return;
    }

    const long maxPasses = std::max(
        1L,
        App::GetApplication()
            .GetParameterGroupByPath("User parameter:BaseApp/Preferences/View")
            ->GetInt("ClipCapMaxPasses", defaultMaxCapPasses)
    );

    SbViewportRegion cameraViewport;
    const SbViewVolume viewVolume = camera->getViewVolume(viewport, cameraViewport);
    const SbMatrix viewProjection = viewVolume.getMatrix();
    const SbVec2s screenOrigin = cameraViewport.getViewportOriginPixels();
    const SbVec2s screenSize = cameraViewport.getViewportSizePixels();

    SbVec3f sceneMin;
    SbVec3f sceneMax;
    sceneBox.getBounds(sceneMin, sceneMax);
    const SbVec3f sceneCentre = (sceneMin + sceneMax) * 0.5F;
    const float quadRadius = capQuadRadiusFactor * (sceneMax - sceneMin).length();

    const SbVec3f viewDirection = viewer.getViewDirection();

    const ScopedCapGLState guard(gl);

    for (int slot = 0; slot < ClipPlaneState::SlotCount; ++slot) {
        const ClipPlaneState& plane = state.planes[slot];
        if (!plane.enabled || !plane.capEnabled || !planeValid[slot]) {
            continue;
        }

        const SbVec3f normal = planes[slot].getNormal();
        const float distance = planes[slot].getDistanceFromOrigin();

        std::vector<ActivePlane> otherPlanes;
        for (int other = 0; other < ClipPlaneState::SlotCount; ++other) {
            if (other != slot && planeValid[other]) {
                otherPlanes.push_back(
                    {planes[other].getNormal(), planes[other].getDistanceFromOrigin()}
                );
            }
        }

        const std::vector<CapPass> passes =
            buildPasses(objects, plane, normal, distance, otherPlanes, maxPasses);
        if (passes.empty()) {
            continue;
        }

        // The counting pass gets this plane and nothing else.
        //
        // The stencil count works because clipping opens exactly one hole in a
        // closed surface: a ray that crosses the hole is left with one unmatched
        // face and so counts non-zero, and every other ray balances out. A second
        // clip plane opens a second hole and destroys that argument in both
        // directions - a ray entering through one hole and leaving through the
        // other balances again, erasing the cap, while a ray crossing only the
        // other plane's hole counts non-zero and paints cap where there is no cut.
        // That is exactly the two-plane corruption: the real cut face empties out
        // and stray patches appear over the other plane's section.
        //
        // The other planes are not lost, they are applied to the quad instead: the
        // cap we want is (section of this plane through the solid) intersected with
        // (kept half-space of every other plane), and that is precisely the stencil
        // from this pass times the clipping on the quad below.
        auto* stencilPlane = static_cast<SoClipPlane*>(stencilClips->getChild(slot));
        stencilPlane->on.setValue(TRUE);

        // The quad lies exactly on this plane, so this plane has to be off while it
        // is drawn or it would clip itself away. Every other active plane stays on,
        // which is what makes caps clip one another correctly.
        auto* quadPlane = static_cast<SoClipPlane*>(quadClips->getChild(slot));
        quadPlane->on.setValue(FALSE);

        // A headlight-equivalent term folded into the colour, so a body-coloured cap
        // does not read brighter than the shaded body it is cut out of.
        const float ndotl = std::fabs(normal.dot(-viewDirection));
        const float shade = capShadeAmbient + capShadeDiffuse * ndotl;

        setQuadGeometry(quadCoords, sceneCentre, quadRadius, normal, distance);

        for (const CapPass& pass : passes) {
            std::array<GLint, 4> scissor {
                screenOrigin[0],
                screenOrigin[1],
                static_cast<GLint>(screenSize[0]),
                static_cast<GLint>(screenSize[1])
            };
            // Narrows the pass - and its stencil clear - to the pixels the group can
            // possibly cover. On failure the whole viewport stays in place, which is
            // what we want when the box crosses the eye plane and the projection
            // cannot be trusted: correct, only slower.
            static_cast<void>(
                projectBox(pass.box, viewProjection, screenOrigin, screenSize, scissor)
            );

            glEnable(GL_SCISSOR_TEST);
            glScissor(
                scissor[0],
                scissor[1],
                static_cast<GLsizei>(scissor[2]),
                static_cast<GLsizei>(scissor[3])
            );
            glClear(GL_STENCIL_BUFFER_BIT);

            glEnable(GL_STENCIL_TEST);
            glColorMask(GL_FALSE, GL_FALSE, GL_FALSE, GL_FALSE);
            gl->glStencilFuncSeparate(GL_FRONT_AND_BACK, GL_ALWAYS, 0, 0xFFU);
            gl->glStencilOpSeparate(GL_BACK, GL_KEEP, GL_KEEP, GL_INCR_WRAP);
            gl->glStencilOpSeparate(GL_FRONT, GL_KEEP, GL_KEEP, GL_DECR_WRAP);

            stencilTarget->removeAllChildren();
            for (SoNode* root : pass.roots) {
                stencilTarget->addChild(root);
            }

            capAction->setTransparencyType(SoGLRenderAction::NONE);
            stencilPassActive = true;
            capAction->apply(stencilRoot);
            stencilPassActive = false;

            // NOTEQUAL against zero, rather than EQUAL 1 or a parity test: the
            // result is then independent of winding order, and overlapping or
            // nested solids union instead of cancelling into holes.
            glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
            gl->glStencilFuncSeparate(GL_FRONT_AND_BACK, GL_NOTEQUAL, 0, 0xFFU);
            gl->glStencilOpSeparate(GL_FRONT_AND_BACK, GL_KEEP, GL_KEEP, GL_KEEP);

            quadMaterial->diffuseColor.setValue(
                SbColor(pass.color[0] * shade, pass.color[1] * shade, pass.color[2] * shade)
            );
            quadMaterial->transparency.setValue(std::clamp(plane.capTransparency, 0.0F, 1.0F));

            // BLEND for this pass only. Under NONE, Coin's lazy element actively
            // disables GL blending, which would throw capTransparency away; BLEND
            // is the non-delayed variant, so the single quad is still drawn inline
            // and exactly once.
            capAction->setTransparencyType(SoGLRenderAction::BLEND);
            capAction->apply(quadRoot);

            glDisable(GL_STENCIL_TEST);
            stencilTarget->removeAllChildren();
        }

        quadPlane->on.setValue(TRUE);
        stencilPlane->on.setValue(FALSE);
    }
}
