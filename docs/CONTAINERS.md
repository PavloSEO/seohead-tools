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

## One-shot SSH job recipe

This recipe runs the **current CLI container**, not a remote API or worker. Replace
the host and paths with an operator-owned disposable machine; do not put credentials,
customer exports, or a public target in the command history.

```bash
ssh audit-host 'mkdir -p /srv/seohead/workspace/{exports,runs,logs}'
scp -r ./docs/examples/exports audit-host:/srv/seohead/workspace/exports/synthetic
ssh audit-host 'cd /srv/seohead && docker compose run --rm seohead \
  sf run --exports-dir /data/exports/synthetic --out /data/runs/synthetic-001 --tasks \
  > workspace/logs/synthetic-001.stdout 2> workspace/logs/synthetic-001.stderr'
```

The bind mount makes `/data/runs/synthetic-001` and its logs survive container
exit. Record the command, image digest or Git revision, UTC start/end time, exit
status, artifact hashes, and any explicit partial/unavailable state in the operator's
incident log. Never place request headers, credentials, unredacted URLs, or report
contents in that log.

The CLI image has no health endpoint: its health signal is the bounded command's exit
status plus the retained artifact it wrote. A future HTTP worker must define its own
authenticated health route, listener, reverse-proxy, firewall and certificate policy;
it cannot reuse this one-shot command as evidence that those controls are deployed.
