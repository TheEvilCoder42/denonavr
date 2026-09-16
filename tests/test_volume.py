#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
This module covers tests of the volume functions of Denon AVR receivers.

:copyright: (c) 2016 by Oliver Goetz.
:license: MIT, see LICENSE for more details.
"""

import pytest

from denonavr.volume import DenonAVRVolume


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
