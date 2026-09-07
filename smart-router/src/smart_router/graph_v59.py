from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable


GRAPH_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class Port:
    id: str
    data_type: str


@dataclass(frozen=True)
class NodePorts:
    inputs: tuple[Port, ...] = ()
    outputs: tuple[Port, ...] = ()


WORKFLOW_PORTS: dict[str, NodePorts] = {
    "input": NodePorts(outputs=(Port("default", "flow"),)),
    "output": NodePorts(inputs=(Port("default", "flow"), Port("answer", "answer"))),
    "agent": NodePorts(
        inputs=(Port("default", "flow"), Port("context", "knowledge"), Port("tools", "tool")),
        outputs=(Port("default", "flow"), Port("result", "answer")),
    ),
    "team": NodePorts(inputs=(Port("default", "flow"),), outputs=(Port("default", "flow"), Port("result", "answer"))),
    "knowledge": NodePorts(outputs=(Port("context", "knowledge"),)),
    "skill": NodePorts(outputs=(Port("tools", "tool"),)),
    "plugin": NodePorts(outputs=(Port("tools", "tool"),)),
    "approval": NodePorts(
        inputs=(Port("default", "flow"),),
        outputs=(
            Port("default", "flow"),
            Port("approved", "flow"),
            Port("rejected", "flow"),
            Port("timeout", "flow"),
        ),
    ),
    "branch": NodePorts(
        inputs=(Port("default", "flow"),),
        outputs=(Port("default", "flow"), Port("true", "flow"), Port("false", "flow")),
    ),
    "parallel": NodePorts(inputs=(Port("default", "flow"),), outputs=(Port("default", "flow"),)),
}


# Agent Studio uses the same typed relationship model as workflow agent nodes, but
# persists a composition graph separately from the agent's execution configuration.
AGENT_PORTS: dict[str, NodePorts] = {
    "input": NodePorts(outputs=(Port("default", "flow"),)),
    "agent": NodePorts(
        inputs=(Port("default", "flow"), Port("context", "knowledge"), Port("tools", "tool")),
        outputs=(Port("result", "answer"),),
    ),
    "knowledge": NodePorts(outputs=(Port("context", "knowledge"),)),
    "skill": NodePorts(outputs=(Port("tools", "tool"),)),
    "plugin": NodePorts(outputs=(Port("tools", "tool"),)),
    "output": NodePorts(inputs=(Port("answer", "answer"),)),
}


KNOWLEDGE_PORTS: dict[str, NodePorts] = {
    "data_source": NodePorts(outputs=(Port("default", "raw"),)),
    "extract": NodePorts(inputs=(Port("default", "raw"),), outputs=(Port("default", "text"),)),
    "transform": NodePorts(inputs=(Port("default", "text"),), outputs=(Port("default", "text"),)),
    # v0.5.8 accepted direct source -> chunk graphs, so raw remains accepted here.
    "chunk": NodePorts(inputs=(Port("default", "text_or_raw"),), outputs=(Port("default", "chunks"),)),
    "embed": NodePorts(inputs=(Port("default", "chunks_or_text"),), outputs=(Port("default", "embeddings"),)),
    "index": NodePorts(inputs=(Port("default", "embeddings"),), outputs=(Port("default", "index"),)),
    # v0.5.8 accepted chunk -> knowledge_base; retain that migration path.
    "knowledge_base": NodePorts(inputs=(Port("default", "knowledge_sink"),), outputs=(Port("default", "knowledge"),)),
    "qa": NodePorts(inputs=(Port("default", "knowledge"),), outputs=(Port("default", "answer"),)),
    "output": NodePorts(inputs=(Port("default", "answer_or_knowledge"),)),
}


# Router Pipeline supports explicit named branch outputs. The graph describes
# orchestration only; approval ports never grant execution authority.
ROUTER_PORTS: dict[str, NodePorts] = {
    "classifier": NodePorts(
        inputs=(Port("default", "route_flow"),),
        outputs=(Port("coding", "route_flow"), Port("vision", "route_flow"), Port("default", "route_flow")),
    ),
    "condition": NodePorts(
        inputs=(Port("default", "route_flow"),),
        outputs=(Port("true", "route_flow"), Port("false", "route_flow"), Port("default", "route_flow")),
    ),
    "capability_filter": NodePorts(
        inputs=(Port("default", "route_flow"),),
        outputs=(Port("matched", "route_flow"), Port("unmatched", "route_flow"), Port("default", "route_flow")),
    ),
    "health_filter": NodePorts(
        inputs=(Port("default", "route_flow"),),
        outputs=(Port("healthy", "route_flow"), Port("unhealthy", "route_flow"), Port("default", "route_flow")),
    ),
    "approval": NodePorts(
        inputs=(Port("default", "route_flow"),),
        outputs=(Port("approved", "route_flow"), Port("rejected", "route_flow"), Port("timeout", "route_flow"), Port("default", "route_flow")),
    ),
    "cost_latency_score": NodePorts(inputs=(Port("default", "route_flow"),), outputs=(Port("default", "route_flow"),)),
    "load_balance": NodePorts(inputs=(Port("default", "route_flow"),), outputs=(Port("default", "route_flow"),)),
    "route": NodePorts(inputs=(Port("default", "route_flow"),), outputs=(Port("default", "route_flow"),)),
    "retry": NodePorts(inputs=(Port("default", "route_flow"),), outputs=(Port("default", "route_flow"),)),
    "fallback": NodePorts(inputs=(Port("default", "route_flow"),), outputs=(Port("default", "route_flow"),)),
}


_COMPATIBILITY: dict[str, set[str]] = {
    "flow": {"flow"},
    "answer": {"answer", "flow", "answer_or_knowledge"},
    "knowledge": {"knowledge", "answer_or_knowledge"},
    "tool": {"tool"},
    "raw": {"raw", "text_or_raw", "chunks_or_text", "knowledge_sink"},
    "text": {"text", "text_or_raw", "chunks_or_text", "knowledge_sink"},
    "chunks": {"chunks_or_text", "knowledge_sink"},
    "embeddings": {"embeddings", "knowledge_sink"},
    "index": {"index", "knowledge_sink"},
    "route_flow": {"route_flow"},
}


_PORT_TABLES: dict[str, dict[str, NodePorts]] = {
    "workflow": WORKFLOW_PORTS,
    "agent": AGENT_PORTS,
    "knowledge": KNOWLEDGE_PORTS,
    "router": ROUTER_PORTS,
}


def ports_for(studio: str, node_type: str) -> NodePorts:
    table = _PORT_TABLES.get(studio)
    if table is None:
        raise ValueError(f"unsupported graph studio: {studio}")
    try:
        return table[node_type]
    except KeyError as exc:
        raise ValueError(f"unsupported {studio} node type: {node_type}") from exc


def port_definitions(studio: str, node_type: str) -> dict[str, list[dict[str, str]]]:
    p = ports_for(studio, node_type)
    return {
        "inputs": [{"id": x.id, "type": x.data_type} for x in p.inputs],
        "outputs": [{"id": x.id, "type": x.data_type} for x in p.outputs],
    }


def _compatible(source_type: str, target_type: str) -> bool:
    return target_type in _COMPATIBILITY.get(source_type, {source_type})


def _find_port(ports: Iterable[Port], port_id: str) -> Port | None:
    return next((p for p in ports if p.id == port_id), None)


def _resolve_ports(
    studio: str,
    source_type: str,
    target_type: str,
    source_port: str | None,
    target_port: str | None,
) -> tuple[Port, Port]:
    source = ports_for(studio, source_type)
    target = ports_for(studio, target_type)
    if not source.outputs:
        raise ValueError(f"{source_type} has no output port")
    if not target.inputs:
        raise ValueError(f"{target_type} has no input port")

    if source_port:
        sp = _find_port(source.outputs, source_port)
        if sp is None:
            raise ValueError(f"unknown output port {source_port!r} on {source_type}")
        source_candidates = (sp,)
    else:
        source_candidates = source.outputs

    if target_port:
        tp = _find_port(target.inputs, target_port)
        if tp is None:
            raise ValueError(f"unknown input port {target_port!r} on {target_type}")
        target_candidates = (tp,)
    else:
        target_candidates = target.inputs

    # Prefer default/default for migrated v0.5.8 edges when compatible.
    ordered_sources = sorted(source_candidates, key=lambda p: p.id != "default")
    ordered_targets = sorted(target_candidates, key=lambda p: p.id != "default")
    for sp in ordered_sources:
        for tp in ordered_targets:
            if _compatible(sp.data_type, tp.data_type):
                return sp, tp
    raise ValueError(f"{source_type} output cannot connect to {target_type} input")


def _edge_id(source_node: str, source_port: str, target_node: str, target_port: str) -> str:
    raw = f"{source_node}\0{source_port}\0{target_node}\0{target_port}".encode()
    return "edge-" + hashlib.sha1(raw).hexdigest()[:16]


def _has_cycle(nodes: set[str], edges: list[tuple[str, str]]) -> bool:
    adjacency: dict[str, list[str]] = {node: [] for node in nodes}
    for source, target in edges:
        adjacency[source].append(target)
    state: dict[str, int] = {node: 0 for node in nodes}

    def visit(node: str) -> bool:
        if state[node] == 1:
            return True
        if state[node] == 2:
            return False
        state[node] = 1
        for nxt in adjacency[node]:
            if visit(nxt):
                return True
        state[node] = 2
        return False

    return any(visit(node) for node in nodes if state[node] == 0)


def normalize_graph(
    value: Any,
    *,
    studio: str,
    max_nodes: int,
    max_edges: int,
    numeric_ref_id: bool = False,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{studio} graph must be an object")
    nodes = value.get("nodes", [])
    edges = value.get("edges", [])
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError(f"{studio} nodes and edges must be lists")
    if len(nodes) > max_nodes or len(edges) > max_edges:
        raise ValueError(f"{studio} graph is too large")

    clean_nodes: list[dict[str, Any]] = []
    node_map: dict[str, dict[str, Any]] = {}
    for raw in nodes:
        if not isinstance(raw, dict):
            raise ValueError(f"{studio} node must be an object")
        node_id = str(raw.get("id", "")).strip()[:80]
        node_type = str(raw.get("type", "")).strip()
        if not node_id or node_id in node_map:
            raise ValueError(f"{studio} node IDs must be unique")
        ports_for(studio, node_type)  # validates type
        ref_id = raw.get("ref_id")
        if numeric_ref_id and ref_id is not None:
            try:
                ref_id = int(ref_id)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{studio} ref_id must be numeric when provided") from exc
        config = raw.get("config") if isinstance(raw.get("config"), dict) else {}
        clean = {
            "id": node_id,
            "type": node_type,
            "label": str(raw.get("label", node_id))[:160],
            "ref_id": ref_id,
            "config": config,
            "ports": port_definitions(studio, node_type),
        }
        clean_nodes.append(clean)
        node_map[node_id] = clean

    clean_edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    topology: list[tuple[str, str]] = []
    for raw in edges:
        if not isinstance(raw, dict):
            raise ValueError(f"{studio} edge must be an object")
        source_node = str(raw.get("source_node", raw.get("from", ""))).strip()
        target_node = str(raw.get("target_node", raw.get("to", ""))).strip()
        if source_node not in node_map or target_node not in node_map:
            raise ValueError(f"{studio} edge references an unknown node")
        if source_node == target_node:
            raise ValueError(f"{studio} self-connections are not supported")
        source_port_value = str(raw.get("source_port", "")).strip() or None
        target_port_value = str(raw.get("target_port", "")).strip() or None
        sp, tp = _resolve_ports(
            studio,
            node_map[source_node]["type"],
            node_map[target_node]["type"],
            source_port_value,
            target_port_value,
        )
        key = (source_node, sp.id, target_node, tp.id)
        if key in seen:
            raise ValueError(f"{studio} duplicate connection is not supported")
        seen.add(key)
        topology.append((source_node, target_node))
        condition = str(raw.get("condition", ""))[:500]
        label_value = raw.get("label")
        label = str(label_value)[:160] if label_value is not None else (condition[:160] or None)
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        clean_edges.append(
            {
                "id": str(raw.get("id", "")).strip()[:120] or _edge_id(source_node, sp.id, target_node, tp.id),
                "source_node": source_node,
                "source_port": sp.id,
                "target_node": target_node,
                "target_port": tp.id,
                # Legacy aliases remain during the v0.5.8 -> v0.5.9 migration window.
                "from": source_node,
                "to": target_node,
                "condition": condition,
                "label": label,
                "metadata": metadata,
            }
        )

    if _has_cycle(set(node_map), topology):
        raise ValueError(f"{studio} graph contains an unsupported cycle")

    return {"nodes": clean_nodes, "edges": clean_edges, "version": GRAPH_SCHEMA_VERSION}


def linear_node_order(graph: dict[str, Any], *, studio: str) -> list[str]:
    """Return a single-chain node order or reject an ambiguous/disconnected graph."""
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    ids = [str(node.get("id", "")) for node in nodes]
    if not ids:
        if edges:
            raise ValueError(f"{studio} graph has edges without nodes")
        return []
    if len(ids) == 1:
        if edges:
            raise ValueError(f"{studio} single-node graph cannot contain an edge")
        return ids
    if len(edges) != len(ids) - 1:
        raise ValueError(f"{studio} graph must be one connected stage chain")

    indegree = {node_id: 0 for node_id in ids}
    outgoing: dict[str, str] = {}
    for edge in edges:
        source = str(edge.get("source_node", edge.get("from", "")))
        target = str(edge.get("target_node", edge.get("to", "")))
        if source in outgoing:
            raise ValueError(f"{studio} graph branching is reserved for the advanced routing phase")
        outgoing[source] = target
        indegree[target] = indegree.get(target, 0) + 1
        if indegree[target] > 1:
            raise ValueError(f"{studio} graph merging is reserved for the advanced routing phase")

    starts = [node_id for node_id in ids if indegree[node_id] == 0]
    if len(starts) != 1:
        raise ValueError(f"{studio} graph must have exactly one starting stage")

    order: list[str] = []
    current: str | None = starts[0]
    while current is not None:
        if current in order:
            raise ValueError(f"{studio} graph contains an unsupported cycle")
        order.append(current)
        current = outgoing.get(current)
    if len(order) != len(ids):
        raise ValueError(f"{studio} graph must be one connected stage chain")
    return order


def router_graph_plan(graph: dict[str, Any]) -> dict[str, Any]:
    """Validate a Router DAG and return deterministic entry/transitions/topological order."""
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    ids = [str(node.get("id", "")) for node in nodes]
    if not ids:
        return {"entry": None, "order": [], "transitions": {}}
    indegree = {node_id: 0 for node_id in ids}
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in ids}
    transitions: dict[str, dict[str, str]] = {node_id: {} for node_id in ids}
    for edge in edges:
        source = str(edge.get("source_node", edge.get("from", "")))
        target = str(edge.get("target_node", edge.get("to", "")))
        port = str(edge.get("source_port", "default"))
        if port in transitions[source]:
            raise ValueError(f"router output port {source}.{port} may have only one connection")
        transitions[source][port] = target
        adjacency[source].append(target)
        indegree[target] += 1
    starts = [node_id for node_id in ids if indegree[node_id] == 0]
    if len(starts) != 1:
        raise ValueError("router graph must have exactly one starting stage")
    # Router joins are allowed, but disconnected nodes are not.
    queue = [starts[0]]
    seen: set[str] = set()
    while queue:
        node = queue.pop(0)
        if node in seen:
            continue
        seen.add(node)
        queue.extend(adjacency[node])
    if seen != set(ids):
        raise ValueError("router graph must be fully connected from its starting stage")
    # Stable Kahn topological order used for the persisted stage list.
    indegree2 = dict(indegree)
    ready = [node_id for node_id in ids if indegree2[node_id] == 0]
    order: list[str] = []
    while ready:
        node = ready.pop(0)
        order.append(node)
        for target in adjacency[node]:
            indegree2[target] -= 1
            if indegree2[target] == 0:
                ready.append(target)
    if len(order) != len(ids):
        raise ValueError("router graph contains an unsupported cycle")
    return {"entry": starts[0], "order": order, "transitions": transitions}
