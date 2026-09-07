from __future__ import annotations

import pytest

from smart_router.graph_v59 import GRAPH_SCHEMA_VERSION, normalize_graph, port_definitions
from smart_router.panel_v58 import PANEL_HTML


def workflow_graph(nodes, edges):
    return normalize_graph({"nodes": nodes, "edges": edges}, studio="workflow", max_nodes=200, max_edges=500)


def knowledge_graph(nodes, edges):
    return normalize_graph(
        {"nodes": nodes, "edges": edges},
        studio="knowledge",
        max_nodes=160,
        max_edges=400,
        numeric_ref_id=True,
    )


def test_legacy_workflow_edges_gain_stable_ports_and_ids():
    graph = workflow_graph(
        [
            {"id": "in", "type": "input"},
            {"id": "agent", "type": "agent"},
            {"id": "out", "type": "output"},
        ],
        [{"from": "in", "to": "agent"}, {"from": "agent", "to": "out"}],
    )
    assert graph["version"] == GRAPH_SCHEMA_VERSION == 2
    edge = graph["edges"][0]
    assert edge["id"].startswith("edge-")
    assert edge["source_node"] == edge["from"] == "in"
    assert edge["source_port"] == "default"
    assert edge["target_node"] == edge["to"] == "agent"
    assert edge["target_port"] == "default"


def test_typed_workflow_edges_auto_select_context_and_tool_ports():
    graph = workflow_graph(
        [
            {"id": "kb", "type": "knowledge"},
            {"id": "skill", "type": "skill"},
            {"id": "agent", "type": "agent"},
        ],
        [{"from": "kb", "to": "agent"}, {"from": "skill", "to": "agent"}],
    )
    by_source = {e["source_node"]: e for e in graph["edges"]}
    assert by_source["kb"]["source_port"] == "context"
    assert by_source["kb"]["target_port"] == "context"
    assert by_source["skill"]["source_port"] == "tools"
    assert by_source["skill"]["target_port"] == "tools"


def test_named_branch_and_approval_outputs_are_persisted():
    graph = workflow_graph(
        [
            {"id": "branch", "type": "branch"},
            {"id": "agent", "type": "agent"},
            {"id": "approval", "type": "approval"},
            {"id": "out", "type": "output"},
        ],
        [
            {"source_node": "branch", "source_port": "true", "target_node": "agent", "target_port": "default"},
            {"source_node": "approval", "source_port": "approved", "target_node": "out", "target_port": "default"},
        ],
    )
    assert [e["source_port"] for e in graph["edges"]] == ["true", "approved"]


def test_duplicate_self_cycle_and_unknown_port_are_rejected():
    nodes = [{"id": "a", "type": "agent"}, {"id": "b", "type": "agent"}]
    with pytest.raises(ValueError, match="duplicate"):
        workflow_graph(nodes, [{"from": "a", "to": "b"}, {"from": "a", "to": "b"}])
    with pytest.raises(ValueError, match="self-connections"):
        workflow_graph(nodes, [{"from": "a", "to": "a"}])
    with pytest.raises(ValueError, match="cycle"):
        workflow_graph(nodes, [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}])
    with pytest.raises(ValueError, match="unknown output port"):
        workflow_graph(nodes, [{"source_node": "a", "source_port": "missing", "target_node": "b"}])


def test_knowledge_pipeline_preserves_v058_shortcuts_but_rejects_reverse_direction():
    graph = knowledge_graph(
        [
            {"id": "source", "type": "data_source"},
            {"id": "chunk", "type": "chunk"},
            {"id": "kb", "type": "knowledge_base", "ref_id": "7"},
        ],
        [{"from": "source", "to": "chunk"}, {"from": "chunk", "to": "kb"}],
    )
    assert graph["nodes"][2]["ref_id"] == 7
    assert graph["version"] == 2
    with pytest.raises(ValueError, match="no output port|no input port|cannot connect"):
        knowledge_graph(
            [{"id": "embed", "type": "embed"}, {"id": "source", "type": "data_source"}],
            [{"from": "embed", "to": "source"}],
        )


def test_port_definitions_expose_named_outputs():
    assert {p["id"] for p in port_definitions("workflow", "approval")["outputs"]} >= {
        "approved", "rejected", "timeout"
    }
    assert {p["id"] for p in port_definitions("workflow", "branch")["outputs"]} >= {"true", "false"}


def test_panel_contains_shared_drag_connection_engine_and_edge_controls():
    for marker in (
        "graphConnectionStart",
        "graphConnectionDrop",
        "graphConnectionCheck",
        "graphNormalizeClientGraph",
        "graphSerialize",
        "graphSelectEdge",
        "Delete connection",
        "Unsaved changes",
    ):
        assert marker in PANEL_HTML
