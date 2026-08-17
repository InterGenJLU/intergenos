# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""intergen.cloud — vendor-neutral raw-HTTP cloud provider adapters.

The shared substrate of the Sentinel + phone-a-friend mandate (design plan
section 1): one raw-urllib adapter layer used by BOTH phone-a-friend assistance
and the cloud scanner. No vendor SDKs, stdlib HTTP only — this satisfies the
NO-PYPI ban and vendor-neutrality in one move. Build a provider adapter with
``create_adapter(config)``.
"""
from intergen.cloud.factory import ADAPTERS, create_adapter
from intergen.cloud.http_adapter import (
    CloudAdapterError,
    HTTPCloudAdapter,
    lookup_secret,
)

__all__ = [
    "ADAPTERS",
    "CloudAdapterError",
    "HTTPCloudAdapter",
    "create_adapter",
    "lookup_secret",
]
