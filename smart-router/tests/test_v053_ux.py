from smart_router import __version__
from smart_router.dashboard import dashboard_response
from smart_router.panel_v56 import PANEL_HTML


def test_v054_runtime_and_flight_deck_surface():
    assert __version__ == "0.5.9"
    body = dashboard_response(version=__version__).body.decode("utf-8")
    assert "Hermes Flight Deck" in body
    assert "Auto refresh" in body
    assert "model=auto" in body
    assert "same-token strong-only" in body
    assert "/control/" in body


def test_operations_center_has_grouped_navigation_and_choices():
    assert "navGroups" in PANEL_HTML
    for group in ("Observe", "Routing", "Access", "Intelligence", "System"):
        assert group in PANEL_HTML
    assert '<select id="f_role">' in PANEL_HTML
    assert '<select id="t_strategy">' in PANEL_HTML
    assert '<select id="p_risk">' in PANEL_HTML
    assert '<select id="f_effect">' in PANEL_HTML
