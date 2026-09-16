#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
This module covers tests of the volume functions of Denon AVR receivers.

:copyright: (c) 2016 by Oliver Goetz.
:license: MIT, see LICENSE for more details.
"""

from unittest import mock

import pytest

from denonavr.const import MAIN_ZONE
from denonavr.exceptions import AvrCommandError
from denonavr.volume import DenonAVRVolume, convert_max_volume, convert_telnet_volume


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
