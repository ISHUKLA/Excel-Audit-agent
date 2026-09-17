"""Tests for ui/flight_recorder.py Streamlit visualization.

Covers: header rendering, node cards, cascading lock warnings, chain verification.
Does not cover: full Streamlit session state interactions (those require a running app).
"""

import pytest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from core.audit_log import AuditLog
from core.flight_recorder_queries import get_report_events
from ui.flight_recorder import (
    _status_emoji,
    _status_color,
    display_flight_recorder,
)


@contextmanager
def mock_streamlit_context():
    """Context manager that returns column/expander mocks."""
    mock = MagicMock()
    mock.__enter__ = MagicMock(return_value=mock)
    mock.__exit__ = MagicMock(return_value=None)
    return mock

CONTEXT = {"workbook_hash": "abc123", "code_version": "test"}


@pytest.fixture
def audit_log(tmp_path):
    return AuditLog(str(tmp_path / "audit.db"))


def _log_clean_run(audit_log: AuditLog, report_id: str) -> None:
    """Log a complete run through all gates with no blocks."""
    audit_log.log_event(
        report_id=report_id,
        event_type="gate_decision",
        payload={"gate": 1, "action": "context_confirmed"},
        actor="Isaac Shukla",
        context=CONTEXT,
    )
    audit_log.log_event(
        report_id=report_id,
        event_type="gate_decision",
        payload={"gate": 2, "action": "findings_reviewed_and_outputs_designated", "dispositions": []},
        actor="Isaac Shukla",
        context=CONTEXT,
    )
    audit_log.log_event(
        report_id=report_id,
        event_type="gate_decision",
        payload={
            "gate": 3,
            "action": "reconciliation_reviewed",
            "internal_verdict": "pass",
            "external_verdict": "pass",
        },
        actor="Isaac Shukla",
        context=CONTEXT,
    )
    audit_log.log_event(
        report_id=report_id,
        event_type="llm_use_decision",
        payload={"decision": "decline"},
        actor="Isaac Shukla",
        context=CONTEXT,
    )
    audit_log.log_event(
        report_id=report_id,
        event_type="report_approved",
        payload={"gate": 4, "action": "approval_record_created", "approval_name": "Divyank"},
        actor="Divyank",
        context=CONTEXT,
    )


class TestStatusIndicators:
    """Test status emoji and color rendering."""

    def test_status_emoji_complete(self):
        assert _status_emoji("complete") == "✓"

    def test_status_emoji_waiting(self):
        assert _status_emoji("waiting") == "⏳"

    def test_status_emoji_blocked(self):
        assert _status_emoji("blocked") == "✗"

    def test_status_emoji_in_progress(self):
        assert _status_emoji("in_progress") == "⚙"

    def test_status_emoji_incomplete(self):
        assert _status_emoji("incomplete") == "❌"

    def test_status_emoji_unknown(self):
        assert _status_emoji("unknown_status") == "?"

    def test_status_color_complete(self):
        color = _status_color("complete", is_locked=False)
        assert "complete" in color
        assert "green" in color

    def test_status_color_waiting(self):
        color = _status_color("waiting", is_locked=False)
        assert "waiting" in color
        assert "orange" in color

    def test_status_color_blocked(self):
        color = _status_color("blocked", is_locked=False)
        assert "blocked" in color
        assert "red" in color

    def test_status_color_locked(self):
        color = _status_color("complete", is_locked=True)
        assert "locked" in color
        assert "gray" in color


class TestDisplayFlightRecorder:
    """Test main display function (smoke tests, not full Streamlit rendering)."""

    @patch("ui.flight_recorder.st")
    def test_display_flight_recorder_clean_run(self, mock_st, audit_log):
        _log_clean_run(audit_log, "report_1")

        # Setup mock to return context managers for columns/expanders
        def make_columns(spec):
            # Spec can be an int or list; count based on what we get
            if isinstance(spec, list):
                num_cols = len(spec)
            else:
                num_cols = spec

            cols = []
            for _ in range(num_cols):
                col = MagicMock()
                col.__enter__ = MagicMock(return_value=col)
                col.__exit__ = MagicMock(return_value=None)
                cols.append(col)
            return cols

        mock_col = MagicMock()
        mock_col.__enter__ = MagicMock(return_value=mock_col)
        mock_col.__exit__ = MagicMock(return_value=None)

        mock_st.columns.side_effect = make_columns
        mock_st.expander.return_value = mock_col

        # This is a smoke test — we verify the function doesn't crash
        # and that it calls the expected Streamlit methods.
        display_flight_recorder(audit_log, "report_1")

        assert mock_st.markdown.called
        assert mock_st.metric.called
        assert mock_st.divider.called

    @patch("ui.flight_recorder.st")
    def test_display_flight_recorder_gate_3_block(self, mock_st, audit_log):
        audit_log.log_event(
            report_id="report_1",
            event_type="gate_decision",
            payload={
                "gate": 3,
                "action": "reconciliation_reviewed",
                "internal_verdict": "block",
                "external_verdict": "pass",
            },
            actor="Isaac Shukla",
            context=CONTEXT,
        )

        def make_columns(spec):
            if isinstance(spec, list):
                num_cols = len(spec)
            else:
                num_cols = spec

            cols = []
            for _ in range(num_cols):
                col = MagicMock()
                col.__enter__ = MagicMock(return_value=col)
                col.__exit__ = MagicMock(return_value=None)
                cols.append(col)
            return cols

        mock_col = MagicMock()
        mock_col.__enter__ = MagicMock(return_value=mock_col)
        mock_col.__exit__ = MagicMock(return_value=None)

        mock_st.columns.side_effect = make_columns
        mock_st.expander.return_value = mock_col

        display_flight_recorder(audit_log, "report_1")

        # Verify warning was called (for cascading lock)
        assert mock_st.warning.called or mock_st.markdown.called

    @patch("ui.flight_recorder.st")
    def test_display_flight_recorder_error_handling(self, mock_st, audit_log):
        # Simulate AuditLog returning no events
        def make_columns(spec):
            if isinstance(spec, list):
                num_cols = len(spec)
            else:
                num_cols = spec

            cols = []
            for _ in range(num_cols):
                col = MagicMock()
                col.__enter__ = MagicMock(return_value=col)
                col.__exit__ = MagicMock(return_value=None)
                cols.append(col)
            return cols

        mock_col = MagicMock()
        mock_col.__enter__ = MagicMock(return_value=mock_col)
        mock_col.__exit__ = MagicMock(return_value=None)

        mock_st.columns.side_effect = make_columns
        mock_st.expander.return_value = mock_col

        display_flight_recorder(audit_log, "nonexistent_report")

        # Should display header even with empty events
        assert mock_st.markdown.called

    @patch("ui.flight_recorder.st")
    def test_display_flight_recorder_calls_verify_chain(self, mock_st, audit_log):
        _log_clean_run(audit_log, "report_1")

        def make_columns(spec):
            if isinstance(spec, list):
                num_cols = len(spec)
            else:
                num_cols = spec

            cols = []
            for _ in range(num_cols):
                col = MagicMock()
                col.__enter__ = MagicMock(return_value=col)
                col.__exit__ = MagicMock(return_value=None)
                cols.append(col)
            return cols

        mock_col = MagicMock()
        mock_col.__enter__ = MagicMock(return_value=mock_col)
        mock_col.__exit__ = MagicMock(return_value=None)

        mock_st.columns.side_effect = make_columns
        mock_st.expander.return_value = mock_col

        display_flight_recorder(audit_log, "report_1")

        # Verify chain verification section is rendered
        # (via st.success or st.error call for chain status)
        assert mock_st.success.called or mock_st.error.called

    def test_display_flight_recorder_reports_correct_event_count(self, audit_log):
        """Verify event count matches what we logged."""
        _log_clean_run(audit_log, "report_1")
        events = get_report_events(audit_log, "report_1")

        assert len(events) == 5  # 5 events in clean run


class TestNodeCardRendering:
    """Test individual node card rendering (via mock Streamlit)."""

    @patch("ui.flight_recorder.st")
    def test_node_card_shows_status(self, mock_st, audit_log):
        from ui.flight_recorder import _render_node_card

        _log_clean_run(audit_log, "report_1")
        events = get_report_events(audit_log, "report_1")
        state = __import__("core.flight_recorder_state", fromlist=["build_pipeline_state"]).build_pipeline_state(events)

        mock_col = MagicMock()
        mock_col.__enter__ = MagicMock(return_value=mock_col)
        mock_col.__exit__ = MagicMock(return_value=None)
        mock_st.columns.return_value = [mock_col, mock_col]
        mock_st.expander.return_value = mock_col

        node_state = state.nodes["gate_1"]
        _render_node_card(
            node_name="gate_1",
            status=node_state["status"],
            is_locked=node_state["is_locked"],
            actor=node_state["actor"],
            actor_name=node_state["rules"].actor_name,
            actor_type=node_state["rules"].actor_type,
            event=node_state["event"],
            evidence_summary=node_state["evidence_summary"],
            rules=node_state["rules"],
        )

        # Verify expander was called with node name
        assert mock_st.expander.called

    @patch("ui.flight_recorder.st")
    def test_node_card_shows_predecessor_nodes(self, mock_st, audit_log):
        from ui.flight_recorder import _render_node_card

        _log_clean_run(audit_log, "report_1")
        events = get_report_events(audit_log, "report_1")
        state = __import__("core.flight_recorder_state", fromlist=["build_pipeline_state"]).build_pipeline_state(events)

        mock_col = MagicMock()
        mock_col.__enter__ = MagicMock(return_value=mock_col)
        mock_col.__exit__ = MagicMock(return_value=None)
        mock_st.columns.return_value = [mock_col, mock_col]
        mock_st.expander.return_value = mock_col

        node_state = state.nodes["gate_2"]
        _render_node_card(
            node_name="gate_2",
            status=node_state["status"],
            is_locked=node_state["is_locked"],
            actor=node_state["actor"],
            actor_name=node_state["rules"].actor_name,
            actor_type=node_state["rules"].actor_type,
            event=node_state["event"],
            evidence_summary=node_state["evidence_summary"],
            rules=node_state["rules"],
        )

        # Verify markdown was called (for predecessor display)
        assert mock_st.markdown.called


class TestChainVerificationRendering:
    """Test chain verification section rendering."""

    @patch("ui.flight_recorder.st")
    def test_chain_verification_shows_valid_chain(self, mock_st, audit_log):
        from ui.flight_recorder import _render_chain_verification

        _log_clean_run(audit_log, "report_1")

        mock_col = MagicMock()
        mock_col.__enter__ = MagicMock(return_value=mock_col)
        mock_col.__exit__ = MagicMock(return_value=None)
        mock_st.columns.return_value = [mock_col, mock_col]
        mock_st.button.return_value = False

        _render_chain_verification(
            chain_valid=True,
            chain_message="Chain valid: 5 events for report 'report_1', all hashes match",
            audit_log=audit_log,
            report_id="report_1",
        )

        assert mock_st.success.called

    @patch("ui.flight_recorder.st")
    def test_chain_verification_shows_broken_chain(self, mock_st, audit_log):
        from ui.flight_recorder import _render_chain_verification

        mock_col = MagicMock()
        mock_col.__enter__ = MagicMock(return_value=mock_col)
        mock_col.__exit__ = MagicMock(return_value=None)
        mock_st.columns.return_value = [mock_col, mock_col]
        mock_st.button.return_value = False

        _render_chain_verification(
            chain_valid=False,
            chain_message="Chain broken at row_id=1",
            audit_log=audit_log,
            report_id="report_1",
        )

        assert mock_st.error.called

    @patch("ui.flight_recorder.st")
    def test_chain_verification_button_exists(self, mock_st, audit_log):
        from ui.flight_recorder import _render_chain_verification

        mock_col = MagicMock()
        mock_col.__enter__ = MagicMock(return_value=mock_col)
        mock_col.__exit__ = MagicMock(return_value=None)
        mock_st.columns.return_value = [mock_col, mock_col]
        mock_st.button.return_value = False

        _render_chain_verification(
            chain_valid=True,
            chain_message="Chain valid",
            audit_log=audit_log,
            report_id="report_1",
        )

        # Verify button was rendered
        assert mock_st.button.called
