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

#include "ClippingState.h"

#include <Base/Persistence.h>
#include <Base/Reader.h>
#include <Base/StringUtils.h>
#include <Base/Writer.h>

#include <algorithm>
#include <charconv>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <ostream>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>

using namespace Gui;

namespace
{

/**
 * Read a floating point attribute, keeping @a fallback unless the file holds a
 * finite number.
 *
 * A NaN or an infinity must never reach an SoClipPlane: Coin accepts it without
 * complaint and the view then renders empty, which reads as a FreeCAD bug rather
 * than as a damaged document.  Base::StringUtils is used instead of
 * XMLReader::getAttribute<double>() because the latter goes through std::stod,
 * which throws on a value that is not a number at all.
 */
double readFinite(const Base::XMLReader& reader, const char* name, double fallback)
{
    if (!reader.hasAttribute(name)) {
        return fallback;
    }

    double value {};
    if (!Base::StringUtils::parseDouble(reader.getAttribute<const char*>(name), value)
        || !std::isfinite(value)) {
        return fallback;
    }
    return value;
}

/**
 * Read an unsigned integer attribute, keeping @a fallback unless the whole
 * attribute is a plain decimal number.
 *
 * Same reasoning as readFinite(): a damaged clipping element should cost the
 * user their clipping planes, not the rest of the document.
 */
unsigned long long readUnsigned(const Base::XMLReader& reader,
                                const char* name,
                                unsigned long long fallback)
{
    if (!reader.hasAttribute(name)) {
        return fallback;
    }

    const std::string_view text = reader.getAttribute<const char*>(name);
    const char* const last = text.data() + text.size();

    unsigned long long value {};
    const auto [end, error] = std::from_chars(text.data(), last, value);
    if (error != std::errc {} || end != last) {
        return fallback;
    }
    return value;
}

}  // namespace

bool ClippingState::isDefault() const
{
    // Deliberately not `!anyPlaneEnabled() && !anyCapEnabled()`: a cap ticked on a
    // plane that is currently switched off is still a choice the user made, and
    // dropping it on save would surprise them the next time they tick the plane.
    return std::ranges::none_of(planes, [](const ClipPlaneState& plane) {
        return plane.enabled || plane.capEnabled;
    });
}

bool ClippingState::anyCapEnabled() const
{
    return std::ranges::any_of(planes, [](const ClipPlaneState& plane) {
        return plane.enabled && plane.capEnabled;
    });
}

bool ClippingState::anyPlaneEnabled() const
{
    return std::ranges::any_of(planes, [](const ClipPlaneState& plane) {
        return plane.enabled;
    });
}

void ClippingState::save(Base::Writer& writer) const
{
    writer.Stream() << writer.ind() << "<Clipping Count=\"" << planes.size() << "\">" << std::endl;

    writer.incInd();
    for (std::size_t slot = 0; slot < planes.size(); ++slot) {
        const ClipPlaneState& plane = planes[slot];

        // Base::StringUtils::formatDouble() is the shortest text that parses back
        // to the very same value, so an offset survives the round trip bit for
        // bit - the stream's own default precision would round it to six digits.
        writer.Stream() << writer.ind() << "<Plane slot=\"" << slot << "\""
                        << " enabled=\"" << (plane.enabled ? 1 : 0) << "\""
                        << " flipped=\"" << (plane.flipped ? 1 : 0) << "\""
                        << " nx=\"" << Base::StringUtils::formatDouble(plane.normal[0]) << "\""
                        << " ny=\"" << Base::StringUtils::formatDouble(plane.normal[1]) << "\""
                        << " nz=\"" << Base::StringUtils::formatDouble(plane.normal[2]) << "\""
                        << " offset=\"" << Base::StringUtils::formatDouble(plane.offset) << "\""
                        << " followView=\"" << (plane.followViewDir ? 1 : 0) << "\""
                        << " cap=\"" << (plane.capEnabled ? 1 : 0) << "\""
                        << " capMode=\"" << static_cast<int>(plane.capColorMode) << "\""
                        << " capColor=\"" << plane.capColor << "\""
                        << " capTransparency=\""
                        << Base::StringUtils::formatDouble(plane.capTransparency) << "\"/>"
                        << std::endl;
    }
    writer.decInd();

    writer.Stream() << writer.ind() << "</Clipping>" << std::endl;
}

void ClippingState::restore(Base::XMLReader& reader)
{
    // Whatever the file leaves out must come back as the documented default, not
    // as whatever this instance happened to be holding.
    planes = {};

    // Only the slots this build knows about can be applied.  A file from a newer
    // FreeCAD listing more of them is read as far as it makes sense; the surplus
    // is skipped by readEndElement() at the bottom.
    const auto count = static_cast<std::size_t>(
        std::min<unsigned long long>(readUnsigned(reader, "Count", 0), planes.size())
    );

    for (std::size_t i = 0; i < count; ++i) {
        reader.readElement("Plane");

        // An out-of-range slot is dropped rather than clamped: folding it into a
        // neighbouring slot would silently move one of the user's planes.
        const unsigned long long slot = readUnsigned(reader, "slot", planes.size());
        if (slot >= planes.size()) {
            continue;
        }
        ClipPlaneState& plane = planes[static_cast<std::size_t>(slot)];

        plane.enabled = reader.getAttribute<bool>("enabled", plane.enabled);
        plane.flipped = reader.getAttribute<bool>("flipped", plane.flipped);
        plane.offset = readFinite(reader, "offset", plane.offset);
        plane.followViewDir = reader.getAttribute<bool>("followView", plane.followViewDir);
        plane.capEnabled = reader.getAttribute<bool>("cap", plane.capEnabled);

        // Keep the normal unit length, but only when the file actually names a
        // direction: a zero-length normal has no plane equation, and one built
        // from huge components would overflow the sum of squares to infinity and
        // normalise to zero.
        const double nx = readFinite(reader, "nx", plane.normal[0]);
        const double ny = readFinite(reader, "ny", plane.normal[1]);
        const double nz = readFinite(reader, "nz", plane.normal[2]);
        const double length = std::sqrt((nx * nx) + (ny * ny) + (nz * nz));
        if (std::isfinite(length) && length > 0.0) {
            plane.normal[0] = static_cast<float>(nx / length);
            plane.normal[1] = static_cast<float>(ny / length);
            plane.normal[2] = static_cast<float>(nz / length);
        }

        const unsigned long long mode =
            readUnsigned(reader, "capMode", static_cast<unsigned long long>(plane.capColorMode));
        if (mode <= static_cast<unsigned long long>(CapColorMode::CustomColor)) {
            plane.capColorMode = static_cast<CapColorMode>(mode);
        }

        const unsigned long long color = readUnsigned(reader, "capColor", plane.capColor);
        if (color <= std::numeric_limits<uint32_t>::max()) {
            plane.capColor = static_cast<uint32_t>(color);
        }

        // The cut object's transparency is replaced by this value, so a figure
        // outside 0..1 would not merely look odd, it would render unpredictably.
        plane.capTransparency = static_cast<float>(
            std::clamp(readFinite(reader, "capTransparency", plane.capTransparency), 0.0, 1.0)
        );
    }

    reader.readEndElement("Clipping");
}

void Gui::saveNamedClippingViews(const NamedClippingViews& views, Base::Writer& writer)
{
    // Nothing at all rather than an empty <ClippingViews/>: a document that has
    // never had a named view must still save byte for byte the way it did before
    // this element existed, so the feature costs an untouched file no diff and an
    // older FreeCAD nothing to trip over.
    if (views.empty()) {
        return;
    }

    writer.Stream() << writer.ind() << "<ClippingViews Count=\"" << views.size() << "\">"
                    << std::endl;

    writer.incInd();
    for (const NamedClippingView& view : views) {
        // The name is text the user typed, so it can hold any of the characters
        // XML reserves; Base::Persistence::encodeAttribute() is what the rest of
        // the document already uses to escape an attribute value.
        writer.Stream() << writer.ind() << "<View name=\""
                        << Base::Persistence::encodeAttribute(view.name) << "\">" << std::endl;

        // Each view wraps exactly one <Clipping> written by ClippingState::save(),
        // so a stored view and the active clipping share a single serialisation:
        // whatever is added to a plane later is written and read in both places
        // without this function having to learn about it.
        writer.incInd();
        view.state.save(writer);
        writer.decInd();

        writer.Stream() << writer.ind() << "</View>" << std::endl;
    }
    writer.decInd();

    writer.Stream() << writer.ind() << "</ClippingViews>" << std::endl;
}

void Gui::restoreNamedClippingViews(NamedClippingViews& views, Base::XMLReader& reader)
{
    // As in ClippingState::restore(): what the file does not hold must not be
    // left over from whatever this vector was carrying.
    views.clear();

    // A corrupt Count must not become a loop that reads past </Document>.
    // readElement() throws once it runs off the end, which would cost the user
    // the whole GuiDocument.xml rather than just its named views, so the count is
    // capped well above any list a person would build and well below anything
    // that could keep the parser busy.
    constexpr unsigned long long maxViews = 1000;
    const auto count = static_cast<std::size_t>(
        std::min<unsigned long long>(readUnsigned(reader, "Count", 0), maxViews)
    );

    // The level of <ClippingViews> itself, used below to tell a <View> that has
    // been entered from one that was already over when it was found.
    const int viewsLevel = reader.level();

    views.reserve(count);
    for (std::size_t i = 0; i < count; ++i) {
        reader.readElement("View");

        // getAttribute<const char*>() throws when the attribute is absent, so a
        // <View> that lost its name is asked about rather than caught.
        std::string name;
        if (reader.hasAttribute("name")) {
            name = reader.getAttribute<const char*>("name");
        }

        // An empty <View/> is already over by the time readElement() returns -
        // XMLReader has put the level back - so there is nothing inside it to
        // read and no end element left to read either. Everything below is
        // therefore conditional on having actually entered the element.
        const bool entered = reader.level() > viewsLevel;

        // The view's <Clipping> is reached with readNextElement() rather than
        // readElement(): readElement() scans forward, so a damaged <View> that
        // has lost its clipping would run on and swallow the next view's element
        // on its way to finding one. Stepping a single element instead keeps a
        // corrupt view's cost to that view alone, which is the same bargain the
        // rest of this file strikes. ClippingState::restore() consumes its own
        // </Clipping>; the readEndElement("View") below resynchronises when there
        // was nothing to restore.
        ClippingState state;
        if (entered && reader.readNextElement() && strcmp(reader.localName(), "Clipping") == 0) {
            state.restore(reader);
        }

        // A nameless view could not be told from its neighbours in the panel and
        // could not be recalled, so it is dropped rather than stored under an
        // invented name. Its element is still read in full, which is what keeps
        // the reader in step with the rest of the file.
        if (!name.empty()) {
            views.push_back(NamedClippingView {std::move(name), state});
        }

        if (entered) {
            reader.readEndElement("View");
        }
    }

    reader.readEndElement("ClippingViews");
}
