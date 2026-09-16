#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
This module covers tests of the volume functions of Denon AVR receivers.

:copyright: (c) 2016 by Oliver Goetz.
:license: MIT, see LICENSE for more details.
"""

from unittest import mock

import pytest
from pytest_httpx import HTTPXMock

from denonavr.appcommand import AppCommands
from denonavr.const import (
    MAIN_ZONE,
    ZONE2,
    ZONE2_TELNET_COMMANDS,
    ZONE2_URLS,
    ZONE3,
    ZONE3_TELNET_COMMANDS,
    ZONE3_URLS,
)
from denonavr.exceptions import AvrCommandError
from denonavr.volume import (
    DenonAVRVolume,
    convert_appcommand_level,
    convert_max_volume,
    convert_telnet_volume,
)


def get_sample_content(filename):
    """Return sample content form file."""
    with open(f"tests/xml/{filename}", encoding="utf-8") as file:
        return file.read()


def volume_instance() -> DenonAVRVolume:
    """Return a volume instance that is ready to be updated."""
    volume = DenonAVRVolume()
    # pylint: disable=protected-access
    volume._device.use_avr_2016_update = True
    return volume


class TestSubwooferLevelsAdjustment:
    """Test case for the subwoofer levels adjustment flag."""

    @pytest.mark.parametrize(
        "value,expected",
        [("0", False), ("1", True), (0, False), (1, True), (False, False)],
    )
    def test_converted_from_xml_value(self, value, expected):
        """Check that a value written from XML is converted, not stored raw."""
        volume = DenonAVRVolume()
        # pylint: disable=protected-access
        volume._subwoofer_levels_adjustment = value
        assert volume._subwoofer_levels_adjustment is expected

    def test_disabled_adjustment_hides_levels(self):
        """Check that levels are reported unknown while adjustment is off."""
        volume = DenonAVRVolume()
        # pylint: disable=protected-access
        volume._subwoofer_levels = {"Subwoofer": 0.0}
        volume._subwoofer_levels_adjustment = "0"
        assert volume.subwoofer_levels is None


class TestConvertAppCommandLevel:
    """Test case for the level scale of the AppCommand interface."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            # Deviceinfo.xml declares 0 to 48, default 24, step 0.5 for both
            # the channel level and the subwoofer level menu
            ("0", -12.0),
            ("4", -10.0),
            ("18", -3.0),
            ("24", 0.0),
            ("48", 12.0),
            (24, 0.0),
            # An unreadable level comes back as an empty tag
            ("", None),
            ("  ", None),
        ],
    )
    def test_level_values(self, value, expected):
        """Check that a level is converted to dB and an empty one to None."""
        assert convert_appcommand_level(value) == expected

    def test_the_telnet_scale_is_a_different_one(self):
        """Check that the two scales are not accidentally interchangeable."""
        # 50 is 0.0 dB over telnet and +13.0 dB here, so a value read on one
        # scale and reported on the other is wrong rather than merely offset
        assert convert_appcommand_level("50") != 0.0


class TestSubwooferLevelUpdate:
    """Test case for reading the subwoofer level from AppCommand.xml."""

    @pytest.mark.asyncio
    async def test_readable_level_is_converted(self, httpx_mock: HTTPXMock):
        """Check that a level read while audio plays lands on the property."""
        httpx_mock.add_response(
            content=get_sample_content(
                "AVR-X1700H-AppCommand-subwooferlevel-playing.xml"
            )
        )
        volume = volume_instance()
        await volume.async_update_attrs_appcommand(
            {AppCommands.GetSubwooferLevel: None}
        )

        # The receiver reports both, and sw1level agrees with the conversion
        # of sw1value that the library does
        assert volume.subwoofer_levels == {"Subwoofer": -10.0}
        assert volume.subwoofer_level("Subwoofer") == -10.0

    @pytest.mark.asyncio
    async def test_unreadable_level_is_unknown(self, httpx_mock: HTTPXMock):
        """Check that an idle receiver reports unknown rather than 0.0 dB."""
        httpx_mock.add_response(
            content=get_sample_content("AVR-X1700H-AppCommand-subwooferlevel-idle.xml")
        )
        volume = volume_instance()
        await volume.async_update_attrs_appcommand(
            {AppCommands.GetSubwooferLevel: None}
        )

        assert volume.subwoofer_levels is None


class TestChannelLevelUpdate:
    """Test case for reading the channel levels from AppCommand.xml."""

    @pytest.mark.asyncio
    async def test_readable_levels_are_converted(self, httpx_mock: HTTPXMock):
        """Check that the channels a playing receiver reports are picked up."""
        httpx_mock.add_response(
            content=get_sample_content("AVR-X1700H-AppCommand-chlevel-playing.xml")
        )
        volume = volume_instance()
        await volume.async_update_channel_levels()

        # A 3.1 layout, upmixed to Dolby Surround by the incoming signal
        assert volume.channel_volumes == {
            "Center": 0.0,
            "Subwoofer": 0.0,
            "Front Left": 0.0,
            "Front Right": 0.0,
        }
        assert volume.channel_volume("Front Left") == 0.0

    @pytest.mark.asyncio
    async def test_idle_levels_are_unknown(self, httpx_mock: HTTPXMock):
        """Check that an idle receiver reports unknown rather than 0.0 dB."""
        # Front left and front right still carry a status of 1 and a value of
        # 24 here, under a top level status of 0
        httpx_mock.add_response(
            content=get_sample_content("AVR-X1700H-AppCommand-chlevel-idle.xml")
        )
        volume = volume_instance()
        await volume.async_update_channel_levels()

        assert volume.channel_volumes is None

    @pytest.mark.asyncio
    async def test_standby_levels_are_unknown(self, httpx_mock: HTTPXMock):
        """Check that a receiver in standby does not report a level."""
        # Standby exposes more than idle does: the subwoofer joins front left
        # and front right in reporting a populated value, still under a top
        # level status of 0
        httpx_mock.add_response(
            content=get_sample_content("AVR-X1700H-AppCommand-chlevel-standby.xml")
        )
        volume = volume_instance()
        await volume.async_update_channel_levels()

        assert volume.channel_volumes is None

    @pytest.mark.asyncio
    async def test_an_unsupported_command_is_not_an_error(self, httpx_mock: HTTPXMock):
        """Check that a receiver which answers an error leaves the levels alone."""
        httpx_mock.add_response(
            content='<?xml version="1.0"?><rx><error>2</error></rx>'
        )
        volume = volume_instance()
        await volume.async_update_channel_levels()

        assert volume.channel_volumes is None


class TestUnknownLevels:
    """Test case for asking for a level the receiver has not reported."""

    def test_unknown_channel_reads_as_none(self):
        """Check that a channel outside the reported set does not raise."""
        volume = DenonAVRVolume()
        # pylint: disable=protected-access
        volume._channel_volumes_appcommand = {"Front Left": 0.0}
        assert volume.channel_volume("Surround Left") is None

    def test_unknown_subwoofer_reads_as_none(self):
        """Check that a subwoofer outside the reported set does not raise."""
        volume = DenonAVRVolume()
        # pylint: disable=protected-access
        volume._subwoofer_level_status = "1"
        volume._subwoofer1_value = "24"
        assert volume.subwoofer_level("Subwoofer 2") is None

    def test_an_invalid_channel_still_raises(self):
        """Check that a name that is not a channel at all is still rejected."""
        volume = DenonAVRVolume()
        with pytest.raises(AvrCommandError):
            volume.channel_volume("Not A Channel")


class TestLevelUpdateCost:
    """Test case for what reading the levels costs on a global update."""

    # Not a capture: the four blocks a global update of the volume module on
    # its own asks for, in the order it registers them
    GLOBAL_UPDATE = """<?xml version="1.0" encoding="utf-8" ?>
<rx>
<cmd><zone1><volume>-40.0</volume><limit>OFF</limit></zone1></cmd>
<cmd><zone1>off</zone1></cmd>
<cmd>
<status>1</status>
<sw1status>1</sw1status>
<sw1dispname>Subwoofer 1</sw1dispname>
<sw1level>-10.0dB</sw1level>
<sw1value>4</sw1value>
</cmd>
<cmd>
<status>1</status>
<chlists>
<ch>
<name>FL</name>
<status>1</status>
<sptype>1</sptype>
<level>-1.0dB</level>
<value>22</value>
</ch>
</chlists>
</cmd>
</rx>
"""

    @pytest.mark.asyncio
    async def test_both_levels_ride_the_existing_request(self, httpx_mock: HTTPXMock):
        """Check that neither level costs a request of its own."""
        # GetChLevel is parsed outside the response pattern mechanism, so it
        # asks for the global response a second time. async_post is cached on
        # the cache_id of the update in progress, so that is a cache hit
        httpx_mock.add_response(content=self.GLOBAL_UPDATE)
        volume = volume_instance()
        await volume.async_update(global_update=True, cache_id="a global update")

        assert len(httpx_mock.get_requests()) == 1
        assert volume.subwoofer_levels == {"Subwoofer": -10.0}
        assert volume.channel_volumes == {"Front Left": -1.0}


class TestChannelLevelSources:
    """Test case for the two interfaces reporting the same channel levels."""

    def test_telnet_levels_win(self):
        """Check that a pushed level is preferred over a polled one."""
        volume = DenonAVRVolume()
        # pylint: disable=protected-access
        volume._channel_volumes_appcommand = {"Front Left": 0.0, "Center": 0.0}
        volume._channel_volume_callback(MAIN_ZONE, "CV", "FL 49")
        assert volume.channel_volumes == {"Front Left": -1.0, "Center": 0.0}

    def test_both_scales_agree_on_zero(self):
        """Check that the telnet and the AppCommand scale are normalised."""
        volume = DenonAVRVolume()
        # pylint: disable=protected-access
        volume._channel_volume_callback(MAIN_ZONE, "CV", "FL 50")
        volume._channel_volumes_appcommand = {"Center": convert_appcommand_level("24")}
        assert volume.channel_volumes == {"Front Left": 0.0, "Center": 0.0}

    def test_surround_back_is_read(self):
        """Check that the single surround back channel is not dropped."""
        volume = DenonAVRVolume()
        # pylint: disable=protected-access
        volume._channel_volume_callback(MAIN_ZONE, "CV", "SB 50")
        assert volume.channel_volumes == {"Surround Back": 0.0}


class TestSubwooferLevelSources:
    """Test case for the two interfaces reporting the same subwoofer level."""

    def test_appcommand_level_is_used_without_telnet(self):
        """Check that an HTTP only setup reports the level it read."""
        volume = DenonAVRVolume()
        # pylint: disable=protected-access
        volume._subwoofer_level_status = "1"
        volume._subwoofer1_value = "20"
        assert volume.subwoofer_levels == {"Subwoofer": -2.0}

    def test_telnet_level_wins(self):
        """Check that a pushed level is preferred over a polled one."""
        volume = DenonAVRVolume()
        # pylint: disable=protected-access
        volume._subwoofer_levels = {"Subwoofer": 1.0}
        volume._subwoofer_level_status = "1"
        volume._subwoofer1_value = "20"
        assert volume.subwoofer_levels == {"Subwoofer": 1.0}

    def test_every_declared_subwoofer_is_read(self):
        """Check that a receiver with four subwoofers reports four levels."""
        # Extrapolated from the single subwoofer shape, which is the only one
        # captured -- see the note in the pull request
        volume = DenonAVRVolume()
        # pylint: disable=protected-access
        volume._subwoofer_level_status = "1"
        volume._subwoofer1_value = "24"
        volume._subwoofer2_value = "20"
        volume._subwoofer3_value = "28"
        volume._subwoofer4_value = "4"
        assert volume.subwoofer_levels == {
            "Subwoofer": 0.0,
            "Subwoofer 2": -2.0,
            "Subwoofer 3": 2.0,
            "Subwoofer 4": -10.0,
        }

    def test_absent_subwoofers_are_left_out(self):
        """Check that a one subwoofer receiver reports one level."""
        volume = DenonAVRVolume()
        # pylint: disable=protected-access
        volume._subwoofer_level_status = "1"
        volume._subwoofer1_value = "24"
        assert volume.subwoofer_levels == {"Subwoofer": 0.0}

    def test_status_gates_the_appcommand_level(self):
        """Check that a value is ignored while the receiver says unreadable."""
        volume = DenonAVRVolume()
        # pylint: disable=protected-access
        volume._subwoofer_level_status = "0"
        volume._subwoofer1_value = "20"
        assert volume.subwoofer_levels is None

    def test_the_status_is_readable(self):
        """Check that the gate the receiver reports is not private to us."""
        volume = DenonAVRVolume()
        # pylint: disable=protected-access
        assert volume.subwoofer_level_status is None
        volume._subwoofer_level_status = "1"
        assert volume.subwoofer_level_status is True
        volume._subwoofer_level_status = "0"
        assert volume.subwoofer_level_status is False

    @pytest.mark.parametrize(
        "status,adjustment",
        [("0", True), ("1", False), ("0", False)],
    )
    def test_the_two_getters_cannot_disagree(self, status, adjustment):
        """Check that one getter never reports a level the other hides."""
        # subwoofer_level() read straight from the merged levels and so
        # skipped the adjustment flag that subwoofer_levels applies, which
        # reported the same value as -2.0 from one and None from the other
        volume = DenonAVRVolume()
        # pylint: disable=protected-access
        volume._subwoofer_level_status = status
        volume._subwoofer_levels_adjustment = adjustment
        volume._subwoofer1_value = "20"
        assert volume.subwoofer_levels is None
        assert volume.subwoofer_level("Subwoofer") is None


class TestConvertMaxVolume:
    """Test case for the volume limit converter."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("-20.0", -20.0),
            ("-10.0", -10.0),
            ("0.0", 0.0),
            # SR6012 pads the value with whitespace, so the OFF/--/"" guard has
            # to strip before comparing, not only before float()
            ("  0.0", 0.0),
            (" OFF ", None),
            ("OFF", None),
            ("--", None),
            ("", None),
            (-20.0, -20.0),
        ],
    )
    def test_limit_values(self, value, expected):
        """Check that a limit is converted and OFF becomes None."""
        assert convert_max_volume(value) == expected


class TestConvertTelnetVolume:
    """Test case for the telnet volume scale."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("0", -80.0),
            ("56", -24.0),
            ("565", -23.5),
            ("98", 18.0),
            # MVMAX arrives as "MVMAX 70", so the parameter keeps the separator
            (" 70", -10.0),
        ],
    )
    def test_absolute_to_relative(self, value, expected):
        """Check both the two and three digit forms."""
        assert convert_telnet_volume(value) == expected


class TestVolumeEventsLeaveTheLimitAlone:
    """Test case for MVMAX not being read as the volume limit."""

    def test_no_callback_takes_the_limit_from_mvmax(self):
        """Check that nothing derives the limit from the MV event family."""
        volume = DenonAVRVolume()
        assert not hasattr(volume, "_max_volume_callback")

    def test_a_volume_change_does_not_disturb_the_limit(self):
        """Check that MV and the limit stay independent."""
        volume = DenonAVRVolume()
        # pylint: disable=protected-access
        volume._max_volume = -10.0
        volume._volume_callback(MAIN_ZONE, "MV", "565")
        assert volume.volume == -23.5
        assert volume.max_volume == -10.0


class TestSetVolumeLimit:
    """Test case for the volume limit enforced by async_set_volume."""

    @pytest.mark.asyncio
    async def test_above_limit_is_rejected(self):
        """Check that a volume above the configured limit raises."""
        volume = DenonAVRVolume()
        # pylint: disable=protected-access
        volume._max_volume = -10.0
        with pytest.raises(AvrCommandError):
            await volume.async_set_volume(-5.0)

    @pytest.mark.asyncio
    async def test_hardware_range_is_still_enforced(self):
        """Check that the hardware range applies with no limit configured."""
        volume = DenonAVRVolume()
        with pytest.raises(AvrCommandError):
            await volume.async_set_volume(18.5)
        with pytest.raises(AvrCommandError):
            await volume.async_set_volume(-80.5)

    @pytest.mark.asyncio
    async def test_at_or_below_limit_is_sent(self):
        """Check that the limit itself is still a settable volume."""
        volume = DenonAVRVolume()
        # pylint: disable=protected-access
        volume._max_volume = -10.0
        volume._device.api.async_get_command = mock.AsyncMock()

        await volume.async_set_volume(-10.0)
        await volume.async_set_volume(-30.0)

        assert volume._device.api.async_get_command.await_count == 2

    @pytest.mark.asyncio
    async def test_no_limit_allows_the_full_range(self):
        """Check that an unset limit does not restrict anything."""
        volume = DenonAVRVolume()
        # pylint: disable=protected-access
        volume._device.api.async_get_command = mock.AsyncMock()

        await volume.async_set_volume(18.0)

        volume._device.api.async_get_command.assert_awaited_once()


def _zone_volume(zone=None, urls=None, telnet_commands=None):
    """Return a DenonAVRVolume bound to a zone, as foundation binds it."""
    volume = DenonAVRVolume()
    # pylint: disable=protected-access
    if zone is not None:
        volume._device.zone = zone
        volume._device.urls = urls
        volume._device.telnet_commands = telnet_commands
    volume._device.api.async_get_command = mock.AsyncMock()
    return volume


class TestSetMaxVolume:
    """
    Test case for the volume limit setter.

    The three zones disagree about padding and about which values they take,
    so each one is pinned separately rather than through a shared formatter.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "max_volume,expected",
        [
            (0.0, "SSVCTZMALIM%2080"),
            (-10.0, "SSVCTZMALIM%2070"),
            (-20.0, "SSVCTZMALIM%2060"),
            # the main zone takes every whole step, not only the round ones
            (-13.0, "SSVCTZMALIM%2067"),
            (None, "SSVCTZMALIM%20OFF"),
        ],
    )
    async def test_main_zone_is_not_padded(self, max_volume, expected):
        """Check that the main zone sends an unpadded value."""
        volume = _zone_volume()
        await volume.async_set_max_volume(max_volume)
        # pylint: disable=protected-access
        url = volume._device.api.async_get_command.await_args[0][0]
        assert url.endswith(expected)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "max_volume,expected",
        [
            (0.0, "SSVCTZ2SLIM%20080"),
            (-10.0, "SSVCTZ2SLIM%20070"),
            (-20.0, "SSVCTZ2SLIM%20060"),
            (None, "SSVCTZ2SLIM%20OFF"),
        ],
    )
    async def test_zone2_is_padded_to_three_digits(self, max_volume, expected):
        """Check that zone 2 sends the value zero padded, unlike the main zone."""
        volume = _zone_volume(ZONE2, ZONE2_URLS, ZONE2_TELNET_COMMANDS)
        await volume.async_set_max_volume(max_volume)
        # pylint: disable=protected-access
        url = volume._device.api.async_get_command.await_args[0][0]
        assert url.endswith(expected)

    @pytest.mark.asyncio
    async def test_zone3_follows_zone2(self):
        """Check that zone 3 uses its own command with zone 2's encoding."""
        volume = _zone_volume(ZONE3, ZONE3_URLS, ZONE3_TELNET_COMMANDS)
        await volume.async_set_max_volume(-10.0)
        # pylint: disable=protected-access
        url = volume._device.api.async_get_command.await_args[0][0]
        assert url.endswith("SSVCTZ3SLIM%20070")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("max_volume", [0.5, -20.5, 1.0, -30.0, -19.5])
    async def test_out_of_domain_is_rejected(self, max_volume):
        """Check that a value outside the main zone domain raises."""
        volume = _zone_volume()
        with pytest.raises(AvrCommandError):
            await volume.async_set_max_volume(max_volume)
        # pylint: disable=protected-access
        volume._device.api.async_get_command.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("max_volume", [-15.0, -5.0, -1.0, -21.0, 10.0])
    async def test_zone2_takes_only_three_values(self, max_volume):
        """Check that zone 2 rejects everything but its three steps."""
        volume = _zone_volume(ZONE2, ZONE2_URLS, ZONE2_TELNET_COMMANDS)
        with pytest.raises(AvrCommandError):
            await volume.async_set_max_volume(max_volume)
        # pylint: disable=protected-access
        volume._device.api.async_get_command.assert_not_awaited()


class TestSetSubwooferLevel:
    """
    Test case for the absolute subwoofer level setter.

    The receiver drops the command whenever it reports the level as not
    adjustable, and says nothing about having done so -- the request is
    acknowledged and the unchanged level is echoed back -- so the refusal has
    to happen on this side.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "subwoofer,level,expected",
        [
            ("Subwoofer", 0.0, "PSSWL%2050"),
            ("Subwoofer", -12.0, "PSSWL%2038"),
            ("Subwoofer", 12.0, "PSSWL%2062"),
            # a half step is a third digit, the CV convention rather than the
            # PSDELAY one
            ("Subwoofer", -8.5, "PSSWL%20415"),
            ("Subwoofer 2", -2.0, "PSSWL2%2048"),
            ("Subwoofer 4", 1.0, "PSSWL4%2051"),
        ],
    )
    async def test_the_level_is_sent_on_the_telnet_scale(
        self, subwoofer, level, expected
    ):
        """Check that dB is encoded as the command wants it, 50 being 0.0."""
        volume = _zone_volume()
        # pylint: disable=protected-access
        volume._subwoofer_level_status = "1"
        await volume.async_set_subwoofer_level(subwoofer, level)
        url = volume._device.api.async_get_command.await_args[0][0]
        assert url.endswith(expected)

    @pytest.mark.asyncio
    async def test_telnet_is_preferred_when_it_is_there(self):
        """Check that the setter takes the same route as its up/down pair."""
        volume = _zone_volume()
        # pylint: disable=protected-access
        volume._subwoofer_level_status = "1"
        volume._device.telnet_api.async_send_commands = mock.AsyncMock()
        with mock.patch.object(type(volume._device), "telnet_available", True):
            await volume.async_set_subwoofer_level("Subwoofer", -10.0)
        volume._device.telnet_api.async_send_commands.assert_awaited_once_with(
            "PSSWL 40"
        )
        volume._device.api.async_get_command.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_shut_gate_refuses_rather_than_reporting_success(self):
        """Check that a write the receiver would drop is not sent at all."""
        volume = _zone_volume()
        # pylint: disable=protected-access
        volume._subwoofer_level_status = "0"
        with pytest.raises(AvrCommandError):
            await volume.async_set_subwoofer_level("Subwoofer", -10.0)
        volume._device.api.async_get_command.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_unreported_gate_is_not_a_shut_one(self):
        """Check that a model without the command can still be set."""
        # A receiver which does not know GetSubwooferLevel answers <error>2</error>
        # and the status stays None, which is not evidence that a write will be
        # dropped -- only a reported 0 is
        volume = _zone_volume()
        # pylint: disable=protected-access
        assert volume.subwoofer_level_status is None
        await volume.async_set_subwoofer_level("Subwoofer", -10.0)
        volume._device.api.async_get_command.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("level", [12.5, -12.5, 0.3, 100.0])
    async def test_a_level_outside_the_domain_is_rejected(self, level):
        """Check that a value the receiver would answer OK to is refused."""
        volume = _zone_volume()
        # pylint: disable=protected-access
        volume._subwoofer_level_status = "1"
        with pytest.raises(AvrCommandError):
            await volume.async_set_subwoofer_level("Subwoofer", level)
        volume._device.api.async_get_command.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_invalid_subwoofer_is_rejected(self):
        """Check that a name that is not a subwoofer at all still raises."""
        volume = _zone_volume()
        # pylint: disable=protected-access
        volume._subwoofer_level_status = "1"
        with pytest.raises(AvrCommandError):
            await volume.async_set_subwoofer_level("Subwoofer 5", -10.0)
        volume._device.api.async_get_command.assert_not_awaited()


class TestMaxVolumeSetupCallback:
    """Test case for the volume limit event pushed by the setup menu."""

    @pytest.mark.parametrize(
        "parameter,expected",
        [
            # the main zone pushes the value unpadded, zone 2 and 3 padded
            (" 70", -10.0),
            (" 60", -20.0),
            (" 80", 0.0),
            (" 070", -10.0),
            (" 060", -20.0),
            (" OFF", None),
        ],
    )
    def test_pushed_value_lands_on_the_volume_scale(self, parameter, expected):
        """Check that both the padded and unpadded forms are read."""
        volume = DenonAVRVolume()
        # pylint: disable=protected-access
        volume._max_volume = 0.0
        volume._max_volume_setup_callback(MAIN_ZONE, "SSVCTZMALIM", parameter)
        assert volume.max_volume == expected

    def test_other_zones_events_are_reported_as_main(self):
        """
        Check that a zone takes its own event whatever zone it is labelled.

        _process_event derives the zone from a Z2/Z3 message prefix, which
        SSVCTZ2SLIM does not have, so zone 2's own limit event arrives labelled
        as the main zone. Verified against an AVR-X1700H, which pushes
        ('Main', 'SSVCTZ2SLIM', ' 060') after a zone 2 write. Each zone only
        ever registers for its own event name, so the callback must not filter
        on the zone it is handed.
        """
        volume = _zone_volume(ZONE2, ZONE2_URLS, ZONE2_TELNET_COMMANDS)
        # pylint: disable=protected-access
        volume._max_volume_setup_callback(MAIN_ZONE, "SSVCTZ2SLIM", " 060")
        assert volume.max_volume == -20.0


class TestVolumeUpCeiling:
    """
    Test case for volume up stopping at the ceiling.

    A receiver at its ceiling ignores the command and answers nothing, so the
    caller cannot tell an applied command from an ignored one, and a telnet
    confirmation would wait for an event that never arrives.
    """

    @pytest.mark.asyncio
    async def test_stops_at_the_configured_limit(self):
        """Check that volume up is not sent once the limit is reached."""
        volume = _zone_volume()
        # pylint: disable=protected-access
        volume._max_volume = -10.0
        volume._volume = -10.0
        await volume.async_volume_up()
        volume._device.api.async_get_command.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stops_at_the_hardware_ceiling_with_no_limit(self):
        """Check that the hardware maximum applies when no limit is set."""
        volume = _zone_volume()
        # pylint: disable=protected-access
        volume._volume = 18.0
        await volume.async_volume_up()
        volume._device.api.async_get_command.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "max_volume,current",
        [(-10.0, -10.5), (None, 17.5), (0.0, -0.5), (-20.0, -80.0)],
    )
    async def test_below_the_ceiling_is_sent(self, max_volume, current):
        """Check that anything below the ceiling still goes out."""
        volume = _zone_volume()
        # pylint: disable=protected-access
        volume._max_volume = max_volume
        volume._volume = current
        await volume.async_volume_up()
        volume._device.api.async_get_command.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_zero_limit_is_not_treated_as_absent(self):
        """
        Check that a limit of 0.0 dB still stops volume up.

        0.0 is a legal limit -- absolute 80 -- and it is falsy, so a
        truthiness check here silently disables the guard.
        """
        volume = _zone_volume()
        # pylint: disable=protected-access
        volume._max_volume = 0.0
        volume._volume = 0.0
        await volume.async_volume_up()
        volume._device.api.async_get_command.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_volume_does_not_block(self):
        """Check that an unknown volume does not stop the command."""
        volume = _zone_volume()
        # pylint: disable=protected-access
        volume._max_volume = -10.0
        await volume.async_volume_up()
        volume._device.api.async_get_command.assert_awaited_once()
