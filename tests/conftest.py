"""Shared fixtures for the IEEE 2030.5 interface test suite. These run without a platform, a proxy, or a server."""
import json

from unittest import mock

import pytest

from gevent.event import AsyncResult

from volttron.driver.base.config import RemoteConfig
from volttron.driver.interfaces.ieee2030_5.ieee2030_5 import Ieee2030_5
from volttron.driver.interfaces.ieee2030_5.config import Ieee2030_5PointConfig


class FakePPM:
    """Stands in for GeventProtocolProxyManager: records payloads, replays queued replies, runs registration hooks."""

    def __init__(self):
        self.sent: list[tuple[str, dict, bool]] = []
        self.replies: list = []
        self.callbacks: dict[str, object] = {}
        self.peer = object()
        self.started = 0
        self.launch: tuple | None = None
        self.registration_waits: list[float] = []

    def queue(self, *replies):
        self.replies.extend(replies)

    def register_callback(self, fn, name, provides_response=False, timeout=30.0):
        self.callbacks[name] = fn

    def start(self):
        self.started += 1

    def select_loop(self):
        pass

    def get_proxy(self, key, **kwargs):
        self.launch = (key, kwargs)
        return self.peer

    def wait_peer_registered(self, peer, timeout, func=None, *args, **kwargs):
        self.registration_waits.append(timeout)
        if func:
            func(*args, **kwargs)

    def send(self, peer, message):
        self.sent.append((message.method_name, json.loads(message.payload.decode('utf8')), message.response_expected))
        reply = self.replies.pop(0) if self.replies else serialized({})
        if isinstance(reply, (bytes, bytearray)):
            result = AsyncResult()
            result.set(reply)
            return result
        return reply       # e.g. False, to simulate an unsendable request

    def payloads(self, method_name=None):
        return [p for m, p, _ in self.sent if method_name is None or m == method_name]


def serialized(result, error=None):
    """A reply as the proxy's serializer would produce it."""
    return json.dumps({'result': result, 'error': error if error is not None else {}}).encode('utf8')


def point(name, path, **extra):
    return Ieee2030_5PointConfig(volttron_point_name=name, path=path, units=extra.pop('units', ''), **extra)


@pytest.fixture
def ppm():
    return FakePPM()


@pytest.fixture
def driver_agent():
    return mock.Mock()


REMOTE = {'driver_type': 'ieee2030_5', 'server_url': 'https://sep2.test:8443', 'cert_path': '/certs/dev.pem',
          'key_path': '/certs/dev.key', 'pin': 111115}


@pytest.fixture
def make_interface(ppm, driver_agent):
    """Build a real Ieee2030_5 interface whose proxy manager is the FakePPM."""
    def build(points=(), base_topic='campus/building/der', **remote):
        remote = {**REMOTE, **remote}
        Ieee2030_5.default_config = {}
        with mock.patch('volttron.driver.interfaces.ieee2030_5.ieee2030_5.GeventProtocolProxyManager') as manager_class:
            manager_class.get_manager.return_value = ppm
            interface = Ieee2030_5(RemoteConfig(**remote), driver_agent=driver_agent)
        for p in points:
            interface.insert_register(interface.create_register(p), base_topic)
        return interface
    return build


STANDARD_POINTS = [
    point('DERSettings/setMaxW', 'DERSettings.setMaxW', units='W'),
    point('DERSettings/setMaxVar', 'DERSettings::setMaxVar', multiplier=1),
    point('DERCapability/type', 'DERCapability.type', starting_value='83'),
    point('DERStatus/connectStatus', 'DERStatus.connectStatus'),
    point('MirrorMeterReading/W', 'MirrorMeterReading.W', units='W', uom=38),
    point('MirrorMeterReading/V/PhaseA', 'MirrorMeterReading/V/PhaseA', scaling=0.1, mrid='ab' * 16),
    point('DERControl/opModMaxLimW', 'DERControl.opModMaxLimW'),
    point('DERControl/opModConnect', 'DERControl.opModConnect'),
    point('DERControl/opModTargetW', 'DERControl.opModTargetW', scaling=0.001),
    point('DERCurve/opModVoltVar/CurveData', 'DERCurve.opModVoltVar.CurveData'),
    point('DERControlList', 'DERControlList'),
]


@pytest.fixture
def interface(make_interface):
    iface = make_interface(STANDARD_POINTS)
    iface.proxy_peer = iface.ppm.peer
    return iface


TOPIC = 'campus/building/der/{}'.format
IDENTITY = {'server_url': 'https://sep2.test:8443', 'lfdi': None, 'cert_path': '/certs/dev.pem'}
