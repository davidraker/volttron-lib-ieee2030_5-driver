"""End-to-end harness: real interface -> real GeventProtocolProxyManager -> real IEEE 2030.5 proxy subprocess -> the
GridAPPS-D Go sep2server in Docker. Run by test_end_to_end.py in its own process (gevent's monkey patching must happen
before other imports). Exit status is non-zero if any check fails.

Arguments: <protocol port> <admin port> <notify port> [work dir]."""
from gevent import monkey; monkey.patch_all()
import gevent, json, logging, os, sys, tempfile, time
from pathlib import Path
logging.basicConfig(filename=os.environ.get('SEP2_E2E_LOG', '/tmp/sep2_e2e.log'), level=logging.DEBUG, format='%(asctime)s %(name)s %(levelname)s %(message)s')
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sep2server import Sep2Server, ensure_image, generate_certs, write_fixture      # noqa: E402
from protocol_proxy.protocol.ieee2030_5.identity import lfdi_from_cert, sfdi_from_lfdi    # noqa: E402
from volttron.driver.base.config import RemoteConfig                                 # noqa: E402
from volttron.driver.interfaces.ieee2030_5.ieee2030_5 import Ieee2030_5             # noqa: E402
from volttron.driver.interfaces.ieee2030_5.config import Ieee2030_5PointConfig       # noqa: E402

PORT, ADMIN_PORT, NOTIFY_PORT = (int(a) for a in sys.argv[1:4])
WORK = Path(sys.argv[4]) if len(sys.argv) > 4 else Path(tempfile.mkdtemp(prefix='sep2e2e-'))
ensure_image()
certs = generate_certs(WORK / 'tls')
LFDI = lfdi_from_cert(certs['device_cert'])
fixture = WORK / 'fixture' / 'boot.yaml'
fixture.parent.mkdir(exist_ok=True)
write_fixture(fixture, LFDI, sfdi_from_lfdi(LFDI))
server = Sep2Server(WORK / 'tls', fixture, PORT, ADMIN_PORT, name=f'sep2e2e-{PORT}')
server.start()


class Core: spawn = staticmethod(gevent.spawn)
class Agent:
    core = Core()
    def __init__(self): self.pushed = []
    def publish_push(self, result): self.pushed.append(result)


Ieee2030_5.default_config = {}
T = 'campus/b/der_sep2/{}'.format
checks = []
def check(name, cond, detail=''):
    checks.append((name, bool(cond))); print(('PASS ' if cond else 'FAIL ') + name + (f'  {detail}' if detail else ''))
def wait_for(predicate, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        gevent.sleep(0.25)
    return predicate()
def pushed_value(agent, topic):
    for result in reversed(agent.pushed):
        if topic in result:
            return result[topic]
    return None

iface = None
agent = Agent()
try:
    iface = Ieee2030_5(RemoteConfig(driver_type='ieee2030_5', server_url=server.url, cert_path=str(certs['device_cert']),
                                    key_path=str(certs['device_key']), ca_path=str(certs['ca']), subscribe=True,
                                    notify_host='127.0.0.1', notify_bind_host='127.0.0.1', notify_port=NOTIFY_PORT,
                                    notify_cert_path=str(certs['notify_cert']), notify_key_path=str(certs['notify_key']),
                                    poll_rate_floor=60, poll_rate_ceiling=600, response_timeout=10), driver_agent=agent)
    rows = [dict(volttron_point_name='DERSettings_setMaxW', path='DERSettings.setMaxW', units='W'),
            dict(volttron_point_name='DERSettings_setGradW', path='DERSettings.setGradW', starting_value=100),
            dict(volttron_point_name='DERCapability_type', path='DERCapability.type', starting_value=83),
            dict(volttron_point_name='DERCapability_rtgMaxW', path='DERCapability.rtgMaxW', starting_value=12000),
            dict(volttron_point_name='DERStatus_operationalModeStatus', path='DERStatus.operationalModeStatus'),
            dict(volttron_point_name='MirrorMeterReading_W', path='MirrorMeterReading.W', units='W'),
            dict(volttron_point_name='DERControl_opModMaxLimW', path='DERControl.opModMaxLimW'),
            dict(volttron_point_name='DERControl_opModConnect', path='DERControl.opModConnect'),
            dict(volttron_point_name='DERControl_mRID', path='DERControl.mRID'),
            dict(volttron_point_name='DERControlList', path='DERControlList'),
            dict(volttron_point_name='DERProgram_primacy', path='DERProgram.primacy')]
    for row in rows:
        iface.insert_register(iface.create_register(Ieee2030_5PointConfig(**row)), 'campus/b/der_sep2')
    t0 = time.time(); iface.finalize_setup(initial_setup=True); t_setup = time.time() - t0
    check('proxy launched and server registered', iface.proxy_peer is not None and iface.proxy_peer.socket_params is not None, f'{t_setup:.1f}s')

    def describe():
        result, errors = iface.parse_proxy_response(iface._send('DESCRIBE_SERVER', iface.config.identity_fields()), ['server'])
        return result if not errors else {}
    ready = wait_for(lambda: describe().get('ready') is True, 30)
    d = describe()
    check('session ready: registration found, program, DER declared, MUP, subscriptions', ready and d.get('lfdi') == LFDI
          and d.get('programs') == ['/edev/1/fsa/0/derp/p1'] and d.get('der') == '/edev/1/der/1' and d.get('mup')
          and d.get('subscriptions', 0) >= 2 and d.get('notifications') is not None, json.dumps(d))

    results, errors = iface.get_multiple_points(list(iface.point_map))
    print('  results:', {k.split('/')[-1]: v for k, v in results.items()}); print('  errors:', {k.split('/')[-1]: v for k, v in errors.items()})
    check('poll: defaults in force, no events, program primacy', results.get(T('DERControl_opModMaxLimW')) == 10000
          and results.get(T('DERControl_opModConnect')) is True and results.get(T('DERControlList')) == []
          and results.get(T('DERProgram_primacy')) == 0 and errors == {}, str(errors))
    check('poll: starting values seeded the upward shadows', results.get(T('DERCapability_type')) == 83 and results.get(T('DERSettings_setGradW')) == 100)
    check('first push carried the defaults', pushed_value(agent, T('DERControl_opModMaxLimW')) == 10000)

    r, e = iface.set_multiple_points([(T('DERSettings_setMaxW'), 5000), (T('DERStatus_operationalModeStatus'), 1),
                                      (T('MirrorMeterReading_W'), 4321.5), (T('DERCapability_rtgMaxW'), 12500)])
    check('set_multiple_points: settings, status, capability PUT and reading POST accepted', e == {} and set(r) == {
        T('DERSettings_setMaxW'), T('DERStatus_operationalModeStatus'), T('MirrorMeterReading_W'), T('DERCapability_rtgMaxW')}, str(e))
    import asyncio
    from protocol_proxy.protocol.ieee2030_5.transport import Sep2Http
    from protocol_proxy.protocol.ieee2030_5.models import sep
    async def fetch(href, cls):
        http = Sep2Http(server.url, cert_path=str(certs['device_cert']), key_path=str(certs['device_key']), ca_path=str(certs['ca']), retries=1)
        try:
            return await http.get(href, cls)
        finally:
            await http.aclose()
    settings = asyncio.run(fetch('/edev/1/der/1/derg', sep.DERSettings))
    status = asyncio.run(fetch('/edev/1/der/1/ders', sep.DERStatus))
    mups = asyncio.run(fetch('/mup', sep.MirrorUsagePointList))
    # sep2server stores setMaxW and the status struct; it drops setGradW (not in its DERSettings model), so that is not asserted.
    check('server holds the PUT DERSettings and DERStatus', settings.setMaxW is not None and settings.setMaxW.value == 5000
          and status.operationalModeStatus is not None and status.operationalModeStatus.value == 1,
          f'{settings.setMaxW} {status.operationalModeStatus}')
    check('server holds our MirrorUsagePoint', any(m.deviceLFDI and m.deviceLFDI.hex().upper() == LFDI for m in mups.MirrorUsagePoint),
          str([m.href for m in mups.MirrorUsagePoint]))
    check('upward rows read back from the shadows', iface.get_point(T('DERSettings_setMaxW')) == 5000 and iface.get_point(T('MirrorMeterReading_W')) == 4321.5)

    agent.pushed.clear()
    now = int(time.time())
    mrid = server.create_control(now + 3, 60, max_lim_w=4000)
    arrived = wait_for(lambda: pushed_value(agent, T('DERControl_opModMaxLimW')) == 4000, 25)
    check('DERControl from the admin API: pushed when it starts (no poll due)', arrived and pushed_value(agent, T('DERControl_mRID')) == mrid, str(agent.pushed[-2:]))
    entries = iface.get_point(T('DERControlList'))
    check('DERControlList holds the active event', isinstance(entries, list) and len(entries) == 1 and entries[0]['mRID'] == mrid
          and entries[0]['status'] == 'active' and entries[0]['DERControl'].get('opModMaxLimW') == 4000, str(entries)[:200])
    d = describe()
    by_status = {c['mRID']: c.get('responses', {}).get('byStatus', {}) for c in server.controls()}
    check('server received the received(1) and started(2) responses', by_status.get(mrid, {}).get('1') == 1 and by_status.get(mrid, {}).get('2') == 1, str(by_status))
    check('notification received over TLS', d.get('notifications', 0) >= 1, str(d.get('notifications')))

    server.cancel_control(mrid)
    reverted = wait_for(lambda: pushed_value(agent, T('DERControl_opModMaxLimW')) == 10000, 25)
    by_status = {c['mRID']: c.get('responses', {}).get('byStatus', {}) for c in server.controls()}
    check('cancel: default pushed back and cancelled(6) response sent', reverted and by_status.get(mrid, {}).get('6') == 1, str(by_status))
    check('get_point on a control refreshes from the server', iface.get_point(T('DERControl_opModMaxLimW')) == 10000 and iface.get_point(T('DERControlList')) == [])
    try:
        iface.set_point(T('DERControl_opModMaxLimW'), 1); check('read-only write rejected', False)
    except Exception as ex:
        check('read-only write rejected', 'read only' in str(ex))
finally:
    for peer in list(getattr(iface.ppm, 'peers', {}).values()) if iface is not None else []:
        proc = getattr(peer, 'process', None)
        if proc: proc.terminate()
    if os.environ.get('SEP2_E2E_KEEP') != '1':
        server.stop()
failed = [n for n, ok in checks if not ok]
print(f'\n{len(checks) - len(failed)}/{len(checks)} passed' + (f'; FAILED: {failed}' if failed else ''))
sys.exit(1 if failed else 0)
