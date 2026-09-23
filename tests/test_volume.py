#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
This module covers tests of the volume limit of Denon AVR receivers.

:copyright: (c) 2016 by Oliver Goetz.
:license: MIT, see LICENSE for more details.
"""

from unittest import mock
from xml.etree import ElementTree as ET

import pytest

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
from denonavr.volume import DenonAVRVolume, convert_max_volume


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
