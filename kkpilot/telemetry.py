"""Content-free, monotonic delivery timings; no SDK, logging or network I/O."""
from __future__ import annotations

import math
import time

STAGES = frozenset({
    "queue_receive_seconds", "claim_seconds", "source_read_seconds",
    "source_check_seconds", "extractor_setup_seconds", "extraction_seconds",
    "validation_seconds", "result_commit_seconds", "ack_seconds",
    "failure_handling_seconds",
})


def safe_workflow_timing(value: object) -> dict:
    """Accept only this version's explicit boundary and bounded numeric durations."""
    if (not isinstance(value, dict) or value.get("version") != "delivery-timing-1"
            or value.get("boundary") not in ("before_result_commit", "delivery_cycle")):
        return {}
    result = {"version": "delivery-timing-1", "boundary": value["boundary"]}
    for key in STAGES | {"total_seconds"}:
        number = value.get(key)
        if type(number) in (int, float) and math.isfinite(number) and 0 <= number <= 86400:
            result[key] = number
    return result


class DeliveryTiming:
    """A fresh instance per delivery; stages must not be added together with OCR subspans."""
    def __init__(self, clock=None):
        self.clock = clock or time.perf_counter
        self.started = self.clock()
        self.stages = {}

    def call(self, stage, function, *args, **kwargs):
        if stage not in STAGES:
            raise ValueError("Unknown timing stage")
        start = self.clock()
        try:
            return function(*args, **kwargs)
        finally:
            self.stages[stage] = self.stages.get(stage, 0.0) + max(0.0, self.clock() - start)

    def snapshot(self, boundary="delivery_cycle"):
        return safe_workflow_timing({
            "version": "delivery-timing-1", "boundary": boundary,
            "total_seconds": max(0.0, self.clock() - self.started),
            **self.stages,
        })


def summarize_deliveries(records):
    """Separate outcomes so empty polls and failed reads cannot improve success latency."""
    groups = {}
    for outcome, timing in records:
        safe = safe_workflow_timing(timing)
        if not safe:
            continue
        group = groups.setdefault(outcome, {})
        for stage in STAGES | {"total_seconds"}:
            if stage in safe:
                group.setdefault(stage, []).append(safe[stage])
    result = {}
    for outcome, stages in groups.items():
        result[outcome] = {}
        for stage, values in stages.items():
            values.sort()
            size = len(values)
            result[outcome][stage] = {
                "count": size, "min": values[0], "max": values[-1],
                "mean": sum(values) / size,
                "p50": values[math.ceil(size * .50) - 1],
                "p95": values[math.ceil(size * .95) - 1],
            }
    return {"version": "delivery-timing-1", "percentile_method": "nearest_rank",
            "boundary": "delivery_cycle", "by_outcome": result}
