"""P8-F2 / P6-D10 — Celery task logs must carry structured fields.

The operator needs to filter task logs by ``task_id`` to trace a
specific run end-to-end. The contract enforced here:

1. ``_log_task_event`` accepts ``task_id`` as a structured field
   (not just substituted into a free-form message).
2. Required fields (``task_id``, ``task_name``) appear in the rendered
   log output.
3. Optional outcome fields (``user_id``, ``duration_ms``, ``outcome``)
   appear when supplied.

The function is tested directly rather than via Celery so the test
does not need a running broker. The test wires a custom log handler
to capture the JSON payload structlog renders.
"""
from __future__ import annotations

import json
import logging

import pytest
import structlog

from app.tasks import _log_task_event


pytestmark = [pytest.mark.unit]


class _ListHandler(logging.Handler):
    """Capture every log record emitted while the handler is attached."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def capture_task_logs():
    """Attach a list-handler to the ``celery.task`` logger for one test.

    structlog renders the JSON payload as ``record.msg`` once the
    standard logging pipeline picks the record up. We assert against
    that payload directly.
    """
    target = logging.getLogger("celery.task")
    handler = _ListHandler()
    handler.setLevel(logging.DEBUG)
    target.addHandler(handler)
    target.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        target.removeHandler(handler)


def _payload_strings(handler: _ListHandler) -> list[str]:
    """Collapse every captured record to its rendered message string."""
    return [r.getMessage() for r in handler.records]


def test_log_task_event_includes_task_id(capture_task_logs):
    """The required ``task_id`` field must appear in the output."""
    _log_task_event(
        "task_start",
        task_id="celery-abc-123",
        task_name="app.tasks.consolidate_user_memory_task",
        user_id="user-xyz",
    )
    rendered = _payload_strings(capture_task_logs)
    assert rendered, "no log record emitted"
    blob = "\n".join(rendered)
    assert "task_id" in blob, (
        "task_id field missing from rendered log payload; got: "
        + blob
    )
    assert "celery-abc-123" in blob


def test_log_task_event_includes_task_name_and_user_id(capture_task_logs):
    """Task identity + user scope are present on every emit."""
    _log_task_event(
        "task_end",
        task_id="celery-abc-123",
        task_name="app.tasks.consolidate_user_memory_task",
        user_id="user-xyz",
        duration_ms=42,
        outcome="success",
        consolidated=7,
    )
    blob = "\n".join(_payload_strings(capture_task_logs))
    for needle in (
        "task_name",
        "consolidate_user_memory_task",
        "user_id",
        "user-xyz",
        "duration_ms",
        "42",
        "outcome",
        "success",
        "consolidated",
    ):
        assert needle in blob, f"expected {needle!r} in rendered log; got: {blob!r}"


def test_log_task_event_omits_optional_fields_when_none(capture_task_logs):
    """Optional fields that are not set should not pollute the payload.

    For example, ``app_id`` is not always available (JWT-issued tasks
    leave it None); the helper must skip it rather than emit
    ``app_id=null`` to keep the structured logs lean.
    """
    _log_task_event(
        "task_start",
        task_id="celery-abc-123",
        task_name="app.tasks.consolidate_all_users",
    )
    blob = "\n".join(_payload_strings(capture_task_logs))
    # Required fields present.
    assert "task_id" in blob
    assert "task_name" in blob
    # Optional fields NOT mentioned.
    assert "app_id" not in blob
    assert "duration_ms" not in blob
    assert "outcome" not in blob


def test_log_task_event_uses_structlog_logger_name(capture_task_logs):
    """The helper must emit on the ``celery.task`` logger, not root."""
    _log_task_event(
        "probe",
        task_id="probe-1",
        task_name="probe",
    )
    assert capture_task_logs.records, "no log record captured"
    record = capture_task_logs.records[-1]
    assert record.name == "celery.task", (
        f"task event emitted on wrong logger: {record.name!r}; "
        "operators filter on 'celery.task' to see only background-task logs"
    )
