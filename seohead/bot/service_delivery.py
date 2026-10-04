"""A configured HTTP handoff adapter for the guided-service boundary.

The module intentionally has no bot SDK or listener.  A service operator
constructs it from an approved HTTPS endpoint and an ``env:`` credential
reference, then passes :meth:`AuthorizedHTTPUpload.send` to
``AuthorizedReportDelivery``.  The report core remains responsible for
authorization, receipt claims, retained-artifact checks, and size limits.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import BinaryIO
from urllib.parse import urlsplit

import httpx

from seohead.job_contracts import OpenedArtifact

_ENV_REFERENCE = re.compile(r"env:[A-Z_][A-Z0-9_]{0,127}\Z")
_DESTINATION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SUCCESS = frozenset({200, 201, 202, 204})


class UploadUnavailable(RuntimeError):
    """A configured delivery endpoint did not accept an artifact envelope."""


@dataclass(frozen=True)
class CredentialReference:
    """A secret name, never its value, retained in adapter configuration."""

    value: str

    def __post_init__(self) -> None:
        if not _ENV_REFERENCE.fullmatch(self.value):
            raise ValueError("credential reference must be an env:NAME value")

    def resolve(self) -> str:
        name = self.value[4:]
        value = os.environ.get(name)
        if not value:
            raise UploadUnavailable(f"configured credential {self.value} is unavailable")
        return value


@dataclass(frozen=True)
class UploadEndpoint:
    """A single service-owned HTTPS endpoint; recipients never supply a URL."""

    url: str
    credential: CredentialReference

    def __post_init__(self) -> None:
        parsed = urlsplit(self.url)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError("upload endpoint must be an absolute HTTPS URL without credentials")


@dataclass
class AuthorizedHTTPUpload:
    """Send one already-authorized file through one trusted upload endpoint.

    ``destination`` is an opaque adapter-side recipient identifier.  It is
    never converted into a URL, so callers cannot turn a report handoff into
    an arbitrary HTTP request.  ``receipt`` is propagated as the downstream
    idempotency key and the transport accepts no success status outside the
    explicit set above.
    """

    endpoint: UploadEndpoint
    client: httpx.Client
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if type(self.timeout_seconds) not in (int, float) or not 0 < self.timeout_seconds <= 300:
            raise ValueError("timeout_seconds must be within 0..300")

    @staticmethod
    def _destination(value: str) -> str:
        if not _DESTINATION.fullmatch(value):
            raise UploadUnavailable("delivery destination is not a valid configured identifier")
        return value

    @staticmethod
    def _remaining(handle: BinaryIO, expected: int) -> None:
        """Reject a changed file before it crosses the service boundary."""
        try:
            position = handle.tell()
            handle.seek(0, os.SEEK_END)
            size = handle.tell() - position
            handle.seek(position)
        except (AttributeError, OSError):
            return
        if size != expected:
            raise UploadUnavailable("opened artifact size changed before upload")

    def send(self, destination: str, opened: OpenedArtifact, receipt: str) -> None:
        """Post a bounded multipart envelope; failures deliberately raise."""
        destination = self._destination(destination)
        if not receipt or len(receipt) > 128:
            raise UploadUnavailable("delivery receipt is invalid")
        self._remaining(opened.handle, opened.size_bytes)
        token = self.endpoint.credential.resolve()
        try:
            response = self.client.post(
                self.endpoint.url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Idempotency-Key": receipt,
                    "X-SEOHEAD-Delivery": "retained-report/1",
                },
                data={
                    "destination": destination,
                    "artifact_name": opened.filename,
                    "media_type": opened.media_type,
                    "size_bytes": str(opened.size_bytes),
                },
                files={"artifact": (opened.filename, opened.handle, opened.media_type)},
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise UploadUnavailable("configured upload endpoint is unavailable") from exc
        if response.status_code not in _SUCCESS:
            raise UploadUnavailable(
                f"configured upload endpoint rejected the artifact ({response.status_code})"
            )
