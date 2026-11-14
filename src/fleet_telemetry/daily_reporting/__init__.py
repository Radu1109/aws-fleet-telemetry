"""Daily report calculation and provider library."""

from .calculations import calculate_daily_summary, haversine_distance_meters
from .generation import DailySummaryGenerator, GenerationResult
from .history import HistoryContractError, HistoryReadFailure, U1HistoryAdapter
from .job import DailyReportCoordinator, JobRunResult, JobTelemetry
from .models import (
    DailyVehicleSummary,
    HistoryCompleteness,
    MetricCompleteness,
    MetricStatus,
)
from .provider import InternalReportProvider, ProviderOutcome, ReportProviderResult
from .repository import InMemorySummaryRepository, SummaryRepositoryPort
from .retries import RetryPolicy, TransientReportError
from .serialization import serialize_summary_csv, serialize_summary_json

LIBRARY_NAME = "fleet-telemetry-daily-reporting"

__all__ = [
    "DailyReportCoordinator",
    "DailySummaryGenerator",
    "DailyVehicleSummary",
    "GenerationResult",
    "HistoryContractError",
    "HistoryCompleteness",
    "HistoryReadFailure",
    "InMemorySummaryRepository",
    "InternalReportProvider",
    "JobRunResult",
    "JobTelemetry",
    "MetricCompleteness",
    "MetricStatus",
    "ProviderOutcome",
    "ReportProviderResult",
    "RetryPolicy",
    "SummaryRepositoryPort",
    "TransientReportError",
    "U1HistoryAdapter",
    "calculate_daily_summary",
    "haversine_distance_meters",
    "serialize_summary_csv",
    "serialize_summary_json",
]
