# Runtime notes

The gateway is either sing-box or Xray. A separate optional sing-box process owns the OpenConnect endpoint and exposes loopback SOCKS5 and API. This deliberately preserves login sessions when restarting or replacing the gateway. Authentication after a system restart is still interactive.

CMU's Full VPN was tested with sing-box 1.14.2, AnyConnect SAML `acSamlv2Token`, and Duo. It assigned CMU DNS servers and a CMU public exit; Nature/Springer homepages returned 200. ACM/IEEE command-line probes received anti-bot responses. Other schools/protocols and the optional local browser helper's entire GUI path have not been proven by this test.

The VPN worker uses `system: false`, OpenConnect DNS, and default domain resolution through pushed VPN DNS. Gateway rules match domain names and sniffed HTTP/TLS/QUIC. If clients send only IPs with encrypted/absent server names, matching is incomplete. Prefer client-provided domain destinations; do not infer domains from shared CDN IPs. Rules work at hostname level, not encrypted URL path level.

`none`: direct default, no VPN worker required. `rules`: selected domains use VPN proxy, everything else direct. `all`: default VPN proxy. Private/local target addresses remain blocked in all modes. Control connections and REALITY handshake use the server's normal network. Without the optional host TUN, no proxy connection means no host-wide interception. Menu 9 adds a boolean Linux host capture switch with DNS restoration. Menu 8 owns the shared none/rules/all policy for both proxy and host traffic. The host forwards TCP/UDP through the same worker; disabling VPN pauses capture without resetting its saved switch.

Do not automatically fallback matched traffic to direct when VPN is down. External SOCKS must support the required TCP/UDP and DNS behavior; it may be backed by a namespace VPN, but configuring that VPN is outside this repository's automatic installer.

Operational diagnosis:

1. Run `status` and inspect the backend service status. `auth-pending` requires the user's MFA; it is not a route failure.
2. Check loopback ports and ensure only the chosen gateway binds its public port.
3. Compare direct and VPN exit IPs with `doctor`. Check pushed DNS and test a selected hostname.
4. Inspect gateway logs for the selected outbound without dumping headers/cookies. IP-only traffic, unlisted PDF domains or shared login redirects can explain incomplete routing.
5. Validate schema before restarting. Deploy preserves an unchanged worker and restarts it if provider configuration changes; re-authentication is then required. Active host routing is paused during deployment and resumed after authentication.

Changing provider does not imply permission to bypass its MFA, endpoint requirements, subscription limits or proxy-sharing restrictions. CMU's official help page discourages proxies/connection sharing with Secure Client; native OpenConnect compatibility is not proof of an officially supported deployment.

A running worker can retain `State: error` after a server session limit (`Max time exceeded`). The auth CLI only handles challenges; menu 3 now resets an owned failed worker once via `prepare_auth`, waits for its API, and starts authentication. Keep the host TUN running during this reset so VPN traffic cannot silently fall back direct; set its resume marker to refresh pushed DNS after successful authentication. Never reset connected or pending sessions just to retry login.
