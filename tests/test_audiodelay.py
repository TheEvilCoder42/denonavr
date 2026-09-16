#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
This module covers tests of the audio delay settings.

:copyright: (c) 2016 by Oliver Goetz.
:license: MIT, see LICENSE for more details.
"""

from unittest import mock

import httpx
import pytest
from pytest_httpx import HTTPXMock

import denonavr
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
    @pytest.mark.parametrize("delay", [-1, 501, 50.0])
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


class TestSettingsUpdate:
    """Test case for the combined AppCommand0300.xml refresh."""

    @pytest.mark.asyncio
    async def test_audyssey_and_audio_delay_share_one_request(
        self, httpx_mock: HTTPXMock
    ):
        """Check that refreshing both settings costs a single request."""
        httpx_mock.add_response(
            content=get_sample_content("AVR-X1700H-AppCommand0300-settings.xml")
        )
        denon = denonavr.DenonAVR("10.0.0.0")
        # pylint: disable=protected-access
        denon._device.use_avr_2016_update = True
        denon.audyssey.setup()
        denon.audiodelay.setup()

        await denon.async_update_settings()

        requests = httpx_mock.get_requests()
        assert len(requests) == 1
        assert requests[0].url.path == APPCOMMAND0300_URL
        assert denon.audyssey.multi_eq == "Reference"
        assert denon.audio_delay == 140
        assert denon.auto_lip_sync is True

    @pytest.mark.asyncio
    async def test_a_zone_reports_no_audio_delay(self, httpx_mock: HTTPXMock):
        """Check that a zone does not report the main zone's audio delay."""
        # The receiver keeps one delay, for the main zone's input source, and a
        # zone ignores the PSDELAY events that would keep its copy current
        httpx_mock.add_response(
            content=get_sample_content("AVR-X1700H-AppCommand0300-settings.xml")
        )
        denon = denonavr.DenonAVR("10.0.0.0", add_zones={ZONE2: ZONE2})
        zone2 = denon.zones[ZONE2]
        # pylint: disable=protected-access
        zone2._device.use_avr_2016_update = True
        zone2.audyssey.setup()
        zone2.audiodelay.setup()

        await zone2.async_update_settings()

        assert zone2.audio_delay is None
        assert zone2.auto_lip_sync is True
        assert zone2.audiodelay.tv_delay == 0

    @pytest.mark.asyncio
    async def test_a_short_answer_does_not_stop_audyssey(self, httpx_mock: HTTPXMock):
        """Check that a device without GetAudioDelay still refreshes Audyssey."""
        audyssey = get_sample_content("AVR-X1700H-AppCommand0300-settings.xml")
        audyssey = audyssey[: audyssey.index("<cmd>\n<name>GetAudioDelay")] + "</rx>"

        # A device that does not know a tag leaves its element out
        def answer(request: httpx.Request) -> httpx.Response:
            if b"GetAudyssey" in request.content:
                return httpx.Response(200, text=audyssey)
            return httpx.Response(200, text="<rx></rx>")

        httpx_mock.add_callback(answer, is_reusable=True)
        denon = denonavr.DenonAVR("10.0.0.0")
        # pylint: disable=protected-access
        denon._device.use_avr_2016_update = True
        denon.audyssey.setup()
        denon.audiodelay.setup()

        await denon.async_update_settings()

        assert denon.audyssey.multi_eq == "Reference"
        assert denon.audio_delay is None

    @pytest.mark.asyncio
    async def test_a_short_answer_does_not_stop_the_audio_delay(
        self, httpx_mock: HTTPXMock
    ):
        """Check that a device without GetAudyssey still refreshes the delay."""
        delay = get_sample_content("AVR-X1700H-AppCommand0300-settings.xml")
        delay = "<rx>" + delay[delay.index("<cmd>\n<name>GetAudioDelay") :]

        # The retry with GetAudyssey alone is answered short too
        def answer(request: httpx.Request) -> httpx.Response:
            if b"GetAudioDelay" in request.content:
                return httpx.Response(200, text=delay)
            return httpx.Response(200, text="<rx></rx>")

        httpx_mock.add_callback(answer, is_reusable=True)
        denon = denonavr.DenonAVR("10.0.0.0")
        # pylint: disable=protected-access
        denon._device.use_avr_2016_update = True
        denon.audyssey.setup()
        denon.audiodelay.setup()

        await denon.async_update_settings()

        assert denon.audyssey.multi_eq is None
        assert denon.audio_delay == 140

    @pytest.mark.asyncio
    async def test_update_audyssey_is_an_alias(self, httpx_mock: HTTPXMock):
        """Check that the deprecated name refreshes both settings."""
        httpx_mock.add_response(
            content=get_sample_content("AVR-X1700H-AppCommand0300-settings.xml")
        )
        denon = denonavr.DenonAVR("10.0.0.0")
        # pylint: disable=protected-access
        denon._device.use_avr_2016_update = True
        denon.audyssey.setup()
        denon.audiodelay.setup()

        await denon.async_update_audyssey()

        assert denon.audyssey.multi_eq == "Reference"
        assert denon.audio_delay == 140
