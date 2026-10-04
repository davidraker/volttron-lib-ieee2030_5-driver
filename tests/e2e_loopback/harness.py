"""End-to-end harness for the server role: one real proxy process serves a CSIP server for an interface in the server
role, and a second interface in the client role reaches it over mutual TLS through the same proxy. Run by
test_end_to_end.py in its own process (gevent's monkey patching must happen before other imports). Exit status is
non-zero if any check fails.

Arguments: <server port> <notify port> [work dir]."""
from gevent import monkey; monkey.patch_all()
import gevent, json, logging, os, sys, tempfile, time
from pathlib import Path
logging.basicConfig(filename=os.environ.get('SEP2_E2E_LOG', '/tmp/sep2_loopback_e2e.log'), level=logging.DEBUG,
                    format='%(asctime)s %(name)s %(levelname)s %(message)s')
sys.path.insert(0, str(Path(__file__).resolve().parent))
from certs import generate_certs                                                      # noqa: E402
from protocol_proxy.protocol.ieee2030_5.identity import lfdi_from_cert                # noqa: E402
from volttron.driver.base.config import RemoteConfig                                 # noqa: E402
from volttron.driver.interfaces.ieee2030_5.ieee2030_5 import Ieee2030_5             # noqa: E402
from volttron.driver.interfaces.ieee2030_5.config import Ieee2030_5PointConfig       # noqa: E402

PORT, NOTIFY_PORT = (int(a) for a in sys.argv[1:3])
WORK = Path(sys.argv[3]) if len(sys.argv) > 3 else Path(tempfile.mkdtemp(prefix='sep2loop-'))
certs = generate_certs(WORK / 'tls')
LFDI = lfdi_from_cert(certs['device_cert'])
PIN = 111115


class Core: spawn = staticmethod(gevent.spawn)
class Agent:
    core = Core()
    def __init__(self): self.pushed = []
    def publish_push(self, result): self.pushed.append(dict(result))
    def latest(self, topic):
        for result in reversed(self.pushed):
            if topic in result: return result[topic]
        return None


Ieee2030_5.default_config = {}
S = 'campus/b/utility/{}'.format           # the served server's topics
C = 'campus/b/der/{}'.format               # the DER client's topics
checks = []
def check(name, cond, detail=''):
    checks.append((name, bool(cond))); print(('PASS ' if cond else 'FAIL ') + name + (f'  {detail}' if detail else ''))
def wait_for(predicate, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate(): return True
        gevent.sleep(0.25)
    return predicate()
def describe(iface):
    result, errors = iface.parse_proxy_response(iface._send('DESCRIBE_SERVER', {}), ['server'])
    return result if not errors else {}


SERVED_ROWS = [dict(volttron_point_name='DefaultDERControl_opModMaxLimW', path='DefaultDERControl.opModMaxLimW', default_value=100),
               dict(volttron_point_name='DefaultDERControl_opModConnect', path='DefaultDERControl.opModConnect', default_value='true'),
               dict(volttron_point_name='DERControl_opModMaxLimW', path='DERControl.opModMaxLimW'),
               dict(volttron_point_name='DERControl_mRID', path='DERControl.mRID'),
               dict(volttron_point_name='DERControlList', path='DERControlList'),
               dict(volttron_point_name='DERProgram_primacy', path='DERProgram.primacy'),
               dict(volttron_point_name='DERSettings_setMaxW', path='DERSettings.setMaxW', units='W'),
               dict(volttron_point_name='DERStatus_operationalModeStatus', path='DERStatus.operationalModeStatus'),
               dict(volttron_point_name='MirrorMeterReading_W', path='MirrorMeterReading.W', units='W')]
CLIENT_ROWS = [dict(volttron_point_name='DERSettings_setMaxW', path='DERSettings.setMaxW', units='W'),
               dict(volttron_point_name='DERStatus_operationalModeStatus', path='DERStatus.operationalModeStatus'),
               dict(volttron_point_name='DERCapability_type', path='DERCapability.type', starting_value=83),
               dict(volttron_point_name='MirrorMeterReading_W', path='MirrorMeterReading.W', units='W'),
               dict(volttron_point_name='DERControl_opModMaxLimW', path='DERControl.opModMaxLimW'),
               dict(volttron_point_name='DERControl_mRID', path='DERControl.mRID'),
               dict(volttron_point_name='DefaultDERControl_opModMaxLimW', path='DefaultDERControl.opModMaxLimW'),
               dict(volttron_point_name='DERControlList', path='DERControlList'),
               dict(volttron_point_name='DERProgram_primacy', path='DERProgram.primacy')]

server = client = None
server_agent, client_agent = Agent(), Agent()
try:
    server = Ieee2030_5(RemoteConfig(driver_type='ieee2030_5', driver_role='server', bind_host='127.0.0.1', port=PORT,
                                     cert_path=str(certs['server_cert']), key_path=str(certs['server_key']), ca_path=str(certs['ca']),
                                     client_lfdi=LFDI, client_pin=PIN, poll_rate=2, immediate_control_duration=120,
                                     state_dir=str(WORK / 'state')), driver_agent=server_agent)
    for row in server.prepare_registry_config([dict(r) for r in SERVED_ROWS], server.config):
        server.insert_register(server.create_register(Ieee2030_5PointConfig(**row)), 'campus/b/utility')
    t0 = time.time(); server.finalize_setup(initial_setup=True); t_setup = time.time() - t0
    check('proxy launched and server served over TLS', server.proxy_peer is not None and server.proxy_peer.socket_params is not None
          and describe(server).get('listening') is True, f'{t_setup:.1f}s {describe(server).get("state_file")}')
    check('registration pushed the served table', wait_for(lambda: server_agent.latest(S('DERProgram_primacy')) == 1)
          and server_agent.latest(S('DefaultDERControl_opModMaxLimW')) == 100, str(server_agent.pushed[:2]))

    client = Ieee2030_5(RemoteConfig(driver_type='ieee2030_5', server_url=f'https://127.0.0.1:{PORT}', cert_path=str(certs['device_cert']),
                                     key_path=str(certs['device_key']), ca_path=str(certs['ca']), pin=PIN, subscribe=True,
                                     notify_host='127.0.0.1', notify_bind_host='127.0.0.1', notify_port=NOTIFY_PORT,
                                     notify_cert_path=str(certs['notify_cert']), notify_key_path=str(certs['notify_key']),
                                     poll_rate_floor=1, poll_rate_ceiling=5, response_timeout=10), driver_agent=client_agent)
    for row in CLIENT_ROWS:
        client.insert_register(client.create_register(Ieee2030_5PointConfig(**row)), 'campus/b/der')
    client.finalize_setup(initial_setup=True)
    check('client registered with the same proxy', client.proxy_peer is server.proxy_peer)
    ready = wait_for(lambda: describe(client).get('ready') is True, 30)
    d = describe(client)
    check('client session ready against the served server: pre-registered EndDevice, program, DER, MUP, subscriptions',
          ready and d.get('lfdi') == LFDI and d.get('end_device') == '/edev/1' and d.get('registered_now') is False
          and d.get('programs') == ['/derp/1'] and d.get('der') == '/edev/1/der/1' and d.get('mup') == '/mup/1'
          and d.get('subscriptions', 0) >= 2, json.dumps(d))
    check('client sees the served defaults', wait_for(lambda: client_agent.latest(C('DefaultDERControl_opModMaxLimW')) == 100)
          and client.get_point(C('DERControl_opModMaxLimW')) == 100 and client.get_point(C('DERProgram_primacy')) == 1)

    # The DER client reports upward: the server interface receives pushes.
    # Readings travel as integers (Reading.value with a power-of-ten multiplier), so the test value is whole.
    r, e = client.set_multiple_points([(C('DERSettings_setMaxW'), 5000), (C('DERStatus_operationalModeStatus'), 2), (C('MirrorMeterReading_W'), 4321)])
    check('client PUTs and POSTs accepted by the served server', e == {} and len(r) == 3, str(e))
    check('client reports pushed to the server interface', wait_for(lambda: server_agent.latest(S('MirrorMeterReading_W')) == 4321)
          and server_agent.latest(S('DERSettings_setMaxW')) == 5000 and server_agent.latest(S('DERStatus_operationalModeStatus')) == 2,
          str(server_agent.pushed[-3:]))
    check('server interface reads the reported values', server.get_point(S('DERSettings_setMaxW')) == 5000 and server.get_point(S('MirrorMeterReading_W')) == 4321)

    # The platform writes the served default control: reflected on the server side, notified to the client.
    server_agent.pushed.clear(); client_agent.pushed.clear()
    r, e = server.set_multiple_points([(S('DefaultDERControl_opModMaxLimW'), 80)])
    check('platform write of the default control stored and reflected', r == {S('DefaultDERControl_opModMaxLimW'): 80} and e == {}
          and server_agent.latest(S('DefaultDERControl_opModMaxLimW')) == 80, str(e))
    check('client notified: new default pushed to the client interface', wait_for(lambda: client_agent.latest(C('DefaultDERControl_opModMaxLimW')) == 80, 15)
          and client_agent.latest(C('DERControl_opModMaxLimW')) == 80, str(client_agent.pushed[-2:]))

    # An immediate control: an active event the client runs and answers.
    client_agent.pushed.clear()
    r, e = server.set_multiple_points([(S('DERControl_opModMaxLimW'), 40)])
    mrid = server.get_point(S('DERControl_mRID'))
    check('immediate control created', e == {} and isinstance(mrid, str) and len(mrid) == 32, f'{e} {mrid}')
    check('client runs the event: control value and mRID pushed', wait_for(lambda: client_agent.latest(C('DERControl_opModMaxLimW')) == 40, 15)
          and wait_for(lambda: client_agent.latest(C('DERControl_mRID')) == mrid, 5), str(client_agent.pushed[-3:]))
    def responses():
        entries = server.get_point(S('DERControlList'))
        return sorted(x['status'] for en in entries if en['mRID'] == mrid for x in en.get('responses', []))
    check('client responded received(1) and started(2); the served DERControlList shows them', wait_for(lambda: responses() == [1, 2], 15), str(responses()))
    entries = client.get_point(C('DERControlList'))
    check('client DERControlList holds the active event', isinstance(entries, list) and len(entries) == 1 and entries[0]['mRID'] == mrid
          and entries[0]['status'] == 'active', str(entries)[:160])

    # A proxy crash: the manager relaunches it, both interfaces register again, and the served state comes back from
    # the state file rather than from the starting values.
    old_peer = server.proxy_peer
    old_peer.process.terminate()
    relaunched = wait_for(lambda: server.proxy_peer is not None and server.proxy_peer is not old_peer
                          and server.proxy_peer.socket_params is not None and client.proxy_peer is server.proxy_peer, 30)
    check('proxy relaunched after a crash and both remotes set up again', relaunched)
    restored = wait_for(lambda: describe(server).get('listening') is True and describe(server).get('devices', {}).get(LFDI) == '/edev/1', 20)
    d = describe(server)
    check('served state restored from the state file: device, controls and received values', restored
          and any(c['mRID'] == mrid for c in d.get('controls', [])) and server.get_point(S('DefaultDERControl_opModMaxLimW')) == 80
          and server.get_point(S('DERSettings_setMaxW')) == 5000, json.dumps(d)[:300])
    ready = wait_for(lambda: describe(client).get('ready') is True, 30)
    check('client session ready again and sees the restored default', ready and client.get_point(C('DefaultDERControl_opModMaxLimW')) == 80)
finally:
    for iface in (server, client):
        if iface is not None and iface.ppm is not None:
            for peer in list(getattr(iface.ppm, 'peers', {}).values()):
                proc = getattr(peer, 'process', None)
                if proc: proc.terminate()
failed = [n for n, ok in checks if not ok]
print(f'\n{len(checks) - len(failed)}/{len(checks)} passed' + (f'; FAILED: {failed}' if failed else ''))
sys.exit(1 if failed else 0)
