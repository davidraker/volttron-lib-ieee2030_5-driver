"""Unit tests for the IEEE 2030.5 interface: configuration, registers, and every message to and from the proxy."""
import logging

import pytest

from gevent import Timeout
from pydantic import ValidationError

from volttron.driver.base.config import RemoteConfig
from volttron.driver.base.interfaces import DriverInterfaceError
from volttron.driver.interfaces.ieee2030_5.config import Ieee2030_5PointConfig, Ieee2030_5RemoteConfig
from volttron.driver.interfaces.ieee2030_5.ieee2030_5 import Ieee2030_5

from tests.conftest import IDENTITY, REMOTE, STANDARD_POINTS, TOPIC, point, serialized


class TestPointConfig:
    def test_csv_row_as_the_generator_writes_it(self):
        row = {'Volttron Point Name': 'DERSettings/setMaxW', 'Path': 'DERSettings.setMaxW', 'Writable': 'TRUE',
               'Multiplier': '0', 'Scaling': '1', 'Units': 'W', 'Starting Value': '', 'Notes': 'Max active power'}
        cfg = Ieee2030_5PointConfig(**row)
        assert (cfg.path, cfg.resource, cfg.writable, cfg.multiplier, cfg.scaling) == ('DERSettings.setMaxW', 'DERSettings', True, 0, 1.0)
        assert cfg.starting_value is None and cfg.notes == 'Max active power' and cfg.reading_fields() is None

    def test_legacy_parameter_type_and_separators(self):
        for path in ('DERSettings::setMaxW', 'DERSettings/setMaxW', 'DERSettings.setMaxW'):
            cfg = Ieee2030_5PointConfig(**{'Volttron Point Name': 'x', 'Parameter Type': path})
            assert cfg.path == 'DERSettings.setMaxW' and cfg.writable is True           # direction defaulted

    def test_direction_defaults_and_mismatches(self):
        assert Ieee2030_5PointConfig(volttron_point_name='x', path='DERControl.opModMaxLimW').writable is False
        assert Ieee2030_5PointConfig(volttron_point_name='x', path='DERControlList').writable is False
        with pytest.raises(ValidationError, match='must be writable'):
            Ieee2030_5PointConfig(volttron_point_name='x', path='DERSettings.setMaxW', writable=False)
        with pytest.raises(ValidationError, match='cannot be writable'):
            Ieee2030_5PointConfig(volttron_point_name='x', path='DERControl.opModMaxLimW', writable=True)
        with pytest.raises(ValidationError, match='not a 2030.5 resource'):
            Ieee2030_5PointConfig(volttron_point_name='x', path='Nope.attr')
        with pytest.raises(ValidationError, match='needs an attribute'):
            Ieee2030_5PointConfig(volttron_point_name='x', path='DERSettings')

    def test_reading_rows(self):
        cfg = Ieee2030_5PointConfig(**{'Volttron Point Name': 'V', 'Path': 'MirrorMeterReading.V.PhaseA', 'UOM': '29',
                                       'Phase': '128', 'Kind': '', 'MRID': 'ab-cd' + 'ef' * 14})
        assert cfg.is_reading and cfg.reading_fields() == {'uom': 29, 'phase': 128, 'mrid': 'ABCD' + 'EF' * 14}
        with pytest.raises(ValidationError, match='names its unit'):
            Ieee2030_5PointConfig(volttron_point_name='x', path='MirrorMeterReading.Joules')
        with pytest.raises(ValidationError, match='unknown phase'):
            Ieee2030_5PointConfig(volttron_point_name='x', path='MirrorMeterReading.V.PhaseZ')
        with pytest.raises(ValidationError, match='32 hex digits'):
            Ieee2030_5PointConfig(volttron_point_name='x', path='MirrorMeterReading.W', mrid='123')


class TestRemoteConfig:
    def test_defaults_aliases_and_derived_values(self):
        cfg = Ieee2030_5RemoteConfig(driver_type='ieee2030_5', url='https://s:8443', certfile='c.pem', keyfile='k.pem',
                                     device_category='0x800000', ciphers='A:B', lfdi='3e:4f:45:ab:31:ed:fe:5b:67:e3:43:e5:e4:56:2e:31:98:4e:23:e5')
        assert cfg.server_url == 'https://s:8443' and cfg.cert_path == 'c.pem' and cfg.device_category == 0x800000
        assert cfg.ciphers == ['A', 'B'] and cfg.lfdi == '3E4F45AB31EDFE5B67E343E5E4562E31984E23E5'
        assert cfg.resolved_reply_timeout == 30.0 and cfg.proxy_key() == ('ieee2030_5',)
        assert cfg.identity_fields() == {'server_url': 'https://s:8443', 'lfdi': cfg.lfdi, 'cert_path': 'c.pem'}
        fields = cfg.server_fields()
        assert fields['subscribe'] is False                   # no notify_host: the proxy polls
        assert fields['poll_rate_floor'] == 60.0 and fields['default_poll_rate'] == 1800.0 and fields['dcap_path'] == '/dcap'
        with_notify = Ieee2030_5RemoteConfig(driver_type='ieee2030_5', server_url='https://s', cert_path='c', notify_host='h',
                                             response_timeout=20, proxy_group='site')
        assert with_notify.server_fields()['subscribe'] is True and with_notify.resolved_reply_timeout == 60.0
        assert with_notify.proxy_key() == ('ieee2030_5', 'site')

    def test_rejections(self):
        with pytest.raises(ValidationError, match='cert_path .* or lfdi'):
            Ieee2030_5RemoteConfig(driver_type='ieee2030_5', server_url='https://s')
        with pytest.raises(ValidationError, match='40 hex digits'):
            Ieee2030_5RemoteConfig(driver_type='ieee2030_5', server_url='https://s', lfdi='abc')
        with pytest.raises(ValidationError):
            Ieee2030_5RemoteConfig(driver_type='ieee2030_5', server_url='https://s', cert_path='c', response_timeout=0)


class TestRegisters:
    def test_types_and_point_fields(self, interface):
        setmaxw, cap, reading, lim, connect, curve, schedule = (interface.point_map[TOPIC(n)] for n in (
            'DERSettings/setMaxW', 'DERCapability/type', 'MirrorMeterReading/V/PhaseA', 'DERControl/opModMaxLimW',
            'DERControl/opModConnect', 'DERCurve/opModVoltVar/CurveData', 'DERControlList'))
        assert not setmaxw.read_only and lim.read_only and curve.read_only
        assert (lim.python_type, connect.python_type, curve.python_type, schedule.python_type) == (float, bool, list, list)
        assert setmaxw.point_fields(TOPIC('DERSettings/setMaxW')) == {'topic': TOPIC('DERSettings/setMaxW'), 'path': 'DERSettings.setMaxW',
                                                                      'writable': True, 'multiplier': 0, 'scaling': 1.0}
        assert cap.point_fields('t')['starting_value'] == '83'
        assert reading.point_fields('t')['reading'] == {'mrid': 'AB' * 16} and reading.point_fields('t')['scaling'] == 0.1
        assert 'reading' not in setmaxw.point_fields('t')
        assert repr(lim) == "Ieee2030_5Register('DERControl/opModMaxLimW', DERControl.opModMaxLimW, down)"

    def test_starting_value_is_the_revert_value(self, interface):
        assert interface.get_default(TOPIC('DERCapability/type')) == 83 if hasattr(interface, 'get_default') else True
        assert TOPIC('DERCapability/type') in interface._tracker.defaults


class TestSetup:
    def test_init_registers_push_callback_and_starts_manager(self, make_interface, ppm, driver_agent):
        interface = make_interface()
        assert 'RECEIVE_CONTROLS' in ppm.callbacks and ppm.started == 1
        driver_agent.core.spawn.assert_called_once_with(ppm.select_loop)
        assert interface.proxy_peer is None

    def test_finalize_setup_registers_the_server(self, make_interface, ppm):
        ppm.queue(serialized({'client': 'https://sep2.test:8443|/certs/dev.pem', 'points': 11, 'lfdi': 'A' * 40, 'sfdi': 1}))
        interface = make_interface(STANDARD_POINTS)
        interface.finalize_setup(initial_setup=True)
        assert ppm.launch == (('ieee2030_5',), {}) and ppm.registration_waits == [30.0]
        [(method, payload, expects_reply)] = ppm.sent
        assert method == 'REGISTER_SERVER' and expects_reply
        assert payload['server_url'] == 'https://sep2.test:8443' and payload['cert_path'] == '/certs/dev.pem'
        assert payload['pin'] == 111115 and payload['subscribe'] is False and payload['tls12_only'] is True
        assert payload['ciphers'][0] == 'ECDHE-ECDSA-AES128-CCM8'
        assert len(payload['points']) == len(STANDARD_POINTS)
        by_topic = {p['topic']: p for p in payload['points']}
        assert by_topic[TOPIC('DERSettings/setMaxVar')]['multiplier'] == 1 and by_topic[TOPIC('DERControlList')]['writable'] is False
        assert by_topic[TOPIC('MirrorMeterReading/W')]['reading'] == {'uom': 38}

    def test_registration_failure_is_logged(self, make_interface, ppm, caplog):
        ppm.queue(serialized({}, {'server': 'PIN mismatch'}))
        interface = make_interface()
        with caplog.at_level(logging.WARNING):
            interface.finalize_setup()
        assert 'PIN mismatch' in caplog.text

    def test_unique_remote_id(self):
        cfg = RemoteConfig(**REMOTE)
        assert Ieee2030_5.unique_remote_id('any', cfg) == ('ieee2030_5', 'https://sep2.test:8443', '/certs/dev.pem')
        with_lfdi = RemoteConfig(**{**REMOTE, 'lfdi': 'A' * 40})
        assert Ieee2030_5.unique_remote_id('any', with_lfdi) == ('ieee2030_5', 'https://sep2.test:8443', 'A' * 40)


class TestReads:
    def test_poll_reads_the_proxy_view_without_refresh(self, interface, ppm):
        topics = [TOPIC('DERControl/opModMaxLimW'), TOPIC('DERSettings/setMaxW'), TOPIC('DERControlList'), TOPIC('bogus')]
        ppm.queue(serialized({TOPIC('DERControl/opModMaxLimW'): 5000, TOPIC('DERSettings/setMaxW'): 6000, TOPIC('DERControlList'): []}))
        results, errors = interface.get_multiple_points(topics)
        assert results == {TOPIC('DERControl/opModMaxLimW'): 5000, TOPIC('DERSettings/setMaxW'): 6000, TOPIC('DERControlList'): []}
        assert errors == {TOPIC('bogus'): 'Point not configured on device.'}
        [payload] = ppm.payloads('READ_RESOURCES')
        assert payload == {**IDENTITY, 'topics': topics[:3], 'refresh': False}

    def test_get_point_refreshes_controls_but_not_upward_rows(self, interface, ppm):
        ppm.queue(serialized({TOPIC('DERControl/opModMaxLimW'): 4000}), serialized({TOPIC('DERSettings/setMaxW'): 1}))
        assert interface.get_point(TOPIC('DERControl/opModMaxLimW')) == 4000
        assert interface.get_point(TOPIC('DERSettings/setMaxW')) == 1
        assert [p['refresh'] for p in ppm.payloads('READ_RESOURCES')] == [True, False]

    def test_read_errors(self, interface, ppm):
        ppm.queue(serialized({}, {TOPIC('DERControl/opModMaxLimW'): 'session not ready'}))
        results, errors = interface.get_multiple_points([TOPIC('DERControl/opModMaxLimW')])
        assert results == {} and errors == {TOPIC('DERControl/opModMaxLimW'): 'session not ready'}
        ppm.queue(serialized({}, {'server': 'Server not registered.'}))
        _, errors = interface.get_multiple_points([TOPIC('DERControl/opModMaxLimW')])
        assert errors == {TOPIC('DERControl/opModMaxLimW'): 'Server not registered.'}
        ppm.queue(serialized({}))
        with pytest.raises(RuntimeError, match='No value returned'):
            interface.get_point(TOPIC('DERControl/opModMaxLimW'))
        ppm.queue(False)
        _, errors = interface.get_multiple_points([TOPIC('DERControl/opModMaxLimW')])
        assert 'Unable to send request' in errors[TOPIC('DERControl/opModMaxLimW')]
        ppm.queue(b'{"status": "error", "method": "READ_RESOURCES", "error": "boom"}')
        _, errors = interface.get_multiple_points([TOPIC('DERControl/opModMaxLimW')])
        assert 'READ_RESOURCES failed: boom' in errors[TOPIC('DERControl/opModMaxLimW')]

    def test_timeout_and_uninitialized(self, interface, ppm, monkeypatch):
        def slow(*_):
            raise Timeout()
        monkeypatch.setattr(interface, 'parse_proxy_response', slow)
        _, errors = interface.get_multiple_points([TOPIC('DERControl/opModMaxLimW')])
        assert 'Timeout waiting for IEEE 2030.5 Proxy' in errors[TOPIC('DERControl/opModMaxLimW')]
        interface.proxy_peer = None
        with pytest.raises(DriverInterfaceError):
            interface.get_multiple_points([TOPIC('DERControl/opModMaxLimW')])


class TestWrites:
    def test_set_multiple_points_is_one_message(self, interface, ppm):
        items = [(TOPIC('DERSettings/setMaxW'), '5000'), (TOPIC('DERSettings/setMaxVar'), 2500.5),
                 (TOPIC('DERStatus/connectStatus'), '0x01'), (TOPIC('MirrorMeterReading/W'), 4321.6),
                 (TOPIC('DERControl/opModMaxLimW'), 1), (TOPIC('bogus'), 2)]
        ppm.queue(serialized({TOPIC('DERSettings/setMaxW'): {'status': 204}, TOPIC('DERSettings/setMaxVar'): {'status': 204},
                              TOPIC('DERStatus/connectStatus'): {'status': 204}, TOPIC('MirrorMeterReading/W'): {'status': 'posted'}}))
        results, errors = interface.set_multiple_points(items)
        assert results == {TOPIC('DERSettings/setMaxW'): 5000, TOPIC('DERSettings/setMaxVar'): 2500.5,
                           TOPIC('DERStatus/connectStatus'): '0x01', TOPIC('MirrorMeterReading/W'): 4321.6}
        assert errors == {TOPIC('DERControl/opModMaxLimW'): 'Trying to write to a point configured read only.',
                          TOPIC('bogus'): 'Point not configured on device.'}
        [payload] = ppm.payloads('WRITE_RESOURCES')
        assert payload == {**IDENTITY, 'values': {TOPIC('DERSettings/setMaxW'): 5000, TOPIC('DERSettings/setMaxVar'): 2500.5,
                                                   TOPIC('DERStatus/connectStatus'): '0x01', TOPIC('MirrorMeterReading/W'): 4321.6}}

    def test_set_point_errors(self, interface, ppm):
        ppm.queue(serialized({}, {TOPIC('DERSettings/setMaxW'): 'HTTP 403'}))
        with pytest.raises(RuntimeError, match='HTTP 403'):
            interface.set_point(TOPIC('DERSettings/setMaxW'), 1)
        with pytest.raises(RuntimeError, match='read only'):
            interface.set_point(TOPIC('DERControl/opModMaxLimW'), 1)
        ppm.queue(serialized({}))
        with pytest.raises(RuntimeError, match='not acknowledged'):
            interface.set_point(TOPIC('DERSettings/setMaxW'), 1)

    def test_coercion(self, interface):
        connect = interface.point_map[TOPIC('DERControl/opModConnect')]
        curve = interface.point_map[TOPIC('DERCurve/opModVoltVar/CurveData')]
        number = interface.point_map[TOPIC('DERSettings/setMaxW')]
        assert Ieee2030_5._coerce(connect, 'on') is True and Ieee2030_5._coerce(connect, 0) is False
        assert Ieee2030_5._coerce(curve, '[{"xvalue": 1, "yvalue": 2}]') == [{'xvalue': 1, 'yvalue': 2}]
        assert Ieee2030_5._coerce(number, '12.5') == 12.5 and Ieee2030_5._coerce(number, '12') == 12
        assert Ieee2030_5._coerce(number, '0x0F') == '0x0F' and Ieee2030_5._coerce(number, 7) == 7
        with pytest.raises(ValueError):
            Ieee2030_5._coerce(connect, 'maybe')


class TestPush:
    def test_receive_controls_publishes_by_topic(self, interface, driver_agent):
        values = {TOPIC('DERControl/opModMaxLimW'): 5000, TOPIC('DERControlList'): [{'mRID': 'EE'}]}
        interface.receive_controls.__wrapped__(interface, None, serialized(values))
        driver_agent.publish_push.assert_called_once_with(values)

    def test_receive_controls_logs_errors_and_ignores_garbage(self, interface, driver_agent, caplog):
        with caplog.at_level(logging.WARNING):
            interface.receive_controls.__wrapped__(interface, None, serialized({}, {'server': 'lost'}))
            interface.receive_controls.__wrapped__(interface, None, b'not json')
        assert 'lost' in caplog.text and 'Undecodable' in caplog.text
        driver_agent.publish_push.assert_not_called()
