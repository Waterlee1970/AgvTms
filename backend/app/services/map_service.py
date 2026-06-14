"""
Map Management Service — Persistence-Enhanced.

Reads/writes map data to PostgreSQL with Redis caching.
Falls back to in-memory storage when DB is unavailable (dev/demo mode).
Maintains backward compatibility with existing API endpoints.
"""

from __future__ import annotations

import copy
import logging
from typing import List, Optional

from ..config import settings
from ..models.schemas import MapEdge, MapGraph, MapNode
from . import redis_service

logger = logging.getLogger(__name__)


class MapService:
    """Service for managing factory layout maps with DB persistence."""

    def __init__(self):
        # In-memory fallback (used when DB is unavailable)
        self._graph: Optional[MapGraph] = None
        self._db_checked = False
        # Initialize demo data immediately for in-memory fallback
        self._init_demo_map()

    def _init_demo_map(self):
        """Initialize demo factory layout (in-memory fallback)."""
        from ..db.seed import build_demo_graph
        self._graph = build_demo_graph()

    # ---- DB Helpers ----

    async def _ensure_loaded(self) -> MapGraph:
        """Load map from DB/Redis/memory, whichever is available."""
        # Try Redis cache first
        cached = await redis_service.cache_get_json(redis_service.map_cache_key())
        if cached:
            return MapGraph.model_validate(cached)

        # Try DB
        if settings.USE_DB_PERSISTENCE:
            graph = await self._load_from_db()
            if graph:
                # Cache it
                await redis_service.cache_set_json(
                    redis_service.map_cache_key(),
                    graph.model_dump(mode="json"),
                    ttl=settings.REDIS_CACHE_TTL,
                )
                self._graph = graph
                return graph

        # Fallback to memory
        return copy.deepcopy(self._graph)

    async def _load_from_db(self) -> Optional[MapGraph]:
        """Load active map from database."""
        try:
            from ..db.database import get_session_factory
            from ..db.models import MapRecord
            from sqlalchemy import select

            factory = get_session_factory()
            async with factory() as session:
                result = await session.execute(
                    select(MapRecord).where(MapRecord.is_active == True).limit(1)
                )
                record = result.scalars().first()
                if record:
                    return MapGraph.model_validate(record.graph_data)
        except Exception as e:
            logger.debug("Failed to load map from DB: %s", e)
        return None

    async def _save_to_db(self, graph: MapGraph):
        """Save or update map in database."""
        try:
            from ..db.database import get_session_factory
            from ..db.models import MapRecord
            from sqlalchemy import select, update

            factory = get_session_factory()
            async with factory() as session:
                result = await session.execute(
                    select(MapRecord).where(MapRecord.is_active == True).limit(1)
                )
                record = result.scalars().first()
                if record:
                    record.graph_data = graph.model_dump(mode="json")
                    record.version = str(graph.version)
                else:
                    session.add(MapRecord(
                        name=graph.name,
                        version=str(graph.version),
                        graph_data=graph.model_dump(mode="json"),
                        is_active=True,
                    ))
                await session.commit()
        except Exception as e:
            logger.warning("Failed to save map to DB: %s", e)

    async def _invalidate_cache(self):
        """Invalidate Redis cache for the map."""
        await redis_service.cache_delete(redis_service.map_cache_key())

    # ---- Public API (async) ----

    async def get_graph_async(self) -> MapGraph:
        """Get the current map graph (async, with DB/Redis)."""
        return await self._ensure_loaded()

    async def get_nodes_async(self) -> List[MapNode]:
        graph = await self._ensure_loaded()
        return copy.deepcopy(graph.nodes)

    async def get_edges_async(self) -> List[MapEdge]:
        graph = await self._ensure_loaded()
        return copy.deepcopy(graph.edges)

    async def add_node_async(self, node: MapNode) -> MapNode:
        graph = await self._ensure_loaded()
        if any(n.id == node.id for n in graph.nodes):
            raise ValueError(f"Node {node.id} already exists")
        graph.nodes.append(node)
        graph.version += 1
        self._graph = copy.deepcopy(graph)
        await self._save_to_db(graph)
        await self._invalidate_cache()
        logger.info("Added node: %s", node.id)
        return node

    async def update_node_async(self, node_id: str, updated: MapNode) -> Optional[MapNode]:
        graph = await self._ensure_loaded()
        for i, n in enumerate(graph.nodes):
            if n.id == node_id:
                graph.nodes[i] = updated
                graph.version += 1
                self._graph = copy.deepcopy(graph)
                await self._save_to_db(graph)
                await self._invalidate_cache()
                logger.info("Updated node: %s", node_id)
                return updated
        return None

    async def delete_node_async(self, node_id: str) -> bool:
        graph = await self._ensure_loaded()
        initial_len = len(graph.nodes)
        graph.nodes = [n for n in graph.nodes if n.id != node_id]
        graph.edges = [
            e for e in graph.edges
            if e.from_node != node_id and e.to_node != node_id
        ]
        if len(graph.nodes) < initial_len:
            graph.version += 1
            self._graph = copy.deepcopy(graph)
            await self._save_to_db(graph)
            await self._invalidate_cache()
            logger.info("Deleted node: %s", node_id)
            return True
        return False

    async def add_edge_async(self, edge: MapEdge) -> MapEdge:
        graph = await self._ensure_loaded()
        graph.edges.append(edge)
        graph.version += 1
        self._graph = copy.deepcopy(graph)
        await self._save_to_db(graph)
        await self._invalidate_cache()
        return edge

    async def delete_edge_async(self, edge_id: str) -> bool:
        graph = await self._ensure_loaded()
        initial_len = len(graph.edges)
        graph.edges = [e for e in graph.edges if e.id != edge_id]
        if len(graph.edges) < initial_len:
            graph.version += 1
            self._graph = copy.deepcopy(graph)
            await self._save_to_db(graph)
            await self._invalidate_cache()
            return True
        return False

    # ---- Synchronous API (backward compatibility, uses in-memory) ----

    def get_graph(self) -> MapGraph:
        """Get the current map graph (sync, in-memory)."""
        if self._graph is None:
            self._init_demo_map()
        return copy.deepcopy(self._graph)

    def get_nodes(self) -> List[MapNode]:
        return copy.deepcopy(self._graph.nodes) if self._graph else []

    def get_edges(self) -> List[MapEdge]:
        return copy.deepcopy(self._graph.edges) if self._graph else []

    def add_node(self, node: MapNode) -> MapNode:
        if self._graph and any(n.id == node.id for n in self._graph.nodes):
            raise ValueError(f"Node {node.id} already exists")
        self._graph.nodes.append(node)
        self._graph.version += 1
        return node

    def update_node(self, node_id: str, updated: MapNode) -> Optional[MapNode]:
        if not self._graph:
            return None
        for i, n in enumerate(self._graph.nodes):
            if n.id == node_id:
                self._graph.nodes[i] = updated
                self._graph.version += 1
                return updated
        return None

    def delete_node(self, node_id: str) -> bool:
        if not self._graph:
            return False
        initial_len = len(self._graph.nodes)
        self._graph.nodes = [n for n in self._graph.nodes if n.id != node_id]
        self._graph.edges = [
            e for e in self._graph.edges
            if e.from_node != node_id and e.to_node != node_id
        ]
        if len(self._graph.nodes) < initial_len:
            self._graph.version += 1
            return True
        return False

    def add_edge(self, edge: MapEdge) -> MapEdge:
        if self._graph:
            self._graph.edges.append(edge)
            self._graph.version += 1
        return edge

    def delete_edge(self, edge_id: str) -> bool:
        if not self._graph:
            return False
        initial_len = len(self._graph.edges)
        self._graph.edges = [e for e in self._graph.edges if e.id != edge_id]
        if len(self._graph.edges) < initial_len:
            self._graph.version += 1
            return True
        return False


# Singleton instance
map_service = MapService()
