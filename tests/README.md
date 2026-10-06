# Validation

Use the system Python after `./entrypoint.sh deps`:

```bash
/usr/bin/python3 -m unittest discover -s tests -p 'test_*.py' -v
bash -n entrypoint.sh
```

The unit/regression tests cover the interactive wizard defaults, saved backend selection, transactional generation and credentials, explicit SNI/port handling, non-mutating settings resolution, and simulated systemd backend switching with rollback. Systemd is mocked in this test; no host services are changed. The artifact tests also check imported identity preservation, cross-client link/key consistency, private file permissions, JSONC handling and cookie origin selection. They do not start services.

Optional live interoperability tests:

```bash
/usr/bin/python3 tests/integration.py \
  --bin-dir /private/state/bin --vpn-port <existing-local-SOCKS-port>
```

Requires an already authenticated, authorized SOCKS VPN whose exit differs from the server's exit. This test starts temporary **loopback-only** gateways/clients with freshly generated credentials, tests 3 routing modes across 2 server and 2 client backends (12 combinations), then tests VPN failure isolation on both servers. It makes real requests to ipify and AWS CheckIP. It does not install systemd units, modify existing gateways or restart the VPN session.

`tests/integration_policy.py` accepts the same arguments and tests ordered direct exceptions, a VPN default and blocking on both engines.

Validated on Ubuntu 24.04 x86_64 with sing-box 1.14.2, Xray 26.3.27 and an authenticated CMU VPN SOCKS upstream: all 12 combinations, both failure-isolation checks and both ordered-policy checks passed. The generated OpenConnect worker also reached CMU's Full VPN browser challenge without manual group selection. Existing Xray listener/configuration and system routes remained unchanged.

Not validated here: other university/provider gateways, ARM hardware, a fresh-machine systemd deployment, complete browser-helper GUI authentication, and paid full-text access. The browser helper has unit checks but still needs a real local GUI run.

Quick-switch regression tests exercise rules → all → none → rules while preserving identity, share-link connection parameters and ordered policy, avoiding installer calls, and asking only the mode. They also cover unchanged-mode no-op, first-time VPN setup and missing installation. Deployment and authentication are mocked for these tests.
