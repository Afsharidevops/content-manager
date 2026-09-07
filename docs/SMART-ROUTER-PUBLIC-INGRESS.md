# Smart Router public ingress — standalone TLS or external reverse proxy

Branch: `main`  
Backend: **9router**

Internal applications use `http://smart-router:8080/v1`. Public exposure is optional. A public client normally uses `https://api.example.com/v1`.

## Mode A — stack Caddy owns TLS

DNS points to the stack VM. Copy `examples/caddy/Caddyfile.smart-router-standalone-tls.example` to `data/caddy/Caddyfile`, edit the domain, and enable the normal `caddy` profile. Caddy owns ports 80/443 and certificate renewal. The example intentionally blocks public `/metrics`; scrape metrics over a private network instead.

## Mode B — another VM owns TLS

```text
Internet -> HTTPS -> edge reverse proxy (Caddy/Nginx/Traefik/other)
                         |
                         | private HTTP
                         v
                    stack VM :8088
                         |
                   HTTP-only Caddy
                         |
                  Smart Router :8080
```

DNS points to the edge VM. On the stack VM use `examples/caddy/Caddyfile.smart-router-external-proxy-http.example` plus `examples/caddy/docker-compose.external-proxy.yml`. Bind the origin listener to the stack VM's private IP and firewall it so only the edge proxy can reach it. Plain HTTP is appropriate only over a trusted private/VPN network; otherwise use TLS or WireGuard/Tailscale between VMs.

Example startup:

```bash
CADDY_HTTP_BIND_IP=10.20.0.20 CADDY_HTTP_PORT=8088 \
  docker compose -f docker-compose.yml \
  -f examples/caddy/docker-compose.external-proxy.yml \
  --profile caddy-http up -d caddy-http
```

### Edge proxy examples

- Caddy: `examples/caddy/Caddyfile.edge-reverse-proxy.example`
- Nginx: `examples/reverse-proxy/nginx-smart-router.conf.example`
- Traefik dynamic config: `examples/reverse-proxy/traefik-smart-router.yml.example`

Any other reverse proxy is fine if it terminates TLS, forwards the original `Host`, sets standard forwarded headers, supports streaming without response buffering, and forwards to the private stack origin.

## Security

Do not expose Smart Router `:8080` directly to the Internet. Keep `nine-router:20128` and other administration interfaces private/VPN-only unless you intentionally add separate access control. `/metrics` should remain private.

## Tests

From the edge VM:

```bash
curl -i http://10.20.0.20:8088/health -H 'Host: api.example.com'
```

Externally:

```bash
curl -i https://api.example.com/health
curl https://api.example.com/v1/models -H 'Authorization: Bearer YOUR_CLIENT_KEY'
```
