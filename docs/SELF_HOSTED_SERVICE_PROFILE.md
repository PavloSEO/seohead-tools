# Self-hosted remote-service operator profile

This guide is for the optional `remote` ASGI adapter, not for the repository's
one-shot CLI container. The code exposes `create_app(backend, authenticator,
target_policy=...)`; it does not ship a listener, system-service unit, domain,
certificate, firewall rule, secret loader, worker daemon, or scheduled expiry
job. An operator supplies those pieces deliberately.

The guide uses reserved names and synthetic paths only. Do not treat it as
authorization to expose a listener, issue a certificate, create DNS records,
copy customer artifacts, or change a server.

## Keep the two modes separate

The current Docker image runs one explicit CLI command and exits. Its persistent
workspace recipe is in [CONTAINERS.md](CONTAINERS.md). It has no HTTP health
route and must not be placed behind a public proxy as though it were the remote
service.

The remote profile is a different, operator-owned deployment:

```text
Internet -> TLS reverse proxy :443 -> 127.0.0.1:<private-port> ASGI app
                                      -> private SQLite job root + job worker
```

Only the proxy is intended to bind a public interface. The ASGI listener binds
loopback (or an equally private Unix socket) and the worker has no public
listener. Keep the state root outside an application release directory.

## Preflight explicit service configuration

`create_app` requires all three current code contracts:

- `SQLiteJobBackend` (or another durable, project-isolated `JobBackend`);
- `TokenAuthenticator` with a project-scoped `Principal` grant map; and
- a `RemoteTargetPolicy` that authorizes each submitted target before queueing.

The repository intentionally has no environment-to-service factory. A local
operator bootstrap must construct these objects from reviewed trusted settings;
an absent backend, authenticator, or target policy refuses admission. Do not
invent environment variable names and assume the library reads them.

Store references to secret files outside the checkout, for example:

```text
/etc/seohead-remote/service.env       mode 0600, operator-owned
/var/lib/seohead-remote/              mode 0700, service-owned state root
/var/log/seohead-remote/              mode 0700, redacted service and incident logs
```

The bootstrap may read a bearer-token **digest** and host-bound `env:NAME`
credential references from that protected configuration. `TokenAuthenticator`
compares SHA-256 digests; `RemoteProjectLimits` resolves host-bound references
only when a worker dispatches a matching request. Never put token values,
credential-header values, private keys, target URLs, or raw artifact content in
a unit file, command line, run journal, or incident log.

Before enabling a listener, confirm that the private state root is not a
symlink and is not group/world-readable. `SQLiteJobBackend` enforces that
boundary for its own root and database; it does not secure an operator's proxy,
supervisor, backups, or log destination.

## Bind, domain, TLS and firewall boundary

Configure the ASGI process to listen only on `127.0.0.1:<private-port>` (or a
private Unix socket). Configure the TLS reverse proxy to forward to that same
private endpoint and to send original Host and forwarding headers according to
the proxy's documented safe configuration. The proxy, not the application,
owns the external domain and certificate.

The host firewall policy must permit the operator's SSH path and the proxy's
TLS port only. It must not permit the ASGI private port from external networks.
Test this policy on an operator-owned disposable host before using a real
domain. The loopback fixture in
[DISPOSABLE_SERVICE_PROFILE.md](DISPOSABLE_SERVICE_PROFILE.md) proves only the
private-upstream/TLS-proxy shape; it does not prove DNS, a public certificate,
or a particular firewall implementation.

Keep public API documentation routes disabled as the current app does. The
only current schema route is authenticated `/api/v1/openapi.json`; it is not a
public liveness probe.

## Supervision and health evidence

The service supervisor should restart a failed ASGI process according to the
operator's policy and record its revision, private bind, state-root path and
exit status. Process liveness alone is insufficient: the worker and durable
backend must also be checked.

| Signal | What it proves | What it does not prove |
| --- | --- | --- |
| Supervisor status | ASGI process stayed alive at its private bind | bearer authorization, target-policy admission, worker progress or artifact integrity |
| Authenticated `/api/v1/openapi.json` request through the proxy | TLS/proxy path and bearer authentication reached the app | a worker can claim/run a job |
| Bounded synthetic job against an operator-owned fixture | backend, target policy, worker, retained evidence and result path | customer-target permission or whole-site coverage |
| `disposable_service_profile.py` metrics | loopback TLS, 401/200 boundary, backup/restore, expiry and pointer rollback | an external deployment |

Do not add an unauthenticated `/healthz` route merely for a load balancer. The
current remote API has no such route. If a future service needs one, define its
authorization, output, rate and information-disclosure contract first.

## Incident record and recovery

Create one redacted incident record for an unavailable, failed, partial,
cancelled, expired-lease or rollback event. Include UTC start/end, service
revision, private ASGI/proxy bind, supervisor/health result, opaque
project/job/artifact IDs, fixed reason code, artifact SHA-256, recovery action,
result, next owner and review time.

Exclude bearer tokens, headers, request bodies, customer URLs, artifact bytes,
certificate keys and stack traces. The backend already keeps operational events
to fixed codes/numeric progress and retains artifacts separately; preserve that
boundary in proxy and supervisor logging.

For a failed worker lease, run the backend's explicit expired-lease recovery
path and inspect the retained private scan before admitting a replacement job.
It deliberately records a named failed outcome and does not replay an
interrupted crawl automatically.

For backup and restore, first quiesce the service or use an operator-owned
SQLite-consistent snapshot procedure that includes the database, its current
WAL state, and the service-owned project/job artifact directories. Restore into
a new private state root, validate permissions and opaque artifact digest
lookups, then perform a bounded synthetic job before changing a live pointer.
Do not restore by copying arbitrary paths supplied by an API caller.

Application rollback and job-data recovery are separate. Keep immutable
release directories and change an operator-owned active pointer only after the
new revision passes the health evidence above; return that pointer to the prior
recorded revision if it fails. Do not roll back SQLite job state merely because
application code is rolled back.

## Artifact expiry

`prune_terminal(project_id, before=...)` is preview-first. It lists eligible
terminal jobs without deleting them; only an explicit `confirm=True` tombstones
and removes project-owned directories/rows. There is no default periodic
deletion job. An operator scheduling expiry must retain the reviewed preview,
project scope, cutoff, revision, artifact count and backup status in the
incident record, then run the confirmed action from a private maintenance
context. A crash leaves tombstones for the next confirmed pass rather than
silently deleting unrelated paths.

## Synthetic verification boundary

Run the repository fixture and inspect its redacted metrics before claiming the
local boundary is intact:

```bash
python scripts/disposable_service_profile.py --metrics-out /tmp/service-profile.json
```

This is a synthetic loopback check. It does not deploy the remote API, create a
domain, load a production secret, alter a firewall, or contact a customer site.


### Interpreting CI evidence and remaining containment claims

The existing Linux lifecycle job measures local installation, HTTP/JS capture,
release switching, artifact readability and sampled process-tree RSS. Its name
is an SSH-operator workflow label, not evidence of a network SSH handshake.
The separate [actual SSH worker fixture](DISPOSABLE_SERVICE_PROFILE.md#actual-ssh-worker-and-bounded-enospc-fixture)
uses a loopback-only sshd and a genuine bounded tmpfs ENOSPC event. It must
finish successfully on a disposable runner before that boundary is accepted.

Do not combine logical quota, kernel file-size limit, and physical ENOSPC into
one claimed measurement. Preserve the exact failure source and terminal state.
The worker fixture keeps the queue database on another filesystem so it can
record failure; simultaneous exhaustion of queue and artifact storage remains
a distinct deployment failure mode. The fixture does not prove a public domain,
firewall, trusted external certificate, production supervision, fair scheduling,
or hard CPU/RAM limits. Those are operator configuration and workload-specific
acceptance, not functionality silently enabled by installing this package.
