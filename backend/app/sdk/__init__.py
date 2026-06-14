"""
AGV-TMS Python SDK.

Easy-to-use client for AGV-TMS API:
    from agvtms import AgvTmsClient

    client = AgvTmsClient("http://localhost:8000")

    # Run V2 scheduling
    result = client.run_schedule(tasks=[...], agvs=[...])

    # Get map
    graph = client.get_map()

    # Manage flows
    flows = client.list_flows()
    client.execute_flow("tpl_production_transport")

    # VDA5050
    client.create_simulated_agv("AGV001")

Installation:
    pip install httpx pydantic
    # Or: pip install agvtms (when published)

Usage:
    client = AgvTmsClient(base_url="http://localhost:8000")
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

try:
    import httpx
except ImportError:
    raise ImportError("httpx is required: pip install httpx")

logger = logging.getLogger(__name__)


class AgvTmsClient:
    """Synchronous client for AGV-TMS API."""

    def __init__(self, base_url: str = "http://localhost:8000", timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout)

    # ---- System ----

    def health(self) -> Dict[str, Any]:
        """Check system health."""
        return self._get("/health")

    def system_info(self) -> Dict[str, Any]:
        """Get system information."""
        return self._get("/api/v2/system/info")

    # ---- Scheduling ----

    def run_schedule(
        self,
        version: str = "v2",
        tasks: Optional[List[Dict]] = None,
        agvs: Optional[List[Dict]] = None,
        conveyor_tasks: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        """Run scheduling (V1 or V2)."""
        endpoint = f"/api/{'v2' if version == 'v2' else ''}/schedule/run"
        params = {}
        if tasks:
            params["tasks"] = tasks
        if agvs:
            params["agvs"] = agvs
        if conveyor_tasks:
            params["conveyor_tasks"] = conveyor_tasks
        return self._post(endpoint, json=params)

    def get_schedule_result(self, result_id: str) -> Dict[str, Any]:
        """Get scheduling result by ID."""
        return self._get(f"/api/v2/schedule/{result_id}")

    def get_schedule_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get scheduling history."""
        return self._get(f"/api/v2/schedule/history", params={"limit": limit})

    # ---- Algorithm ----

    def get_active_algorithm(self) -> str:
        """Get active algorithm version."""
        return self._get("/api/v2/algorithm/active")["active_version"]

    def set_active_algorithm(self, version: str) -> Dict[str, Any]:
        """Set active algorithm version (v1 or v2)."""
        return self._put("/api/v2/algorithm/active", params={"version": version})

    def get_algorithm_config(self) -> Dict[str, Any]:
        """Get algorithm configuration."""
        return self._get("/api/algorithm/config")

    def update_algorithm_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Update algorithm configuration."""
        return self._put("/api/algorithm/config", json=config)

    # ---- Map ----

    def get_map(self) -> Dict[str, Any]:
        """Get complete map graph."""
        return self._get("/api/map/graph")

    def get_nodes(self) -> List[Dict[str, Any]]:
        """Get all map nodes."""
        return self._get("/api/map/nodes")

    def get_edges(self) -> List[Dict[str, Any]]:
        """Get all map edges."""
        return self._get("/api/map/edges")

    def add_node(self, node: Dict[str, Any]) -> Dict[str, Any]:
        """Add a map node."""
        return self._post("/api/map/node", json=node)

    def delete_node(self, node_id: str) -> Dict[str, Any]:
        """Delete a map node."""
        return self._delete(f"/api/map/node/{node_id}")

    # ---- AGV ----

    def get_agvs(self) -> List[Dict[str, Any]]:
        """Get all AGV statuses."""
        return self._get("/api/agv/status")

    def update_agv(self, agv_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update AGV status."""
        return self._put(f"/api/agv/{agv_id}/status", json=updates)

    # ---- Tasks ----

    def get_tasks(self) -> List[Dict[str, Any]]:
        """Get all tasks."""
        return self._get("/api/tasks")

    def create_tasks(self, tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Create tasks."""
        return self._post("/api/tasks", json=tasks)

    # ---- Flows ----

    def list_flows(self) -> List[Dict[str, Any]]:
        """List all flow templates."""
        return self._get("/api/v2/flows/")

    def get_flow(self, flow_id: str) -> Dict[str, Any]:
        """Get a flow definition."""
        return self._get(f"/api/v2/flows/{flow_id}")

    def create_flow(self, flow: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new flow."""
        return self._post("/api/v2/flows/", json=flow)

    def execute_flow(self, flow_id: str, variables: Optional[Dict] = None) -> Dict[str, Any]:
        """Execute a flow."""
        return self._post(f"/api/v2/flows/{flow_id}/execute", json=variables)

    # ---- VDA5050 ----

    def get_vda5050_info(self) -> Dict[str, Any]:
        """Get VDA5050 protocol info."""
        return self._get("/api/v2/vda5050/info")

    def list_simulated_agvs(self) -> List[Dict[str, Any]]:
        """List simulated VDA5050 AGVs."""
        return self._get("/api/v2/vda5050/agvs")

    def create_simulated_agv(
        self, serial: str, start_node: str = "N_P00",
        speed: float = 1.5, battery: float = 100.0,
    ) -> Dict[str, Any]:
        """Create a simulated AGV."""
        return self._post("/api/v2/vda5050/agvs", params={
            "serial": serial, "start_node": start_node,
            "speed": speed, "battery": battery,
        })

    def send_order(self, serial: str, order: Dict[str, Any]) -> Dict[str, Any]:
        """Send a VDA5050 order to an AGV."""
        return self._post(f"/api/v2/vda5050/agvs/{serial}/order", json=order)

    def send_instant_action(self, serial: str, action_type: str) -> Dict[str, Any]:
        """Send an instant action (stop, cancelOrder, start)."""
        return self._post(f"/api/v2/vda5050/agvs/{serial}/instant-action", json={
            "instantActionType": action_type,
        })

    # ---- Vehicles ----

    def list_vehicle_types(self) -> List[Dict[str, Any]]:
        """List all vehicle types."""
        return self._get("/api/v2/vehicles/types")

    def match_vehicle_type(self, requirements: Dict[str, Any]) -> Dict[str, Any]:
        """Find best vehicle type for task requirements."""
        return self._post("/api/v2/vehicles/types/match", json=requirements)

    # ---- Analytics ----

    def get_dashboard(self) -> Dict[str, Any]:
        """Get analytics dashboard summary."""
        return self._get("/api/v2/analytics/dashboard")

    def get_task_stats(self, hours: int = 24) -> Dict[str, Any]:
        """Get task statistics."""
        return self._get("/api/v2/analytics/tasks", params={"hours": hours})

    def get_alerts(self, hours: int = 24) -> Dict[str, Any]:
        """Get alert statistics."""
        return self._get("/api/v2/analytics/alerts", params={"hours": hours})

    # ---- Simulation ----

    def run_simulation(
        self, duration: float = 3600, speedup: float = 5.0,
    ) -> Dict[str, Any]:
        """Run a fast-forward simulation."""
        return self._post("/api/v2/simulation/run", params={
            "duration": duration, "speedup": speedup,
        })

    # ---- Metrics ----

    def get_prometheus_metrics(self) -> str:
        """Get Prometheus-format metrics."""
        response = self._client.get("/metrics")
        return response.text

    # ---- HTTP helpers ----

    def _get(self, endpoint: str, **kwargs) -> Any:
        response = self._client.get(endpoint, **kwargs)
        response.raise_for_status()
        return response.json()

    def _post(self, endpoint: str, **kwargs) -> Any:
        response = self._client.post(endpoint, **kwargs)
        response.raise_for_status()
        return response.json()

    def _put(self, endpoint: str, **kwargs) -> Any:
        response = self._client.put(endpoint, **kwargs)
        response.raise_for_status()
        return response.json()

    def _delete(self, endpoint: str, **kwargs) -> Any:
        response = self._client.delete(endpoint, **kwargs)
        response.raise_for_status()
        return response.json()

    def close(self):
        """Close the client."""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class AsyncAgvTmsClient:
    """Asynchronous client for AGV-TMS API."""

    def __init__(self, base_url: str = "http://localhost:8000", timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def health(self) -> Dict[str, Any]:
        return await self._get("/health")

    async def run_schedule(self, version: str = "v2", **kwargs) -> Dict[str, Any]:
        endpoint = f"/api/{'v2' if version == 'v2' else ''}/schedule/run"
        return await self._post(endpoint, json=kwargs)

    async def get_map(self) -> Dict[str, Any]:
        return await self._get("/api/map/graph")

    async def get_agvs(self) -> List[Dict[str, Any]]:
        return await self._get("/api/agv/status")

    async def close(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def _get(self, endpoint: str, **kwargs) -> Any:
        response = await self._client.get(endpoint, **kwargs)
        response.raise_for_status()
        return response.json()

    async def _post(self, endpoint: str, **kwargs) -> Any:
        response = await self._client.post(endpoint, **kwargs)
        response.raise_for_status()
        return response.json()
