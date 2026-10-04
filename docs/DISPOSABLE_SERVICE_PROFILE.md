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
