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

Independent rule groups:

```bash
/usr/bin/python3 tests/integration_groups.py --bin-dir /private/state/bin
```

This uses a local recording SOCKS5 server and temporary VLESS gateway/client pairs to test all four academic/CMU switch combinations on both backends. It verifies enabled domains reach the upstream as hostnames (including an ECE-style name), disabled groups do not use the upstream, and generated configurations pass the native validators. It does not require a VPN login. REALITY handshake traffic may contact the configured public handshake host.

Additional regression tests verify group persistence through all/none scopes, same-scope toggles, legacy migration, custom policy priority, an empty selection, and extensible group definitions. With the existing authenticated CMU worker, both temporary VLESS gateway backends also returned an SSH greeting from `ece017.ece.local.cmu.edu:22`; no school account login or GPU job was performed.

Linux host routing:

```bash
/usr/bin/python3 tests/integration_host.py
```

Requires root, installed binaries and an authenticated internal VPN in the default state (or pass `--state`). It creates a disposable network namespace and per-namespace resolver configuration. It tests unproxied curl exits, DNS/ECE SSH, UDP STUN, unavailable-VPN isolation and route cleanup in host rules/all modes, while keeping the real host's routes and DNS unchanged. ECE node availability and public test services are external dependencies.

Live-host validation on this Ubuntu machine also passed: ordinary curl used the VPN exit in all mode; ordinary traffic used the VPS exit while ECE worked in rules mode; current SSH retained its physical route; the worker PID stayed unchanged; normal stop restored the original resolver symlink and exit. A forced TUN-process SIGKILL also restored DNS, IPv4/IPv6 policy rules and nftables. The host was returned to off after tests. These live mutation checks are intentionally not run automatically by the namespace test.

Two-axis regression tests cover the boolean capture menu/default, legacy host-mode migration, shared-policy generation, rules → all → none → rules with capture preserved, and disabled capture staying off across policy changes. VPN-off keeps the preference without starting a TUN.

Authentication recovery tests cover resetting a failed endpoint once, leaving connected/pending endpoints alone, rejecting unrelated worker units, and retaining the host capture preference with a post-auth refresh. Live recovery reached a new CMU browser challenge after `Max time exceeded`; completing the new session still requires the user’s browser login.

Authentication bootstrap routing: `/usr/bin/python3 tests/integration_auth_routes.py` requires root and installed default-state binaries, but no VPN session. Temporary loopback gateways and a disposable host network namespace use a deliberately unavailable SOCKS port. In rules/all modes, both server engines and host TUN must reach the public CMU VPN/login pages (HTTP 200), while an ACM request must fail. Native config validation is included; the test leaves the installed services and routing choices untouched. This verifies pre-login reachability, not completion of browser MFA.
