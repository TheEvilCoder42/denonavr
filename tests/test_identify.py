#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for receiver identification when port 80 redirects to HTTPS."""

# pylint: disable=protected-access

import httpx
import pytest
from pytest_httpx import HTTPXMock

import denonavr
from denonavr.const import AVR, AVR_X_2016
from denonavr.exceptions import AvrNetworkError, AvrTimoutError

HOST = "10.0.0.0"
URL_80 = f"http://{HOST}/goform/Deviceinfo.xml"
URL_8080 = f"http://{HOST}:8080/goform/Deviceinfo.xml"
HTTPS_LOCATION = f"https://{HOST}/goform/Deviceinfo.xml"


def _deviceinfo(comm_api_vers: str, model_name: str = "AVR-S750H") -> str:
    return (
        "<Device_Info><CommApiVers>{}</CommApiVers>"
        "<ModelName>{}</ModelName></Device_Info>"
    ).format(comm_api_vers, model_name)


def _redirect_80(httpx_mock: HTTPXMock, status: int, location: str) -> None:
    httpx_mock.add_response(
        url=URL_80, status_code=status, headers={"Location": location}
    )


@pytest.mark.asyncio
async def test_https_redirect_with_8080_timeout_raises(httpx_mock: HTTPXMock) -> None:
    """Raise the 8080 timeout instead of falling back to port 80."""
    _redirect_80(httpx_mock, 301, HTTPS_LOCATION)
    httpx_mock.add_exception(httpx.ReadTimeout("timeout"), url=URL_8080)

    avr = denonavr.DenonAVR(HOST)
    with pytest.raises(AvrTimoutError):
        await avr._device.async_identify_receiver()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "port_8080",
    [
        pytest.param({"status_code": 503}, id="8080-http-error"),
        pytest.param({"text": _deviceinfo("0100", "Foo")}, id="8080-rejected-document"),
    ],
)
async def test_https_redirect_without_identified_8080_raises(
    httpx_mock: HTTPXMock, port_8080: dict
) -> None:
    """Raise a network error instead of falling back to port 80."""
    _redirect_80(httpx_mock, 301, HTTPS_LOCATION)
    httpx_mock.add_response(url=URL_8080, **port_8080)

    avr = denonavr.DenonAVR(HOST)
    with pytest.raises(AvrNetworkError):
        await avr._device.async_identify_receiver()


@pytest.mark.asyncio
async def test_https_redirect_with_avr_x_2016_on_8080(httpx_mock: HTTPXMock) -> None:
    """Identify as AVR-X 2016 on port 8080 as before."""
    _redirect_80(httpx_mock, 301, HTTPS_LOCATION)
    httpx_mock.add_response(url=URL_8080, text=_deviceinfo("0301"))

    avr = denonavr.DenonAVR(HOST)
    await avr._device.async_identify_receiver()
    assert avr._device.receiver == AVR_X_2016
    assert avr._device.api.port == AVR_X_2016.port


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "location"),
    [
        pytest.param(404, None, id="80-not-found"),
        pytest.param(
            302, f"http://{HOST}/goform/Deviceinfo.xml", id="80-http-redirect"
        ),
        pytest.param(
            301, "https://other.example/goform/Deviceinfo.xml", id="80-other-host"
        ),
    ],
)
async def test_no_https_redirect_falls_back_to_avr(
    httpx_mock: HTTPXMock, status: int, location: str | None
) -> None:
    """Fall back to AVR on port 80 unless port 80 redirects to HTTPS."""
    headers = {"Location": location} if location else None
    httpx_mock.add_response(url=URL_80, status_code=status, headers=headers)
    httpx_mock.add_exception(httpx.ConnectError("refused"), url=URL_8080)

    avr = denonavr.DenonAVR(HOST)
    await avr._device.async_identify_receiver()
    assert avr._device.receiver == AVR
    assert avr._device.api.port == AVR.port
