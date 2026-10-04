"""Synthetic transport checks for the optional guided-service handoff."""

from __future__ import annotations

import io

import httpx
import pytest

from seohead.bot.service_delivery import (
    AuthorizedHTTPUpload,
    CredentialReference,
    UploadEndpoint,
    UploadUnavailable,
)
from seohead.job_contracts import OpenedArtifact


def _opened(body: bytes = b"synthetic audit") -> OpenedArtifact:
    return OpenedArtifact(
        handle=io.BytesIO(body),
        size_bytes=len(body),
        filename="report.json",
        media_type="application/json",
    )


def _transport(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_configured_https_upload_sends_the_bounded_receipt_envelope(monkeypatch):
    monkeypatch.setenv("SEOHEAD_SYNTHETIC_UPLOAD_TOKEN", "synthetic-secret")
    requests = []
    upload = AuthorizedHTTPUpload(
        UploadEndpoint(
            "https://delivery.example.test/v1/artifacts",
            CredentialReference("env:SEOHEAD_SYNTHETIC_UPLOAD_TOKEN"),
        ),
        _transport(lambda request: requests.append(request) or httpx.Response(201)),
    )
    upload.send("requester-42", _opened(), "receipt-42")
    request = requests[0]
    assert request.url == "https://delivery.example.test/v1/artifacts"
    assert request.headers["Authorization"] == "Bearer synthetic-secret"
    assert request.headers["Idempotency-Key"] == "receipt-42"
    assert request.headers["X-SEOHEAD-Delivery"] == "retained-report/1"
    assert b"requester-42" in request.content
    assert b"synthetic audit" in request.content


@pytest.mark.parametrize(
    "url",
    [
        "http://delivery.example.test/v1/artifacts",
        "https://user:pass@delivery.example.test/v1/artifacts",
        "https://delivery.example.test/v1/artifacts#fragment",
    ],
)
def test_upload_endpoint_refuses_non_service_configuration(url):
    with pytest.raises(ValueError, match="absolute HTTPS"):
        UploadEndpoint(url, CredentialReference("env:SEOHEAD_SYNTHETIC_UPLOAD_TOKEN"))


@pytest.mark.parametrize("status", [202, 503])
def test_missing_credential_and_rejected_upload_are_honest(monkeypatch, status):
    endpoint = UploadEndpoint(
        "https://delivery.example.test/v1/artifacts",
        CredentialReference("env:SEOHEAD_SYNTHETIC_UPLOAD_TOKEN"),
    )
    upload = AuthorizedHTTPUpload(endpoint, _transport(lambda _request: httpx.Response(status)))
    monkeypatch.delenv("SEOHEAD_SYNTHETIC_UPLOAD_TOKEN", raising=False)
    with pytest.raises(UploadUnavailable, match="credential"):
        upload.send("requester-42", _opened(), "receipt-42")
    monkeypatch.setenv("SEOHEAD_SYNTHETIC_UPLOAD_TOKEN", "synthetic-secret")
    with pytest.raises(UploadUnavailable, match="rejected"):
        upload.send("requester-42", _opened(), "receipt-42")


def test_upload_refuses_an_invalid_destination_or_changed_artifact(monkeypatch):
    monkeypatch.setenv("SEOHEAD_SYNTHETIC_UPLOAD_TOKEN", "synthetic-secret")
    endpoint = UploadEndpoint(
        "https://delivery.example.test/v1/artifacts",
        CredentialReference("env:SEOHEAD_SYNTHETIC_UPLOAD_TOKEN"),
    )
    upload = AuthorizedHTTPUpload(endpoint, _transport(lambda _request: httpx.Response(200)))
    with pytest.raises(UploadUnavailable, match="destination"):
        upload.send("https://attacker.example/", _opened(), "receipt-42")
    changed = _opened(b"short")
    changed.size_bytes += 1
    with pytest.raises(UploadUnavailable, match="size changed"):
        upload.send("requester-42", changed, "receipt-42")
