# Container boundaries

The repository's Docker image is a one-shot `seohead` CLI container. Its
entrypoint is `seohead`, its default command is `--help`, and the included
Compose file mounts `./workspace` at `/data` for an explicit command such as
`docker compose run --rm seohead sf run ...`. It does not start HTTP, create an
account, accept web submissions, run a worker, or expose MCP over the network.

The optional remote API is a separate Python contract. An operator must create
an authenticated ASGI app with a durable backend, decide its bind/proxy/TLS
policy, and start it with their own process manager. The application defaults
to no listener. A synthetic container smoke can therefore verify the CLI image
without implying a deployed service:

```bash
docker build -t seohead-tools:local .
docker run --rm seohead-tools:local --help
```

Keep scan artifacts on an explicit service-owned volume when operating the
queue. Exposure behind a domain, firewall rules, TLS certificates, backup and
restore are operator actions and require their own disposable-environment
evidence; this repository does not provide a production deployment command.
The repository's [disposable service profile](DISPOSABLE_SERVICE_PROFILE.md)
exercises a loopback TLS reverse proxy, authorization, retained-artifact
backup/restore, expiry, and release rollback without claiming a public deploy.
