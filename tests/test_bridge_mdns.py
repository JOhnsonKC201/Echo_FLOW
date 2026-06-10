"""Tests for src/bridge.py advertise_mdns.

advertise_mdns imports zeroconf *inside the function*, so each test installs a
fake `zeroconf` module into sys.modules (monkeypatch-scoped). No real mDNS
sockets, no network — the real zeroconf library is never touched.
"""
from __future__ import annotations

import socket
import sys
import types

from src import bridge


# ---------------------------------------------------------------------------
# Fake zeroconf module
# ---------------------------------------------------------------------------

class _FakeServiceInfo:
    def __init__(self, type_=None, name=None, addresses=None, port=None,
                 properties=None, server=None):
        self.type_ = type_
        self.name = name
        self.addresses = addresses
        self.port = port
        self.properties = properties
        self.server = server


class _FakeIPVersion:
    V4Only = "v4-only-sentinel"


def _fake_zeroconf_module(*, ctor_raises=False, register_raises=False):
    """Build a stand-in `zeroconf` module exposing exactly the symbols
    advertise_mdns imports: IPVersion, ServiceInfo, Zeroconf."""
    created: list = []

    class _FakeZeroconf:
        def __init__(self, *args, **kwargs):
            if ctor_raises:
                raise OSError("simulated: no usable network interface")
            self.init_kwargs = kwargs
            self.registered: list = []
            created.append(self)

        def register_service(self, info):
            if register_raises:
                raise RuntimeError("simulated: registration refused")
            self.registered.append(info)

    mod = types.ModuleType("zeroconf")
    mod.IPVersion = _FakeIPVersion
    mod.ServiceInfo = _FakeServiceInfo
    mod.Zeroconf = _FakeZeroconf
    mod.__created__ = created
    return mod


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------

def test_advertise_mdns_registers_service_with_expected_fields(monkeypatch):
    mod = _fake_zeroconf_module()
    monkeypatch.setitem(sys.modules, "zeroconf", mod)

    zc = bridge.advertise_mdns("192.168.1.50", 8765)

    assert zc is not None
    assert zc is mod.__created__[0]
    # Zeroconf pinned to IPv4 (matches the inet_aton address encoding).
    assert zc.init_kwargs.get("ip_version") == _FakeIPVersion.V4Only
    # Exactly one service registered, with the advertised identity.
    assert len(zc.registered) == 1
    info = zc.registered[0]
    assert info.type_ == "_echoflow._tcp.local."
    assert info.name == "EchoFlow._echoflow._tcp.local."
    assert info.port == 8765
    assert info.server == "echoflow.local."
    assert info.properties == {"path": "/v1"}
    assert info.addresses == [socket.inet_aton("192.168.1.50")]


def test_advertise_mdns_wildcard_host_uses_local_ip_hint(monkeypatch):
    mod = _fake_zeroconf_module()
    monkeypatch.setitem(sys.modules, "zeroconf", mod)
    # Don't let the hint open a real UDP socket in tests.
    monkeypatch.setattr(bridge, "_local_ip_hint", lambda: "10.0.0.5")

    zc = bridge.advertise_mdns("0.0.0.0", 9000)

    assert zc is not None
    info = zc.registered[0]
    assert info.addresses == [socket.inet_aton("10.0.0.5")]
    assert info.port == 9000


# ---------------------------------------------------------------------------
# Failure paths — best-effort: log and return None, never raise
# ---------------------------------------------------------------------------

def test_advertise_mdns_returns_none_when_registration_raises(monkeypatch):
    mod = _fake_zeroconf_module(register_raises=True)
    monkeypatch.setitem(sys.modules, "zeroconf", mod)

    assert bridge.advertise_mdns("192.168.1.50", 8765) is None


def test_advertise_mdns_returns_none_when_constructor_raises(monkeypatch):
    mod = _fake_zeroconf_module(ctor_raises=True)
    monkeypatch.setitem(sys.modules, "zeroconf", mod)

    assert bridge.advertise_mdns("192.168.1.50", 8765) is None


def test_advertise_mdns_returns_none_when_zeroconf_missing(monkeypatch):
    """A zeroconf module without the needed symbols (like a bare stub, or the
    package absent) must hit the 'mDNS disabled' path and return None."""
    monkeypatch.setitem(sys.modules, "zeroconf", types.ModuleType("zeroconf"))

    assert bridge.advertise_mdns("192.168.1.50", 8765) is None


def test_advertise_mdns_returns_none_for_unparseable_host(monkeypatch):
    """socket.inet_aton failing on a hostname must be swallowed, not raised."""
    mod = _fake_zeroconf_module()
    monkeypatch.setitem(sys.modules, "zeroconf", mod)

    assert bridge.advertise_mdns("not-an-ip-address", 8765) is None
    # Nothing got registered along the way.
    assert mod.__created__ == [] or all(not z.registered for z in mod.__created__)
