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
