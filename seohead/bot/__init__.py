"""Guided-bot conversation contract and scan configuration wizard.

This package is the bot-facing half of the guided-scan roadmap (epic #751).
It defines a versioned conversation state machine and a session driver that
walks a user from a site address to a confirmed scan job. Its optional Bot
API wire adapter is caller-driven and disabled by default: it has no polling,
listener, account provisioning, or crawl logic. Job submission goes through
the ``JobSubmitter`` protocol into the shared core.
"""

from seohead.bot.contract import (
    CONTRACT_VERSION,
    Action,
    Field,
    State,
    allowed_actions,
    describe_contract,
)
from seohead.bot.job_adapter import (
    AuthorizedJobSubmitter,
    JobOwnershipStore,
    ProjectAuthorizationStore,
)
from seohead.bot.report_delivery import (
    AuthorizedReportDelivery,
    DeliveryAmbiguous,
    DeliveryReceipts,
    DeliveryUnavailable,
    ReportPreview,
    ReportProfile,
)
from seohead.bot.service_delivery import (
    AuthorizedHTTPUpload,
    CredentialReference,
    UploadEndpoint,
    UploadUnavailable,
)
from seohead.bot.telegram_adapter import (
    TelegramAmbiguous,
    TelegramAuthorizedSessions,
    TelegramBotClient,
    TelegramBotConfig,
    TelegramChatAuthorizationStore,
    TelegramDocumentTransport,
    TelegramGuidedAdapter,
    TelegramSessionBindingStore,
    TelegramUnavailable,
    telegram_destination,
    telegram_subject,
)
from seohead.bot.wizard import (
    POLICY_PRESETS,
    Event,
    JobSubmitter,
    Reply,
    ScanJobSpec,
    WizardSession,
)

__all__ = [
    "CONTRACT_VERSION",
    "POLICY_PRESETS",
    "Action",
    "AuthorizedHTTPUpload",
    "AuthorizedJobSubmitter",
    "AuthorizedReportDelivery",
    "CredentialReference",
    "DeliveryAmbiguous",
    "DeliveryReceipts",
    "DeliveryUnavailable",
    "Event",
    "Field",
    "JobOwnershipStore",
    "JobSubmitter",
    "ProjectAuthorizationStore",
    "Reply",
    "ReportPreview",
    "ReportProfile",
    "ScanJobSpec",
    "State",
    "TelegramAmbiguous",
    "TelegramAuthorizedSessions",
    "TelegramBotClient",
    "TelegramBotConfig",
    "TelegramChatAuthorizationStore",
    "TelegramDocumentTransport",
    "TelegramGuidedAdapter",
    "TelegramSessionBindingStore",
    "TelegramUnavailable",
    "UploadEndpoint",
    "UploadUnavailable",
    "WizardSession",
    "allowed_actions",
    "describe_contract",
    "telegram_destination",
    "telegram_subject",
]
