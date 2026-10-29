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
"""IEEE 2030.5 (Smart Energy Profile 2.0, CSIP) interface for the VOLTTRON Platform Driver: a DER client reaching a
server (``driver_role: client``, the default) or a server served to DER clients (``driver_role: server``).

The protocol runs in an IEEE 2030.5 Protocol Proxy process (``protocol_proxy.protocol.ieee2030_5``); this interface
registers the server, the device certificate and the device's resource attributes with that proxy, reads the controls
the server issues and writes the device's status, settings, capability and readings through it, and publishes the
control changes the proxy pushes back. The exchange itself is :class:`ProxyBackedInterface`; this module holds what is
2030.5: the resource paths, their direction and the value coercion.

The remote "device" is the 2030.5 server, so the polarity of the registry is inverted from a field device: writable
rows are what this client reports upward (DERStatus, DERSettings, DERCapability, DERAvailability, DeviceInformation,
MirrorMeterReading) and read-only rows are the controls received (DERControl, DefaultDERControl, DERCurve,
DERControlList, DERProgram). Paths follow the interoperability service's ``2030.5`` convention.

In the server role the directions are mirrored: the platform writes the controls the server serves (DefaultDERControl,
immediate DERControl events, the DERControlList schedule, DERCurves, the DERProgram) and reflects them into the
equipment tree at once, while the DER client's reports (DERStatus, DERSettings, DERCapability, DERAvailability,
DeviceInformation, MirrorMeterReading) and its DERControlResponses arrive as pushes. Served rows default to
``data_source: server``, so they are never polled.
"""
from gevent import monkey
monkey.patch_socket()

import json
import logging

from typing import Any, cast

from volttron.driver.base.interfaces import BaseInterface, BaseRegister, BasicRevert
from volttron.driver.base.proxy_interface import ProxyBackedInterface

from volttron.driver.base.config import RemoteConfig

from .config import DOWNWARD_RESOURCES, DriverRole, Ieee2030_5PointConfig, Ieee2030_5RemoteConfig, ROLE_ALIASES

_log = logging.getLogger(__name__)
_POLLED_SOURCES = {'short_poll', 'long_poll', 'poll_once', 'shortpoll', 'longpoll', 'pollonce'}

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
        self.remote_writable = point.resolved_remote_writable     # served rows only
        leaf = self.path.split('.')[-1]
        if leaf in LIST_ATTRIBUTES:
            self.python_type: type = list
        elif leaf in BOOLEAN_ATTRIBUTES:
            self.python_type = bool
        else:
            self.python_type = float

    def point_fields(self, topic: str, served: bool = False) -> dict:
        """This register's entry in the registration point table; a served row adds what serving it needs."""
        fields = {'topic': topic, 'path': self.path, 'writable': not self.read_only, 'multiplier': self.multiplier,
                  'scaling': self.scaling}
        if self.starting_value not in (None, ''):
            fields['starting_value'] = self.starting_value
        if self.reading is not None:
            fields['reading'] = self.reading
        if served:
            fields['remote_writable'] = self.remote_writable
        return fields

    def __repr__(self) -> str:
        return f'Ieee2030_5Register({self.point_name!r}, {self.path}, {"up" if not self.read_only else "down"})'


class Ieee2030_5(ProxyBackedInterface, BasicRevert, BaseInterface):
    """Platform Driver interface for an IEEE 2030.5 server, served by the IEEE 2030.5 Protocol Proxy."""

    REGISTER_CONFIG_CLASS = Ieee2030_5PointConfig
    INTERFACE_CONFIG_CLASS = Ieee2030_5RemoteConfig
    PROXY_NAME, PROXY_LABEL = 'ieee2030_5', 'IEEE 2030.5 Proxy'
    REGISTER_METHOD, READ_METHOD, WRITE_METHOD = 'REGISTER_REMOTE', 'READ_RESOURCES', 'WRITE_RESOURCES'
    PUSH_METHOD = 'RECEIVE_CONTROLS'
    REQUEST_ERROR_KEY = 'server'

    def __init__(self, config, *args, **kwargs):
        BaseInterface.__init__(self, config, *args, **kwargs)
        BasicRevert.__init__(self, **kwargs)
        self.config: Ieee2030_5RemoteConfig
        self.init_proxy()

    # ---- registers --------------------------------------------------------------------------------------------
    def prepare_registry_config(self, registry_config: list[dict], remote_config: RemoteConfig | None = None) -> list[dict]:
        """In the server role, rows take the ``server`` data source (and so the mirrored direction).

        A blank source defaults to it; a polling source, which says how often a client reads a shared registry,
        means nothing to a served point and is replaced. ``static`` and ``never_poll`` are kept.
        """
        role = getattr(remote_config, 'driver_role', None) if remote_config is not None else self.config.driver_role
        role = getattr(role, 'value', role)
        if ROLE_ALIASES.get(role, role) != DriverRole.server.value:
            return registry_config
        prepared = []
        for row in registry_config:
            row = dict(row)
            source = next((row[key] for key in ('data_source', 'Data Source') if row.get(key) not in (None, '')), None)
            if source is None or str(getattr(source, 'value', source)).strip().lower() in _POLLED_SOURCES:
                row.pop('Data Source', None)
                row['data_source'] = 'server'
            prepared.append(row)
        return prepared

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

    # ---- the proxy exchange, in 2030.5 terms --------------------------------------------------------------------
    @property
    def is_server(self) -> bool:
        return self.config.is_server

    def finalize_setup(self, initial_setup: bool = False):
        if not self.is_server and self.config.subscribe and not self.config.notify_host:
            _log.info(f'{self.config.server_url}: subscribe is on but no notify_host is configured; the proxy will poll.')
        super().finalize_setup(initial_setup)

    def identity_fields(self) -> dict:
        return self.config.identity_fields()

    def point_fields(self, topic: str, register: BaseRegister) -> dict:
        return cast(Ieee2030_5Register, register).point_fields(topic, served=self.is_server)

    def registration_payload(self) -> dict:
        payload = {**self.config.server_fields(),
                   'points': [self.point_fields(topic, register) for topic, register in self.point_map.items()]}
        if self.is_server:
            payload['values'] = self.tree_values()         # seeds a served server that has no state to restore
        return payload

    def after_registration(self, result: dict, initial_setup: bool):
        if self.is_server:
            _log.info(f"2030.5 server served on {self.config.bind_host}:{result.get('port', self.config.port)} by the proxy as"
                      f" {result.get('server')} with {result.get('points')} points.")
        else:
            _log.info(f"2030.5 server {self.config.server_url} registered with the proxy as {result.get('client')} with "
                      f"{result.get('points')} points; LFDI {result.get('lfdi')}, SFDI {result.get('sfdi')}.")

    def interpret_write(self, items: list[tuple[str, Any]], result: Any, request_errors: Any) -> tuple[dict, dict]:
        results, errors = super().interpret_write(items, result, request_errors)
        if self.is_server and results:
            # The served controls changed: reflect them into the equipment tree and publish at once.
            self.driver_agent.publish_push(dict(results))
        return results, errors

    def read_payload(self, topics: list[str], refresh: bool = False, **kwargs) -> dict:
        """``refresh`` makes the proxy fetch the server's controls before answering."""
        return {'topics': list(topics), 'refresh': bool(refresh)}

    def get_point(self, topic: str, **kwargs):
        register = cast(Ieee2030_5Register | None, self.point_map.get(topic))
        kwargs.setdefault('refresh', not self.is_server and register is not None and register.read_only)
        return super().get_point(topic, **kwargs)

    def coerce(self, register: BaseRegister, value: Any) -> Any:
        return self._coerce(cast(Ieee2030_5Register, register), value)

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
        """Identifies the remote: one DriverAgent per (server, device certificate) reached, or per listener served."""
        cfg = cls.INTERFACE_CONFIG_CLASS(**config.model_dump())
        if cfg.is_server:
            return 'ieee2030_5', 'server', cfg.bind_host, cfg.port
        return 'ieee2030_5', 'client', cfg.server_url, cfg.lfdi or cfg.cert_path
