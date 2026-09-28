#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared pytest fixtures for denonavr tests."""

import pytest

from denonavr.decorators import clear_cached_results
from denonavr.rate_limiter import AdaptiveLimiter


@pytest.fixture(autouse=True)
def clear_response_cache():
    """Drop cached HTTP responses between tests.

    Their cache key is id(device), which CPython reuses, so without this a
    test can be served a document a collected device fetched.
    """
    clear_cached_results()
    yield
    clear_cached_results()


@pytest.fixture(autouse=True)
def fast_rate_limiter(monkeypatch):
    """Make AdaptiveLimiter effectively unlimited under mocked HTTP."""
    original_init = AdaptiveLimiter.__init__

    def fast_init(self, **kwargs):
        kwargs.setdefault("enabled", False)
        original_init(self, **kwargs)

    monkeypatch.setattr(AdaptiveLimiter, "__init__", fast_init)
