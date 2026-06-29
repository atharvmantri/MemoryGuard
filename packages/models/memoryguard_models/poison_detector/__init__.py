# SPDX-License-Identifier: Apache-2.0
"""Poison/injection detector exports for the OSS public alpha."""

from memoryguard_models.poison_detector.basic import (
    DEFAULT_MODEL_ID,
    DEFAULT_VERSION,
    BasicPoisonDetector,
)

__all__ = ["BasicPoisonDetector", "DEFAULT_MODEL_ID", "DEFAULT_VERSION"]
