"""Compatibility imports for the remote API's shared job contracts.

The contracts are core data types: local guided adapters and the remote API
both depend on them, so they live in :mod:`seohead.job_contracts` rather than
making core modules reach into an interface package.
"""

from seohead.job_contracts import *  # noqa: F403
