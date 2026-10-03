# End-to-end test against the GridAPPS-D Go `sep2server`

`harness.py` runs the whole stack: this interface, a real `GeventProtocolProxyManager`, the IEEE 2030.5 proxy as a
subprocess, and the GridAPPS-D Go IEEE 2030.5 server (`sep2server`) in Docker as the utility side. It mints a CA,
server, device and receiver certificate with the server's own `certs` commands, seeds the server from a boot fixture
(one EndDevice with the device certificate's LFDI and SFDI, one FSA, one DERProgram with a DefaultDERControl), then
checks registration, polling, PUT/POST of the upward resources, an admin-created DERControl arriving by notification
and pushed as it starts, the DERControlResponses the server records, cancellation and read-only enforcement.

```shell
python -m pytest tests/test_end_to_end.py          # skipped without Docker; builds the image the first time
python tests/e2e_sep2server/harness.py 18443 18080 18444 /tmp/sep2e2e   # by hand, with fixed ports and a work dir
```

Environment: `SEP2SERVER_IMAGE` (default `sep2server:e2e`), `SEP2SERVER_REPO` (the Go server's git URL),
`SEP2SERVER_SKIP_BUILD=1` to fail rather than build a missing image, `SEP2_E2E_KEEP=1` to leave the container running
for inspection (`docker logs sep2e2e-<port>`), `SEP2_E2E_LOG` for the harness log file.

What the Go server needs, learnt the hard way and encoded in `sep2server.py`:

* It speaks TLS 1.2 with `ECDHE-ECDSA-AES128-CCM8` only; OpenSSL 3 enables that suite only at `@SECLEVEL=0`, which
  the proxy's `make_ssl_context` sets whenever CCM-8 is requested.
* Boot-fixture EndDevice ids must be decimal strings, and the registration, FSA-list and DER-list links must be
  declared on the EndDevice or it carries none. The DERList starts empty; the client declares its DER with
  `PUT /edev/1/der/1` (`create_der_if_missing`). The EndDevice advertises no SubscriptionListLink, but
  `/edev/1/sub` exists; the proxy tries that conventional path.
* Its notifier is a plain Go HTTP client: it trusts the system roots (hence `SSL_CERT_FILE=/tls/ca.crt`) and rejects a
  device certificate as a server certificate (critical CSIP extensions), hence the separate receiver certificate
  (`notify_cert_path`).
* The admin API needs `SEP2_PEN` to mint control mRIDs, `Authorization: Bearer <SEP2_ADMIN_KEY>`, and DERControls of
  at least 60 s starting in the future.

`Dockerfile.e2e` is the upstream Dockerfile without the `go mod download` step, building from the repository's vendored
modules (`-mod=vendor`), which is what succeeds without module-proxy access.
