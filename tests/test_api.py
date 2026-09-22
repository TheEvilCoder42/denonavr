#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
This module covers tests of the telnet events carrying the volume limit.

:copyright: (c) 2016 by Oliver Goetz.
:license: MIT, see LICENSE for more details.
"""

from unittest import mock

import pytest

from denonavr.api import DenonAVRTelnetApi


class TestMaxVolumeEvent:
    """
    Test case for the setup menu volume limit events.

    SS is already a registered event, so these have to win over it for
    _send_confirmation_callback to tell one limit write from another SS push.
    """

    @pytest.mark.parametrize(
        "message,event,parameter",
        [
            ("SSVCTZMALIM 70", "SSVCTZMALIM", "70"),
            ("SSVCTZ2SLIM 070", "SSVCTZ2SLIM", "070"),
            ("SSVCTZ3SLIM 070", "SSVCTZ3SLIM", "070"),
            ("SSVCTZMALIM OFF", "SSVCTZMALIM", "OFF"),
        ],
    )
    def test_limit_events_win_over_ss(self, message, event, parameter):
        """Check that each zone's limit command resolves to its own event."""
        api = DenonAVRTelnetApi()
        callback = mock.Mock()
        api.register_callback(event, callback)

        # pylint: disable=protected-access
        api._process_event(message)

        callback.assert_called_once()
        assert callback.call_args[0][1] == event
        assert callback.call_args[0][2].strip() == parameter

    def test_other_ss_messages_are_unaffected(self):
        """Check that the rest of the SS family still resolves to SS."""
        api = DenonAVRTelnetApi()
        callback = mock.Mock()
        api.register_callback("SS", callback)

        # pylint: disable=protected-access
        api._process_event("SSHOSALSON")

        callback.assert_called_once_with(mock.ANY, "SS", "HOSALSON")

    def test_another_zone_does_not_confirm_a_limit_write(self):
        """Check that only the zone's own push confirms its limit command."""
        api = DenonAVRTelnetApi()
        # pylint: disable=protected-access
        api._send_confirmation_command = "SSVCTZMALIM 70"

        api._send_confirmation_callback("SSVCTZ2SLIM 070")
        assert not api._send_confirmation_event.is_set()

        api._send_confirmation_callback("SSVCTZMALIM 70")
        assert api._send_confirmation_event.is_set()
