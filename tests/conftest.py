"""Shared fixtures for the IEEE 2030.5 interface test suite. These run without a platform, a proxy, or a server."""
from unittest import mock

import pytest

from volttron.driver.base.testing import FakePPM, build_interface, serialized      # noqa: F401  (re-exported for tests)
from volttron.driver.interfaces.ieee2030_5.ieee2030_5 import Ieee2030_5
from volttron.driver.interfaces.ieee2030_5.config import Ieee2030_5PointConfig


def point(name, path, **extra):
    return Ieee2030_5PointConfig(volttron_point_name=name, path=path, units=extra.pop('units', ''), **extra)


@pytest.fixture
def ppm():
    return FakePPM()


@pytest.fixture
def driver_agent():
    return mock.Mock(equipment_model=None)


REMOTE = {'driver_type': 'ieee2030_5', 'server_url': 'https://sep2.test:8443', 'cert_path': '/certs/dev.pem',
          'key_path': '/certs/dev.key', 'pin': 111115}


@pytest.fixture
def make_interface(ppm, driver_agent):
    """Build a real Ieee2030_5 interface whose proxy manager is the FakePPM."""
    def build(points=(), base_topic='campus/building/der', **remote):
        return build_interface(Ieee2030_5, {**REMOTE, **remote}, driver_agent=driver_agent, ppm=ppm, points=points,
                               base_topic=base_topic)
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
IDENTITY = {'role': 'client', 'server_url': 'https://sep2.test:8443', 'lfdi': None, 'cert_path': '/certs/dev.pem'}
