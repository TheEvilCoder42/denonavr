#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
This module covers tests of the speaker preset setting.

:copyright: (c) 2016 by Oliver Goetz.
:license: MIT, see LICENSE for more details.
"""

from typing import Optional
from unittest import mock

import pytest
from pytest_httpx import HTTPXMock

import denonavr
from denonavr.appcommand import AppCommands
from denonavr.const import MAIN_ZONE, ZONE2
from denonavr.exceptions import AvrCommandError, AvrProcessingError
from denonavr.speakerpreset import DenonAVRSpeakerPreset

APPCOMMAND0300_URL = "/goform/AppCommand0300.xml"
DEVICEINFO_URL = "/goform/Deviceinfo.xml"
DIRECT_URL = "/goform/formiPhoneAppDirect.xml"
FAKE_IP = "10.0.0.0"
# What a bare module instance talks to, unlike DenonAVR(FAKE_IP).
DEFAULT_HOST = "localhost"

# Two presets, from a real dump. Every model captured so far lists two.
DEVICEINFO_TWO_PRESETS = "AVC-X3700H-Deviceinfo-8080.xml"
# A model whose Deviceinfo.xml does not describe the setting at all.
DEVICEINFO_NO_PRESETS = "AVR-X4300H-Deviceinfo-8080.xml"
# Synthetic - see the file's own comment.
DEVICEINFO_FOUR_PRESETS = "Synthetic-Deviceinfo-speakerpreset-four.xml"


def get_sample_content(filename: str) -> str:
    """Return sample content form file."""
    with open(f"tests/xml/{filename}", encoding="utf-8") as file:
        return file.read()


def add_deviceinfo_response(
    httpx_mock: HTTPXMock, filename: str, host: str = DEFAULT_HOST
) -> None:
    """Answer the Deviceinfo.xml fetch that setup makes."""
    httpx_mock.add_response(
        url=f"http://{host}{DEVICEINFO_URL}",
        content=get_sample_content(filename),
    )


def appcommand0300_requests(httpx_mock: HTTPXMock) -> list:
    """Return the AppCommand0300.xml requests that were actually sent."""
    return [
        request
        for request in httpx_mock.get_requests()
        if request.url.path == APPCOMMAND0300_URL
    ]


def speaker_preset_instance() -> DenonAVRSpeakerPreset:
    """Return a speaker preset instance that is ready to be updated."""
    speaker_preset = DenonAVRSpeakerPreset()
    # pylint: disable=protected-access
    speaker_preset._device.use_avr_2016_update = True
    return speaker_preset


async def setup_speaker_preset(
    httpx_mock: HTTPXMock, filename: str
) -> DenonAVRSpeakerPreset:
    """Return an instance set up against one model's Deviceinfo.xml."""
    add_deviceinfo_response(httpx_mock, filename)
    speaker_preset = speaker_preset_instance()
    await speaker_preset.async_setup()
    return speaker_preset


class TestSpeakerPresetUpdate:
    """Test case for reading the speaker preset from AppCommand0300.xml."""

    @pytest.mark.asyncio
    async def test_the_preset_is_read(self, httpx_mock: HTTPXMock):
        """Check that the preset parameter is picked up as an int."""
        add_deviceinfo_response(httpx_mock, DEVICEINFO_TWO_PRESETS)
        httpx_mock.add_response(
            content=get_sample_content("AVR-X1700H-AppCommand0300-speakerpreset.xml")
        )
        speaker_preset = speaker_preset_instance()
        await speaker_preset.async_update()

        assert speaker_preset.speaker_preset == 1

    @pytest.mark.asyncio
    async def test_the_param_name_is_sent(self, httpx_mock: HTTPXMock):
        """Check that the one param name the receiver answers to is used."""
        # The receiver recognises "preset" and nothing else; a wrong param
        # name takes the whole command out of the response
        add_deviceinfo_response(httpx_mock, DEVICEINFO_TWO_PRESETS)
        httpx_mock.add_response(
            content=get_sample_content("AVR-X1700H-AppCommand0300-speakerpreset.xml")
        )
        speaker_preset = speaker_preset_instance()
        await speaker_preset.async_update()

        body = httpx_mock.get_requests()[-1].content.decode("utf-8")
        assert "<name>GetSpeakerPreset</name>" in body
        assert '<param name="preset"' in body

    @pytest.mark.asyncio
    async def test_a_device_without_the_command_does_not_raise(
        self, httpx_mock: HTTPXMock
    ):
        """Check that an unanswered command leaves the preset unknown."""
        add_deviceinfo_response(httpx_mock, DEVICEINFO_TWO_PRESETS)
        httpx_mock.add_response(
            content=get_sample_content(
                "AVR-X1700H-AppCommand0300-speakerpreset-unsupported.xml"
            )
        )
        speaker_preset = speaker_preset_instance()
        await speaker_preset.async_update()

        assert speaker_preset.speaker_preset is None

    @pytest.mark.asyncio
    async def test_an_unsetup_device_raises(self):
        """Check that an unknown update method is reported."""
        speaker_preset = DenonAVRSpeakerPreset()
        with pytest.raises(AvrProcessingError):
            await speaker_preset.async_update()

    @pytest.mark.asyncio
    async def test_the_tag_is_registered_for_a_global_update(
        self, httpx_mock: HTTPXMock
    ):
        """Check that a global AppCommand0300.xml update carries the tag."""
        speaker_preset = await setup_speaker_preset(httpx_mock, DEVICEINFO_TWO_PRESETS)

        # pylint: disable=protected-access
        tags = speaker_preset._device.api._appcommand0300_update_tags
        assert [tag.name for tag in tags] == ["GetSpeakerPreset"]
        assert AppCommands.GetSpeakerPreset.param_list == tags[0].param_list

    @pytest.mark.asyncio
    async def test_the_preset_rides_a_request_another_update_made(
        self, httpx_mock: HTTPXMock
    ):
        """Check that sharing a cache id costs no second request.

        AppCommand0300.xml answers every registered tag at once, so the
        preset is already in the answer a refresh has in hand.
        """
        speaker_preset = await setup_speaker_preset(httpx_mock, DEVICEINFO_TWO_PRESETS)
        httpx_mock.add_response(
            content=get_sample_content("AVR-X1700H-AppCommand0300-speakerpreset.xml")
        )
        cache_id = "one refresh"

        # What audyssey and the audio delay do to share their request
        # pylint: disable=protected-access
        await speaker_preset._device.api.async_get_global_appcommand(
            appcommand0300=True, cache_id=cache_id
        )
        await speaker_preset.async_update(global_update=True, cache_id=cache_id)

        assert len(appcommand0300_requests(httpx_mock)) == 1
        assert speaker_preset.speaker_preset == 1

    @pytest.mark.asyncio
    async def test_the_facade_passes_the_cache_id_on(self, httpx_mock: HTTPXMock):
        """Check that a caller can batch the preset read through DenonAVR."""
        add_deviceinfo_response(httpx_mock, DEVICEINFO_TWO_PRESETS, host=FAKE_IP)
        httpx_mock.add_response(
            content=get_sample_content("AVR-X1700H-AppCommand0300-speakerpreset.xml")
        )
        denon = denonavr.DenonAVR(FAKE_IP)
        # pylint: disable=protected-access
        denon._device.use_avr_2016_update = True
        await denon.speakerpreset.async_setup()
        cache_id = "one refresh"

        await denon._device.api.async_get_global_appcommand(
            appcommand0300=True, cache_id=cache_id
        )
        await denon.async_update_speaker_preset(global_update=True, cache_id=cache_id)

        assert len(appcommand0300_requests(httpx_mock)) == 1
        assert denon.speaker_preset == 1


class TestSpeakerPresetCallback:
    """Test case for the SPPR telnet callback."""

    @pytest.mark.parametrize(
        "parameter,expected",
        [
            pytest.param("PR 1", 1, id="preset-1"),
            pytest.param("PR 2", 2, id="preset-2"),
        ],
    )
    def test_a_preset_event_is_read(self, parameter: str, expected: int):
        """Check that a speaker preset event updates the preset."""
        speaker_preset = DenonAVRSpeakerPreset()
        # pylint: disable=protected-access
        speaker_preset._speaker_preset_callback(MAIN_ZONE, "SP", parameter)
        assert speaker_preset.speaker_preset == expected

    def test_another_sp_event_is_ignored(self):
        """Check that another SP event does not touch the preset."""
        speaker_preset = DenonAVRSpeakerPreset()
        # pylint: disable=protected-access
        speaker_preset._speaker_preset_callback(MAIN_ZONE, "SP", "ATT ON")
        assert speaker_preset.speaker_preset is None

    def test_the_zone_is_not_filtered(self):
        """Check that the preset is taken from any zone."""
        # The setting belongs to the device rather than to a zone, and the
        # receiver reports it without a zone prefix
        speaker_preset = DenonAVRSpeakerPreset()
        # pylint: disable=protected-access
        speaker_preset._speaker_preset_callback(ZONE2, "SP", "PR 2")
        assert speaker_preset.speaker_preset == 2


class TestSetSpeakerPreset:
    """Test case for the speaker preset setter."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("preset", [1, 2])
    async def test_the_preset_is_sent(self, httpx_mock: HTTPXMock, preset: int):
        """Check that the preset number ends up in the direct command."""
        httpx_mock.add_response()
        speaker_preset = DenonAVRSpeakerPreset()
        await speaker_preset.async_speaker_preset(preset)

        request = httpx_mock.get_requests()[0]
        assert request.url.path == DIRECT_URL
        assert str(request.url.query, "utf-8") == f"SPPR%20{preset}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("preset", [0, 3])
    async def test_a_preset_out_of_range_is_rejected(self, preset: int):
        """Check that a preset the receiver does not have raises."""
        speaker_preset = DenonAVRSpeakerPreset()
        with pytest.raises(AvrCommandError):
            await speaker_preset.async_speaker_preset(preset)

    @pytest.mark.asyncio
    async def test_telnet_is_preferred(self):
        """Check that the command is sent over telnet when it is available."""
        speaker_preset = DenonAVRSpeakerPreset()
        # pylint: disable=protected-access
        telnet_api = speaker_preset._device.telnet_api
        with mock.patch.object(
            type(speaker_preset._device),
            "telnet_available",
            mock.PropertyMock(return_value=True),
        ), mock.patch.object(
            telnet_api, "async_send_commands", mock.AsyncMock()
        ) as send:
            await speaker_preset.async_speaker_preset(1)

        send.assert_awaited_once_with("SPPR 1")


class TestSpeakerPresetToggle:
    """Test case for the speaker preset toggle."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "current,expected",
        [
            pytest.param(1, 2, id="1-to-2"),
            pytest.param(2, 1, id="2-to-1"),
            pytest.param(None, 1, id="unknown-to-first"),
        ],
    )
    async def test_the_preset_that_was_read_is_toggled(
        self, httpx_mock: HTTPXMock, current: Optional[int], expected: int
    ):
        """Check which preset the toggle switches to."""
        httpx_mock.add_response()
        speaker_preset = DenonAVRSpeakerPreset()
        # pylint: disable=protected-access
        speaker_preset._speaker_preset = current
        await speaker_preset.async_speaker_preset_toggle()

        query = str(httpx_mock.get_requests()[0].url.query, "utf-8")
        assert query == f"SPPR%20{expected}"


class TestSpeakerPresetOnDenonAVR:
    """Test case for the speaker preset members of DenonAVR."""

    @pytest.mark.asyncio
    async def test_the_update_entry_point_reads_the_preset(self, httpx_mock: HTTPXMock):
        """Check that an HTTP only device knows its preset after an update."""
        add_deviceinfo_response(httpx_mock, DEVICEINFO_TWO_PRESETS, host=FAKE_IP)
        httpx_mock.add_response(
            content=get_sample_content("AVR-X1700H-AppCommand0300-speakerpreset.xml")
        )
        denon = denonavr.DenonAVR(FAKE_IP)
        # pylint: disable=protected-access
        denon._device.use_avr_2016_update = True

        await denon.async_update_speaker_preset()

        assert denon.speaker_preset == 1
        appcommand_requests = [
            request
            for request in httpx_mock.get_requests()
            if request.url.path == APPCOMMAND0300_URL
        ]
        assert len(appcommand_requests) == 1

    @pytest.mark.asyncio
    async def test_the_setter_delegates_to_the_module(self, httpx_mock: HTTPXMock):
        """Check that the public setter still reaches the receiver."""
        httpx_mock.add_response()
        denon = denonavr.DenonAVR(FAKE_IP)

        await denon.async_speaker_preset(2)

        request = httpx_mock.get_requests()[0]
        assert request.url.path == DIRECT_URL
        assert str(request.url.query, "utf-8") == "SPPR%202"


class TestSpeakerPresetList:
    """Test case for the preset list Deviceinfo.xml declares."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "filename,expected",
        [
            pytest.param(DEVICEINFO_TWO_PRESETS, [1, 2], id="declared-two"),
            pytest.param(DEVICEINFO_FOUR_PRESETS, [1, 2, 3, 4], id="declared-four"),
            pytest.param(DEVICEINFO_NO_PRESETS, [1, 2], id="not-declared"),
        ],
    )
    async def test_the_list_comes_from_the_receiver(
        self, httpx_mock: HTTPXMock, filename: str, expected: list
    ):
        """Check that the receiver's own list bounds the setting."""
        speaker_preset = await setup_speaker_preset(httpx_mock, filename)

        assert speaker_preset.speaker_preset_list == expected

    @pytest.mark.asyncio
    async def test_an_unreachable_deviceinfo_keeps_the_fallback(self):
        """Check that setup survives a receiver that has no Deviceinfo.xml."""
        speaker_preset = speaker_preset_instance()
        await speaker_preset.async_setup()

        assert speaker_preset.speaker_preset_list == [1, 2]

    @pytest.mark.asyncio
    async def test_a_preset_the_receiver_lists_is_accepted(self, httpx_mock: HTTPXMock):
        """Check that a preset beyond the old hardcoded bound is sent."""
        speaker_preset = await setup_speaker_preset(httpx_mock, DEVICEINFO_FOUR_PRESETS)
        httpx_mock.add_response()

        await speaker_preset.async_speaker_preset(4)

        request = httpx_mock.get_requests()[-1]
        assert str(request.url.query, "utf-8") == "SPPR%204"

    @pytest.mark.asyncio
    async def test_a_preset_the_receiver_does_not_list_is_rejected(
        self, httpx_mock: HTTPXMock
    ):
        """Check that the bound follows the receiver rather than a constant."""
        speaker_preset = await setup_speaker_preset(httpx_mock, DEVICEINFO_FOUR_PRESETS)

        with pytest.raises(AvrCommandError):
            await speaker_preset.async_speaker_preset(5)

    @pytest.mark.asyncio
    async def test_the_toggle_cycles_through_every_preset(self, httpx_mock: HTTPXMock):
        """Check that the toggle can reach presets the old one could not."""
        speaker_preset = await setup_speaker_preset(httpx_mock, DEVICEINFO_FOUR_PRESETS)
        httpx_mock.add_response()
        # pylint: disable=protected-access
        speaker_preset._speaker_preset = 3

        await speaker_preset.async_speaker_preset_toggle()

        query = str(httpx_mock.get_requests()[-1].url.query, "utf-8")
        assert query == "SPPR%204"

    @pytest.mark.asyncio
    async def test_the_toggle_wraps_at_the_end_of_the_list(self, httpx_mock: HTTPXMock):
        """Check that the last preset cycles back to the first."""
        speaker_preset = await setup_speaker_preset(httpx_mock, DEVICEINFO_FOUR_PRESETS)
        httpx_mock.add_response()
        # pylint: disable=protected-access
        speaker_preset._speaker_preset = 4

        await speaker_preset.async_speaker_preset_toggle()

        query = str(httpx_mock.get_requests()[-1].url.query, "utf-8")
        assert query == "SPPR%201"

    @pytest.mark.asyncio
    async def test_the_list_is_exposed_on_denonavr(self, httpx_mock: HTTPXMock):
        """Check that the facade reports the same list."""
        add_deviceinfo_response(httpx_mock, DEVICEINFO_FOUR_PRESETS, host=FAKE_IP)
        denon = denonavr.DenonAVR(FAKE_IP)
        # pylint: disable=protected-access
        await denon.speakerpreset.async_setup()

        assert denon.speaker_preset_list == [1, 2, 3, 4]
