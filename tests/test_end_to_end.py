"""Runs the end-to-end harnesses in subprocesses: the client role against the Go sep2server in Docker (needs Docker and
three free local ports; skipped when Docker is unavailable or the image cannot be built), and the server role served by
the proxy to a client-role interface over mutual TLS on the loopback interface (needs nothing but two free ports).
"""
import os
import socket
import subprocess
import sys

from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).with_name('e2e_sep2server')))
import sep2server  # noqa: E402

HARNESS = Path(__file__).with_name('e2e_sep2server') / 'harness.py'
LOOPBACK_HARNESS = Path(__file__).with_name('e2e_loopback') / 'harness.py'
EXPECTED_CHECKS = 16
EXPECTED_LOOPBACK_CHECKS = 17


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.mark.skipif(not sep2server.docker_available(), reason='Docker is not available')
def test_end_to_end(tmp_path):
    try:
        sep2server.ensure_image()
    except Exception as e:                                  # network or build failure
        pytest.skip(f'sep2server image unavailable: {e}')
    log = tmp_path / 'e2e.log'
    proc = subprocess.run([sys.executable, str(HARNESS), str(_free_port()), str(_free_port()), str(_free_port()), str(tmp_path / 'work')],
                          capture_output=True, text=True, timeout=600, env={**os.environ, 'SEP2_E2E_LOG': str(log)})
    report = proc.stdout + proc.stderr
    if proc.returncode != 0 and log.exists():
        report += '\n--- log ---\n' + log.read_text()[-6000:]
    assert proc.returncode == 0, report
    assert f'{EXPECTED_CHECKS}/{EXPECTED_CHECKS} passed' in proc.stdout, report


def test_end_to_end_server_role(tmp_path):
    """A served server and a DER client, both interfaces through one real proxy process, over mutual TLS."""
    log = tmp_path / 'e2e_loopback.log'
    proc = subprocess.run([sys.executable, str(LOOPBACK_HARNESS), str(_free_port()), str(_free_port()), str(tmp_path / 'work')],
                          capture_output=True, text=True, timeout=400, env={**os.environ, 'SEP2_E2E_LOG': str(log)})
    report = proc.stdout + proc.stderr
    if proc.returncode != 0 and log.exists():
        report += '\n--- log ---\n' + log.read_text()[-6000:]
    assert proc.returncode == 0, report
    assert f'{EXPECTED_LOOPBACK_CHECKS}/{EXPECTED_LOOPBACK_CHECKS} passed' in proc.stdout, report
