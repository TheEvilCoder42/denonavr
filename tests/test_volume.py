#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
This module covers tests of the volume functions of Denon AVR receivers.

:copyright: (c) 2016 by Oliver Goetz.
:license: MIT, see LICENSE for more details.
"""

from unittest import mock
from xml.etree import ElementTree as ET

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
from denonavr.foundation import DenonAVRDeviceInfo
from denonavr.volume import (
    DenonAVRVolume,
    convert_appcommand_level,
    convert_max_volume,
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

    def test_status_gates_the_appcommand_level(self):
        """Check that a value is ignored while the receiver says unreadable."""
        volume = DenonAVRVolume()
        # pylint: disable=protected-access
        volume._subwoofer_level_status = "0"
        volume._subwoofer1_value = "20"
        assert volume.subwoofer_levels is None


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


class TestVolumeEventsLeaveTheLimitAlone:
    """Test case for volume events not being read as the volume limit."""

    def test_a_volume_change_does_not_disturb_the_limit(self):
        """Check that MV and the limit stay independent."""
        volume = DenonAVRVolume()
        # pylint: disable=protected-access
        volume._max_volume = -10.0
        volume._volume_callback(MAIN_ZONE, "MV", "565")
        assert volume.volume == -23.5
        assert volume.max_volume == -10.0


class TestMaxVolumeKnown:
    """Test case for telling an unreported limit from no limit."""

    def test_not_known_before_any_report(self):
        """Check that a fresh instance reads as no limit, but not known."""
        volume = DenonAVRVolume()
        assert volume.max_volume == 18.0
        assert volume.max_volume_known is False

    @pytest.mark.parametrize(
        "parameter,expected",
        [(" OFF", 18.0), (" 70", -10.0)],
    )
    def test_telnet_report_makes_it_known(self, parameter, expected):
        """Check that OFF over telnet is a report, not an unchanged None."""
        volume = DenonAVRVolume()
        # pylint: disable=protected-access
        volume._max_volume_callback(MAIN_ZONE, "SSVCTZMALIM", parameter)
        assert volume.max_volume == expected
        assert volume.max_volume_known is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "zone_xml,known,expected",
        [
            ("<volume>-40.0</volume><limit>OFF</limit>", True, 18.0),
            ("<volume>-40.0</volume><limit>-20.0</limit>", True, -20.0),
            ("<volume>-40.0</volume>", False, 18.0),
        ],
    )
    async def test_http_report_makes_it_known(self, zone_xml, known, expected):
        """Check that only a <limit> in the response makes the limit known."""
        volume = DenonAVRVolume()
        # pylint: disable=protected-access
        volume._device.api.async_post_appcommand = mock.AsyncMock(
            return_value=ET.fromstring(
                f'<rx><cmd cmd_text="GetAllZoneVolume"><zone1>{zone_xml}'
                "</zone1></cmd></rx>"
            )
        )
        await volume.async_update_attrs_appcommand({AppCommands.GetAllZoneVolume: None})
        assert volume.max_volume == expected
        assert volume.max_volume_known is known


def _zone_volume(zone=None, urls=None, telnet_commands=None):
    """Return a DenonAVRVolume bound to a zone, as foundation binds it."""
    volume = DenonAVRVolume()
    # pylint: disable=protected-access
    if zone is not None:
        volume._device.zone = zone
        volume._device.urls = urls
        volume._device.telnet_commands = telnet_commands
    volume._device.api.async_get_command = mock.AsyncMock()
    volume._device.telnet_api.async_send_commands = mock.AsyncMock()
    return volume


def _telnet_connected():
    """Patch the device to report a healthy telnet connection."""
    return mock.patch.object(
        DenonAVRDeviceInfo,
        "telnet_available",
        new_callable=mock.PropertyMock,
        return_value=True,
    )


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


class TestMaxVolumeCallback:
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
            # no limit reads as the hardware ceiling, as max_volume defines it
            (" OFF", 18.0),
        ],
    )
    def test_pushed_value_lands_on_the_volume_scale(self, parameter, expected):
        """Check that both the padded and unpadded forms are read."""
        volume = DenonAVRVolume()
        # pylint: disable=protected-access
        volume._max_volume = 0.0
        volume._max_volume_callback(MAIN_ZONE, "SSVCTZMALIM", parameter)
        assert volume.max_volume == expected

    def test_other_zones_events_are_reported_as_main(self):
        """
        Check that a zone takes its own event whatever zone it is labelled.

        _process_event derives the zone from a Z2/Z3 message prefix, which
        SSVCTZ2SLIM does not have, so an AVR-X1700H pushes zone 2's own limit
        event as ('Main', 'SSVCTZ2SLIM', ' 060'). Each zone registers for its
        own event name alone, so the callback must not filter on the zone.
        """
        volume = _zone_volume(ZONE2, ZONE2_URLS, ZONE2_TELNET_COMMANDS)
        # pylint: disable=protected-access
        volume._max_volume_callback(MAIN_ZONE, "SSVCTZ2SLIM", " 060")
        assert volume.max_volume == -20.0


class TestVolumeUpCeiling:
    """
    Test case for volume up stopping at the ceiling over telnet.

    The ceiling is the limit, or the hardware maximum when none is set.
    Volumes are given as telnet reports them, the limit on the volume scale.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "max_volume,current",
        [
            (-10.0, "70"),
            # a limit lowered below the current volume
            (-20.0, "70"),
            # no limit falls back to the hardware maximum
            (None, "98"),
        ],
    )
    async def test_stops_at_the_ceiling(self, max_volume, current):
        """Check that volume up is not sent at or above the ceiling."""
        volume = _zone_volume()
        # pylint: disable=protected-access
        volume._max_volume = max_volume
        volume._volume = current
        with _telnet_connected():
            await volume.async_volume_up()
        volume._device.telnet_api.async_send_commands.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_zero_limit_is_not_treated_as_absent(self):
        """
        Check that a limit of 0.0 dB still stops volume up.

        0.0 is a legal limit and it is falsy, so a truthiness check on the
        limit would fall back to the hardware maximum instead.
        """
        volume = _zone_volume()
        # pylint: disable=protected-access
        volume._max_volume = 0.0
        volume._volume = "80"
        with _telnet_connected():
            await volume.async_volume_up()
        volume._device.telnet_api.async_send_commands.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "max_volume,current",
        [(-10.0, "695"), (0.0, "795"), (None, "975")],
    )
    async def test_below_the_ceiling_is_sent(self, max_volume, current):
        """Check that half a step below the ceiling still goes out."""
        volume = _zone_volume()
        # pylint: disable=protected-access
        volume._max_volume = max_volume
        volume._volume = current
        with _telnet_connected():
            await volume.async_volume_up()
        volume._device.telnet_api.async_send_commands.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unknown_volume_does_not_block(self):
        """Check that volume up is sent before any volume was reported."""
        volume = _zone_volume()
        # pylint: disable=protected-access
        volume._max_volume = -10.0
        with _telnet_connected():
            await volume.async_volume_up()
        volume._device.telnet_api.async_send_commands.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_http_is_not_gated(self):
        """Check that the guard applies to telnet only."""
        volume = _zone_volume()
        # pylint: disable=protected-access
        volume._max_volume = -10.0
        volume._volume = "70"
        await volume.async_volume_up()
        volume._device.api.async_get_command.assert_awaited_once()


class TestSetVolume:
    """Test case for setting the volume against the hardware range and limit."""

    @pytest.mark.asyncio
    async def test_clamped_to_the_limit_over_telnet(self):
        """Check that a volume above the limit is sent as the limit."""
        volume = _zone_volume()
        # pylint: disable=protected-access
        volume._max_volume = -10.0
        volume._volume = "40"
        with _telnet_connected():
            await volume.async_set_volume(-5.0)
        volume._device.telnet_api.async_send_commands.assert_awaited_once_with("MV70")

    @pytest.mark.asyncio
    async def test_clamped_to_the_current_volume_is_skipped(self):
        """Check that nothing is sent when the clamped value is already set."""
        volume = _zone_volume()
        # pylint: disable=protected-access
        volume._max_volume = -10.0
        volume._volume = "70"
        with _telnet_connected():
            await volume.async_set_volume(-5.0)
        volume._device.telnet_api.async_send_commands.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_http_is_not_clamped(self):
        """Check that the clamp applies to telnet only."""
        volume = _zone_volume()
        # pylint: disable=protected-access
        volume._max_volume = -10.0
        volume._volume = "40"
        await volume.async_set_volume(-5.0)
        url = volume._device.api.async_get_command.await_args[0][0]
        assert url.endswith("?1+-5.0")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", [-80.0, 18.0])
    async def test_hardware_range_is_accepted(self, value):
        """Check that both ends of the hardware range are sent."""
        volume = _zone_volume()
        await volume.async_set_volume(value)
        # pylint: disable=protected-access
        volume._device.api.async_get_command.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", [-80.5, 18.5])
    async def test_outside_the_hardware_range_is_rejected(self, value):
        """Check that a volume outside -80.0 to 18.0 raises."""
        volume = _zone_volume()
        with pytest.raises(AvrCommandError):
            await volume.async_set_volume(value)
        # pylint: disable=protected-access
        volume._device.api.async_get_command.assert_not_awaited()


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
