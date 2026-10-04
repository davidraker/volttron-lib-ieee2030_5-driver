# VOLTTRON IEEE 2030.5 Driver Interface

An IEEE 2030.5 (Smart Energy Profile 2.0, CSIP) **client** interface for the modular VOLTTRON Platform Driver. The
protocol runs in a separate [IEEE 2030.5 Protocol Proxy](https://github.com/eclipse-volttron/lib-protocol-proxy-ieee2030_5)
process; this interface registers the server, the device certificate and the device's resource attributes with that
proxy, reads the controls the server issues and writes the device's status, settings, capability and meter readings
through it, and publishes the control changes the proxy pushes back.

It replaces the unfinished IEEE 2030.5 agent of monolithic VOLTTRON. Unlike that agent it is a driver interface, so a
2030.5 server is a device like any other: its controls are points to poll (and pushes), its upward resources are points
to write, and the interoperability service's transforms move data between it and the physical device behind another
driver.

## Requirements

- `volttron-platform-driver` and `volttron-lib-base-driver` >= 2.0.0rc6
- `protocol-proxy-ieee2030_5` >= 2.0.0rc0 (installed as a dependency), which brings `protocol-proxy`, `httpx`,
  `xsdata` and `cryptography`
- Python >= 3.11
- A device certificate and key issued for the server's PKI (the LFDI and SFDI are derived from the certificate)

## Quick start

```shell
vctl install volttron-platform-driver --vip-identity platform.driver --start
pip install volttron-lib-ieee2030_5-driver
vctl config store platform.driver ieee2030_5.csv example-config/ieee2030_5.csv --csv
vctl config store platform.driver devices/campus/building/der_sep2 example-config/ieee2030_5.config
```

## Device configuration

```json
{
    "remote_config": {
        "driver_type": "ieee2030_5",
        "server_url": "https://sep2.utility.example:8443",
        "cert_path": "/etc/volttron/sep2/device.pem",
        "key_path": "/etc/volttron/sep2/device.key",
        "ca_path": "/etc/volttron/sep2/ca.pem",
        "pin": 111115,
        "subscribe": true,
        "notify_host": "192.0.2.10",
        "notify_port": 8443
    },
    "registry_config": "config://ieee2030_5.csv",
    "interval": 60,
    "timezone": "UTC",
    "publish_depth_first_all": true
}
```

| Key | Meaning |
|---|---|
| `driver_role` | `client` (the default) reaches the server below as a DER; `server` serves a CSIP server to DER clients, see [Server role](#server-role). |
| `server_url` (or `url`, `device_address`) | The server's base URL (client role). `dcap_path` (`/dcap`) is the only path configured; every other href comes from the server's links. |
| `cert_path`, `key_path`, `ca_path` | The device certificate, its key and the CA to verify the server. `tls_verify` (true) and `tls12_only` (true: IEEE 2030.5 mandates TLS 1.2 with `ECDHE-ECDSA-AES128-CCM8`, which the proxy offers first; `ciphers` overrides the list). |
| `lfdi` | 40 hex digits; derived from the certificate when absent. One of `cert_path` or `lfdi` is required. |
| `pin` | The registration PIN the server gave for this device; checked against `Registration.pIN` when set. |
| `register_if_missing` (true) | POST an EndDevice when the server has none for this LFDI. `device_category` (0x400000, other generation system; 0x800000 for storage). |
| `der_index` (0) | Which entry of the EndDevice's DERList carries the DER links. |
| `poll_rate_floor` (60 s), `default_poll_rate` (1800 s), `poll_rate_ceiling` | Bounds on how often the proxy fetches controls; the server's `pollRate` is used between floor and ceiling (no ceiling by default). |
| `subscribe` (true), `notify_host`, `notify_port` (8443), `notify_bind_host` (`0.0.0.0`), `notify_client_auth` (false), `notify_cert_path`, `notify_key_path` | Subscribe to the server's control lists and receive notifications at `https://<notify_host>:<notify_port>/notify`. Without a `notify_host` the proxy polls only. The receiver presents the device certificate unless `notify_cert_path` names a server-style certificate (strict verifiers such as the Go `sep2server` reject a device certificate's critical CSIP extensions on a server). |
| `response_timeout` (10 s), `reply_timeout`, `registration_timeout` (30 s), `proxy_group` | Timeouts and proxy process selection, as in the other proxy-backed drivers. |

## Registry

Each row is one attribute of a 2030.5 resource in the interoperability service's `2030.5` convention. The `Path`
column (the legacy agent's `Parameter Type` header is accepted) is written `DERSettings.setMaxW`, `DERSettings::setMaxW`
or `DERSettings/setMaxW`.

```csv
Volttron Point Name,Path,Writable,Multiplier,Scaling,Units,Starting Value,Notes
DERSettings_setMaxW,DERSettings.setMaxW,TRUE,0,1,W,,Max active power setting
DERStatus_operationalModeStatus,DERStatus.operationalModeStatus,TRUE,,,,,
MirrorMeterReading_W,MirrorMeterReading.W,TRUE,,,W,,Active power reading
MirrorMeterReading_V_PhaseA,MirrorMeterReading.V.PhaseA,TRUE,,,V,,
DERCapability_type,DERCapability.type,TRUE,,,,83,Required by the schema
DERControl_opModMaxLimW,DERControl.opModMaxLimW,FALSE,,,% x100,,Control in force
DERCurve_opModVoltVar_CurveData,DERCurve.opModVoltVar.CurveData,FALSE,,,,,List of {xvalue, yvalue}
DERControlList,DERControlList,FALSE,,,,,Every scheduled and active event
```

| Column | Meaning |
|---|---|
| `Path` | Resource and attribute. Upward (writable) resources: `DERStatus`, `DERSettings`, `DERCapability`, `DERAvailability`, `DeviceInformation`, `MirrorMeterReading.<unit>[.<phase>]`. Downward (read-only): `DERControl.<attr>` (the control in force), `DefaultDERControl.<attr>`, `DERCurve.<curve attribute>.<attr>`, `DERControlList`, `DERProgram.<attr>`. |
| `Writable` | Defaults from the resource's direction; a mismatch is rejected. |
| `Multiplier` | Power of ten the wire quantity is expressed in (`{value, multiplier}`); 0 by default. |
| `Scaling` | Multiplies values read and divides values written. |
| `Starting Value` | Seeds the upward resource so required attributes are present from the first PUT; also the revert value. |
| `UOM`, `Phase`, `Kind`, `Flow Direction`, `Data Qualifier`, `Accumulation Behaviour`, `Commodity`, `Power Of Ten Multiplier`, `MRID` | ReadingType of a `MirrorMeterReading` row; defaulted from the unit and phase in the path. |

The interoperability service's `discovery/ieee2030_5.py` generates this registry, the device configuration and the
device format for any device whose format reaches `2030.5` in the transform graph.

## Behaviour

- `get_multiple_points` (the scheduled poll) reads the proxy's current view: controls as the engine holds them, upward
  rows as last written. `get_point` on a control row makes the proxy fetch the server first.
- `set_point` and `set_multiple_points` send every value in one message; the proxy groups them into one PUT per
  resource (so writing all DERSettings rows at once is one PUT) and one MirrorMeterReading POST per reading row.
- Control changes, whether from a poll, a notification or a scheduled event transition, arrive as pushes and are
  published like any other point (`publish_push`), so a consumer sees a DERControl start and end without waiting for
  the next poll. As with BACnet CoV, the base driver still polls pushed points on the schedule.
- Quantities are one engineering number per row; status enumerations one value; `connectStatus` and the hex-binary
  bitmaps (`modesEnabled`, `alarmStatus`) take an integer or hex string and read back as hex. Curves and
  `DERControlList` are list-valued points.

## Server role

With `"driver_role": "server"` the device *is* a CSIP server: the proxy listens on `bind_host:port` with the server
certificate, the DER client described by `client_lfdi`/`client_pin` is pre-registered (other clients may register too
unless `register_unknown_clients` is false), and the registry rows are the served points.

```json
{
    "remote_config": {
        "driver_type": "ieee2030_5",
        "driver_role": "server",
        "bind_host": "0.0.0.0",
        "port": 7443,
        "cert_path": "/etc/volttron/sep2/server.pem",
        "key_path": "/etc/volttron/sep2/server.key",
        "ca_path": "/etc/volttron/sep2/ca.pem",
        "client_lfdi": "407FA71D59F3A360EB6AF1815834967AF67211DD",
        "client_pin": 111115,
        "poll_rate": 60,
        "state_dir": "/var/lib/volttron/sep2"
    },
    "registry_config": "config://served.csv"
}
```

| Key | Default | Meaning |
|---|---|---|
| `bind_host`, `port` | `0.0.0.0`, 7443 | The listener. One DriverAgent per listener. |
| `cert_path`, `key_path`, `ca_path` | required | The server certificate and key; the CA that signed the clients' certificates (`client_auth`, true, verifies them). `tls: false` serves plain HTTP for testing. |
| `client_lfdi`, `client_pin` | unset | The DER client whose reports map to the registry rows, pre-registered with this PIN. Without it the first client to register is that DER. |
| `register_unknown_clients` | true | Let other DER clients create their EndDevice by POST. |
| `poll_rate` | 60 | The `pollRate` advertised to clients. |
| `primacy` | 1 | The served DERProgram's primacy. |
| `immediate_control_duration` | 3600 | Seconds a `DERControl.*` write stays in force. |
| `state_dir` | unset | Directory where the proxy keeps the served state (registrations, subscriptions, controls, curves, received values) across restarts; in memory only when unset. |

The rows are the same paths with the directions mirrored, and default to `Data Source` `server` (never polled):

* `DefaultDERControl.*`, `DERControl.*`, `DERControlList`, `DERCurve.<attribute>.*` and `DERProgram.*` are what the
  platform writes (served controls). `set_point` stores the value in the served resource and reflects it into the
  equipment tree at once; `Starting Value` seeds a control that holds nothing. A `DERControl.*` write creates one
  immediate event (start now, duration `immediate_control_duration`) per `set_multiple_points` call, superseding the
  previous one; `DERControlList` is written as the whole schedule, a list of `{mRID?, interval: {start, duration},
  DERControl: {...}}` entries; a `DERCurve.<attribute>.CurveData` write serves a curve that controls setting that
  attribute (to true, or to its href) link to.
* `DERStatus.*`, `DERSettings.*`, `DERCapability.*`, `DERAvailability.*`, `DeviceInformation.*` and
  `MirrorMeterReading.<unit>[.<phase>]` are what the DER client reports (`Remote Writable` by default); they arrive as
  pushes and read back as the last value received. `DERControlList` reads back as the events with their status and the
  DERControlResponses the client posted.
* Clients subscribed to the control resources are notified of every platform write; others see it at their next poll.
* Restarts: the proxy manager relaunches a crashed proxy and the interface registers again; the served state comes back
  from `state_dir`, so clients stay registered and subscribed, and the equipment tree's current values seed anything the
  state does not hold.

`tests/e2e_loopback/` runs this role end to end: a served server and a client-role interface through one real proxy
process over mutual TLS with generated certificates, no Docker needed.

## Testing

`python -m pytest tests` runs the unit tests against a fake proxy manager, and (with Docker available) the end-to-end
harness under `tests/e2e_sep2server/`: this interface, a real proxy manager and proxy subprocess, and the GridAPPS-D
Go `sep2server` in a container, exercising registration, upward PUTs and POSTs, an admin-issued DERControl arriving by
notification, the responses it records and its cancellation. See that directory's README.
