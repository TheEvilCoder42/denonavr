#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
This module implements the speaker preset setting of Denon AVR receivers.

:copyright: (c) 2020 by Oliver Goetz.
:license: MIT, see LICENSE for more details.
"""

import logging
from collections.abc import Hashable
from typing import Optional

import attr

from .appcommand import AppCommands
from .const import DENON_ATTR_SETATTR
from .exceptions import (
    AvrCommandError,
    AvrIncompleteResponseError,
    AvrProcessingError,
)
from .foundation import DenonAVRFoundation

_LOGGER = logging.getLogger(__name__)


@attr.s(auto_attribs=True, on_setattr=DENON_ATTR_SETATTR)
class DenonAVRSpeakerPreset(DenonAVRFoundation):
    """Speaker preset setting."""

    _speaker_preset: Optional[int] = attr.ib(
        converter=attr.converters.optional(int), default=None
    )

    # Update tags for attributes
    # AppCommand0300.xml interface
    appcommand0300_attrs = {AppCommands.GetSpeakerPreset: None}

    def setup(self) -> None:
        """Ensure that the instance is initialized."""
        # Add tags for a potential AppCommand0300.xml update
        for tag in self.appcommand0300_attrs:
            self._device.api.add_appcommand0300_update_tag(tag)

        self._device.telnet_api.register_callback("SP", self._speaker_preset_callback)

        self._is_setup = True

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
            self.setup()

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

        Possible values are: "1", "2"
        """
        return self._speaker_preset

    ##########
    # Setter #
    ##########
    async def async_speaker_preset(self, preset: int) -> None:
        """
        Set speaker preset on receiver.

        Valid preset values are 1-2.
        """
        if preset < 1 or preset > 2:
            raise AvrCommandError("Speaker preset number must be 1 or 2")

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
        speaker_preset = 1 if self._speaker_preset == 2 else 2
        await self.async_speaker_preset(speaker_preset)


def speaker_preset_factory(instance: DenonAVRFoundation) -> DenonAVRSpeakerPreset:
    """Create DenonAVRSpeakerPreset at receiver instances."""
    # pylint: disable=protected-access
    new = DenonAVRSpeakerPreset(device=instance._device)
    return new
