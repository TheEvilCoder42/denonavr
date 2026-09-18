#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
This module covers tests of the GetSurroundParameter reads.

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
from denonavr.exceptions import AvrProcessingError
from denonavr.volume import DenonAVRVolume, convert_subwoofer_output

APPCOMMAND_URL = "/goform/AppCommand.xml"
APPCOMMAND0300_URL = "/goform/AppCommand0300.xml"
DIRECT_URL = "/goform/formiPhoneAppDirect.xml"
FAKE_IP = "10.0.0.0"

# Every fixture reproduces a state the AVR-X1700H was measured in. The two
# parameters have never been readable in the same response: lfe answers only
# while the incoming stream carries an LFE channel, sw only in MSSTEREO
BITSTREAM = "AVR-X1700H-AppCommand0300-surroundparameter-bitstream.xml"
STEREO = "AVR-X1700H-AppCommand0300-surroundparameter-stereo.xml"
STEREO_OFF = "AVR-X1700H-AppCommand0300-surroundparameter-stereo-off.xml"
NOT_APPLICABLE = "AVR-X1700H-AppCommand0300-surroundparameter-notapplicable.xml"
UNSUPPORTED = "AVR-X1700H-AppCommand0300-surroundparameter-unsupported.xml"


def get_sample_content(filename: str) -> str:
    """Return sample content form file."""
    with open(f"tests/xml/{filename}", encoding="utf-8") as file:
        return file.read()


def volume_instance() -> DenonAVRVolume:
    """Return a volume instance that is ready to be updated."""
    volume = DenonAVRVolume()
    # pylint: disable=protected-access
    volume._device.use_avr_2016_update = True
    return volume


class TestLfeLevelUpdate:
    """Test case for reading the LFE level from AppCommand0300.xml."""

    @pytest.mark.asyncio
    async def test_the_http_value_is_already_signed(self, httpx_mock: HTTPXMock):
        """Check that the level is taken as sent, without a sign flip."""
        # The receiver sends -5 over HTTP and the magnitude 05 over telnet, so
        # the telnet callback's * -1 must not be reused here: it would land on
        # +5, which is inside the plausible range and would go unnoticed
        httpx_mock.add_response(content=get_sample_content(BITSTREAM))
        volume = volume_instance()
        await volume.async_update_lfe()

        assert volume.lfe == -5

    def test_the_telnet_event_lands_on_the_same_number(self):
        """Check that both transports report the same level."""
        volume = DenonAVRVolume()
        # pylint: disable=protected-access
        volume._lfe_callback(MAIN_ZONE, "PS", "LFE 05")

        assert volume.lfe == -5

    def test_the_top_of_the_range_is_read(self):
        """Check that a level of 0 is a level and not an absent value."""
        volume = DenonAVRVolume()
        # pylint: disable=protected-access
        volume._lfe_callback(MAIN_ZONE, "PS", "LFE 00")

        assert volume.lfe == 0


class TestSubwooferOutputUpdate:
    """Test case for reading the subwoofer output from AppCommand0300.xml."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "content,expected",
        [
            pytest.param(STEREO, True, id="on"),
            pytest.param(STEREO_OFF, False, id="off"),
        ],
    )
    async def test_the_state_is_read(
        self, httpx_mock: HTTPXMock, content: str, expected: bool
    ):
        """Check that the 1/0 the receiver sends becomes a bool."""
        httpx_mock.add_response(content=get_sample_content(content))
        volume = volume_instance()
        await volume.async_update_lfe()

        assert volume.subwoofer is expected

    @pytest.mark.parametrize(
        "value,expected",
        [
            pytest.param("ON", True, id="telnet-on"),
            pytest.param("OFF", False, id="telnet-off"),
            pytest.param("1", True, id="http-on"),
            pytest.param("0", False, id="http-off"),
            pytest.param("", None, id="empty"),
            pytest.param("2", None, id="unknown"),
        ],
    )
    def test_both_spellings_are_converted(self, value: str, expected: Optional[bool]):
        """Check that the converter takes the telnet and the HTTP spelling."""
        assert convert_subwoofer_output(value) is expected


class TestUnreadableParameters:
    """Test case for the control attribute of GetSurroundParameter."""

    @pytest.mark.asyncio
    async def test_a_not_applicable_parameter_is_unknown(self, httpx_mock: HTTPXMock):
        """Check that an empty body is reported as unknown, not as a value."""
        # Rendering an unreadable subwoofer toggle as "off" is worse than
        # rendering it unknown, because the user's next action is to press it
        httpx_mock.add_response(content=get_sample_content(NOT_APPLICABLE))
        volume = volume_instance()
        await volume.async_update_lfe()

        assert volume.lfe is None
        assert volume.subwoofer is None

    @pytest.mark.asyncio
    async def test_the_control_attribute_is_read(self, httpx_mock: HTTPXMock):
        """Check that the readability of each parameter is picked up."""
        httpx_mock.add_response(content=get_sample_content(NOT_APPLICABLE))
        volume = volume_instance()
        await volume.async_update_lfe()

        # pylint: disable=protected-access
        assert volume._lfe_control == 0
        assert volume._subwoofer_control == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("content", "lfe_control", "subwoofer_control"),
        [
            pytest.param(BITSTREAM, 2, 0, id="bitstream"),
            pytest.param(STEREO, 0, 2, id="stereo"),
        ],
    )
    async def test_the_two_parameters_are_gated_separately(
        self,
        httpx_mock: HTTPXMock,
        content: str,
        lfe_control: int,
        subwoofer_control: int,
    ):
        """Check that each parameter carries its own readable marker."""
        # Measured on an AVR-X1700H: the two are never readable at once, so a
        # reader that took one parameter's control for the other's would
        # report a value the receiver declined to give in every state
        httpx_mock.add_response(content=get_sample_content(content))
        volume = volume_instance()
        await volume.async_update_lfe()

        # pylint: disable=protected-access
        assert volume._lfe_control == lfe_control
        assert volume._subwoofer_control == subwoofer_control

    @pytest.mark.asyncio
    async def test_the_subwoofer_is_unknown_under_a_bitstream(
        self, httpx_mock: HTTPXMock
    ):
        """Check that an unreadable subwoofer output is not reported as off."""
        # The receiver answers control="0" for sw while a bitstream plays,
        # with GetSubwooferLevel reporting status 1 and telnet PSSWR ON in the
        # same moment: the subwoofer is on and the HTTP read declines to say so
        httpx_mock.add_response(content=get_sample_content(BITSTREAM))
        volume = volume_instance()
        await volume.async_update_lfe()

        assert volume.lfe == -5
        assert volume.subwoofer is None


class TestSurroundParameterRequest:
    """Test case for the request the update sends."""

    @pytest.mark.asyncio
    async def test_the_param_names_are_sent(self, httpx_mock: HTTPXMock):
        """Check that the two param names the receiver answers to are used."""
        # The receiver recognises "lfe" and "sw"; "subwoofer" is rejected and
        # a wrong param name takes the whole command out of the response
        httpx_mock.add_response(content=get_sample_content(BITSTREAM))
        volume = volume_instance()
        await volume.async_update_lfe()

        request = httpx_mock.get_requests()[0]
        body = request.content.decode("utf-8")
        assert request.url.path == APPCOMMAND0300_URL
        assert "<name>GetSurroundParameter</name>" in body
        assert '<param name="lfe"' in body
        assert '<param name="sw"' in body

    @pytest.mark.asyncio
    async def test_a_device_without_the_command_does_not_raise(
        self, httpx_mock: HTTPXMock
    ):
        """Check that an unanswered command leaves both values unknown."""
        httpx_mock.add_response(content=get_sample_content(UNSUPPORTED))
        volume = volume_instance()
        await volume.async_update_lfe()

        assert volume.lfe is None
        assert volume.subwoofer is None

    @pytest.mark.asyncio
    async def test_an_unsetup_device_raises(self):
        """Check that an unknown update method is reported."""
        volume = DenonAVRVolume()
        with pytest.raises(AvrProcessingError):
            await volume.async_update_lfe()

    def test_the_tag_is_registered_for_a_global_update(self):
        """Check that a global AppCommand0300.xml update carries the tag."""
        volume = DenonAVRVolume()
        volume.setup()

        # pylint: disable=protected-access
        tags = volume._device.api._appcommand0300_update_tags
        assert [tag.name for tag in tags] == ["GetSurroundParameter"]
        assert AppCommands.GetSurroundParameter.param_list == tags[0].param_list

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "zone,expected",
        [
            pytest.param(MAIN_ZONE, -42.5, id="main"),
            pytest.param(ZONE2, -40.0, id="zone2"),
        ],
    )
    async def test_the_every_poll_update_does_not_request_it(
        self, httpx_mock: HTTPXMock, zone: str, expected: float
    ):
        """Check that the poll path stays on AppCommand.xml."""
        # async_update_volume runs for every zone on every poll; adding a
        # 0300 request to it would cost a POST per zone per cycle
        httpx_mock.add_response(
            content=get_sample_content("AVR-X1700H-AppCommand-update-volume.xml")
        )
        volume = volume_instance()
        # pylint: disable=protected-access
        volume._device.zone = zone
        await volume.async_update()

        paths = {request.url.path for request in httpx_mock.get_requests()}
        assert paths == {APPCOMMAND_URL}
        assert volume.volume == expected
        assert volume.lfe is None


class TestSubwooferToggle:
    """Test case for the subwoofer toggle over HTTP."""

    @pytest.mark.asyncio
    async def test_the_toggle_follows_the_http_read(self, httpx_mock: HTTPXMock):
        """Check that a toggle turns the subwoofer off once it reads on."""
        # Without the read the state stays None on an HTTP only device and
        # the toggle sends ON every time
        httpx_mock.add_response(content=get_sample_content(STEREO))
        volume = volume_instance()
        await volume.async_update_lfe()

        httpx_mock.add_response()
        await volume.async_subwoofer_toggle()

        request = httpx_mock.get_requests()[-1]
        assert request.url.path == DIRECT_URL
        assert str(request.url.query, "utf-8") == "PSSWR%20OFF"


class TestFacadeDelegation:
    """Test case for reaching both settings without going through .vol."""

    def test_the_lfe_level_is_forwarded(self):
        """Check that the LFE level reaches the volume module."""
        denon = denonavr.DenonAVR(FAKE_IP)
        # pylint: disable=protected-access
        assert denon.lfe is None
        denon.vol._lfe = "-2"
        assert denon.lfe == -2

    def test_the_subwoofer_output_is_forwarded(self):
        """Check that the subwoofer output state reaches the volume module."""
        denon = denonavr.DenonAVR(FAKE_IP)
        # pylint: disable=protected-access
        assert denon.subwoofer is None
        denon.vol._subwoofer = "1"
        assert denon.subwoofer is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "args"),
        [
            pytest.param("async_lfe", (-5,), id="lfe-set"),
            pytest.param("async_lfe_up", (), id="lfe-up"),
            pytest.param("async_lfe_down", (), id="lfe-down"),
            pytest.param("async_subwoofer_on", (), id="subwoofer-on"),
            pytest.param("async_subwoofer_off", (), id="subwoofer-off"),
            pytest.param("async_subwoofer_toggle", (), id="subwoofer-toggle"),
            pytest.param("async_update_lfe", (), id="update"),
        ],
    )
    async def test_the_setters_are_forwarded(self, method: str, args: tuple):
        """Check that the facade forwards rather than reimplementing."""
        denon = denonavr.DenonAVR(FAKE_IP)
        setattr(denon.vol, method, mock.AsyncMock())
        await getattr(denon, method)(*args)
        getattr(denon.vol, method).assert_awaited_once_with(*args)
