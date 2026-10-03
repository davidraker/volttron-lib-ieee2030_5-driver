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
"""Configuration models for the IEEE 2030.5 driver interface: one registry row and one server.

Kept free of protocol_proxy imports so configuration tooling can validate registries without the proxy installed.
"""
from typing import Any

from pydantic import AliasChoices, Field, field_validator, model_validator

from volttron.driver.base.config import PointConfig, RemoteConfig

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
    alongside snake_case. Rows under the upward resources are writable by default; rows under the controls are not.
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
                data['writable'] = normalize_path(raw_path).split('.')[0] in UPWARD_RESOURCES
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
        if resource in UPWARD_RESOURCES:
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
    """One 2030.5 server, and the client certificate that identifies this device to it."""
    server_url: str = Field(validation_alias=AliasChoices('server_url', 'url', 'device_address', 'server'))
    dcap_path: str = '/dcap'
    cert_path: str | None = Field(default=None, validation_alias=AliasChoices('cert_path', 'certfile', 'cert'))
    key_path: str | None = Field(default=None, validation_alias=AliasChoices('key_path', 'keyfile', 'key'))
    ca_path: str | None = Field(default=None, validation_alias=AliasChoices('ca_path', 'cafile', 'ca'))
    tls_verify: bool = True
    tls12_only: bool = True
    ciphers: list[str] = Field(default_factory=lambda: list(DEFAULT_CIPHERS))
    # 40 hex digits; derived from the certificate when absent.
    lfdi: str | None = None
    pin: int | None = None
    register_if_missing: bool = True
    device_category: int = DEFAULT_DEVICE_CATEGORY
    der_index: int = Field(default=0, ge=0)
    description: str = 'VOLTTRON'
    poll_rate_floor: float = Field(default=60.0, ge=0)
    default_poll_rate: float = Field(default=1800.0, gt=0)
    # Caps the server's poll rate; None honours it. Mainly for test harnesses.
    poll_rate_ceiling: float | None = Field(default=None, gt=0)
    subscribe: bool = True
    # The address the server reaches our notification receiver at; required for subscriptions.
    notify_host: str | None = None
    notify_port: int = Field(default=8443, ge=0)
    notify_bind_host: str = '0.0.0.0'
    notify_client_auth: bool = False
    # Server-style certificate for the receiver when the device certificate is refused as one; defaults to the device's.
    notify_cert_path: str | None = None
    notify_key_path: str | None = None
    # How long the proxy waits for the server to answer one request.
    response_timeout: float = Field(default=10.0, gt=0)
    # How long this interface waits for the proxy's reply; defaults to three response timeouts, at least 30 s.
    reply_timeout: float | None = Field(default=None, gt=0)
    registration_timeout: float = Field(default=30.0, gt=0)
    # All servers share one proxy process unless a group is named here.
    proxy_group: str | None = None

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

    @field_validator('lfdi', mode='before')
    @classmethod
    def _lfdi(cls, v):
        if v in (None, ''):
            return None
        text = str(v).replace(':', '').replace(' ', '').upper()
        if len(text) != 40 or any(c not in '0123456789ABCDEF' for c in text):
            raise ValueError('an LFDI is 40 hex digits')
        return text

    @model_validator(mode='after')
    def _identity_present(self):
        if self.lfdi is None and self.cert_path is None:
            raise ValueError('Either cert_path (the device certificate) or lfdi is required.')
        if self.subscribe and not self.notify_host:
            # Not an error: subscriptions are simply not attempted. The interface logs it.
            pass
        return self

    @property
    def resolved_reply_timeout(self) -> float:
        return self.reply_timeout if self.reply_timeout is not None else max(30.0, 3 * self.response_timeout)

    def proxy_key(self) -> tuple:
        """Selects the proxy process. Constant by default so all servers share one."""
        return ('ieee2030_5',) if self.proxy_group is None else ('ieee2030_5', self.proxy_group)

    def identity_fields(self) -> dict:
        """The identity sent with every message to the proxy."""
        return {'server_url': self.server_url, 'lfdi': self.lfdi, 'cert_path': self.cert_path}

    def server_fields(self) -> dict:
        """Everything REGISTER_SERVER needs besides the point table."""
        return {**self.identity_fields(), 'dcap_path': self.dcap_path, 'key_path': self.key_path, 'ca_path': self.ca_path,
                'tls_verify': self.tls_verify, 'tls12_only': self.tls12_only, 'ciphers': list(self.ciphers),
                'pin': self.pin, 'register_if_missing': self.register_if_missing, 'device_category': self.device_category,
                'der_index': self.der_index, 'description': self.description, 'poll_rate_floor': self.poll_rate_floor,
                'default_poll_rate': self.default_poll_rate, 'poll_rate_ceiling': self.poll_rate_ceiling, 'subscribe': self.subscribe and bool(self.notify_host),
                'notify_host': self.notify_host, 'notify_port': self.notify_port, 'notify_bind_host': self.notify_bind_host,
                'notify_client_auth': self.notify_client_auth, 'notify_cert_path': self.notify_cert_path,
                'notify_key_path': self.notify_key_path, 'response_timeout': self.response_timeout}
