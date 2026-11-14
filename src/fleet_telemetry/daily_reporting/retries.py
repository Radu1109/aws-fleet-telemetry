"""Configurable retry policy for injected U1 and repository boundaries."""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

T = TypeVar("T")


class TransientReportError(RuntimeError):
    """A boundary failure explicitly classified as safe to retry."""


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int
    initial_delay_seconds: float
    max_delay_seconds: float
    jitter_fraction: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.max_attempts, int) or isinstance(self.max_attempts, bool):
            raise ValueError("max_attempts must be an integer")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if (
            not math.isfinite(self.initial_delay_seconds)
            or not math.isfinite(self.max_delay_seconds)
            or self.initial_delay_seconds < 0
            or self.max_delay_seconds < 0
        ):
            raise ValueError("retry delays must not be negative")
        if self.max_delay_seconds < self.initial_delay_seconds:
            raise ValueError("max_delay_seconds must be at least initial_delay_seconds")
        if not math.isfinite(self.jitter_fraction) or not 0 <= self.jitter_fraction <= 1:
            raise ValueError("jitter_fraction must be between zero and one")


def retry_call(
    operation: Callable[[], T],
    policy: RetryPolicy,
    *,
    retryable: Callable[[Exception], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    random_value: Callable[[], float] = random.random,
) -> T:
    """Call an operation with bounded exponential delay for classified failures."""
    is_retryable = retryable or (lambda error: isinstance(error, TransientReportError))
    for attempt in range(policy.max_attempts):
        try:
            return operation()
        except Exception as error:
            if attempt + 1 >= policy.max_attempts or not is_retryable(error):
                raise
            base_delay = min(
                policy.initial_delay_seconds * (2**attempt),
                policy.max_delay_seconds,
            )
            sample = random_value()
            if not math.isfinite(sample) or not 0 <= sample <= 1:
                raise ValueError("random_value must return a value between zero and one")
            factor = 1 + policy.jitter_fraction * (2 * sample - 1)
            sleep(max(0.0, base_delay * factor))
    raise RuntimeError("unreachable retry state")
