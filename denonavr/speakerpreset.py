#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
This module implements the speaker preset setting of Denon AVR receivers.

:copyright: (c) 2020 by Oliver Goetz.
:license: MIT, see LICENSE for more details.
"""

import asyncio
import logging
from collections.abc import Hashable
from typing import List, Optional

import attr

from .appcommand import AppCommands
from .const import DENON_ATTR_SETATTR, SPEAKER_PRESETS_FALLBACK
from .exceptions import (
    AvrCommandError,
    AvrIncompleteResponseError,
    AvrProcessingError,
    AvrRequestError,
)
from .foundation import DenonAVRFoundation

_LOGGER = logging.getLogger(__name__)


@attr.s(auto_attribs=True, on_setattr=DENON_ATTR_SETATTR)
class DenonAVRSpeakerPreset(DenonAVRFoundation):
    """Speaker preset setting."""

    _speaker_preset: Optional[int] = attr.ib(
        converter=attr.converters.optional(int), default=None
    )
    _speaker_preset_list: List[int] = attr.ib(
        converter=list, default=SPEAKER_PRESETS_FALLBACK
    )
    _setup_lock: asyncio.Lock = attr.ib(default=attr.Factory(asyncio.Lock))

    # Update tags for attributes
    # AppCommand0300.xml interface
    appcommand0300_attrs = {AppCommands.GetSpeakerPreset: None}

    async def async_setup(self) -> None:
        """Ensure that the instance is initialized."""
        async with self._setup_lock:
            _LOGGER.debug("Starting speaker preset setup")

            # Add tags for a potential AppCommand0300.xml update
            for tag in self.appcommand0300_attrs:
                self._device.api.add_appcommand0300_update_tag(tag)

            await self.async_update_speaker_preset_list()

            self._device.telnet_api.register_callback(
                "SP", self._speaker_preset_callback
            )

            self._is_setup = True
            _LOGGER.debug("Finished speaker preset setup")

    async def async_update_speaker_preset_list(self) -> None:
        """Read the presets this receiver offers from Deviceinfo.xml.

        Keeps the fallback when the document is unreachable or does not
        describe the setting: plenty of models that accept SPPR do not
        enumerate it, so an absent element means "not described", not
        "not supported".
        """
        try:
            # Keyed on the api, which every zone shares by reference, not on
            # the per-zone device: the document and the setting are both
            # receiver-wide, so one fetch serves every zone.
            xml = await self._device.api.async_get_xml(
                self._device.urls.deviceinfo, cache_id=id(self._device.api)
            )
        except AvrRequestError as err:
            _LOGGER.debug("Error getting the speaker preset list: %s", err)
            return

        presets = []
        preset_path = ".//DeviceCapabilities/Setup/SpeakerPreset/List/Value"
        for value in xml.findall(preset_path):
            try:
                presets.append(int(value.findtext("CmdNo")))
            except (TypeError, ValueError):
                _LOGGER.debug("Skipping a speaker preset without a usable CmdNo")

        if presets:
            self._speaker_preset_list = presets

    def _speaker_preset_callback(self, zone: str, event: str, parameter: str) -> None:
        """Handle a speaker preset change event."""
        if parameter[0:2] == "PR":
            self._speaker_preset = parameter[3:]

    async def async_update(
        self, global_update: bool = False, cache_id: Optional[Hashable] = None
    ) -> None:
        """Update speaker preset asynchronously."""
        _LOGGER.debug("Starting speaker preset update")
        # Ensure instance is setup before updating
        if not self._is_setup:
            await self.async_setup()

        # Update state
        await self.async_update_speaker_preset(
            global_update=global_update, cache_id=cache_id
        )
        _LOGGER.debug("Finished speaker preset update")

    async def async_update_speaker_preset(
        self, global_update: bool = False, cache_id: Optional[Hashable] = None
    ) -> None:
        """Update speaker preset status of device."""
        if self._device.use_avr_2016_update is None:
            raise AvrProcessingError(
                "Device is not setup correctly, update method not set"
            )

        # The speaker preset is only available for avr 2016 update
        if self._device.use_avr_2016_update:
            try:
                await self.async_update_attrs_appcommand(
                    self.appcommand0300_attrs,
                    appcommand0300=True,
                    global_update=global_update,
                    cache_id=cache_id,
                )
            except (AvrProcessingError, AvrIncompleteResponseError) as err:
                # Don't raise an error here, because not all devices support
                # it. A device that does not know one of the tags of the
                # shared AppCommand0300.xml request answers it short, which
                # arrives as an incomplete response.
                _LOGGER.debug("Updating speaker preset failed: %s", err)

    ##############
    # Properties #
    ##############
    @property
    def speaker_preset(self) -> Optional[int]:
        """
        Return the speaker preset for the device.

        Over HTTP this is only known after async_update_speaker_preset().

        The values this receiver accepts are speaker_preset_list.
        """
        return self._speaker_preset

    @property
    def speaker_preset_list(self) -> List[int]:
        """Return the preset numbers this receiver accepts."""
        return list(self._speaker_preset_list)

    ##########
    # Setter #
    ##########
    async def async_speaker_preset(self, preset: int) -> None:
        """
        Set speaker preset on receiver.

        Valid preset values are speaker_preset_list.
        """
        if preset not in self._speaker_preset_list:
            raise AvrCommandError(
                "Speaker preset number must be one of "
                f"{self._speaker_preset_list}, got {preset}"
            )

        if self._device.telnet_available:
            await self._device.telnet_api.async_send_commands(
                self._device.telnet_commands.command_speaker_preset.format(
                    number=preset
                )
            )
        else:
            await self._device.api.async_get_command(
                self._device.urls.command_speaker_preset.format(number=preset)
            )

    async def async_speaker_preset_toggle(self) -> None:
        """
        Toggle speaker preset on receiver.

        The preset switched away from is the one that was last read, so this
        needs either Telnet or an update of the setting beforehand.
        """
        presets = self._speaker_preset_list
        try:
            position = presets.index(self._speaker_preset)
        except ValueError:
            # Nothing read yet, or a value this receiver does not list
            position = -1
        await self.async_speaker_preset(presets[(position + 1) % len(presets)])


def speaker_preset_factory(instance: DenonAVRFoundation) -> DenonAVRSpeakerPreset:
    """Create DenonAVRSpeakerPreset at receiver instances."""
    # pylint: disable=protected-access
    new = DenonAVRSpeakerPreset(device=instance._device)
    return new
