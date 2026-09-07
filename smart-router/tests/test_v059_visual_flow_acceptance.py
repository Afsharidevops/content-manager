from __future__ import annotations

from smart_router.graph_v59 import normalize_graph, port_definitions, router_graph_plan
from smart_router.panel_v58 import PANEL_HTML


def test_all_four_studios_use_one_shared_graph_shell():
    markers = (
        "graphStudioShell(graphStudio.name,WORKFLOW_TYPES)",
        "graphStudioShell(a.name,AGENT_TYPES)",
        "graphStudioShell(graphStudio.name,STAGE_TYPES)",
        "graphStudioShell(graphStudio.name,KNOWLEDGE_TYPES)",
    )
    for marker in markers:
        assert marker in PANEL_HTML


def test_named_workflow_and_router_outputs_are_explicit():
    workflow = port_definitions("workflow", "approval")["outputs"]
    assert {x["id"] for x in workflow} >= {"approved", "rejected", "timeout"}
    condition = port_definitions("workflow", "branch")["outputs"]
    assert {x["id"] for x in condition} >= {"true", "false"}

    assert {x["id"] for x in port_definitions("router", "classifier")["outputs"]} == {"coding", "vision", "default"}
    assert {x["id"] for x in port_definitions("router", "health_filter")["outputs"]} >= {"healthy", "unhealthy"}
    assert {x["id"] for x in port_definitions("router", "approval")["outputs"]} >= {"approved", "rejected", "timeout"}


def test_router_named_branches_persist_exact_source_ports():
    graph = normalize_graph(
        {
            "nodes": [
                {"id": "classifier", "type": "classifier"},
                {"id": "coding", "type": "route"},
                {"id": "vision", "type": "route"},
                {"id": "fallback", "type": "fallback"},
            ],
            "edges": [
                {"source_node": "classifier", "source_port": "coding", "target_node": "coding", "target_port": "default"},
                {"source_node": "classifier", "source_port": "vision", "target_node": "vision", "target_port": "default"},
                {"source_node": "classifier", "source_port": "default", "target_node": "fallback", "target_port": "default"},
            ],
        },
        studio="router",
        max_nodes=100,
        max_edges=200,
    )
    plan = router_graph_plan(graph)
    assert plan["transitions"]["classifier"] == {
        "coding": "coding",
        "vision": "vision",
        "default": "fallback",
    }


def test_editor_productivity_and_accessibility_features_are_present():
    markers = (
        "graphReconnectStart",
        "graphQuickAddCandidates",
        "Add next node",
        "graphUndo()",
        "graphRedo()",
        "graphFit()",
        "graphZoom(",
        "graphCanvasPanStart",
        "graphCanvasWheel",
        "ev.ctrlKey",
        "aria-label=",
        "tabindex=\"0\"",
        "Saving...",
        "Save failed",
        "Unsaved changes",
    )
    for marker in markers:
        assert marker in PANEL_HTML


def test_drag_connect_validation_and_theme_tokens_remain_shared():
    markers = (
        "graphConnectionStart",
        "graphConnectionDrop",
        "graphConnectionCheck",
        "graphWouldCycle",
        "graph-preview",
        "graph-node-port",
        "valid",
        "invalid",
        "var(--surface)",
        "var(--accent)",
        "[data-theme=\"light\"]",
    )
    for marker in markers:
        assert marker in PANEL_HTML


def test_visual_approval_never_claims_execution_authority():
    # UI copy and backend behavior must keep the existing trust boundary explicit.
    assert "Approval connections define structure only and never grant execution authority" in PANEL_HTML
