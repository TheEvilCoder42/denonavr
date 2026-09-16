#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
This module implements the handler for volume of Denon AVR receivers.

:copyright: (c) 2021 by Oliver Goetz.
:license: MIT, see LICENSE for more details.
"""

import logging
from collections.abc import Hashable
from typing import Dict, Optional, Union, get_args

import attr

from .appcommand import AppCommands
from .const import (
    CHANNEL_MAP,
    CHANNEL_MAP_REVERSE,
    CHANNEL_VOLUME_MAP,
    CHANNEL_VOLUME_MAP_REVERSE,
    DENON_ATTR_SETATTR,
    MAIN_ZONE,
    MAX_VOLUME_MAX,
    MAX_VOLUME_MIN,
    MAX_VOLUME_STEP,
    MAX_VOLUME_TELNET_EVENT,
    STATE_ON,
    SUBWOOFERS_MAP,
    SUBWOOFERS_MAP_REVERSE,
    VOLUME_MAX,
    VOLUME_MIN,
    Channels,
    Subwoofers,
)
from .exceptions import AvrCommandError, AvrProcessingError
from .foundation import (
    DenonAVRFoundation,
    convert_on_off_bool,
    convert_string_int_bool,
)

_LOGGER = logging.getLogger(__name__)


def convert_muted(value: str) -> bool:
    """Convert muted to bool."""
    return bool(value.lower() == STATE_ON)


def convert_volume(value: Union[float, str]) -> float:
    """Convert volume to float."""
    if value == "--":
        return -80.0
    return float(value)


def convert_telnet_volume(value: str) -> float:
    """
    Convert a telnet volume parameter to the relative dB scale.

    Telnet reports volume on the absolute scale, as two digits or as three
    where the last one is a half step: "56" is -24.0 and "565" is -23.5.
    """
    value = value.strip()
    if len(value) < 3:
        return VOLUME_MIN + float(value)

    whole_number = float(value[0:2])
    fraction = 0.1 * float(value[2])
    return VOLUME_MIN + whole_number + fraction


def convert_max_volume(value: Union[float, str]) -> Optional[float]:
    """Convert the volume limit to float, or None when no limit is set."""
    if isinstance(value, str):
        value = value.strip()
    if value in ("OFF", "--", ""):
        return None
    return float(value)


@attr.s(auto_attribs=True, on_setattr=DENON_ATTR_SETATTR)
class DenonAVRVolume(DenonAVRFoundation):
    """This class implements volume functions of Denon AVR receiver."""

    _volume: Optional[float] = attr.ib(
        converter=attr.converters.optional(convert_volume), default=None
    )
    _max_volume: Optional[float] = attr.ib(
        converter=attr.converters.optional(convert_max_volume), default=None
    )
    _muted: Optional[bool] = attr.ib(
        converter=attr.converters.optional(convert_muted), default=None
    )
    _channel_volumes: Optional[Dict[Channels, float]] = attr.ib(default=None)
    _valid_channels = get_args(Channels)
    _subwoofer: Optional[bool] = attr.ib(
        converter=attr.converters.optional(convert_on_off_bool), default=None
    )
    _subwoofer_levels_adjustment: bool = attr.ib(
        converter=attr.converters.optional(convert_string_int_bool), default=True
    )
    _subwoofer_levels: Optional[Dict[Subwoofers, float]] = attr.ib(default=None)
    _valid_subwoofers = get_args(Subwoofers)
    _lfe: Optional[int] = attr.ib(converter=attr.converters.optional(int), default=None)
    _bass_sync: Optional[int] = attr.ib(
        converter=attr.converters.optional(int), default=None
    )
    # Update tags for attributes
    # AppCommand.xml interface
    appcommand_attrs = {
        AppCommands.GetAllZoneVolume: None,
        AppCommands.GetAllZoneMuteStatus: None,
    }
    # Status.xml interface
    status_xml_attrs = {"_volume": "./MasterVolume/value", "_muted": "./Mute/value"}

    def setup(self) -> None:
        """Ensure that the instance is initialized."""
        # Add tags for a potential AppCommand.xml update
        for tag in self.appcommand_attrs:
            self._device.api.add_appcommand_update_tag(tag)

        self._device.telnet_api.register_callback("MV", self._volume_callback)
        # MVMAX is registered as an event but deliberately not read: it is not
        # a trustworthy source for the volume limit, see
        # _max_volume_setup_callback. Registering it keeps it from confirming a
        # pending MV command or reaching _volume_callback.
        self._device.telnet_api.register_callback(
            MAX_VOLUME_TELNET_EVENT[self._device.zone],
            self._max_volume_setup_callback,
        )
        self._device.telnet_api.register_callback("MU", self._mute_callback)
        self._device.telnet_api.register_callback("CV", self._channel_volume_callback)
        self._device.telnet_api.register_callback("PS", self._subwoofer_state_callback)
        self._device.telnet_api.register_callback("PS", self._subwoofer_levels_callback)
        self._device.telnet_api.register_callback("PS", self._lfe_callback)
        self._device.telnet_api.register_callback("PS", self._bass_sync_callback)

        self._is_setup = True

    def _volume_callback(self, zone: str, event: str, parameter: str) -> None:
        """Handle a volume change event."""
        if self._device.zone != zone:
            return

        self._volume = convert_telnet_volume(parameter)

    def _max_volume_setup_callback(self, zone: str, event: str, parameter: str) -> None:
        """
        Handle a volume limit change event.

        This is the only telnet source for the limit. MVMAX looks like a second
        one and is not trustworthy: on an AVR-X1700H it answered a MV? query
        with 98, the hardware ceiling, while the limit was off, but pushed 71
        after that same limit was cleared from 70 -- neither the limit nor the
        ceiling, and indistinguishable from a real limit of -9.0. Reading it
        would intermittently overwrite the correct value this callback sets.

        Every change to the limit is pushed here, whoever makes it, so a change
        from the front panel or the setup menu arrives the same way.
        """
        value = parameter.strip()
        if value in ("OFF", ""):
            self._max_volume = None
            return

        # The value is a plain integer on the absolute scale, zero padded on
        # zone 2 and zone 3 but never a half step, so it is not the same
        # encoding as the MV events.
        self._max_volume = VOLUME_MIN + float(value)

    def _mute_callback(self, zone: str, event: str, parameter: str) -> None:
        """Handle a muting change event."""
        if self._device.zone != zone:
            return

        self._muted = parameter

    def _channel_volume_callback(self, zone: str, event: str, parameter: str) -> None:
        """Handle a channel volume change event."""
        if event != "CV":
            return

        channel_volume = parameter.split()
        if (
            len(channel_volume) != 2
            or channel_volume[0] not in CHANNEL_MAP
            or channel_volume[1] not in CHANNEL_VOLUME_MAP
        ):
            return

        if self._channel_volumes is None:
            self._channel_volumes = {}

        channel = CHANNEL_MAP[channel_volume[0]]
        volume = channel_volume[1]
        self._channel_volumes[channel] = CHANNEL_VOLUME_MAP[volume]

    def _subwoofer_state_callback(self, zone: str, event: str, parameter: str) -> None:
        """Handle a subwoofer state change event."""
        if parameter[:3] == "SWR":
            self._subwoofer = parameter[4:]

    def _subwoofer_levels_callback(self, zone: str, event: str, parameter: str) -> None:
        """Handle a subwoofer levels change event."""
        if parameter[:3] != "SWL":
            return

        subwoofer_volume = parameter.split()
        if len(subwoofer_volume) != 2 or subwoofer_volume[0] not in SUBWOOFERS_MAP:
            return

        if self._subwoofer_levels is None:
            self._subwoofer_levels = {}

        subwoofer = SUBWOOFERS_MAP[subwoofer_volume[0]]
        level = subwoofer_volume[1]
        val = convert_on_off_bool(level)
        if val is not None:
            self._subwoofer_levels_adjustment = val
        elif level in CHANNEL_VOLUME_MAP:
            self._subwoofer_levels[subwoofer] = CHANNEL_VOLUME_MAP[level]

    def _lfe_callback(self, zone: str, event: str, parameter: str) -> None:
        """Handle a LFE change event."""
        if parameter[:3] != "LFE":
            return

        self._lfe = int(parameter[4:]) * -1

    def _bass_sync_callback(self, zone: str, event: str, parameter: str) -> None:
        """Handle a LFE change event."""
        if parameter[:3] != "BSC":
            return

        self._bass_sync = int(parameter[4:]) * -1

    async def async_update(
        self, global_update: bool = False, cache_id: Optional[Hashable] = None
    ) -> None:
        """Update volume asynchronously."""
        _LOGGER.debug("Starting volume update")
        # Ensure instance is setup before updating
        if not self._is_setup:
            self.setup()

        # Update state
        await self.async_update_volume(global_update=global_update, cache_id=cache_id)
        _LOGGER.debug("Finished volume update")

    async def async_update_volume(
        self, global_update: bool = False, cache_id: Optional[Hashable] = None
    ):
        """Update volume status of device."""
        if self._device.use_avr_2016_update is None:
            raise AvrProcessingError(
                "Device is not setup correctly, update method not set"
            )

        if self._device.use_avr_2016_update:
            await self.async_update_attrs_appcommand(
                self.appcommand_attrs, global_update=global_update, cache_id=cache_id
            )
        else:
            urls = [self._device.urls.status]
            if self._device.zone == MAIN_ZONE:
                urls.append(self._device.urls.mainzone)
            await self.async_update_attrs_status_xml(
                self.status_xml_attrs, urls, cache_id=cache_id
            )

    ##############
    # Properties #
    ##############
    @property
    def muted(self) -> Optional[bool]:
        """
        Boolean if volume is currently muted.

        Return "True" if muted and "False" if not muted.
        """
        return self._muted

    @property
    def volume(self) -> Optional[float]:
        """
        Return volume of Denon AVR as float.

        Volume is send in a format like -50.0.
        Minimum is -80.0, maximum at 18.0
        """
        return self._volume

    @property
    def max_volume(self) -> Optional[float]:
        """
        Return the configured volume limit in the same scale as volume.

        None means no limit is configured, in which case the ceiling is the
        hardware maximum of 18.0.
        """
        return self._max_volume

    @property
    def channel_volumes(self) -> Optional[Dict[Channels, float]]:
        """
        Return the channel levels of the device in dB.

        Only available if using Telnet.
        """
        return self._channel_volumes

    @property
    def subwoofer(self) -> Optional[bool]:
        """
        Return the state of the subwoofer.

        Only available if using Telnet.
        """
        return self._subwoofer

    @property
    def subwoofer_levels(self) -> Optional[Dict[Subwoofers, Union[bool, float]]]:
        """
        Return the subwoofer levels of the device in dB when enabled.

        Only available if using Telnet.
        """
        if self._subwoofer_levels_adjustment:
            return self._subwoofer_levels

        return None

    @property
    def lfe(self) -> Optional[int]:
        """
        Return LFE level in dB.

        Only available if using Telnet.
        """
        return self._lfe

    @property
    def bass_sync(self) -> Optional[int]:
        """
        Return Bass Sync level in dB.

        Only available if using Telnet.
        """
        return self._bass_sync

    ##########
    # Getter #
    ##########
    def channel_volume(self, channel: Channels) -> Optional[float]:
        """
        Return the volume of a channel in dB.

        Only available if using Telnet.
        """
        self._is_valid_channel(channel)
        if self._channel_volumes is None:
            return None
        return self._channel_volumes[channel]

    def subwoofer_level(self, subwoofer: Subwoofers) -> Optional[float]:
        """
        Return the volume of a subwoofer in dB.

        Only available if using Telnet.
        """
        self._is_valid_subwoofer(subwoofer)
        if self._subwoofer_levels is None:
            return None
        return self._subwoofer_levels[subwoofer]

    ##########
    # Setter #
    ##########
    async def async_volume_up(self) -> None:
        """Volume up receiver."""
        # A receiver already at its ceiling ignores the command and answers
        # nothing at all -- no HTTP body, no telnet event -- so repeating it
        # is pure noise on a device that is documented to dislike being
        # spammed. Compare against None rather than truthiness: a limit of
        # 0.0 dB is a legal setting, and so is a volume of 0.0.
        ceiling = VOLUME_MAX if self._max_volume is None else self._max_volume
        if self._volume is not None and self._volume >= ceiling:
            _LOGGER.debug("Volume already at %s, not sending volume up", ceiling)
            return

        if self._device.telnet_available:
            await self._device.telnet_api.async_send_commands(
                self._device.telnet_commands.command_volume_up, skip_confirmation=True
            )
        else:
            await self._device.api.async_get_command(
                self._device.urls.command_volume_up,
            )

    async def async_volume_down(self) -> None:
        """Volume down receiver."""
        if self._device.telnet_available:
            await self._device.telnet_api.async_send_commands(
                self._device.telnet_commands.command_volume_down, skip_confirmation=True
            )
        else:
            await self._device.api.async_get_command(
                self._device.urls.command_volume_down,
            )

    async def async_set_volume(self, volume: float) -> None:
        """
        Set receiver volume.

        Volume is send in a format like -50.0.
        Minimum is -80.0, maximum at 18.0, or max_volume when a volume limit
        is configured on the receiver.
        """
        if volume < VOLUME_MIN or volume > VOLUME_MAX:
            raise AvrCommandError(f"Invalid volume: {volume}")

        # The receiver clamps silently, which is indistinguishable from having
        # applied the value, so reject it here instead of reporting a volume
        # the receiver never took.
        if self._max_volume is not None and volume > self._max_volume:
            raise AvrCommandError(
                f"Volume {volume} exceeds the configured limit of "
                f"{self._max_volume}"
            )

        # Round volume because only values which are a multi of 0.5 are working
        volume = round(volume * 2) / 2.0
        if self._device.telnet_available:
            await self._device.telnet_api.async_send_commands(
                self._device.telnet_commands.command_set_volume.format(
                    volume=int(volume + 80)
                )
            )
        else:
            await self._device.api.async_get_command(
                self._device.urls.command_set_volume.format(volume=volume)
            )

    async def async_set_max_volume(self, max_volume: Optional[float]) -> None:
        """
        Set the receiver volume limit.

        The limit is given in the same scale as the volume, from -20.0 to 0.0,
        and None turns it off. The main zone takes every whole step; zone 2 and
        zone 3 only take multiples of 10.0.
        """
        if max_volume is None:
            telnet_command = self._device.telnet_commands.command_max_volume_off
            url = self._device.urls.command_max_volume_off
        else:
            step = MAX_VOLUME_STEP[self._device.zone]
            if (
                max_volume < MAX_VOLUME_MIN
                or max_volume > MAX_VOLUME_MAX
                or max_volume % step != 0
            ):
                raise AvrCommandError(f"Invalid max volume: {max_volume}")

            # The receiver ignores a value it does not accept, answering the
            # same 200 with an empty body either way, so the domain is checked
            # here rather than read back.
            value = int(max_volume - VOLUME_MIN)
            telnet_command = self._device.telnet_commands.command_set_max_volume.format(
                value=value
            )
            url = self._device.urls.command_set_max_volume.format(value=value)

        if self._device.telnet_available:
            await self._device.telnet_api.async_send_commands(telnet_command)
        else:
            await self._device.api.async_get_command(url)

    async def async_mute(self, mute: bool) -> None:
        """Mute receiver."""
        if mute:
            if self._device.telnet_available:
                await self._device.telnet_api.async_send_commands(
                    self._device.telnet_commands.command_mute_on
                )
            else:
                await self._device.api.async_get_command(
                    self._device.urls.command_mute_on
                )
        else:
            if self._device.telnet_available:
                await self._device.telnet_api.async_send_commands(
                    self._device.telnet_commands.command_mute_off
                )
            else:
                await self._device.api.async_get_command(
                    self._device.urls.command_mute_off
                )

    async def async_channel_volume_up(self, channel: Channels) -> None:
        """Increase Channel volume on receiver."""
        self._is_valid_channel(channel)

        mapped_channel = CHANNEL_MAP_REVERSE[channel]
        if self._device.telnet_available:
            await self._device.telnet_api.async_send_commands(
                self._device.telnet_commands.command_channel_volume.format(
                    channel=mapped_channel, value="UP"
                )
            )
        else:
            await self._device.api.async_get_command(
                self._device.urls.command_channel_volume.format(
                    channel=mapped_channel, value="UP"
                )
            )

    async def async_channel_volume_down(self, channel: Channels) -> None:
        """Decrease Channel volume on receiver."""
        self._is_valid_channel(channel)

        mapped_channel = CHANNEL_MAP_REVERSE[channel]
        if self._device.telnet_available:
            await self._device.telnet_api.async_send_commands(
                self._device.telnet_commands.command_channel_volume.format(
                    channel=mapped_channel, value="DOWN"
                )
            )
        else:
            await self._device.api.async_get_command(
                self._device.urls.command_channel_volume.format(
                    channel=mapped_channel, value="DOWN"
                )
            )

    async def async_channel_volume(self, channel: Channels, volume: float) -> None:
        """
        Set Channel volume on receiver.

        :param channel: Channel to set.
        :param volume: Volume to set. Valid values are -12 to 12 with 0.5 steps.
        """
        self._is_valid_channel(channel)
        if volume not in CHANNEL_VOLUME_MAP_REVERSE:
            raise AvrCommandError(f"Invalid channel volume: {volume}")

        mapped_channel = CHANNEL_MAP_REVERSE[channel]
        mapped_volume = CHANNEL_VOLUME_MAP_REVERSE[volume]
        if self._device.telnet_available:
            await self._device.telnet_api.async_send_commands(
                self._device.telnet_commands.command_channel_volume.format(
                    channel=mapped_channel, value=mapped_volume
                )
            )
        else:
            await self._device.api.async_get_command(
                self._device.urls.command_channel_volume.format(
                    channel=mapped_channel, value=mapped_volume
                )
            )

    async def async_channel_volumes_reset(self) -> None:
        """Reset all channel volumes on receiver."""
        if self._device.telnet_available:
            await self._device.telnet_api.async_send_commands(
                self._device.telnet_commands.command_channel_volumes_reset
            )
        else:
            await self._device.api.async_get_command(
                self._device.urls.command_channel_volumes_reset
            )

    async def async_subwoofer_on(self) -> None:
        """Turn on Subwoofer on receiver."""
        if self._device.telnet_available:
            await self._device.telnet_api.async_send_commands(
                self._device.telnet_commands.command_subwoofer_on_off.format(mode="ON")
            )
        else:
            await self._device.api.async_get_command(
                self._device.urls.command_subwoofer_on_off.format(mode="ON")
            )

    async def async_subwoofer_off(self) -> None:
        """Turn off Subwoofer on receiver."""
        if self._device.telnet_available:
            await self._device.telnet_api.async_send_commands(
                self._device.telnet_commands.command_subwoofer_on_off.format(mode="OFF")
            )
        else:
            await self._device.api.async_get_command(
                self._device.urls.command_subwoofer_on_off.format(mode="OFF")
            )

    async def async_subwoofer_toggle(self) -> None:
        """
        Toggle Subwoofer on receiver.

        Only available if using Telnet.
        """
        if self._subwoofer:
            await self.async_subwoofer_off()
        else:
            await self.async_subwoofer_on()

    async def async_subwoofer_level_up(self, subwoofer: Subwoofers) -> None:
        """Increase Subwoofer level on receiver."""
        self._is_valid_subwoofer(subwoofer)
        mapped_subwoofer = SUBWOOFERS_MAP_REVERSE[subwoofer]
        if self._device.telnet_available:
            await self._device.telnet_api.async_send_commands(
                self._device.telnet_commands.command_subwoofer_level.format(
                    number=mapped_subwoofer, mode="UP"
                )
            )
        else:
            await self._device.api.async_get_command(
                self._device.urls.command_subwoofer_level.format(
                    number=mapped_subwoofer, mode="UP"
                )
            )

    async def async_subwoofer_level_down(self, subwoofer: Subwoofers) -> None:
        """Decrease Subwoofer level on receiver."""
        self._is_valid_subwoofer(subwoofer)
        mapped_subwoofer = SUBWOOFERS_MAP_REVERSE[subwoofer]
        if self._device.telnet_available:
            await self._device.telnet_api.async_send_commands(
                self._device.telnet_commands.command_subwoofer_level.format(
                    number=mapped_subwoofer, mode="DOWN"
                )
            )
        else:
            await self._device.api.async_get_command(
                self._device.urls.command_subwoofer_level.format(
                    number=mapped_subwoofer, mode="DOWN"
                )
            )

    async def async_lfe_up(self) -> None:
        """Increase LFE on receiver."""
        if self._device.telnet_available:
            await self._device.telnet_api.async_send_commands(
                self._device.telnet_commands.command_lfe.format(mode="UP")
            )
        else:
            await self._device.api.async_get_command(
                self._device.urls.command_lfe.format(mode="UP")
            )

    async def async_lfe_down(self) -> None:
        """Decrease LFE on receiver."""
        if self._device.telnet_available:
            await self._device.telnet_api.async_send_commands(
                self._device.telnet_commands.command_lfe.format(mode="DOWN")
            )
        else:
            await self._device.api.async_get_command(
                self._device.urls.command_lfe.format(mode="DOWN")
            )

    async def async_lfe(self, lfe: int) -> None:
        """
        Set LFE level on receiver.

        :param lfe: LFE level to set. Valid values are -10 to 0.
        """
        if lfe < -10 or lfe > 0:
            raise AvrCommandError(f"Invalid LFE: {lfe}")

        lfe_local = str(lfe).replace("-", "").zfill(2)
        if self._device.telnet_available:
            await self._device.telnet_api.async_send_commands(
                self._device.telnet_commands.command_lfe.format(mode=lfe_local)
            )
        else:
            await self._device.api.async_get_command(
                self._device.urls.command_lfe.format(mode=lfe_local)
            )

    async def async_bass_sync_up(self) -> None:
        """Increase Bass Sync on receiver."""
        if self._device.telnet_available:
            await self._device.telnet_api.async_send_commands(
                self._device.telnet_commands.command_bass_sync.format(mode="UP")
            )
        else:
            await self._device.api.async_get_command(
                self._device.urls.command_bass_sync.format(mode="UP")
            )

    async def async_bass_sync_down(self) -> None:
        """Decrease Bass Sync on receiver."""
        if self._device.telnet_available:
            await self._device.telnet_api.async_send_commands(
                self._device.telnet_commands.command_bass_sync.format(mode="DOWN")
            )
        else:
            await self._device.api.async_get_command(
                self._device.urls.command_bass_sync.format(mode="DOWN")
            )

    async def async_bass_sync(self, lfe: int) -> None:
        """
        Set Bass Sync level on receiver.

        :param lfe: Bass Sync level to set. Valid values are -10 to 0.
        """
        if lfe < -10 or lfe > 0:
            raise AvrCommandError(f"Invalid Bass Sync: {lfe}")

        bass_sync_local = str(lfe).replace("-", "").zfill(2)
        if self._device.telnet_available:
            await self._device.telnet_api.async_send_commands(
                self._device.telnet_commands.command_bass_sync.format(
                    mode=bass_sync_local
                )
            )
        else:
            await self._device.api.async_get_command(
                self._device.urls.command_bass_sync.format(mode=bass_sync_local)
            )

    @staticmethod
    def _is_valid_channel(channel: Channels):
        if channel not in DenonAVRVolume._valid_channels:
            raise AvrCommandError("Invalid channel")

    @staticmethod
    def _is_valid_subwoofer(subwoofer: Subwoofers):
        if subwoofer not in DenonAVRVolume._valid_subwoofers:
            raise AvrCommandError("Invalid subwoofer")


def volume_factory(instance: DenonAVRFoundation) -> DenonAVRVolume:
    """Create DenonAVRVolume at receiver instances."""
    # pylint: disable=protected-access
    new = DenonAVRVolume(device=instance._device)
    return new
