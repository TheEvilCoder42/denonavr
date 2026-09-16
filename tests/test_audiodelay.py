#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
This module covers tests of the audio delay settings.

:copyright: (c) 2016 by Oliver Goetz.
:license: MIT, see LICENSE for more details.
"""

from unittest import mock

import pytest
from pytest_httpx import HTTPXMock

from denonavr.audiodelay import DenonAVRAudioDelay
from denonavr.const import MAIN_ZONE, ZONE2
from denonavr.exceptions import AvrCommandError

APPCOMMAND0300_URL = "/goform/AppCommand0300.xml"
DIRECT_URL = "/goform/formiPhoneAppDirect.xml"


def get_sample_content(filename):
    """Return sample content form file."""
    with open(f"tests/xml/{filename}", encoding="utf-8") as file:
        return file.read()


def audio_delay_instance() -> DenonAVRAudioDelay:
    """Return an audio delay instance that is ready to be updated."""
    audio_delay = DenonAVRAudioDelay()
    # pylint: disable=protected-access
    audio_delay._device.use_avr_2016_update = True
    return audio_delay


class TestAudioDelayUpdate:
    """Test case for reading the audio delay from AppCommand0300.xml."""

    @pytest.mark.asyncio
    async def test_values_are_read(self, httpx_mock: HTTPXMock):
        """Check that every parameter of GetAudioDelay is picked up."""
        httpx_mock.add_response(
            content=get_sample_content("AVR-X1700H-AppCommand0300-audiodelay.xml")
        )
        audio_delay = audio_delay_instance()
        await audio_delay.async_update()

        assert audio_delay.audio_delay == 140
        assert audio_delay.auto_lip_sync is True
        assert audio_delay.tv_delay == 0

    @pytest.mark.asyncio
    async def test_unavailable_tv_delay_reads_as_none(self, httpx_mock: HTTPXMock):
        """Check that an empty parameter does not break the update."""
        # The receiver empties tvdelay and sets its control attribute to 0
        # while audio is playing, which is when a client is most likely to poll
        httpx_mock.add_response(
            content=get_sample_content(
                "AVR-X1700H-AppCommand0300-audiodelay-playing.xml"
            )
        )
        audio_delay = audio_delay_instance()
        await audio_delay.async_update()

        assert audio_delay.tv_delay is None
        assert audio_delay.audio_delay == 140


class TestAudioDelayCallback:
    """Test case for the PSDELAY telnet callback."""

    def test_delay_event_is_read(self):
        """Check that a delay event updates the audio delay."""
        audio_delay = DenonAVRAudioDelay()
        # pylint: disable=protected-access
        audio_delay._delay_callback(MAIN_ZONE, "PS", "DELAY 140")
        assert audio_delay.audio_delay == 140

    def test_other_sound_details_are_ignored(self):
        """Check that another PS event does not touch the audio delay."""
        audio_delay = DenonAVRAudioDelay()
        # pylint: disable=protected-access
        audio_delay._delay_callback(MAIN_ZONE, "PS", "DEL 050")
        assert audio_delay.audio_delay is None

    def test_other_zones_are_ignored(self):
        """Check that a zone only takes the delay reported for itself."""
        audio_delay = DenonAVRAudioDelay()
        # pylint: disable=protected-access
        audio_delay._delay_callback(ZONE2, "PS", "DELAY 140")
        assert audio_delay.audio_delay is None


class TestSetAudioDelay:
    """Test case for the audio delay setter."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("delay,expected", [(0, "000"), (5, "005"), (500, "500")])
    async def test_value_is_zero_padded(self, httpx_mock: HTTPXMock, delay, expected):
        """Check that the value is sent as three digits."""
        # A value that is not three digits long is answered with HTTP 200 and
        # silently dropped by the receiver
        httpx_mock.add_response()
        audio_delay = DenonAVRAudioDelay()
        await audio_delay.async_delay(delay)

        request = httpx_mock.get_requests()[0]
        assert request.url.path == DIRECT_URL
        assert str(request.url.query, "utf-8") == f"PSDELAY%20{expected}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("delay", [-1, 501])
    async def test_values_out_of_range_are_rejected(self, delay):
        """Check that a value the receiver would ignore raises instead."""
        audio_delay = DenonAVRAudioDelay()
        with pytest.raises(AvrCommandError):
            await audio_delay.async_delay(delay)

    @pytest.mark.asyncio
    async def test_telnet_is_preferred(self):
        """Check that the command is sent over telnet when it is available."""
        audio_delay = DenonAVRAudioDelay()
        # pylint: disable=protected-access
        telnet_api = audio_delay._device.telnet_api
        with mock.patch.object(
            type(audio_delay._device),
            "telnet_available",
            mock.PropertyMock(return_value=True),
        ), mock.patch.object(
            telnet_api, "async_send_commands", mock.AsyncMock()
        ) as send:
            await audio_delay.async_delay(50)

        send.assert_awaited_once_with("PSDELAY 050")
