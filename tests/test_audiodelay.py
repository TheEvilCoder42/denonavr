#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
This module covers tests of the audio delay settings.

:copyright: (c) 2016 by Oliver Goetz.
:license: MIT, see LICENSE for more details.
"""

import time
from unittest import mock

import pytest
from pytest_httpx import HTTPXMock

import denonavr
from denonavr.audiodelay import DenonAVRAudioDelay
from denonavr.const import MAIN_ZONE, ZONE2
from denonavr.exceptions import AvrCommandError, AvrProcessingError

APPCOMMAND0300_URL = "/goform/AppCommand0300.xml"
DIRECT_URL = "/goform/formiPhoneAppDirect.xml"

# ver-2 setters answer with the status directly inside the cmd element
OK_RESPONSE = b'<?xml version="1.0" encoding="utf-8" ?><rx><cmd>OK</cmd></rx>'


def get_sample_content(filename):
    """Return sample content form file."""
    with open(f"tests/xml/{filename}", encoding="utf-8") as file:
        return file.read()


def settings_receiver(**kwargs) -> denonavr.DenonAVR:
    """Return a receiver whose AppCommand0300 settings are ready to refresh."""
    denon = denonavr.DenonAVR("10.0.0.0", **kwargs)
    for zone_receiver in denon.zones.values():
        # pylint: disable=protected-access
        zone_receiver._device.use_avr_2016_update = True
        zone_receiver.audyssey.setup()
        zone_receiver.audiodelay.setup()
    return denon


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
    @pytest.mark.parametrize(
        "fixture,expected",
        [
            pytest.param("AVR-X1700H-AppCommand0300-audiodelay.xml", True, id="on"),
            pytest.param(
                "AVR-X1700H-AppCommand0300-audiodelay-lipsync-off.xml", False, id="off"
            ),
        ],
    )
    async def test_both_auto_lip_sync_states_are_read(
        self, httpx_mock: HTTPXMock, fixture: str, expected: bool
    ):
        """Check that the off state is not confused with an absent value."""
        httpx_mock.add_response(content=get_sample_content(fixture))
        audio_delay = audio_delay_instance()
        await audio_delay.async_update()

        assert audio_delay.auto_lip_sync is expected

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


class TestSetAutoLipSync:
    """Test case for the AppCommand0300 auto lip sync setter."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "enabled,expected",
        [pytest.param(True, "1", id="on"), pytest.param(False, "0", id="off")],
    )
    async def test_the_value_is_sent_as_a_digit(
        self, httpx_mock: HTTPXMock, enabled: bool, expected: str
    ):
        """Check that the AppCommand parameter carries 1 or 0."""
        httpx_mock.add_response(content=OK_RESPONSE)
        audio_delay = DenonAVRAudioDelay()
        await audio_delay.async_set_auto_lip_sync(enabled)

        request = httpx_mock.get_requests()[0]
        assert request.url.path == APPCOMMAND0300_URL
        body = request.content.decode("utf-8")
        assert f'<param name="autolipsync">{expected}</param>' in body
        assert "<name>SetAudioDelay</name>" in body

    @pytest.mark.asyncio
    async def test_a_rejected_command_raises(self, httpx_mock: HTTPXMock):
        """Check that an answer other than OK is not reported as success."""
        httpx_mock.add_response(content=b"<rx><cmd>ERROR</cmd></rx>")
        audio_delay = DenonAVRAudioDelay()

        with pytest.raises(AvrProcessingError):
            await audio_delay.async_set_auto_lip_sync(True)


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
        denon = settings_receiver()

        await denon.async_update_settings()

        requests = httpx_mock.get_requests()
        assert len(requests) == 1
        assert requests[0].url.path == APPCOMMAND0300_URL
        assert denon.audyssey.multi_eq == "Reference"
        assert denon.audio_delay == 140
        assert denon.auto_lip_sync is True

    @pytest.mark.asyncio
    async def test_update_audyssey_is_an_alias(self, httpx_mock: HTTPXMock):
        """Check that the deprecated name refreshes both settings."""
        httpx_mock.add_response(
            content=get_sample_content("AVR-X1700H-AppCommand0300-settings.xml")
        )
        denon = settings_receiver()

        await denon.async_update_audyssey()

        assert denon.audyssey.multi_eq == "Reference"
        assert denon.audio_delay == 140

    @pytest.mark.asyncio
    @pytest.mark.httpx_mock(can_send_already_matched_responses=True)
    async def test_one_cache_id_serves_every_zone(self, httpx_mock: HTTPXMock):
        """Check that refreshing two zones costs a single request.

        The body carries no zone, so the second zone would otherwise post
        the same bytes and get the same document back.
        """
        httpx_mock.add_response(
            content=get_sample_content("AVR-X1700H-AppCommand0300-settings.xml")
        )
        denon = settings_receiver(add_zones={ZONE2: ZONE2})

        cache_id = time.time()
        for zone_receiver in denon.zones.values():
            await zone_receiver.async_update_settings(cache_id=cache_id)

        requests = httpx_mock.get_requests()
        assert len(requests) == 1
        assert requests[0].url.path == APPCOMMAND0300_URL
        assert denon.audio_delay == 140
        assert denon.zones[ZONE2].audio_delay == 140

    @pytest.mark.asyncio
    @pytest.mark.httpx_mock(can_send_already_matched_responses=True)
    async def test_each_refresh_asks_again(self, httpx_mock: HTTPXMock):
        """Check that a refresh without a cache id is not served a stale answer."""
        httpx_mock.add_response(
            content=get_sample_content("AVR-X1700H-AppCommand0300-settings.xml")
        )
        denon = settings_receiver()

        await denon.async_update_settings()
        await denon.async_update_settings()

        assert len(httpx_mock.get_requests()) == 2


class TestPerSourceInvalidation:
    """Test case for forgetting the delay after an input source change."""

    def test_the_first_source_is_not_a_change(self):
        """Check that a delay read before the first update survives."""
        audio_delay = DenonAVRAudioDelay()
        # pylint: disable=protected-access
        audio_delay._delay_callback(MAIN_ZONE, "PS", "DELAY 140")
        audio_delay.notify_input_func("SAT/CBL")
        assert audio_delay.audio_delay == 140

    def test_a_source_change_clears_the_delay(self):
        """Check that the delay of the previous source is not reported."""
        audio_delay = DenonAVRAudioDelay()
        # pylint: disable=protected-access
        audio_delay._delay_callback(MAIN_ZONE, "PS", "DELAY 140")
        audio_delay.notify_input_func("SAT/CBL")
        audio_delay.notify_input_func("NET")
        assert audio_delay.audio_delay is None

    def test_the_same_source_keeps_the_delay(self):
        """Check that an update without a source change changes nothing."""
        audio_delay = DenonAVRAudioDelay()
        # pylint: disable=protected-access
        audio_delay.notify_input_func("SAT/CBL")
        audio_delay._delay_callback(MAIN_ZONE, "PS", "DELAY 140")
        audio_delay.notify_input_func("SAT/CBL")
        assert audio_delay.audio_delay == 140

    def test_a_source_change_keeps_the_pushed_delay_on_telnet(self):
        """Check that the value the receiver pushed is not cleared."""
        audio_delay = DenonAVRAudioDelay()
        # pylint: disable=protected-access
        audio_delay.notify_input_func("SAT/CBL")
        with mock.patch.object(
            type(audio_delay._device),
            "telnet_available",
            mock.PropertyMock(return_value=True),
        ):
            # The receiver pushes the new source's delay before the status
            # update that reports the source change arrives
            audio_delay._delay_callback(MAIN_ZONE, "PS", "DELAY 000")
            audio_delay.notify_input_func("NET")

        assert audio_delay.audio_delay == 0
