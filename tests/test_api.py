#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
This module covers tests of the API helpers of Denon AVR receivers.

:copyright: (c) 2016 by Oliver Goetz.
:license: MIT, see LICENSE for more details.
"""

from unittest import mock

import pytest
from defusedxml.ElementTree import fromstring

from denonavr.api import DenonAVRTelnetApi, appcommand_status


class TestAppCommandStatus:
    """Test case for the AppCommand(0300) setter response parser."""

    @pytest.mark.parametrize(
        "response,expected",
        [
            # ver-2 setters answer with the status as text of <cmd>
            ("<rx><cmd>OK</cmd></rx>", "OK"),
            ("<rx><cmd>Error</cmd></rx>", "Error"),
            # ver-1 setters nest it one level deeper
            ("<rx><cmd><value>OK</value></cmd></rx>", "OK"),
            ("<rx><cmd>\n<value>OK</value>\n</cmd></rx>", "OK"),
            # nothing usable
            ("<rx><cmd></cmd></rx>", None),
            ("<rx><cmd><value></value></cmd></rx>", None),
            ("<rx><error>2</error></rx>", None),
        ],
    )
    def test_both_response_shapes(self, response, expected):
        """Check that both command versions report their status."""
        assert appcommand_status(fromstring(response)) == expected


class TestMaxVolumeEvent:
    """
    Test case for the MVMAX telnet event.

    MVMAX follows every MV, so it has to stay out of the generic listeners to
    keep them from firing twice, while remaining reachable as its own event.
    """

    def test_mvmax_resolves_to_its_own_event(self):
        """Check that MVMAX wins over MV, which is its prefix."""
        api = DenonAVRTelnetApi()
        callback = mock.Mock()
        api.register_callback("MVMAX", callback)

        # pylint: disable=protected-access
        api._process_event("MVMAX 70")

        callback.assert_called_once()
        _, event, parameter = callback.call_args[0]
        assert event == "MVMAX"
        assert parameter.strip() == "70"

    def test_mv_is_unaffected(self):
        """Check that a plain volume event still resolves to MV."""
        api = DenonAVRTelnetApi()
        callback = mock.Mock()
        api.register_callback("MV", callback)

        # pylint: disable=protected-access
        api._process_event("MV565")

        callback.assert_called_once_with(mock.ANY, "MV", "565")

    def test_mvmax_does_not_reach_generic_listeners(self):
        """Check that a volume change notifies the generic listeners once."""
        api = DenonAVRTelnetApi()
        callback = mock.Mock()
        api.register_callback("ALL", callback)

        # pylint: disable=protected-access
        api._process_event("MV56")
        api._process_event("MVMAX 98")

        callback.assert_called_once_with(mock.ANY, "MV", "56")

    def test_mvmax_does_not_confirm_a_pending_mv(self):
        """Check that MVMAX cannot confirm a volume command as executed."""
        api = DenonAVRTelnetApi()
        # pylint: disable=protected-access
        api._send_confirmation_command = "MV56"

        api._send_confirmation_callback("MVMAX 98")
        assert not api._send_confirmation_event.is_set()

        api._send_confirmation_callback("MV56")
        assert api._send_confirmation_event.is_set()


class TestMaxVolumeSetupEvent:
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
