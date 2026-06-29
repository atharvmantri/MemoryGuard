# SPDX-License-Identifier: Apache-2.0
"""OSS sensitive-data detection (regex/rules).

Exposes :class:`BasicSensitiveDataDetector`, the local, deterministic OSS
default behind the :class:`~memoryguard_models.base.SensitiveDataDetector`
interface. It also implements the core ``IngestionInspector`` duck-type and runs
with no external LLM API.
"""

from memoryguard_models.sensitive_data.basic import (
    DEFAULT_MODEL_ID,
    DEFAULT_TASK,
    DEFAULT_VERSION,
    BasicSensitiveDataDetector,
)

__all__ = [
    "BasicSensitiveDataDetector",
    "DEFAULT_MODEL_ID",
    "DEFAULT_VERSION",
    "DEFAULT_TASK",
]
