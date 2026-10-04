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
