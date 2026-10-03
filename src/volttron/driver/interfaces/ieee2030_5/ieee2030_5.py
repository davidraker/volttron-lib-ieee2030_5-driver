# -*- coding: utf-8 -*- {{{
# ===----------------------------------------------------------------------===
#
#                 Installable Component of Eclipse VOLTTRON
#
# ===----------------------------------------------------------------------===
#
# Copyright 2025 Battelle Memorial Institute
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy
# of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
#
# ===----------------------------------------------------------------------===
# }}}
"""IEEE 2030.5 (Smart Energy Profile 2.0, CSIP) client interface for the VOLTTRON Platform Driver.

The protocol runs in an IEEE 2030.5 Protocol Proxy process (``protocol_proxy.protocol.ieee2030_5``); this interface
registers the server, the device certificate and the device's resource attributes with that proxy, reads the controls
the server issues and writes the device's status, settings, capability and readings through it, and publishes the
control changes the proxy pushes back.

The remote "device" is the 2030.5 server, so the polarity of the registry is inverted from a field device: writable
rows are what this client reports upward (DERStatus, DERSettings, DERCapability, DERAvailability, DeviceInformation,
MirrorMeterReading) and read-only rows are the controls received (DERControl, DefaultDERControl, DERCurve,
DERControlList, DERProgram). Paths follow the interoperability service's ``2030.5`` convention.
"""
from gevent import monkey
monkey.patch_socket()

import json
import logging

from typing import Any, Iterable, cast

from gevent import Timeout
from gevent.event import AsyncResult

from protocol_proxy.ipc import callback, ProtocolProxyMessage, ProtocolProxyPeer
from protocol_proxy.manager.gevent import GeventProtocolProxyManager

from volttron.driver.base.interfaces import BaseInterface, BaseRegister, BasicRevert, DriverInterfaceError

from .config import Ieee2030_5PointConfig, Ieee2030_5RemoteConfig

_log = logging.getLogger(__name__)

PROXY_NAME = 'ieee2030_5'
TRUE_STRINGS = ('true', 't', 'on', 'yes', 'y', '1')
FALSE_STRINGS = ('false', 'f', 'off', 'no', 'n', '0', '')
BOOLEAN_ATTRIBUTES = frozenset({'opModConnect', 'opModEnergize', 'excitation', 'enabled', 'autonomousVRefEnable',
                                'potentiallySuperseded'})
LIST_ATTRIBUTES = frozenset({'CurveData', 'DERControlList'})


class Ieee2030_5Register(BaseRegister):
    """One attribute of a 2030.5 resource: its convention path and how the proxy should scale and send it."""

    def __init__(self, point: Ieee2030_5PointConfig):
        super().__init__('byte', not point.writable, point.volttron_point_name, point.units,
                         description=point.description or point.notes)
        self.path = point.path
        self.resource = point.resource
        self.multiplier = point.multiplier
        self.scaling = point.scaling
        self.starting_value = point.starting_value
        self.reading = point.reading_fields()
        leaf = self.path.split('.')[-1]
        if leaf in LIST_ATTRIBUTES:
            self.python_type: type = list
        elif leaf in BOOLEAN_ATTRIBUTES:
            self.python_type = bool
        else:
            self.python_type = float

    def point_fields(self, topic: str) -> dict:
        """This register's entry in the REGISTER_SERVER point table."""
        fields = {'topic': topic, 'path': self.path, 'writable': not self.read_only, 'multiplier': self.multiplier,
                  'scaling': self.scaling}
        if self.starting_value not in (None, ''):
            fields['starting_value'] = self.starting_value
        if self.reading is not None:
            fields['reading'] = self.reading
        return fields

    def __repr__(self) -> str:
        return f'Ieee2030_5Register({self.point_name!r}, {self.path}, {"up" if not self.read_only else "down"})'


class Ieee2030_5(BasicRevert, BaseInterface):
    """Platform Driver interface for an IEEE 2030.5 server, served by the IEEE 2030.5 Protocol Proxy."""

    REGISTER_CONFIG_CLASS = Ieee2030_5PointConfig
    INTERFACE_CONFIG_CLASS = Ieee2030_5RemoteConfig

    def __init__(self, config, *args, **kwargs):
        BaseInterface.__init__(self, config, *args, **kwargs)
        BasicRevert.__init__(self, **kwargs)
        self.config: Ieee2030_5RemoteConfig
        self.ppm: GeventProtocolProxyManager = GeventProtocolProxyManager.get_manager(PROXY_NAME)
        self.proxy_peer: ProtocolProxyPeer | None = None
        # The manager is shared by every 2030.5 interface instance and keeps the first registration of a callback, so
        # pushes for every server arrive at one instance's handler; it publishes by topic and never consults its own
        # point_map (the same pattern as the BACnet interface's RECEIVE_COV and the DNP3 interface's unsolicited path).
        self.ppm.register_callback(self.receive_controls, 'RECEIVE_CONTROLS', provides_response=False)
        self.ppm.start()
        self.driver_agent.core.spawn(self.ppm.select_loop)

    # ---- registers --------------------------------------------------------------------------------------------
    def create_register(self, register_definition: Ieee2030_5PointConfig) -> Ieee2030_5Register:
        return Ieee2030_5Register(register_definition)

    def insert_register(self, register: BaseRegister, base_topic: str):
        register = cast(Ieee2030_5Register, register)
        super().insert_register(register, base_topic)
        if not register.read_only and register.starting_value not in (None, '') and register.python_type is not list:
            try:
                self.set_default('/'.join([base_topic, register.point_name]), self._coerce(register, register.starting_value))
            except (TypeError, ValueError):
                _log.warning(f'Starting value {register.starting_value!r} for {register.point_name} is not usable as a '
                             'revert value; using the default revert method.')

    def finalize_setup(self, initial_setup: bool = False):
        if self.config.subscribe and not self.config.notify_host:
            _log.info(f'{self.config.server_url}: subscribe is on but no notify_host is configured; the proxy will poll.')
        self.proxy_peer = self.ppm.get_proxy(self.config.proxy_key())
        self.ppm.wait_peer_registered(self.proxy_peer, self.config.registration_timeout, self.register_server)

    def register_server(self):
        """Declare (or redeclare) the server, the certificate and the full point table to the proxy."""
        payload = {**self.config.server_fields(),
                   'points': [register.point_fields(topic) for topic, register in self.point_map.items()]}
        response = self._send('REGISTER_SERVER', payload)
        result, errors = self.parse_proxy_response(response, ['server'])
        if errors:
            _log.warning(f'Failed to register 2030.5 server {self.config.server_url} with the proxy: {errors}')
            return
        _log.info(f"2030.5 server {self.config.server_url} registered with the proxy as {result.get('client')} with "
                  f"{result.get('points')} points; LFDI {result.get('lfdi')}, SFDI {result.get('sfdi')}.")

    # ---- pushed controls --------------------------------------------------------------------------------------
    @callback
    def receive_controls(self, _, raw_message: bytes):
        """Publish the control values the proxy pushes when the server's controls change, keyed by full point topic."""
        try:
            message = json.loads(raw_message.decode('utf8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            _log.warning(f'Undecodable RECEIVE_CONTROLS message from the IEEE 2030.5 Proxy: {e}')
            return
        if error := message.get('error'):
            _log.warning(f'Error received with 2030.5 control values: {error}')
        if result := message.get('result'):
            self.driver_agent.publish_push(result)

    # ---- reads ------------------------------------------------------------------------------------------------
    def get_point(self, topic: str, **kwargs):
        register = cast(Ieee2030_5Register | None, self.point_map.get(topic))
        results, errors = self._read([topic], refresh=register is not None and register.read_only)
        if topic in results:
            return results[topic]
        message = f"Error reading point: {topic} --- {errors.get(topic, errors)}"
        _log.warning(message)
        raise RuntimeError(message)

    def _get_multiple_points(self, topics: Iterable[str], **kwargs) -> tuple[dict, dict]:
        return self._read(list(topics), refresh=False)

    def _read(self, topics: list[str], refresh: bool) -> tuple[dict, dict]:
        if self.proxy_peer is None:
            raise DriverInterfaceError('IEEE 2030.5 interface not initialized. No proxy peer available.')
        results, errors = {}, {}
        known = [t for t in topics if t in self.point_map]
        for topic in topics:
            if topic not in self.point_map:
                errors[topic] = 'Point not configured on device.'
        if not known:
            return results, errors
        payload = {**self.config.identity_fields(), 'topics': known, 'refresh': refresh}
        try:
            response = self._send('READ_RESOURCES', payload)
            values, request_errors = self.parse_proxy_response(response, known)
            for topic in known:
                if isinstance(values, dict) and topic in values:
                    results[topic] = values[topic]
                elif topic in request_errors:
                    errors[topic] = request_errors[topic]
                elif 'server' in request_errors:
                    errors[topic] = request_errors['server']
                else:
                    errors[topic] = 'No value returned by the IEEE 2030.5 Proxy.'
        except Timeout as e:
            _log.warning(f'Request timed out reading from {self.config.server_url}: {e}')
            for topic in known:
                errors.setdefault(topic, f'Timeout waiting for IEEE 2030.5 Proxy: {e}')
        except Exception as e:
            _log.warning(f'Unexpected error reading from {self.config.server_url}: {e}')
            for topic in known:
                errors.setdefault(topic, f'Unexpected error: {e}')
        return results, errors

    # ---- writes -----------------------------------------------------------------------------------------------
    def _set_point(self, topic: str, value: Any, **kwargs):
        results, errors = self._write_points([(topic, value)])
        if topic in errors:
            message = f"Error writing point: {topic} --- {errors[topic]}"
            _log.warning(message)
            raise RuntimeError(message)
        return results[topic]

    def set_multiple_points(self, topics_values, **kwargs):
        results, errors = self._write_points(list(topics_values))
        for topic in results:
            self._tracker.mark_dirty_point(topic)
        if errors:
            _log.warning(f'Errors encountered setting points: {errors}')
        return results, errors

    def _write_points(self, topics_values: list[tuple[str, Any]]) -> tuple[dict, dict]:
        """Send every value in one WRITE_RESOURCES; the proxy groups them into one PUT per resource."""
        if self.proxy_peer is None:
            raise DriverInterfaceError('IEEE 2030.5 interface not initialized. No proxy peer available.')
        results, errors = {}, {}
        values: dict[str, Any] = {}
        for topic, value in topics_values:
            register = cast(Ieee2030_5Register | None, self.point_map.get(topic))
            if register is None:
                errors[topic] = 'Point not configured on device.'
                continue
            if register.read_only:
                errors[topic] = 'Trying to write to a point configured read only.'
                continue
            try:
                values[topic] = self._coerce(register, value)
            except (TypeError, ValueError) as e:
                errors[topic] = f'Unable to convert {value!r} for {topic}: {e}'
        if not values:
            return results, errors
        payload = {**self.config.identity_fields(), 'values': values}
        try:
            response = self._send('WRITE_RESOURCES', payload)
            result, request_errors = self.parse_proxy_response(response, list(values))
            for topic, value in values.items():
                if isinstance(result, dict) and topic in result:
                    results[topic] = value
                elif topic in request_errors:
                    errors[topic] = request_errors[topic]
                elif 'server' in request_errors:
                    errors[topic] = request_errors['server']
                else:
                    errors[topic] = 'Write not acknowledged by the IEEE 2030.5 Proxy.'
        except Timeout as e:
            _log.warning(f'Request timed out writing to {self.config.server_url}: {e}')
            for topic in values:
                errors.setdefault(topic, f'Timeout waiting for IEEE 2030.5 Proxy: {e}')
        except Exception as e:
            _log.warning(f'Unexpected error writing to {self.config.server_url}: {e}')
            for topic in values:
                errors.setdefault(topic, f'Unexpected error: {e}')
        return results, errors

    # ---- proxy transport --------------------------------------------------------------------------------------
    def _send(self, method_name: str, payload: dict):
        return self.ppm.send(self.proxy_peer, ProtocolProxyMessage(method_name=method_name,
                                                                   payload=json.dumps(payload).encode('utf8'),
                                                                   response_expected=True))

    def parse_proxy_response(self, response: Any, error_keys: Iterable[str]) -> tuple[Any, dict]:
        """Wait for and unpack a proxy reply: ``(result, errors)``. A failure to get a reply is reported against every
        key in ``error_keys``. A gevent Timeout waiting on the AsyncResult is left to propagate to the caller."""
        def failed(message: str) -> tuple[dict, dict]:
            return {}, {key: message for key in error_keys}

        if not isinstance(response, AsyncResult):
            return failed(f'Unable to send request to IEEE 2030.5 Proxy (send returned {response!r}).')
        raw = response.get(timeout=self.config.resolved_reply_timeout)
        if not raw:
            return failed('Empty response from IEEE 2030.5 Proxy.')
        try:
            payload = json.loads(raw.decode('utf8') if isinstance(raw, (bytes, bytearray)) else raw)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as e:
            return failed(f'Undecodable response from IEEE 2030.5 Proxy: {e}')
        if not isinstance(payload, dict):
            return failed(f'Unexpected response from IEEE 2030.5 Proxy: {payload!r}')
        if payload.get('status') == 'error':
            return failed(f"IEEE 2030.5 Proxy {payload.get('method')} failed: {payload.get('error')}")
        errors = payload.get('error')
        return payload.get('result', {}), errors if isinstance(errors, dict) else {}

    # ---- helpers ----------------------------------------------------------------------------------------------
    @staticmethod
    def _coerce(register: Ieee2030_5Register, value: Any):
        """Light coercion: the proxy converts to the schema's exact types (ints, hex, structs)."""
        if isinstance(value, str):
            text = value.strip()
            if register.python_type is list or text[:1] in ('[', '{'):
                return json.loads(text)
            if register.python_type is bool:
                if text.lower() in TRUE_STRINGS:
                    return True
                if text.lower() in FALSE_STRINGS:
                    return False
                raise ValueError(f'{value!r} is not a boolean')
            try:
                number = float(text)
            except ValueError:
                return text                                 # hex strings such as modesEnabled '0x3'
            return int(number) if number.is_integer() and '.' not in text and 'e' not in text.lower() else number
        if register.python_type is bool and not isinstance(value, bool):
            return bool(value)
        return value

    @classmethod
    def unique_remote_id(cls, config_name: str, config) -> tuple:
        """Identifies the server session: one DriverAgent per (server, device certificate)."""
        cfg = cls.INTERFACE_CONFIG_CLASS(**config.model_dump())
        return 'ieee2030_5', cfg.server_url, cfg.lfdi or cfg.cert_path
