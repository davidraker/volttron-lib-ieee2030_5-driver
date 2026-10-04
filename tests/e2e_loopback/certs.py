"""CSIP-style test certificates with the cryptography library: a CA, a server certificate for 127.0.0.1, a device
certificate (the DER client's identity, hence its LFDI) and a server-style certificate for the client's notification
receiver. Elliptic-curve P-256 keys, as IEEE 2030.5 mandates."""
from __future__ import annotations

import datetime
import ipaddress
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def _write(path: Path, cert, key):
    path.with_suffix('.crt').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    path.with_suffix('.key').write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                           serialization.NoEncryption()))
    return path.with_suffix('.crt'), path.with_suffix('.key')


def generate_certs(cert_dir: Path) -> dict[str, Path]:
    cert_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc)
    before, after = now - datetime.timedelta(days=1), now + datetime.timedelta(days=30)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'VOLTTRON test CA')])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(before).not_valid_after(after)
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .sign(ca_key, hashes.SHA256()))
    ca_cert, ca_key_path = _write(cert_dir / 'ca', ca, ca_key)

    def leaf(name: str, usages, san=None):
        key = ec.generate_private_key(ec.SECP256R1())
        builder = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
                   .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(before).not_valid_after(after)
                   .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                   .add_extension(x509.ExtendedKeyUsage(usages), critical=False))
        if san:
            builder = builder.add_extension(x509.SubjectAlternativeName(san), critical=False)
        return _write(cert_dir / name, builder.sign(ca_key, hashes.SHA256()), key)

    server_san = [x509.IPAddress(ipaddress.ip_address('127.0.0.1')), x509.DNSName('localhost')]
    server_cert, server_key = leaf('server', [ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH], server_san)
    notify_cert, notify_key = leaf('notify', [ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH], server_san)
    device_cert, device_key = leaf('device', [ExtendedKeyUsageOID.CLIENT_AUTH])
    return {'ca': ca_cert, 'server_cert': server_cert, 'server_key': server_key, 'device_cert': device_cert,
            'device_key': device_key, 'notify_cert': notify_cert, 'notify_key': notify_key}
