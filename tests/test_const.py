#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
This module covers some tests for mappings in const.py.

:copyright: (c) 2016 by Oliver Goetz.
:license: MIT, see LICENSE for more details.
"""

import glob
import unittest
import xml.etree.ElementTree as ET
from typing import get_args

from denonavr.const import (
    CHANNEL_MAP,
    CHANNEL_MAP_REVERSE,
    DIMMER_MODE_MAP_REVERSE,
    DIRAC_FILTER_MAP_REVERSE,
    ECO_MODE_MAP_REVERSE,
    HDMI_OUTPUT_MAP_REVERSE,
    Channels,
    DimmerModes,
    DiracFilters,
    EcoModes,
    HDMIOutputs,
)


class TestMappings(unittest.TestCase):
    """Test case for mappings in const.py."""

    def test_dimmer_modes_mappings(self):
        """Test that all dimmer modes are in the mapping."""
        for mode in get_args(DimmerModes):
            self.assertIn(mode, DIMMER_MODE_MAP_REVERSE)

    def test_eco_modes_mappings(self):
        """Test that all eco-modes are in the mapping."""
        for mode in get_args(EcoModes):
            self.assertIn(mode, ECO_MODE_MAP_REVERSE)

    def test_hdmi_outputs_mappings(self):
        """Test that all hdmi outputs are in the mapping."""
        for hdmi_output in get_args(HDMIOutputs):
            self.assertIn(hdmi_output, HDMI_OUTPUT_MAP_REVERSE)

    def test_channel_mappings(self):
        """Test that all channels are in the mapping."""
        for channel in get_args(Channels):
            self.assertIn(channel, CHANNEL_MAP_REVERSE)

    def test_dirac_filter_mappings(self):
        """Test that dirac filters are in the mapping."""
        for dirac_filter in get_args(DiracFilters):
            self.assertIn(dirac_filter, DIRAC_FILTER_MAP_REVERSE)


class TestDeclaredChannels(unittest.TestCase):
    """Test case for the channels real receivers declare."""

    def test_every_declared_channel_is_mapped(self):
        """Test that every channel in a Deviceinfo sample is in the mapping."""
        samples = sorted(glob.glob("tests/xml/*Deviceinfo*.xml"))
        self.assertTrue(samples, "no Deviceinfo samples found")

        declared = 0
        for sample in samples:
            root = ET.parse(sample).getroot()
            for channel in root.findall(".//ChannelLevel/ChLists/Ch/Name"):
                # ZRL is the reset entry of the channel level menu rather than
                # a speaker, and no receiver reports a level for it
                if channel.text == "ZRL":
                    continue
                declared += 1
                self.assertIn(
                    channel.text,
                    CHANNEL_MAP,
                    f"{sample} declares channel {channel.text}",
                )

        self.assertGreater(declared, 0, "no channels found in the samples")


if __name__ == "__main__":
    unittest.main()
