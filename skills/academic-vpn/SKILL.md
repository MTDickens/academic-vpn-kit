---
name: academic-vpn
description: Maintain or migrate this VPN Route Kit repository, its sing-box/Xray VLESS REALITY gateways, optional university VPN, domain routing, private client artifacts, and SAML browser helper.
---

# VPN Route Kit maintenance

Read the repository README for the interactive wizard and deployed file layout. Normal usage is `sudo ./entrypoint.sh` without arguments; preserve numbered choices, visible defaults and saved settings. Keep CLI arguments only for automation. Resolve the repository relative to this skill (`../..`), not a hardcoded user's home directory. Use [runtime notes](references/runtime.md) when diagnosing authentication, routing, or migration failures.

Preserve the user's selected gateway backend, `none`/`rules`/`all` mode, VPN provider and custom lists. The default is a normal node without VPN. `all` covers traffic entering the proxy, not the host's network. A university's protocol cannot be inferred from its name; obtain its official VPN instructions before adding a preset.

Runtime state and all generated artifacts stay outside the source tree. UUID, REALITY private key, cookies, QR codes and client links must not enter commits, logs or issue reports. Read identity locally when needed but print only relevant non-secret fields. Never request passwords or tokens in chat. Browser helper handles a narrowly supported Cookie-type AnyConnect SAML flow; preserve terminal authentication as fallback and keep MFA interactive.

For a generator change, validate both server and client configurations with the installed pinned sing-box and Xray binaries. Test observable routes in `none`, `rules`, and `all`; use loopback listeners and separate ports. If a currently authenticated worker is available, an authorized test may use it as a SOCKS upstream without restarting its session. Never describe a successful homepage response as proof of licensed full-text access.

For migrations, reuse node identity unless rotation is requested. Export both backend variants and Mihomo files from the same identity. Both variants represent the same endpoint; only one service can bind its port. Generate and validate before changing the live listener. Do not stop unrelated service units. Preserve the original configuration for rollback.

The repository intentionally uses official HTTPS downloads and pinned versions without additional SHA checks, approval gates or audit workflows. Keep this user preference unless explicitly changed. Configuration validation, supported-version checks and port-conflict errors are runtime requirements, not approval steps.

Update source fields from official schema documentation for the installed version. Keep version constants and migration notes aligned. More complex routing should be added only for a concrete requested policy; do not silently broaden the shipped scholarly domains to whole universities or shared hosting platforms.
