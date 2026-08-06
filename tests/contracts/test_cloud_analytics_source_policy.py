from app.services.cloud_analytics_source_policy import retrieve_cloud_analytics


class Database:
    def __init__(self, result): self.result, self.calls = result, []
    def read(self, tenant_id, query_kind, parameters):
        self.calls.append((tenant_id, query_kind, parameters)); return self.result


class Cad:
    def __init__(self, result): self.result, self.calls = result, []
    def read_current(self, tenant_id, operation, parameters, *, timeout_seconds):
        self.calls.append((tenant_id, operation, parameters, timeout_seconds)); return self.result


def snapshot(rows=(), freshness="current"):
    return {"tenant_id": "logan-synthetic", "rows": list(rows), "freshness": freshness, "observed_at": "2026-08-06T12:00:00Z"}


def test_database_is_primary_when_current():
    db, cad = Database(snapshot([{"count": 4}])), Cad(snapshot([{"count": 9}]))
    result = retrieve_cloud_analytics(tenant_id="logan-synthetic", query_kind="current_calls", parameters={}, database=db, cad=cad, cad_operation="search_calls", current_answer_required=True)
    assert result.source == "cloud_database" and not result.fallback_used
    assert not cad.calls


def test_cad_fallback_only_for_required_current_answer_and_minimizes_fields():
    db = Database(snapshot([], "stale"))
    cad = Cad(snapshot([{"cfs_number": "safe", "status": "Open", "narrative": "drop"}]))
    result = retrieve_cloud_analytics(tenant_id="logan-synthetic", query_kind="current_calls", parameters={"active": True}, database=db, cad=cad, cad_operation="search_calls", current_answer_required=True)
    assert result.source == "read_only_cad" and result.fallback_used
    assert result.data == ({"cfs_number": "safe", "status": "Open"},)
    assert result.observed_at and result.freshness == "current"


def test_historical_backfill_never_falls_back_to_cad():
    db, cad = Database(snapshot([], "empty")), Cad(snapshot([{"cfs_number": "x"}]))
    result = retrieve_cloud_analytics(tenant_id="logan-synthetic", query_kind="backfill", parameters={}, database=db, cad=cad, cad_operation="search_calls", current_answer_required=True)
    assert result.denial == "historical_cad_fallback_denied"
    assert not cad.calls


def test_unknown_or_mutating_operation_is_denied_before_cad_call():
    db, cad = Database(snapshot([], "stale")), Cad(snapshot([]))
    result = retrieve_cloud_analytics(tenant_id="logan-synthetic", query_kind="current_calls", parameters={}, database=db, cad=cad, cad_operation="update_call", current_answer_required=True)
    assert result.denial == "cad_fallback_unavailable"
    assert not cad.calls


def test_tenant_mismatch_fails_closed():
    db = Database({**snapshot([{"count": 1}]), "tenant_id": "other"})
    result = retrieve_cloud_analytics(tenant_id="logan-synthetic", query_kind="report", parameters={}, database=db)
    assert result.denial == "tenant_mismatch" and not result.data


def test_non_current_empty_or_stale_database_result_is_attributed_without_fallback():
    db, cad = Database(snapshot([], "stale")), Cad(snapshot([{"cfs_number": "x"}]))
    result = retrieve_cloud_analytics(tenant_id="logan-synthetic", query_kind="report", parameters={}, database=db, cad=cad, cad_operation="search_calls")
    assert result.source == "cloud_database" and result.freshness == "stale"
    assert not result.fallback_used and not cad.calls
