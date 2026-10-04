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
control changes the proxy pushes back. The exchange itself is :class:`ProxyBackedInterface`; this module holds what is
2030.5: the resource paths, their direction and the value coercion.

The remote "device" is the 2030.5 server, so the polarity of the registry is inverted from a field device: writable
rows are what this client reports upward (DERStatus, DERSettings, DERCapability, DERAvailability, DeviceInformation,
MirrorMeterReading) and read-only rows are the controls received (DERControl, DefaultDERControl, DERCurve,
DERControlList, DERProgram). Paths follow the interoperability service's ``2030.5`` convention.
"""
from gevent import monkey
monkey.patch_socket()

import json
import logging

from typing import Any, cast

from volttron.driver.base.interfaces import BaseInterface, BaseRegister, BasicRevert
from volttron.driver.base.proxy_interface import ProxyBackedInterface

from .config import Ieee2030_5PointConfig, Ieee2030_5RemoteConfig

_log = logging.getLogger(__name__)

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


class Ieee2030_5(ProxyBackedInterface, BasicRevert, BaseInterface):
    """Platform Driver interface for an IEEE 2030.5 server, served by the IEEE 2030.5 Protocol Proxy."""

    REGISTER_CONFIG_CLASS = Ieee2030_5PointConfig
    INTERFACE_CONFIG_CLASS = Ieee2030_5RemoteConfig
    PROXY_NAME, PROXY_LABEL = 'ieee2030_5', 'IEEE 2030.5 Proxy'
    REGISTER_METHOD, READ_METHOD, WRITE_METHOD = 'REGISTER_SERVER', 'READ_RESOURCES', 'WRITE_RESOURCES'
    PUSH_METHOD = 'RECEIVE_CONTROLS'
    REQUEST_ERROR_KEY = 'server'

    def __init__(self, config, *args, **kwargs):
        BaseInterface.__init__(self, config, *args, **kwargs)
        BasicRevert.__init__(self, **kwargs)
        self.config: Ieee2030_5RemoteConfig
        self.init_proxy()

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

    # ---- the proxy exchange, in 2030.5 terms --------------------------------------------------------------------
    def finalize_setup(self, initial_setup: bool = False):
        if self.config.subscribe and not self.config.notify_host:
            _log.info(f'{self.config.server_url}: subscribe is on but no notify_host is configured; the proxy will poll.')
        super().finalize_setup(initial_setup)

    def registration_payload(self) -> dict:
        return {**self.config.server_fields(),
                'points': [register.point_fields(topic) for topic, register in self.point_map.items()]}

    def after_registration(self, result: dict, initial_setup: bool):
        _log.info(f"2030.5 server {self.config.server_url} registered with the proxy as {result.get('client')} with "
                  f"{result.get('points')} points; LFDI {result.get('lfdi')}, SFDI {result.get('sfdi')}.")

    def read_payload(self, topics: list[str], refresh: bool = False, **kwargs) -> dict:
        """``refresh`` makes the proxy fetch the server's controls before answering."""
        return {'topics': list(topics), 'refresh': bool(refresh)}

    def get_point(self, topic: str, **kwargs):
        register = cast(Ieee2030_5Register | None, self.point_map.get(topic))
        kwargs.setdefault('refresh', register is not None and register.read_only)
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
        """Identifies the server session: one DriverAgent per (server, device certificate)."""
        cfg = cls.INTERFACE_CONFIG_CLASS(**config.model_dump())
        return 'ieee2030_5', cfg.server_url, cfg.lfdi or cfg.cert_path
