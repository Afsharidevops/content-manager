from smart_router.dashboard import dashboard_response


def test_dashboard_is_self_contained_and_calls_local_summary_api():
    response = dashboard_response(version="0.5.0")
    body = response.body.decode("utf-8")
    assert "Hermes Smart Router" in body
    assert "/dashboard/api/summary" in body
    assert "same-token strong-only" in body
    assert "v<span id=\"version\"" in body
