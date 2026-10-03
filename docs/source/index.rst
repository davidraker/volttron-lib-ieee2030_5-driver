.. _IEEE-2030-5-Driver:

==================
IEEE 2030.5 Driver
==================

VOLTTRON's IEEE 2030.5 driver acts as a Smart Energy Profile 2.0 (CSIP) **client** towards a utility or aggregator
server. The protocol runs in a separate IEEE 2030.5 Protocol Proxy process; the driver interface registers the server,
the device certificate and the device's resource attributes with that proxy, polls the controls the server issues,
writes the device's status, settings, capability and readings, and publishes the control changes the proxy pushes back.

Because the remote "device" is the server, the registry's polarity is inverted from a field device: **writable** rows
are what the client reports upward (``DERStatus``, ``DERSettings``, ``DERCapability``, ``DERAvailability``,
``DeviceInformation``, ``MirrorMeterReading``) and **read-only** rows are the controls it receives (``DERControl``,
``DefaultDERControl``, ``DERCurve``, ``DERControlList``, ``DERProgram``). Paths follow the interoperability service's
``2030.5`` convention, so its transforms carry data between this driver and the physical device behind another one.

Requirements
============

The interface depends on ``protocol-proxy-ieee2030_5``, which installs ``protocol-proxy``, ``httpx``, ``xsdata`` and
``cryptography``. Python 3.11 or later is required. A device certificate issued for the server's PKI is needed; the
device's LFDI and SFDI are derived from it.

Driver Configuration
====================

The device configuration follows the :ref:`Device Configuration File <Device-Configuration-File>` convention. Its
``remote_config`` names the server and the device identity:

    - **server_url** (or ``url``) - the server's base URL; ``dcap_path`` (``/dcap``) is the only configured path.
    - **cert_path**, **key_path**, **ca_path** - the device certificate, key and the CA to verify the server.
      ``tls_verify`` (true), ``tls12_only`` (true), ``ciphers`` (CCM-8 first, GCM second).
    - **lfdi** - 40 hex digits; derived from the certificate when absent.
    - **pin** - registration PIN, checked against the server's ``Registration`` when given.
    - **register_if_missing** (true), **device_category** (``0x400000``), **der_index** (0).
    - **poll_rate_floor** (60 s), **default_poll_rate** (1800 s) - bounds on the control poll rate.
    - **subscribe** (true), **notify_host**, **notify_port** (8443), **notify_bind_host**, **notify_client_auth** -
      subscriptions and the notification receiver; without a ``notify_host`` the proxy polls.
    - **response_timeout**, **reply_timeout**, **registration_timeout**, **proxy_group** - see the README.

.. code-block:: json

    {
      "remote_config": {
        "driver_type": "ieee2030_5",
        "server_url": "https://sep2.utility.example:8443",
        "cert_path": "/etc/volttron/sep2/device.pem",
        "key_path": "/etc/volttron/sep2/device.key",
        "ca_path": "/etc/volttron/sep2/ca.pem",
        "pin": 111115,
        "subscribe": true,
        "notify_host": "192.0.2.10"
      },
      "registry_config": "config://ieee2030_5.csv",
      "interval": 60
    }

Registry Configuration
======================

Each row is one attribute of a 2030.5 resource:

    - **Volttron Point Name** - the point's name under the device topic (``DERSettings_setMaxW``; underscores, since a slash would nest the point under the device).
    - **Path** (or the legacy ``Parameter Type``) - ``DERSettings.setMaxW``, ``DERSettings::setMaxW`` or
      ``DERSettings/setMaxW``. Readings are ``MirrorMeterReading.<unit>`` with voltages by phase
      (``MirrorMeterReading.V.PhaseA``); curves ``DERCurve.<curve attribute>.<attribute>``; schedules ``DERControlList``.
    - **Writable** - defaults from the resource's direction.
    - **Multiplier** - power of ten of the wire quantity; **Scaling** - multiplies reads and divides writes.
    - **Starting Value** - seeds the upward resource (and is the revert value).
    - **UOM**, **Phase**, **Kind**, **Flow Direction**, **Data Qualifier**, **Accumulation Behaviour**, **Commodity**,
      **Power Of Ten Multiplier**, **MRID** - the ReadingType of a reading row, defaulted from the unit and phase.

.. csv-table:: Example registry
   :header: Volttron Point Name,Path,Writable,Multiplier,Scaling,Units,Starting Value

   DERSettings_setMaxW,DERSettings.setMaxW,TRUE,0,1,W,
   DERCapability_type,DERCapability.type,TRUE,,,,83
   MirrorMeterReading_W,MirrorMeterReading.W,TRUE,,,W,
   DERControl_opModMaxLimW,DERControl.opModMaxLimW,FALSE,,,,
   DERCurve_opModVoltVar_CurveData,DERCurve.opModVoltVar.CurveData,FALSE,,,,
   DERControlList,DERControlList,FALSE,,,,

Behaviour
=========

Polls read the proxy's current view of the controls and of what was last written; ``get_point`` on a control row makes
the proxy fetch the server first. Writes are sent in one message and become one PUT per resource plus one
MirrorMeterReading POST per reading row. Control changes from polls, notifications or scheduled event transitions
arrive as pushes and are published immediately. The proxy answers every event transition the server asks about with a
DERControlResponse.
