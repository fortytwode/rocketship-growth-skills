"""Shared test safety: no test in this folder may open a network connection or see a real key."""
from __future__ import annotations

import socket
import urllib.request

import pytest


@pytest.fixture(autouse=True)
def no_network_and_no_real_keys(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    for name in ("KIE_API_KEY", "REPLICATE_API_TOKEN", "ELEVENLABS_API_KEY"):
        monkeypatch.delenv(name, raising=False)
