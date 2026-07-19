"""Strategy lineage: permanent parent/child ancestry and elimination history.

Lineage is evidence and is kept indefinitely (never compacted by retention).
A version's ancestry survives its elimination — an eliminated version stays in
the graph as a permanent record and as a reusable *ancestor reference*, even
though its code hash may never be reactivated.
"""

from __future__ import annotations

from dataclasses import dataclass, field

NOVEL = "novel"
MUTATION = "mutation"
DARK_HORSE_UPGRADE = "dark_horse_upgrade"
DARK_HORSE_DAILY_ADAPTATION = "dark_horse_daily_adaptation"

RELATIONSHIP_TYPES = frozenset({NOVEL, MUTATION, DARK_HORSE_UPGRADE,
                                DARK_HORSE_DAILY_ADAPTATION})


@dataclass(frozen=True, slots=True)
class LineageEdge:
    child_version_id: str
    parent_version_id: str | None  # None for a root/novel version
    relationship: str
    mutation_description: str = ""


@dataclass(slots=True)
class LineageGraph:
    _edges: list[LineageEdge] = field(default_factory=list)
    _by_child: dict[str, LineageEdge] = field(default_factory=dict)

    def add(self, edge: LineageEdge) -> None:
        if edge.relationship not in RELATIONSHIP_TYPES:
            raise ValueError(f"unknown relationship: {edge.relationship}")
        if edge.relationship == MUTATION and not edge.parent_version_id:
            raise ValueError("mutation requires a parent version")
        if edge.relationship == MUTATION and not edge.mutation_description:
            raise ValueError("mutation requires a description")
        if edge.child_version_id in self._by_child:
            raise ValueError(f"duplicate lineage for {edge.child_version_id}")
        if edge.parent_version_id == edge.child_version_id:
            raise ValueError("a version cannot be its own parent")
        self._edges.append(edge)
        self._by_child[edge.child_version_id] = edge

    def parent_of(self, version_id: str) -> str | None:
        edge = self._by_child.get(version_id)
        return edge.parent_version_id if edge else None

    def ancestors(self, version_id: str) -> list[str]:
        """Walk to the root. Cycle-safe."""

        out: list[str] = []
        seen = {version_id}
        current = self.parent_of(version_id)
        while current and current not in seen:
            out.append(current)
            seen.add(current)
            current = self.parent_of(current)
        return out

    def children_of(self, version_id: str) -> list[str]:
        return [e.child_version_id for e in self._edges
                if e.parent_version_id == version_id]

    def generation(self, version_id: str) -> int:
        """Root novel version is generation 0."""

        return len(self.ancestors(version_id))

    def describe(self, version_id: str) -> LineageEdge | None:
        return self._by_child.get(version_id)
