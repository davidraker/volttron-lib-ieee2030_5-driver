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
"""Configuration models for the IEEE 2030.5 driver interface: one registry row and one remote, which is either a
server this driver reaches as a DER client (``driver_role: client``, the default) or a server this driver serves to DER
clients (``driver_role: server``).

Kept free of protocol_proxy imports so configuration tooling can validate registries without the proxy installed.
"""
from enum import Enum
from typing import Any

from pydantic import AliasChoices, Field, field_validator, model_validator

from volttron.driver.base.config import DataSource, PointConfig, RemoteConfig


class DriverRole(str, Enum):
    """Which side of 2030.5 this remote is. ``master``/``outstation`` are accepted for the two as well."""
    client = 'client'
    server = 'server'


ROLE_ALIASES = {'master': 'client', 'outstation': 'server'}

#: Resources this client sends to the server (writable rows) and the ones it receives (read-only rows).
UPWARD_RESOURCES = ('DERCapability', 'DERSettings', 'DERStatus', 'DERAvailability', 'DeviceInformation', 'MirrorMeterReading')
DOWNWARD_RESOURCES = ('DERControl', 'DefaultDERControl', 'DERCurve', 'DERControlList', 'DERProgram')
#: Units of the ``MirrorMeterReading.<unit>`` convention and the voltage phases.
READING_UNITS = ('W', 'var', 'VAr', 'VA', 'Hz', 'A', 'V', 'PF', 'Wh', 'varh', 'VAh', 'degC')
PHASES = ('PhaseA', 'PhaseB', 'PhaseC', 'PhaseAB', 'PhaseBC', 'PhaseCA', 'PhaseN', 'PhaseAN', 'PhaseBN', 'PhaseCN', 'PhaseABC')
#: IEEE 2030.5 mandated TLS 1.2 suite first, a widely implemented fallback second (OpenSSL names).
DEFAULT_CIPHERS = ['ECDHE-ECDSA-AES128-CCM8', 'ECDHE-ECDSA-AES128-GCM-SHA256']
#: DeviceCategoryType bit 22, "other generation system"; a storage system is bit 23 (0x800000).
DEFAULT_DEVICE_CATEGORY = 0x400000

READING_COLUMNS = {'uom': ('uom', 'UOM', 'Uom'), 'phase': ('phase', 'Phase'), 'kind': ('kind', 'Kind'),
                   'flowDirection': ('flow_direction', 'Flow Direction', 'flowDirection'),
                   'dataQualifier': ('data_qualifier', 'Data Qualifier', 'dataQualifier'),
                   'accumulationBehaviour': ('accumulation_behaviour', 'Accumulation Behaviour', 'accumulationBehaviour'),
                   'commodity': ('commodity', 'Commodity'),
                   'powerOfTenMultiplier': ('power_of_ten_multiplier', 'Power Of Ten Multiplier', 'powerOfTenMultiplier')}


def normalize_path(path: str) -> str:
    """``DERSettings::setMaxW``, ``DERSettings/setMaxW`` and ``DERSettings.setMaxW`` all become the dotted form."""
    text = str(path).strip().replace('::', '.').replace('/', '.')
    return '.'.join(p for p in text.split('.') if p)


def _blank_to_none(value):
    return None if isinstance(value, str) and value.strip() == '' else value


class Ieee2030_5PointConfig(PointConfig):
    """One registry row: a 2030.5 attribute of the device's resources, in the interoperability convention.

    Column headers as the discovery tooling writes them (and the legacy agent's ``Parameter Type``) are accepted
    alongside snake_case. In the client role rows under the upward resources are writable by default and rows under the
    controls are not; a served row (``data_source: server``) is the mirror image: the platform writes the controls and
    the DER client reports the upward resources (``remote_writable``).
    """
    path: str = Field(validation_alias=AliasChoices('path', 'Path', 'Parameter Type', 'parameter_type', 'sep2_path'))
    # Power of ten the wire value of a quantity is expressed in ({value, multiplier}); 0 sends the value as is.
    multiplier: int = Field(default=0, validation_alias=AliasChoices('multiplier', 'Multiplier'))
    # Engineering value = convention value * scaling; writes divide by it. The proxy applies it.
    scaling: float = Field(default=1.0, validation_alias=AliasChoices('scaling', 'Scaling'))
    # mRID of a MirrorMeterReading row (32 hex digits); derived from the LFDI and the path when blank.
    mrid: str | None = Field(default=None, validation_alias=AliasChoices('mrid', 'MRID', 'mRID'))
    uom: int | None = Field(default=None, validation_alias=AliasChoices(*READING_COLUMNS['uom']))
    phase: int | None = Field(default=None, validation_alias=AliasChoices(*READING_COLUMNS['phase']))
    kind: int | None = Field(default=None, validation_alias=AliasChoices(*READING_COLUMNS['kind']))
    flow_direction: int | None = Field(default=None, validation_alias=AliasChoices(*READING_COLUMNS['flowDirection']))
    data_qualifier: int | None = Field(default=None, validation_alias=AliasChoices(*READING_COLUMNS['dataQualifier']))
    accumulation_behaviour: int | None = Field(default=None, validation_alias=AliasChoices(*READING_COLUMNS['accumulationBehaviour']))
    commodity: int | None = Field(default=None, validation_alias=AliasChoices(*READING_COLUMNS['commodity']))
    power_of_ten_multiplier: int | None = Field(default=None, validation_alias=AliasChoices(*READING_COLUMNS['powerOfTenMultiplier']))
    # Seeds the shadow of an upward resource so required attributes are present from the first PUT.
    starting_value: Any = Field(default=None, validation_alias=AliasChoices('starting_value', 'Starting Value',
                                                                            'default_value', 'Default Value'))
    description: str = Field(default='', validation_alias=AliasChoices('description', 'Description'))
    # TODO: transform is not yet implemented; it should be handled by the base driver for all interfaces.
    transform: str = Field(default='', validation_alias=AliasChoices('transform', 'Transform'))

    @model_validator(mode='before')
    @classmethod
    def _blank_optionals_and_default_direction(cls, data):
        if isinstance(data, dict):
            data = dict(data)
            for key in list(data):
                if key not in ('path', 'Path', 'Parameter Type', 'parameter_type', 'sep2_path', 'Volttron Point Name',
                               'volttron_point_name', 'Units', 'units', 'Notes', 'notes', 'Description', 'description') \
                        and _blank_to_none(data[key]) is None:
                    del data[key]
            raw_path = next((data[k] for k in ('path', 'Path', 'Parameter Type', 'parameter_type', 'sep2_path') if k in data), None)
            if raw_path is not None and not any(k in data for k in ('writable', 'Writable')):
                source = str(next((data[k] for k in ('data_source', 'Data Source') if k in data), '')).strip().lower()
                resource = normalize_path(raw_path).split('.')[0]
                data['writable'] = resource in DOWNWARD_RESOURCES if source == 'server' else resource in UPWARD_RESOURCES
        return data

    @field_validator('path', mode='before')
    @classmethod
    def _normalize(cls, v):
        return normalize_path(v)

    @model_validator(mode='after')
    def _check_path(self):
        parts = self.path.split('.')
        resource = parts[0]
        name = self.volttron_point_name
        if self.is_served:
            if resource in UPWARD_RESOURCES and self.writable:
                raise ValueError(f'Point {name}: {resource} is reported by the DER client and cannot be writable here.')
            if resource not in UPWARD_RESOURCES and resource not in DOWNWARD_RESOURCES:
                raise ValueError(f'Point {name}: {resource} is not a 2030.5 resource this driver handles '
                                 f'({", ".join(UPWARD_RESOURCES + DOWNWARD_RESOURCES)}).')
        elif resource in UPWARD_RESOURCES:
            if not self.writable:
                raise ValueError(f'Point {name}: {resource} is sent to the server; the row must be writable.')
        elif resource in DOWNWARD_RESOURCES:
            if self.writable:
                raise ValueError(f'Point {name}: {resource} is received from the server and cannot be writable.')
        else:
            raise ValueError(f'Point {name}: {resource} is not a 2030.5 resource this driver handles '
                             f'({", ".join(UPWARD_RESOURCES + DOWNWARD_RESOURCES)}).')
        if resource == 'MirrorMeterReading':
            if len(parts) < 2 or parts[1] not in READING_UNITS:
                raise ValueError(f'Point {name}: a MirrorMeterReading row names its unit, one of {READING_UNITS}.')
            if len(parts) > 2 and parts[2] not in PHASES:
                raise ValueError(f'Point {name}: unknown phase {parts[2]}; one of {PHASES}.')
        elif resource != 'DERControlList' and len(parts) < 2:
            raise ValueError(f'Point {name}: {resource} needs an attribute (for example {resource}.mRID).')
        return self

    @field_validator('mrid', mode='before')
    @classmethod
    def _mrid(cls, v):
        if v in (None, ''):
            return None
        text = str(v).replace('-', '').strip()
        if len(text) != 32 or any(c not in '0123456789abcdefABCDEF' for c in text):
            raise ValueError('an mRID is 32 hex digits')
        return text.upper()

    @property
    def resource(self) -> str:
        return self.path.split('.')[0]

    @property
    def is_served(self) -> bool:
        """A served point (``data_source: server``) of a server this driver serves."""
        return self.data_source is DataSource.SERVER

    @property
    def resolved_remote_writable(self) -> bool:
        """Whether the DER client writes this served point: as configured, else when it is an upward resource."""
        return self.remote_writable if self.remote_writable is not None else self.resource in UPWARD_RESOURCES

    @property
    def is_reading(self) -> bool:
        return self.resource == 'MirrorMeterReading'

    def reading_fields(self) -> dict | None:
        """The ReadingType settings given for a MirrorMeterReading row (the proxy defaults the rest)."""
        if not self.is_reading:
            return None
        given = {'uom': self.uom, 'phase': self.phase, 'kind': self.kind, 'flowDirection': self.flow_direction,
                 'dataQualifier': self.data_qualifier, 'accumulationBehaviour': self.accumulation_behaviour,
                 'commodity': self.commodity, 'powerOfTenMultiplier': self.power_of_ten_multiplier, 'mrid': self.mrid,
                 'description': self.description or None}
        return {k: v for k, v in given.items() if v is not None}


class Ieee2030_5RemoteConfig(RemoteConfig):
    """One remote: the 2030.5 server to reach with the client certificate that identifies this device to it (role
    ``client``), or the server to serve with its server certificate (role ``server``)."""
    driver_role: DriverRole = Field(default=DriverRole.client,
                                    description='client reaches a 2030.5 server as a DER device; server serves one to '
                                                'DER clients.',
                                    json_schema_extra={'group': 'Device', 'order': 20})
    # Client role: the server to reach. Required in that role.
    server_url: str | None = Field(default=None,
                                   validation_alias=AliasChoices('server_url', 'url', 'device_address', 'server'),
                                   description='Client: the server to reach.',
                                   json_schema_extra={'group': 'Connection',
                                                      'show_when': {'driver_role': ['client', 'master']}, 'order': 10})
    dcap_path: str = Field(default='/dcap', description='Path of the DeviceCapability resource on the server.',
                           json_schema_extra={'group': 'Connection', 'advanced': True,
                                              'show_when': {'driver_role': ['client', 'master']}, 'order': 20})
    # Server role: the listener, and the DER client whose data the registry describes.
    bind_host: str = Field(default='0.0.0.0', validation_alias=AliasChoices('bind_host', 'listen_host'),
                           description='Server: the interface to listen on.',
                           json_schema_extra={'group': 'Connection',
                                              'show_when': {'driver_role': ['server', 'slave', 'outstation']},
                                              'order': 30})
    port: int = Field(default=7443, ge=0, validation_alias=AliasChoices('port', 'listen_port'),
                      description='Server: the port to listen on.',
                      json_schema_extra={'group': 'Connection',
                                         'show_when': {'driver_role': ['server', 'slave', 'outstation']}, 'order': 40})
    tls: bool = Field(default=True, description='Serve or connect over TLS.',
                      json_schema_extra={'group': 'TLS', 'order': 50})
    client_auth: bool = Field(default=True, description='Server: require clients to present a certificate.',
                              json_schema_extra={'group': 'TLS', 'advanced': True,
                                                 'show_when': {'driver_role': ['server', 'slave', 'outstation']},
                                                 'order': 100})
    client_lfdi: str | None = Field(default=None,
                                    description='Server: the DER client whose data the registry describes.',
                                    json_schema_extra={'group': 'Identity',
                                                       'show_when': {'driver_role': ['server', 'slave', 'outstation']},
                                                       'order': 190})
    client_pin: int | None = Field(default=None,
                                   json_schema_extra={'group': 'Identity',
                                                      'show_when': {'driver_role': ['server', 'slave', 'outstation']},
                                                      'order': 200})
    register_unknown_clients: bool = Field(default=True,
                                           json_schema_extra={'group': 'Identity', 'advanced': True,
                                                              'show_when': {'driver_role': ['server', 'slave', 'outstation']},
                                                              'order': 210})
    # pollRate advertised to clients, the served program's primacy, and how long a DERControl.* write stays in force.
    poll_rate: int = Field(default=60, ge=1, description='Server: pollRate advertised to clients.',
                           json_schema_extra={'group': 'Polling',
                                              'show_when': {'driver_role': ['server', 'slave', 'outstation']},
                                              'unit': 's', 'order': 260})
    primacy: int = Field(default=1, ge=0, description="Server: the served program's primacy.",
                         json_schema_extra={'group': 'Polling', 'advanced': True,
                                            'show_when': {'driver_role': ['server', 'slave', 'outstation']},
                                            'order': 270})
    immediate_control_duration: int = Field(default=3600, ge=1,
                                            description='Server: how long a DERControl write stays in force.',
                                            json_schema_extra={'group': 'Polling', 'advanced': True,
                                                               'show_when': {'driver_role': ['server', 'slave', 'outstation']},
                                                               'unit': 's', 'order': 280})
    # Directory where the proxy keeps the served state (registrations, subscriptions, controls); None keeps it in memory.
    state_dir: str | None = Field(default=None,
                                  description='Server: where the proxy keeps registrations, subscriptions and '
                                              'controls; blank keeps them in memory.',
                                  json_schema_extra={'group': 'Proxy', 'advanced': True,
                                                     'show_when': {'driver_role': ['server', 'slave', 'outstation']},
                                                     'order': 360})
    notify_tls_verify: bool = Field(default=False,
                                    json_schema_extra={'group': 'Notifications', 'advanced': True,
                                                       'show_when': {'driver_role': ['client', 'master']},
                                                       'order': 330})
    cert_path: str | None = Field(default=None, validation_alias=AliasChoices('cert_path', 'certfile', 'cert'),
                                  description='Client: the device certificate that identifies it. Server: the server '
                                              'certificate.',
                                  json_schema_extra={'group': 'TLS', 'order': 60})
    key_path: str | None = Field(default=None, validation_alias=AliasChoices('key_path', 'keyfile', 'key'),
                                 json_schema_extra={'group': 'TLS', 'order': 70})
    ca_path: str | None = Field(default=None, validation_alias=AliasChoices('ca_path', 'cafile', 'ca'),
                                description='The CA that verifies the other side.',
                                json_schema_extra={'group': 'TLS', 'order': 80})
    tls_verify: bool = Field(default=True, json_schema_extra={'group': 'TLS', 'advanced': True, 'order': 90})
    tls12_only: bool = Field(default=True, json_schema_extra={'group': 'TLS', 'advanced': True, 'order': 110})
    ciphers: list[str] = Field(default_factory=lambda: list(DEFAULT_CIPHERS),
                               json_schema_extra={'group': 'TLS', 'advanced': True, 'order': 120})
    # 40 hex digits; derived from the certificate when absent.
    lfdi: str | None = Field(default=None,
                             description='Client: 40 hex digits; derived from the certificate when absent.',
                             json_schema_extra={'group': 'Identity', 'show_when': {'driver_role': ['client', 'master']},
                                                'order': 130})
    pin: int | None = Field(default=None, description='Client: registration PIN.',
                            json_schema_extra={'group': 'Identity', 'show_when': {'driver_role': ['client', 'master']},
                                               'order': 140})
    register_if_missing: bool = Field(default=True,
                                      json_schema_extra={'group': 'Identity', 'advanced': True,
                                                         'show_when': {'driver_role': ['client', 'master']},
                                                         'order': 150})
    device_category: int = Field(default=DEFAULT_DEVICE_CATEGORY,
                                 json_schema_extra={'group': 'Identity', 'advanced': True,
                                                    'show_when': {'driver_role': ['client', 'master']}, 'order': 160})
    der_index: int = Field(default=0, ge=0, description='Which DER of the end device this remote is.',
                           json_schema_extra={'group': 'Identity', 'advanced': True,
                                              'show_when': {'driver_role': ['client', 'master']}, 'order': 170})
    description: str = Field(default='VOLTTRON',
                             json_schema_extra={'group': 'Identity', 'advanced': True,
                                                'show_when': {'driver_role': ['client', 'master']}, 'order': 180})
    poll_rate_floor: float = Field(default=60.0, ge=0,
                                   description='Client: never poll faster than this whatever the server advertises.',
                                   json_schema_extra={'group': 'Polling', 'advanced': True,
                                                      'show_when': {'driver_role': ['client', 'master']}, 'unit': 's',
                                                      'order': 220})
    default_poll_rate: float = Field(default=1800.0, gt=0,
                                     description='Client: poll rate when the server advertises none.',
                                     json_schema_extra={'group': 'Polling', 'advanced': True,
                                                        'show_when': {'driver_role': ['client', 'master']}, 'unit': 's',
                                                        'order': 230})
    # Caps the server's poll rate; None honours it. Mainly for test harnesses.
    poll_rate_ceiling: float | None = Field(default=None, gt=0,
                                            description="Client: caps the server's poll rate; blank honours it.",
                                            json_schema_extra={'group': 'Polling', 'advanced': True,
                                                               'show_when': {'driver_role': ['client', 'master']},
                                                               'unit': 's', 'order': 240})
    subscribe: bool = Field(default=True,
                            description='Client: subscribe to server notifications instead of polling alone.',
                            json_schema_extra={'group': 'Polling', 'show_when': {'driver_role': ['client', 'master']},
                                               'order': 250})
    # The address the server reaches our notification receiver at; required for subscriptions.
    notify_host: str | None = Field(default=None,
                                    description='The address the server reaches our notification receiver at; required '
                                                'for subscriptions.',
                                    json_schema_extra={'group': 'Notifications', 'advanced': True,
                                                       'show_when': {'driver_role': ['client', 'master']},
                                                       'order': 290})
    notify_port: int = Field(default=8443, ge=0,
                             json_schema_extra={'group': 'Notifications', 'advanced': True,
                                                'show_when': {'driver_role': ['client', 'master']}, 'order': 300})
    notify_bind_host: str = Field(default='0.0.0.0',
                                  json_schema_extra={'group': 'Notifications', 'advanced': True,
                                                     'show_when': {'driver_role': ['client', 'master']}, 'order': 310})
    notify_client_auth: bool = Field(default=False,
                                     json_schema_extra={'group': 'Notifications', 'advanced': True,
                                                        'show_when': {'driver_role': ['client', 'master']},
                                                        'order': 320})
    # Server-style certificate for the receiver when the device certificate is refused as one; defaults to the device's.
    notify_cert_path: str | None = Field(default=None,
                                         description='Server-style certificate for the receiver when the device '
                                                     'certificate is refused as one.',
                                         json_schema_extra={'group': 'Notifications', 'advanced': True,
                                                            'show_when': {'driver_role': ['client', 'master']},
                                                            'order': 340})
    notify_key_path: str | None = Field(default=None,
                                        json_schema_extra={'group': 'Notifications', 'advanced': True,
                                                           'show_when': {'driver_role': ['client', 'master']},
                                                           'order': 350})
    # How long the proxy waits for the server to answer one request.
    response_timeout: float = Field(default=10.0, gt=0,
                                    description='How long the proxy waits for the server to answer one request.',
                                    json_schema_extra={'group': 'Proxy', 'advanced': True, 'unit': 's', 'order': 370})
    # How long this interface waits for the proxy's reply; defaults to three response timeouts, at least 30 s.
    reply_timeout: float | None = Field(default=None, gt=0,
                                        description="How long this interface waits for the proxy's reply; defaults to "
                                                    'three response timeouts, at least 30 s.',
                                        json_schema_extra={'group': 'Proxy', 'advanced': True, 'unit': 's',
                                                           'order': 380})
    registration_timeout: float = Field(default=30.0, gt=0,
                                        json_schema_extra={'group': 'Proxy', 'advanced': True, 'unit': 's',
                                                           'order': 390})
    persistent_connection: bool = Field(default=True, description='Keep one IPC connection to the proxy open for every'
                                        ' request and push instead of one connection per message.',
                                        json_schema_extra={'group': 'Proxy', 'advanced': True, 'order': 400})
    # All servers share one proxy process unless a group is named here.
    proxy_group: str | None = Field(default=None,
                                    description='All servers share one proxy process unless a group is named here.',
                                    json_schema_extra={'group': 'Proxy', 'advanced': True, 'order': 410})

    @field_validator('device_category', mode='before')
    @classmethod
    def _hex_or_int(cls, v):
        if isinstance(v, str):
            text = v.strip().lower()
            return int(text, 16) if text.startswith('0x') else int(text)
        return v

    @field_validator('ciphers', mode='before')
    @classmethod
    def _cipher_list(cls, v):
        return [c for c in v.replace(',', ':').split(':') if c] if isinstance(v, str) else v

    @field_validator('lfdi', 'client_lfdi', mode='before')
    @classmethod
    def _lfdi(cls, v):
        if v in (None, ''):
            return None
        text = str(v).replace(':', '').replace(' ', '').upper()
        if len(text) != 40 or any(c not in '0123456789ABCDEF' for c in text):
            raise ValueError('an LFDI is 40 hex digits')
        return text

    @field_validator('driver_role', mode='before')
    @classmethod
    def _role(cls, v):
        if isinstance(v, str):
            v = v.lower().strip() or 'client'
            return ROLE_ALIASES.get(v, v)
        return DriverRole.client if v is None else v

    @model_validator(mode='after')
    def _identity_present(self):
        if self.is_server:
            if self.tls and (self.cert_path is None or self.key_path is None):
                raise ValueError('A served 2030.5 server needs cert_path and key_path (its server certificate), or tls false.')
            return self
        if not self.server_url:
            raise ValueError('A 2030.5 client needs the server_url of the server to reach.')
        if self.lfdi is None and self.cert_path is None:
            raise ValueError('Either cert_path (the device certificate) or lfdi is required.')
        return self

    @property
    def is_server(self) -> bool:
        return self.driver_role is DriverRole.server

    @property
    def resolved_reply_timeout(self) -> float:
        return self.reply_timeout if self.reply_timeout is not None else max(30.0, 3 * self.response_timeout)

    def proxy_key(self) -> tuple:
        """Selects the proxy process. Constant by default so all servers share one."""
        return ('ieee2030_5',) if self.proxy_group is None else ('ieee2030_5', self.proxy_group)

    def identity_fields(self) -> dict:
        """What identifies the remote to the proxy: the server reached and our certificate, or the listener served."""
        if self.is_server:
            return {'role': 'server', 'bind_host': self.bind_host, 'port': self.port}
        return {'role': 'client', 'server_url': self.server_url, 'lfdi': self.lfdi, 'cert_path': self.cert_path}

    def server_fields(self) -> dict:
        """Everything the registration needs besides the point table."""
        if self.is_server:
            return {**self.identity_fields(), 'cert_path': self.cert_path, 'key_path': self.key_path, 'ca_path': self.ca_path,
                    'tls': self.tls, 'client_auth': self.client_auth, 'tls12_only': self.tls12_only, 'ciphers': list(self.ciphers),
                    'poll_rate': self.poll_rate, 'primacy': self.primacy, 'client_lfdi': self.client_lfdi,
                    'client_pin': self.client_pin, 'register_unknown_clients': self.register_unknown_clients,
                    'immediate_control_duration': self.immediate_control_duration, 'state_dir': self.state_dir,
                    'notify_tls_verify': self.notify_tls_verify, 'response_timeout': self.response_timeout,
                    'description': self.description}
        return {**self.identity_fields(), 'dcap_path': self.dcap_path, 'key_path': self.key_path, 'ca_path': self.ca_path,
                'tls_verify': self.tls_verify, 'tls12_only': self.tls12_only, 'ciphers': list(self.ciphers),
                'pin': self.pin, 'register_if_missing': self.register_if_missing, 'device_category': self.device_category,
                'der_index': self.der_index, 'description': self.description, 'poll_rate_floor': self.poll_rate_floor,
                'default_poll_rate': self.default_poll_rate, 'poll_rate_ceiling': self.poll_rate_ceiling, 'subscribe': self.subscribe and bool(self.notify_host),
                'notify_host': self.notify_host, 'notify_port': self.notify_port, 'notify_bind_host': self.notify_bind_host,
                'notify_client_auth': self.notify_client_auth, 'notify_cert_path': self.notify_cert_path,
                'notify_key_path': self.notify_key_path, 'response_timeout': self.response_timeout}
