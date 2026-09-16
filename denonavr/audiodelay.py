#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
This module implements the audio delay settings of Denon AVR receivers.

:copyright: (c) 2020 by Oliver Goetz.
:license: MIT, see LICENSE for more details.
"""

import logging
from collections.abc import Hashable
from typing import Optional

import attr

from .appcommand import AppCommands
from .const import DENON_ATTR_SETATTR
from .exceptions import AvrCommandError, AvrProcessingError
from .foundation import DenonAVRFoundation, convert_string_int_bool

_LOGGER = logging.getLogger(__name__)


@attr.s(auto_attribs=True, on_setattr=DENON_ATTR_SETATTR)
class DenonAVRAudioDelay(DenonAVRFoundation):
    """Audio delay settings."""

    _audio_delay: Optional[int] = attr.ib(
        converter=attr.converters.optional(int), default=None
    )
    # The control attribute of GetAudioDelay is not a boolean, unlike the one
    # GetAudyssey returns: an AVR-X1700H answers with 2 for a readable value
    # and with 0 for one that does not apply right now
    _audio_delay_control: Optional[int] = attr.ib(
        converter=attr.converters.optional(int), default=None
    )
    _auto_lip_sync: Optional[bool] = attr.ib(
        converter=attr.converters.optional(convert_string_int_bool), default=None
    )
    _auto_lip_sync_control: Optional[int] = attr.ib(
        converter=attr.converters.optional(int), default=None
    )
    _tv_delay: Optional[int] = attr.ib(
        converter=attr.converters.optional(int), default=None
    )
    _tv_delay_control: Optional[int] = attr.ib(
        converter=attr.converters.optional(int), default=None
    )

    # Update tags for attributes
    # AppCommand0300.xml interface
    appcommand0300_attrs = {AppCommands.GetAudioDelay: None}

    def setup(self) -> None:
        """Ensure that the instance is initialized."""
        # Add tags for a potential AppCommand0300.xml update
        for tag in self.appcommand0300_attrs:
            self._device.api.add_appcommand0300_update_tag(tag)

        self._device.telnet_api.register_callback("PS", self._delay_callback)

        self._is_setup = True

    def _delay_callback(self, zone: str, event: str, parameter: str) -> None:
        """Handle a delay change event."""
        if zone == self._device.zone and parameter[0:5] == "DELAY":
            self._audio_delay = parameter[6:]

    async def async_update(
        self, global_update: bool = False, cache_id: Optional[Hashable] = None
    ) -> None:
        """Update audio delay asynchronously."""
        _LOGGER.debug("Starting audio delay update")
        # Ensure instance is setup before updating
        if not self._is_setup:
            self.setup()

        # Update state
        await self.async_update_audio_delay(
            global_update=global_update, cache_id=cache_id
        )
        _LOGGER.debug("Finished audio delay update")

    async def async_update_audio_delay(
        self, global_update: bool = False, cache_id: Optional[Hashable] = None
    ) -> None:
        """Update audio delay status of device."""
        if self._device.use_avr_2016_update is None:
            raise AvrProcessingError(
                "Device is not setup correctly, update method not set"
            )

        # The audio delay is only available for avr 2016 update
        if self._device.use_avr_2016_update:
            try:
                await self.async_update_attrs_appcommand(
                    self.appcommand0300_attrs,
                    appcommand0300=True,
                    global_update=global_update,
                    cache_id=cache_id,
                )
            except AvrProcessingError as err:
                # Don't raise an error here, because not all devices support it
                _LOGGER.debug("Updating audio delay failed: %s", err)

    ##############
    # Properties #
    ##############
    @property
    def audio_delay(self) -> Optional[int]:
        """
        Return the audio delay of the device in ms.

        The value is stored per input source on the receiver, so it is reset
        to None when the input source changes and is only known again after
        the next update.
        """
        return self._audio_delay

    @property
    def auto_lip_sync(self) -> Optional[bool]:
        """Return the auto lip sync status of the device."""
        return self._auto_lip_sync

    @property
    def tv_delay(self) -> Optional[int]:
        """
        Return the TV audio delay of the device in ms.

        This is None whenever the receiver reports the setting as not
        applicable, which it does while audio is playing.
        """
        return self._tv_delay

    ##########
    # Setter #
    ##########
    async def async_delay(self, delay: int) -> None:
        """
        Set the audio delay on the receiver.

        :param delay: Audio delay in ms. Valid values are 0-500.
        """
        if delay < 0 or delay > 500:
            raise AvrCommandError(f"Invalid audio delay: {delay}")

        # The receiver silently drops a value that is not three digits long
        value = f"{delay:03d}"
        if self._device.telnet_available:
            await self._device.telnet_api.async_send_commands(
                self._device.telnet_commands.command_delay.format(value=value)
            )
            return
        await self._device.api.async_get_command(
            self._device.urls.command_delay.format(value=value)
        )

    async def async_delay_up(self) -> None:
        """Increase the audio delay by one step."""
        if self._device.telnet_available:
            await self._device.telnet_api.async_send_commands(
                self._device.telnet_commands.command_delay_up
            )
            return
        await self._device.api.async_get_command(self._device.urls.command_delay_up)

    async def async_delay_down(self) -> None:
        """Decrease the audio delay by one step."""
        if self._device.telnet_available:
            await self._device.telnet_api.async_send_commands(
                self._device.telnet_commands.command_delay_down
            )
            return
        await self._device.api.async_get_command(self._device.urls.command_delay_down)


def audio_delay_factory(instance: DenonAVRFoundation) -> DenonAVRAudioDelay:
    """Create DenonAVRAudioDelay at receiver instances."""
    # pylint: disable=protected-access
    new = DenonAVRAudioDelay(device=instance._device)
    return new
