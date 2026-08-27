// SPDX-License-Identifier: LGPL-2.1-or-later

#include <gtest/gtest.h>

#include <cmath>
#include <sstream>
#include <string>

#include <xercesc/util/PlatformUtils.hpp>

#include <FCConfig.h>

#include <Base/Reader.h>
#include <Base/Writer.h>
#include <Gui/ClippingState.h>

using Gui::CapColorMode;
using Gui::ClipPlaneState;
using Gui::ClippingState;

namespace
{

/// Wrap a fragment in a minimal document so Base::XMLReader has something to parse.
std::string asDocument(const std::string& fragment)
{
    return "<?xml version='1.0' encoding='utf-8'?>\n<Document>\n" + fragment + "\n</Document>\n";
}

/// Restore from a hand-written `<Clipping>` element, entering restore() the way
/// Gui::Document does: positioned on the start element.
ClippingState restoreFrom(const std::string& fragment)
{
    std::string xml = asDocument(fragment);
    std::stringstream stream(xml);
    Base::XMLReader reader("Document.xml", stream);
    reader.readElement("Clipping");

    ClippingState restored;
    restored.restore(reader);
    return restored;
}

std::string saveToString(const ClippingState& state)
{
    Base::StringWriter writer;
    state.save(writer);
    return writer.getString();
}

ClippingState roundTrip(const ClippingState& state)
{
    return restoreFrom(saveToString(state));
}

/// A plane whose normal is already unit length, so save/restore is an identity.
ClipPlaneState axisPlane(int axis)
{
    ClipPlaneState plane;
    plane.normal[0] = axis == 0 ? 1.0F : 0.0F;
    plane.normal[1] = axis == 1 ? 1.0F : 0.0F;
    plane.normal[2] = axis == 2 ? 1.0F : 0.0F;
    return plane;
}

}  // namespace

class ClippingStateTest: public ::testing::Test
{
protected:
    static void SetUpTestSuite()
    {
        // Base::XMLReader builds a Xerces parser in its constructor. Initialize() is
        // reference counted, so it is safe even when another test already ran it.
        XERCES_CPP_NAMESPACE::XMLPlatformUtils::Initialize();
    }
};

TEST_F(ClippingStateTest, defaultStateIsDefault)
{
    const ClippingState state;

    EXPECT_TRUE(state.isDefault());
    EXPECT_FALSE(state.anyPlaneEnabled());
    EXPECT_FALSE(state.anyCapEnabled());
}

TEST_F(ClippingStateTest, defaultStateRoundTrips)
{
    const ClippingState state;
    const ClippingState restored = roundTrip(state);

    EXPECT_EQ(restored, state);
    EXPECT_TRUE(restored.isDefault());
}

TEST_F(ClippingStateTest, savesOneElementPerSlot)
{
    const std::string xml = saveToString(ClippingState {});

    EXPECT_NE(xml.find("<Clipping Count=\"4\">"), std::string::npos);
    EXPECT_NE(xml.find("slot=\"0\""), std::string::npos);
    EXPECT_NE(xml.find("slot=\"3\""), std::string::npos);
    EXPECT_NE(xml.find("</Clipping>"), std::string::npos);
}

TEST_F(ClippingStateTest, allPlanesRoundTrip)
{
    ClippingState state;

    state.planes[ClipPlaneState::SlotX] = axisPlane(0);
    state.planes[ClipPlaneState::SlotX].enabled = true;
    state.planes[ClipPlaneState::SlotX].offset = 12.5;
    state.planes[ClipPlaneState::SlotX].capEnabled = true;
    state.planes[ClipPlaneState::SlotX].capColorMode = CapColorMode::CustomColor;
    state.planes[ClipPlaneState::SlotX].capColor = 0xff8000ffU;
    state.planes[ClipPlaneState::SlotX].capTransparency = 0.25F;

    state.planes[ClipPlaneState::SlotY] = axisPlane(1);
    state.planes[ClipPlaneState::SlotY].enabled = true;
    state.planes[ClipPlaneState::SlotY].flipped = true;
    state.planes[ClipPlaneState::SlotY].offset = -3.75;
    state.planes[ClipPlaneState::SlotY].capEnabled = true;
    state.planes[ClipPlaneState::SlotY].capColorMode = CapColorMode::BodyColor;
    state.planes[ClipPlaneState::SlotY].capTransparency = 0.3F;

    state.planes[ClipPlaneState::SlotZ] = axisPlane(2);
    state.planes[ClipPlaneState::SlotZ].enabled = true;
    state.planes[ClipPlaneState::SlotZ].flipped = true;
    state.planes[ClipPlaneState::SlotZ].offset = 0.1;
    state.planes[ClipPlaneState::SlotZ].capColor = 0x00000001U;

    state.planes[ClipPlaneState::SlotCustom] = axisPlane(2);
    state.planes[ClipPlaneState::SlotCustom].normal[2] = -1.0F;
    state.planes[ClipPlaneState::SlotCustom].enabled = true;
    state.planes[ClipPlaneState::SlotCustom].followViewDir = true;
    state.planes[ClipPlaneState::SlotCustom].offset = 1234.5678;
    state.planes[ClipPlaneState::SlotCustom].capEnabled = true;
    state.planes[ClipPlaneState::SlotCustom].capColorMode = CapColorMode::CustomColor;
    state.planes[ClipPlaneState::SlotCustom].capColor = 0xffffffffU;
    state.planes[ClipPlaneState::SlotCustom].capTransparency = 1.0F;

    EXPECT_EQ(roundTrip(state), state);
}

TEST_F(ClippingStateTest, extremeOffsetsRoundTrip)
{
    ClippingState state;
    state.planes[ClipPlaneState::SlotX] = axisPlane(0);
    state.planes[ClipPlaneState::SlotX].enabled = true;
    state.planes[ClipPlaneState::SlotX].offset = 1e7;
    state.planes[ClipPlaneState::SlotY] = axisPlane(1);
    state.planes[ClipPlaneState::SlotY].enabled = true;
    state.planes[ClipPlaneState::SlotY].offset = -1e-4;

    const ClippingState restored = roundTrip(state);

    EXPECT_EQ(restored, state);
    EXPECT_DOUBLE_EQ(restored.planes[ClipPlaneState::SlotX].offset, 1e7);
    EXPECT_DOUBLE_EQ(restored.planes[ClipPlaneState::SlotY].offset, -1e-4);
}

TEST_F(ClippingStateTest, nonFiniteAttributesFallBackToDefaults)
{
    const ClippingState restored = restoreFrom(
        "<Clipping Count=\"2\">\n"
        "  <Plane slot=\"0\" enabled=\"1\" nx=\"nan\" ny=\"0\" nz=\"0\" offset=\"nan\"/>\n"
        "  <Plane slot=\"1\" enabled=\"1\" nx=\"0\" ny=\"inf\" nz=\"0\" offset=\"-inf\"/>\n"
        "</Clipping>"
    );

    for (const auto slot : {ClipPlaneState::SlotX, ClipPlaneState::SlotY}) {
        const ClipPlaneState& plane = restored.planes[slot];
        EXPECT_TRUE(plane.enabled);
        EXPECT_TRUE(std::isfinite(plane.offset));
        EXPECT_DOUBLE_EQ(plane.offset, 0.0);
        EXPECT_TRUE(std::isfinite(plane.normal[0]));
        EXPECT_TRUE(std::isfinite(plane.normal[1]));
        EXPECT_TRUE(std::isfinite(plane.normal[2]));
    }
}

TEST_F(ClippingStateTest, garbageNumbersFallBackToDefaults)
{
    const ClippingState restored = restoreFrom(
        "<Clipping Count=\"2\">\n"
        "  <Plane slot=\"0\" enabled=\"1\" offset=\"twelve\" capMode=\"77\""
        " capColor=\"99999999999\" capTransparency=\"5\"/>\n"
        "  <Plane slot=\"1\" enabled=\"1\" offset=\"1e999\"/>\n"
        "</Clipping>"
    );

    const ClipPlaneState defaults;

    const ClipPlaneState& plane = restored.planes[ClipPlaneState::SlotX];
    EXPECT_TRUE(plane.enabled);
    EXPECT_DOUBLE_EQ(plane.offset, defaults.offset);
    EXPECT_EQ(plane.capColorMode, defaults.capColorMode);
    EXPECT_EQ(plane.capColor, defaults.capColor);
    // Out of range rather than unreadable, so it is pulled back into 0..1.
    EXPECT_FLOAT_EQ(plane.capTransparency, 1.0F);

    // An offset that overflows a double must not come back as HUGE_VAL.
    const ClipPlaneState& overflowed = restored.planes[ClipPlaneState::SlotY];
    EXPECT_TRUE(std::isfinite(overflowed.offset));
}

TEST_F(ClippingStateTest, outOfRangeSlotIsIgnored)
{
    const ClippingState restored = restoreFrom(
        "<Clipping Count=\"2\">\n"
        "  <Plane slot=\"9\" enabled=\"1\" cap=\"1\" offset=\"5\"/>\n"
        "  <Plane slot=\"4\" enabled=\"1\" cap=\"1\" offset=\"5\"/>\n"
        "</Clipping>"
    );

    EXPECT_TRUE(restored.isDefault());
    EXPECT_EQ(restored, ClippingState {});
}

TEST_F(ClippingStateTest, missingAttributesKeepDefaults)
{
    const ClippingState restored = restoreFrom(
        "<Clipping Count=\"1\">\n"
        "  <Plane slot=\"2\" enabled=\"1\"/>\n"
        "</Clipping>"
    );

    ClippingState expected;
    expected.planes[ClipPlaneState::SlotZ].enabled = true;

    EXPECT_EQ(restored, expected);
}

TEST_F(ClippingStateTest, unnormalisedNormalIsNormalised)
{
    const ClippingState restored = restoreFrom(
        "<Clipping Count=\"1\">\n"
        "  <Plane slot=\"0\" enabled=\"1\" nx=\"3\" ny=\"4\" nz=\"0\"/>\n"
        "</Clipping>"
    );

    const ClipPlaneState& plane = restored.planes[ClipPlaneState::SlotX];
    EXPECT_NEAR(plane.normal[0], 0.6F, 1e-6);
    EXPECT_NEAR(plane.normal[1], 0.8F, 1e-6);
    EXPECT_NEAR(plane.normal[2], 0.0F, 1e-6);
}

TEST_F(ClippingStateTest, degenerateNormalKeepsDefault)
{
    const ClippingState restored = restoreFrom(
        "<Clipping Count=\"1\">\n"
        "  <Plane slot=\"0\" enabled=\"1\" nx=\"0\" ny=\"0\" nz=\"0\"/>\n"
        "</Clipping>"
    );

    const ClipPlaneState defaults;
    const ClipPlaneState& plane = restored.planes[ClipPlaneState::SlotX];
    EXPECT_FLOAT_EQ(plane.normal[0], defaults.normal[0]);
    EXPECT_FLOAT_EQ(plane.normal[1], defaults.normal[1]);
    EXPECT_FLOAT_EQ(plane.normal[2], defaults.normal[2]);
}

TEST_F(ClippingStateTest, restoreClearsPreviousContent)
{
    ClippingState state;
    state.planes[ClipPlaneState::SlotX].enabled = true;
    state.planes[ClipPlaneState::SlotX].offset = 42.0;

    const std::string xml = asDocument("<Clipping Count=\"0\"/>");
    std::stringstream stream(xml);
    Base::XMLReader reader("Document.xml", stream);
    reader.readElement("Clipping");
    state.restore(reader);

    EXPECT_EQ(state, ClippingState {});
}

TEST_F(ClippingStateTest, enabledFlagsTruthTable)
{
    // A plane that is off but carries a cap is still non-default: the user made
    // that choice and it has to survive a save.
    ClippingState capOnDisabledPlane;
    capOnDisabledPlane.planes[ClipPlaneState::SlotX].capEnabled = true;
    EXPECT_FALSE(capOnDisabledPlane.anyPlaneEnabled());
    EXPECT_FALSE(capOnDisabledPlane.anyCapEnabled());
    EXPECT_FALSE(capOnDisabledPlane.isDefault());

    ClippingState planeOnly;
    planeOnly.planes[ClipPlaneState::SlotY].enabled = true;
    EXPECT_TRUE(planeOnly.anyPlaneEnabled());
    EXPECT_FALSE(planeOnly.anyCapEnabled());
    EXPECT_FALSE(planeOnly.isDefault());

    ClippingState planeAndCap;
    planeAndCap.planes[ClipPlaneState::SlotCustom].enabled = true;
    planeAndCap.planes[ClipPlaneState::SlotCustom].capEnabled = true;
    EXPECT_TRUE(planeAndCap.anyPlaneEnabled());
    EXPECT_TRUE(planeAndCap.anyCapEnabled());
    EXPECT_FALSE(planeAndCap.isDefault());
}
