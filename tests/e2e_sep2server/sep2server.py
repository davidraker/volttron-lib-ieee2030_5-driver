"""Run the GridAPPS-D Go IEEE 2030.5 server (``sep2server``) in Docker as the end-to-end target.

The image is built from https://github.com/GRIDAPPSD/ieee-2030_5-server-go with ``Dockerfile.e2e`` (the upstream
Dockerfile minus ``go mod download``, building from the repository's vendored modules). Certificates are minted with
the server's own ``certs`` sub-commands; the boot fixture seeds one EndDevice (the device certificate's LFDI and SFDI),
one FSA and one DERProgram with a DefaultDERControl. Environment: ``SEP2SERVER_IMAGE`` (default ``sep2server:e2e``),
``SEP2SERVER_REPO`` (git URL), ``SEP2SERVER_SKIP_BUILD=1`` to fail instead of building when the image is missing.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

IMAGE = os.environ.get('SEP2SERVER_IMAGE', 'sep2server:e2e')
REPO = os.environ.get('SEP2SERVER_REPO', 'https://github.com/GRIDAPPSD/ieee-2030_5-server-go.git')
ADMIN_KEY = 'e2e-admin-key'
PEN = '40732'
HERE = Path(__file__).resolve().parent


def docker_available() -> bool:
    return shutil.which('docker') is not None and subprocess.run(['docker', 'version'], capture_output=True).returncode == 0


def image_present() -> bool:
    return subprocess.run(['docker', 'image', 'inspect', IMAGE], capture_output=True).returncode == 0


def ensure_image(log=print) -> None:
    """Build the image from the upstream repository when it is missing."""
    if image_present():
        return
    if os.environ.get('SEP2SERVER_SKIP_BUILD'):
        raise RuntimeError(f'Docker image {IMAGE} is missing and SEP2SERVER_SKIP_BUILD is set')
    with tempfile.TemporaryDirectory() as tmp:
        log(f'cloning {REPO}')
        subprocess.run(['git', 'clone', '-q', '--depth', '1', REPO, tmp], check=True)
        shutil.copy(HERE / 'Dockerfile.e2e', Path(tmp) / 'Dockerfile.e2e')
        log(f'building {IMAGE} (a few minutes the first time)')
        subprocess.run(['docker', 'build', '-q', '-f', 'Dockerfile.e2e', '-t', IMAGE, '.'], cwd=tmp, check=True)


def _run_certs(cert_dir: Path, *args: str) -> str:
    user = f'{os.getuid()}:{os.getgid()}'
    result = subprocess.run(['docker', 'run', '--rm', '-u', user, '-e', 'HOME=/tmp', '-v', f'{cert_dir}:/tls', IMAGE, 'certs', *args],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f'certs {args[0]} failed: {result.stdout} {result.stderr}')
    return result.stdout


def generate_certs(cert_dir: Path) -> dict[str, Path]:
    """CA, server, device and a server-style receiver certificate. Returns the paths."""
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert_dir.chmod(0o755)
    _run_certs(cert_dir, 'generate-ca', '-out', '/tls')
    _run_certs(cert_dir, 'generate-server', '-out', '/tls', '-ca', '/tls/ca.crt', '-ca-key', '/tls/ca.key', '-hosts', '127.0.0.1,localhost')
    _run_certs(cert_dir, 'generate-device', '-out', '/tls', '-ca', '/tls/ca.crt', '-ca-key', '/tls/ca.key', '-name', 'volttron',
               '-hw-serial', 'VT0001', '-hw-type', f'1.3.6.1.4.1.{PEN}.1')
    (cert_dir / 'notify').mkdir(exist_ok=True)
    _run_certs(cert_dir, 'generate-server', '-out', '/tls/notify', '-ca', '/tls/ca.crt', '-ca-key', '/tls/ca.key',
               '-hosts', '127.0.0.1,localhost', '-cn', 'VOLTTRON notify')
    for key in cert_dir.rglob('*.key'):
        key.chmod(0o644)                     # the server container runs as this user; the proxy reads the device key
    return {'ca': cert_dir / 'ca.crt', 'device_cert': cert_dir / 'volttron.crt', 'device_key': cert_dir / 'volttron.key',
            'notify_cert': cert_dir / 'notify' / 'server.crt', 'notify_key': cert_dir / 'notify' / 'server.key'}


def write_fixture(path: Path, lfdi: str, sfdi: int) -> None:
    """One EndDevice (decimal id, every link declared), one FSA, one program with a DefaultDERControl."""
    path.write_text(f'''end_devices:
  - id: "1"
    sfdi: "{sfdi:012d}"
    lfdi: "{lfdi}"
    enabled: true
    changed_time: 100
    registration_link: "/edev/1/rg"
    function_set_assignments_list_link:
      href: "/edev/1/fsa"
      all: 1
    der_list_link:
      href: "/edev/1/der"
      all: 1
fsas:
  - end_device_id: "1"
    id: "0"
    der_program_list_link:
      href: "/edev/1/fsa/0/derp"
      all: 1
der_programs:
  - end_device_id: "1"
    fsa_id: "0"
    id: p1
    mrid: "D2D2D2D2D2D2D2D2"
    description: "e2e program"
    primacy: 0
    default_der_control_link: "/edev/1/fsa/0/derp/p1/dderc"
    der_control_list_link:
      href: "/edev/1/fsa/0/derp/p1/derc"
      all: 0
default_der_controls:
  - end_device_id: "1"
    fsa_id: "0"
    der_program_id: p1
    der_control_base:
      op_mod_connect: true
      op_mod_max_lim_w: 10000
''')
    path.chmod(0o644)
    path.parent.chmod(0o755)


class Sep2Server:
    """A running container on the host network: protocol on ``port``, admin API on ``admin_port``."""

    def __init__(self, cert_dir: Path, fixture: Path, port: int, admin_port: int, name: str = 'sep2e2e'):
        self.cert_dir, self.fixture, self.port, self.admin_port, self.name = cert_dir, fixture, port, admin_port, name
        self.url = f'https://127.0.0.1:{port}'
        self.admin_url = f'http://127.0.0.1:{admin_port}'

    def start(self, timeout: float = 20.0) -> None:
        subprocess.run(['docker', 'rm', '-f', self.name], capture_output=True)
        user = f'{os.getuid()}:{os.getgid()}'
        # SSL_CERT_FILE: the server's notifier is a plain Go HTTP client trusting the system roots, so the test CA that
        # signed the receiver's certificate has to be the root bundle inside the container.
        cmd = ['docker', 'run', '-d', '--name', self.name, '--network', 'host', '-u', user, '-e', 'HOME=/tmp',
               '-e', 'SSL_CERT_FILE=/tls/ca.crt',
               '-v', f'{self.cert_dir}:/tls:ro', '-v', f'{self.fixture.parent}:/fix:ro',
               '-e', 'SEP2_CERT_DIR=/tls', '-e', f'SEP2_ADDR=127.0.0.1:{self.port}', '-e', f'SEP2_ADMIN_LISTEN=127.0.0.1:{self.admin_port}',
               '-e', f'SEP2_ADMIN_KEY={ADMIN_KEY}', '-e', f'SEP2_PEN={PEN}', '-e', 'SEP2_NOTIFICATION_ALLOW_LOOPBACK=true',
               '-e', f'SEP2_BOOT_FIXTURE=/fix/{self.fixture.name}', IMAGE, 'serve']
        subprocess.run(cmd, check=True, capture_output=True)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                self.admin('GET', '/api/der/controls?device=1')
                return
            except Exception:
                time.sleep(0.3)
        raise RuntimeError(f'sep2server did not come up:\n{self.logs()[-3000:]}')

    def stop(self) -> None:
        subprocess.run(['docker', 'rm', '-f', self.name], capture_output=True)

    def logs(self) -> str:
        return subprocess.run(['docker', 'logs', self.name], capture_output=True, text=True).stdout

    def admin(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.admin_url + path, data=data, method=method,
                                         headers={'Authorization': f'Bearer {ADMIN_KEY}', 'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=10) as response:
            text = response.read().decode()
        return json.loads(text) if text else {}

    def create_control(self, start: int, duration: int, *, max_lim_w: int | None = None, control_type: str = 'maxLimW',
                       description: str = 'e2e') -> str:
        body: dict = {'derProgramHref': '/edev/1/fsa/0/derp/p1', 'type': control_type, 'startTime': start,
                      'durationSeconds': max(60, duration), 'description': description}
        if max_lim_w is not None:
            body['maxLimW'] = max_lim_w
        return self.admin('POST', '/api/der/controls', body)['mRID']

    def cancel_control(self, mrid: str) -> None:
        self.admin('POST', f'/api/der/controls/{mrid}/cancel', {'reason': 'e2e'})

    def controls(self) -> list[dict]:
        return self.admin('GET', '/api/der/controls?device=1').get('controls', [])
