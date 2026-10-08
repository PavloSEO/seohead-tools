# Disposable service-profile proof

`scripts/disposable_service_profile.py` proves the operator-facing boundary in
one temporary, loopback-only fixture. It starts a private HTTP upstream and a
separate TLS-terminating reverse proxy, verifies that unauthenticated artifact
access receives `401` while an authorized request returns the exact retained
bytes, then exercises owned artifact backup/restore, explicit artifact expiry,
and atomic release-pointer upgrade and rollback.

Run it locally or on a disposable CI runner:

```bash
python scripts/disposable_service_profile.py --metrics-out /tmp/service-profile.json
```

The metrics contain bind addresses, status codes and completed checks; they do
not contain the synthetic credential or artifact path. CI runs the same fixture
on Ubuntu 24.04 and retains its JSON evidence briefly.

This is intentionally a loopback TLS fixture with a one-day self-signed
certificate for `localhost` and `127.0.0.1`. It proves the repository's safe
bind, reverse-proxy, authorization, volume and rollback mechanics without
claiming a public DNS route, firewall configuration, external certificate, or
production deployment. Those remain explicit operator actions.

## Synthetic recovery checklist

The fixture is the repeatable recovery procedure for this repository boundary:

1. The private upstream serves `/healthz`; the TLS proxy is the only fixture
   endpoint observed by the client.
2. An unauthenticated artifact request must return `401`; an authorized request
   must return the retained bytes unchanged.
3. Copy the owned artifact volume, mutate the original, then restore the copy and
   compare the recovered bytes.
4. Delete only the owned fixture artifact to exercise expiry; it never scans a
   host directory or a customer path.
5. Switch the atomic `current` pointer to a new release, read its revision, then
   switch back and read the prior revision.

The emitted JSON names each completed check. An operator investigating a failed
future service run should retain that JSON alongside a redacted incident record:
UTC time, service/revision identity, private/proxy bind addresses, health status,
artifact digest, recovery action, result, and follow-up owner. Do not include
tokens, headers, customer URLs, artifact contents, or certificate private keys.

CI's completed Docker and disposable-profile jobs are build evidence for the
repository image and fixture only. They do not prove that a local Docker daemon,
an external DNS name, a firewall rule, or a production certificate is present.


## Actual SSH worker and bounded ENOSPC fixture

`scripts/disposable_worker_recovery.py` is a separate Linux-only acceptance
fixture. Run it only on a reviewed disposable runner as its ordinary non-root
user. It needs the existing Python environment with `remote`, `reports`, and
`render` extras, the runner's sandboxed Chrome via `SEOHEAD_CHROME`, OpenSSH
client/server, `openssl`, `findmnt`, GNU `timeout`, and noninteractive sudo for
an owned sshd process and a bounded tmpfs mount. The runner must already have
OpenSSH's `/run/sshd` privilege-separation directory. The helper installs
nothing and refuses execution on a personal Mac or Windows machine.

```bash
python scripts/disposable_worker_recovery.py \
  --metrics-out "$RUNNER_TEMP/disposable-worker-recovery.json" \
  --evidence-out "$RUNNER_TEMP/disposable-worker-evidence"
```

The evidence destination must not exist before the run. The fixture:

1. Generates temporary host/client SSH keys and a dedicated sshd configuration.
   SSH binds only `127.0.0.1`; authentication is public-key-only with a pinned
   host key, no forwarding, no user rc, and a forced, wall-clock-bounded worker
   command. It never reads personal SSH configuration or changes accounts.
2. Starts the existing authenticated ASGI app behind the existing TLS fixture.
   API and TLS bind loopback. A reserved synthetic target hostname maps only
   inside the fixture to its owned RFC1918 HTTP listener; real network policy,
   sockets and collector code remain active.
3. Submits a baseline scan through HTTPS, reads its authenticated terminal
   status, executes the actual queue worker over SSH, and records hashes of
   authenticated retained artifact downloads. It also cancels a queued job
   through the API and proves that a worker cannot dispatch it.
4. Queues a second job, mounts an **8 MiB tmpfs only on that job's directory**,
   and fills it until the operating system reports `ENOSPC`. The queue database
   and baseline evidence remain outside the full filesystem. A thin observer
   delegates to the real native handler unchanged and records exception codes.
   The gate requires an actual worker `ENOSPC` or `SQLITE_FULL`; a permission
   error, size limit, reserve refusal, or unrelated worker failure cannot pass.
5. Requires a failed/partial result visible through the API, unchanged baseline
   hashes, no successful failed-job result after remount recovery, no remaining
   lease recovery, and no automatic replay. A fresh SSH worker then runs a new
   JS job successfully and publishes its terminal status.
   The two known JavaScript pages use the existing full-render policy, so the
   worker renders each page directly instead of probing and then fetching it
   again. The retained configuration must still show a 60-second render/crawl
   budget and 30-request ceiling, and both pages must have actual rendered
   evidence. A partial outcome cannot pass this positive recovery gate.
6. Preserves a SQLite-consistent queue snapshot, synthetic job files, and
   redacted error codes outside the tmpfs before cleanup. SSH/TLS keys, bearer
   credentials and filler bytes are excluded from the evidence artifact.
7. Copies the quiescent synthetic queue with SQLite's backup API and its complete
   artifact directories, restores into a new private root, and exercises the
   actual ASGI app in-process. Job status, coverage, opaque references and
   authenticated download hashes must match the original; anonymous access
   and an ungranted project are rejected. Replaying each original submission
   must return its existing job. The separate portable regression also proves
   that a queued job survives restoration and runs only once. This proof uses
   the real queue and saved artifacts, unlike the static TLS fixture above;
   it starts no additional listener and does not restore a production service.

The JSON has `ok: true` only after all checks pass. Until a reviewed CI run
produces that evidence, the script and its portable guard tests are a prepared
fixture, not proof of SSH or worker ENOSPC acceptance. A failed run retains its
phase and available synthetic state. Mounts and owned listeners are cleaned up;
no system sshd configuration, public endpoint or production service is changed.

These boundaries are separate from application quotas: this tmpfs enforces a
small job-directory storage boundary, while the queue's project budget remains
an application-level check. GNU timeout bounds this fixture's worker lifetime;
it is not CPU/RAM containment or fair multi-user scheduling.
