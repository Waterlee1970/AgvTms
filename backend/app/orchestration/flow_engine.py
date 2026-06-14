"""
Flow Orchestration Engine.

Implements a DAG-based workflow engine for AGV task orchestration,
inspired by RCS-2000's process orchestration engine.

Supports 7 node types:
1. transport  — AGV搬运 (pickup → dropoff)
2. wait       — 等待条件满足
3. dock       — AGV与机台/输送线对接
4. check      — 条件检查/质量验证
5. branch     — 条件分支
6. merge      — 多分支合并
7. script     — 自定义Python脚本

Flow definitions are YAML/JSON DSL, e.g.:

```yaml
name: 产线A到仓库B循环搬运
nodes:
  - id: start
    type: transport
    params:
      pickup: N_P00
      dropoff: N_D03
      agv: auto
    next: [check_quality]
  - id: check_quality
    type: check
    params:
      condition: "cargo.intact == true"
    next_on_pass: [return_empty]
    next_on_fail: [alert]
  - id: return_empty
    type: transport
    params:
      pickup: N_D03
      dropoff: N_P00
    next: [start]
  - id: alert
    type: script
    params:
      code: "notify_operator('质量异常')"
    next: [start]
```
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# =============================================================================
# Flow Node Types
# =============================================================================

class FlowNodeType(str, Enum):
    TRANSPORT = "transport"
    WAIT = "wait"
    DOCK = "dock"
    CHECK = "check"
    BRANCH = "branch"
    MERGE = "merge"
    SCRIPT = "script"
    START = "start"
    END = "end"


class FlowNodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


# =============================================================================
# Flow Definition Models
# =============================================================================

class FlowNodeDef(BaseModel):
    """Definition of a flow node."""
    id: str = Field(..., description="Unique node ID within the flow")
    type: FlowNodeType = Field(..., description="Node type")
    name: str = Field(default="", description="Human-readable name")
    params: Dict[str, Any] = Field(default_factory=dict, description="Node parameters")
    next: List[str] = Field(default_factory=list, description="Next node IDs (normal flow)")
    next_on_pass: Optional[str] = Field(default=None, description="Next node if check passes")
    next_on_fail: Optional[str] = Field(default=None, description="Next node if check fails")
    timeout: float = Field(default=300.0, description="Timeout in seconds")


class FlowDefinition(BaseModel):
    """Complete flow definition (YAML/JSON DSL)."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = Field(..., description="Flow name")
    description: str = Field(default="")
    version: str = Field(default="1.0")
    nodes: List[FlowNodeDef] = Field(..., description="Flow nodes")
    variables: Dict[str, Any] = Field(default_factory=dict, description="Initial variables")
    loop: bool = Field(default=False, description="Whether to loop the flow")


class FlowExecutionState(BaseModel):
    """Runtime state of a flow execution."""
    execution_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    flow_id: str
    flow_name: str
    status: str = Field(default="running")  # running, completed, failed, cancelled
    current_nodes: List[str] = Field(default_factory=list)
    completed_nodes: List[str] = Field(default_factory=list)
    failed_nodes: List[str] = Field(default_factory=list)
    variables: Dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    log: List[Dict[str, Any]] = Field(default_factory=list)


# =============================================================================
# Flow Engine
# =============================================================================

class FlowEngine:
    """
    DAG-based flow execution engine.

    Executes flow definitions by processing nodes in topological order,
    supporting branches, merges, loops, and custom scripts.
    """

    def __init__(self):
        self._definitions: Dict[str, FlowDefinition] = {}
        self._executions: Dict[str, FlowExecutionState] = {}
        self._script_handlers: Dict[str, Callable] = {}
        self._transport_callback: Optional[Callable] = None
        self._dock_callback: Optional[Callable] = None
        self._check_callback: Optional[Callable] = None

    # ---- Registration ----

    def register_flow(self, flow: FlowDefinition) -> str:
        """Register a flow definition."""
        self._definitions[flow.id] = flow
        logger.info("Registered flow: %s (%s)", flow.name, flow.id)
        return flow.id

    def set_transport_callback(self, callback: Callable):
        """Set callback for transport nodes (async callable)."""
        self._transport_callback = callback

    def set_dock_callback(self, callback: Callable):
        """Set callback for dock nodes."""
        self._dock_callback = callback

    def set_check_callback(self, callback: Callable):
        """Set callback for check nodes."""
        self._check_callback = callback

    def register_script_handler(self, name: str, handler: Callable):
        """Register a custom script handler."""
        self._script_handlers[name] = handler

    # ---- Execution ----

    async def execute(self, flow_id: str, variables: Optional[Dict] = None) -> FlowExecutionState:
        """Execute a registered flow."""
        flow = self._definitions.get(flow_id)
        if not flow:
            raise ValueError(f"Flow not found: {flow_id}")

        state = FlowExecutionState(
            flow_id=flow_id,
            flow_name=flow.name,
            variables={**flow.variables, **(variables or {})},
        )
        self._executions[state.execution_id] = state

        # Find start nodes (first node or nodes with type=START)
        start_nodes = self._find_start_nodes(flow)
        if not start_nodes:
            state.status = "failed"
            state.error = "No start node found"
            return state

        logger.info("Starting flow execution %s: %s", state.execution_id, flow.name)

        # Execute flow
        try:
            await self._execute_nodes(flow, state, start_nodes)

            if flow.loop and state.status == "completed":
                # Reset and loop
                while flow.loop and state.status == "completed":
                    state.completed_nodes = []
                    state.current_nodes = []
                    await self._execute_nodes(flow, state, start_nodes)

            state.status = "completed" if state.status == "running" else state.status
        except Exception as e:
            state.status = "failed"
            state.error = str(e)
            logger.exception("Flow execution failed: %s", state.execution_id)

        state.completed_at = datetime.now()
        return state

    def _find_start_nodes(self, flow: FlowDefinition) -> List[str]:
        """Find entry points of the flow."""
        all_next = set()
        for node in flow.nodes:
            all_next.update(node.next)
            if node.next_on_pass:
                all_next.add(node.next_on_pass)
            if node.next_on_fail:
                all_next.add(node.next_on_fail)

        # Nodes that are not referenced by any other node's next
        start_nodes = [n.id for n in flow.nodes if n.id not in all_next]
        return start_nodes if start_nodes else [flow.nodes[0].id]

    async def _execute_nodes(
        self, flow: FlowDefinition, state: FlowExecutionState, node_ids: List[str]
    ):
        """Execute a set of nodes (potentially in parallel)."""
        node_map = {n.id: n for n in flow.nodes}

        for node_id in node_ids:
            if node_id in state.completed_nodes or node_id in state.failed_nodes:
                continue

            node = node_map.get(node_id)
            if not node:
                state.log.append({"node": node_id, "error": "Node not found"})
                continue

            state.current_nodes.append(node_id)
            state.log.append({
                "node": node_id, "type": node.type.value,
                "status": "running", "timestamp": datetime.now().isoformat(),
            })

            try:
                next_nodes = await self._execute_node(node, state)
                state.completed_nodes.append(node_id)
                state.log.append({
                    "node": node_id, "status": "completed",
                    "timestamp": datetime.now().isoformat(),
                })

                if next_nodes:
                    await self._execute_nodes(flow, state, next_nodes)

            except Exception as e:
                state.failed_nodes.append(node_id)
                state.log.append({
                    "node": node_id, "status": "failed",
                    "error": str(e), "timestamp": datetime.now().isoformat(),
                })
                logger.warning("Node %s failed: %s", node_id, e)

            state.current_nodes = [n for n in state.current_nodes if n != node_id]

    async def _execute_node(self, node: FlowNodeDef, state: FlowExecutionState) -> List[str]:
        """Execute a single node and return next node IDs."""
        if node.type == FlowNodeType.TRANSPORT:
            return await self._exec_transport(node, state)
        elif node.type == FlowNodeType.WAIT:
            return await self._exec_wait(node, state)
        elif node.type == FlowNodeType.DOCK:
            return await self._exec_dock(node, state)
        elif node.type == FlowNodeType.CHECK:
            return await self._exec_check(node, state)
        elif node.type == FlowNodeType.BRANCH:
            return await self._exec_branch(node, state)
        elif node.type == FlowNodeType.MERGE:
            return await self._exec_merge(node, state)
        elif node.type == FlowNodeType.SCRIPT:
            return await self._exec_script(node, state)
        elif node.type in (FlowNodeType.START, FlowNodeType.END):
            return node.next
        else:
            logger.warning("Unknown node type: %s", node.type)
            return node.next

    async def _exec_transport(self, node: FlowNodeDef, state: FlowExecutionState) -> List[str]:
        """Execute transport node — trigger AGV搬运."""
        params = node.params
        pickup = params.get("pickup", "")
        dropoff = params.get("dropoff", "")
        agv = params.get("agv", "auto")

        state.log.append({
            "node": node.id, "action": "transport",
            "pickup": pickup, "dropoff": dropoff, "agv": agv,
        })
        logger.info("[Flow] Transport: %s → %s (AGV: %s)", pickup, dropoff, agv)

        if self._transport_callback:
            try:
                result = self._transport_callback(pickup, dropoff, agv, state.variables)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.warning("Transport callback failed: %s", e)

        # Update variables
        state.variables["last_pickup"] = pickup
        state.variables["last_dropoff"] = dropoff

        return node.next

    async def _exec_wait(self, node: FlowNodeDef, state: FlowExecutionState) -> List[str]:
        """Execute wait node."""
        duration = node.params.get("duration", 0)
        condition = node.params.get("condition", "")

        if duration > 0:
            logger.info("[Flow] Wait %.1fs", duration)
            await asyncio.sleep(min(duration, node.timeout))

        return node.next

    async def _exec_dock(self, node: FlowNodeDef, state: FlowExecutionState) -> List[str]:
        """Execute dock node — AGV与机台对接."""
        target = node.params.get("target", "")
        logger.info("[Flow] Dock at %s", target)

        if self._dock_callback:
            try:
                result = self._dock_callback(target, state.variables)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.warning("Dock callback failed: %s", e)

        return node.next

    async def _exec_check(self, node: FlowNodeDef, state: FlowExecutionState) -> List[str]:
        """Execute check node — condition evaluation."""
        condition = node.params.get("condition", "true")

        passed = self._evaluate_condition(condition, state.variables)

        logger.info("[Flow] Check '%s' → %s", condition, "PASS" if passed else "FAIL")

        if passed:
            state.variables[f"{node.id}_result"] = "pass"
            return [node.next_on_pass] if node.next_on_pass else node.next
        else:
            state.variables[f"{node.id}_result"] = "fail"
            return [node.next_on_fail] if node.next_on_fail else node.next

    async def _exec_branch(self, node: FlowNodeDef, state: FlowExecutionState) -> List[str]:
        """Execute branch node."""
        condition = node.params.get("condition", "")
        passed = self._evaluate_condition(condition, state.variables)

        if passed:
            return [node.next_on_pass] if node.next_on_pass else node.next
        else:
            return [node.next_on_fail] if node.next_on_fail else node.next

    async def _exec_merge(self, node: FlowNodeDef, state: FlowExecutionState) -> List[str]:
        """Execute merge node — just pass through."""
        logger.info("[Flow] Merge at %s", node.id)
        return node.next

    async def _exec_script(self, node: FlowNodeDef, state: FlowExecutionState) -> List[str]:
        """Execute script node."""
        handler_name = node.params.get("handler", "default")
        code = node.params.get("code", "")

        handler = self._script_handlers.get(handler_name)
        if handler:
            try:
                result = handler(state.variables, code)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.warning("Script handler '%s' failed: %s", handler_name, e)
        else:
            logger.info("[Flow] Script (no handler): %s", code[:100])

        return node.next

    def _evaluate_condition(self, condition: str, variables: Dict[str, Any]) -> bool:
        """Safely evaluate a condition string."""
        if not condition or condition == "true":
            return True
        try:
            # Safe eval with variables as locals
            safe_globals = {"__builtins__": {"True": True, "False": False, "len": len, "str": str}}
            return bool(eval(condition, safe_globals, variables))
        except Exception as e:
            logger.debug("Condition eval failed '%s': %s", condition, e)
            return False

    # ---- Query ----

    def get_execution(self, execution_id: str) -> Optional[FlowExecutionState]:
        return self._executions.get(execution_id)

    def list_flows(self) -> List[Dict[str, Any]]:
        """List all registered flows."""
        return [
            {
                "id": f.id, "name": f.name, "description": f.description,
                "version": f.version, "node_count": len(f.nodes), "loop": f.loop,
            }
            for f in self._definitions.values()
        ]

    def get_flow(self, flow_id: str) -> Optional[FlowDefinition]:
        return self._definitions.get(flow_id)


# =============================================================================
# Standard Flow Templates
# =============================================================================

def get_standard_templates() -> List[FlowDefinition]:
    """Return 5 standard flow templates (对标RCS-2000 100+模板的基础版)."""
    return [
        # 1. 产线搬运
        FlowDefinition(
            id="tpl_production_transport",
            name="产线搬运",
            description="从取货点搬运到产线卸货点",
            nodes=[
                FlowNodeDef(id="start", type=FlowNodeType.START, name="开始", next=["transport"]),
                FlowNodeDef(
                    id="transport", type=FlowNodeType.TRANSPORT, name="搬运到产线",
                    params={"pickup": "N_P00", "dropoff": "N_D03", "agv": "auto"},
                    next=["dock"],
                ),
                FlowNodeDef(
                    id="dock", type=FlowNodeType.DOCK, name="对接产线",
                    params={"target": "machine_A"},
                    next=["end"],
                ),
                FlowNodeDef(id="end", type=FlowNodeType.END, name="完成"),
            ],
        ),
        # 2. 仓储出入库
        FlowDefinition(
            id="tpl_warehouse_inout",
            name="仓储出入库",
            description="货物从入库到货架的完整流程",
            nodes=[
                FlowNodeDef(id="start", type=FlowNodeType.START, name="开始", next=["pickup"]),
                FlowNodeDef(
                    id="pickup", type=FlowNodeType.TRANSPORT, name="取货",
                    params={"pickup": "N_P01", "dropoff": "N_CE", "agv": "auto"},
                    next=["conveyor_transport"],
                ),
                FlowNodeDef(
                    id="conveyor_transport", type=FlowNodeType.WAIT, name="输送线运输",
                    params={"duration": 30},
                    next=["dropoff"],
                ),
                FlowNodeDef(
                    id="dropoff", type=FlowNodeType.TRANSPORT, name="放货到货架",
                    params={"pickup": "N_CX", "dropoff": "N_D01", "agv": "auto"},
                    next=["end"],
                ),
                FlowNodeDef(id="end", type=FlowNodeType.END, name="完成"),
            ],
        ),
        # 3. 循环搬运
        FlowDefinition(
            id="tpl_circular_transport",
            name="循环搬运",
            description="产线A→仓库B→产线A循环搬运",
            loop=True,
            nodes=[
                FlowNodeDef(id="start", type=FlowNodeType.START, name="开始", next=["go_to_warehouse"]),
                FlowNodeDef(
                    id="go_to_warehouse", type=FlowNodeType.TRANSPORT, name="运到仓库",
                    params={"pickup": "N_P00", "dropoff": "N_D03", "agv": "auto"},
                    next=["return_empty"],
                ),
                FlowNodeDef(
                    id="return_empty", type=FlowNodeType.TRANSPORT, name="返回产线",
                    params={"pickup": "N_D03", "dropoff": "N_P00", "agv": "auto"},
                    next=["check_continue"],
                ),
                FlowNodeDef(
                    id="check_continue", type=FlowNodeType.CHECK, name="检查是否继续",
                    params={"condition": "continue_loop != false"},
                    next_on_pass="go_to_warehouse",
                    next_on_fail="end",
                ),
                FlowNodeDef(id="end", type=FlowNodeType.END, name="结束循环"),
            ],
        ),
        # 4. 充电流程
        FlowDefinition(
            id="tpl_charging",
            name="充电流程",
            description="低电量AGV自动充电",
            nodes=[
                FlowNodeDef(id="start", type=FlowNodeType.START, name="开始", next=["check_battery"]),
                FlowNodeDef(
                    id="check_battery", type=FlowNodeType.CHECK, name="检查电量",
                    params={"condition": "battery < 20"},
                    next_on_pass="go_charge",
                    next_on_fail="end",
                ),
                FlowNodeDef(
                    id="go_charge", type=FlowNodeType.TRANSPORT, name="前往充电站",
                    params={"pickup": "current", "dropoff": "N_CH1", "agv": "self"},
                    next=["charging"],
                ),
                FlowNodeDef(
                    id="charging", type=FlowNodeType.WAIT, name="充电中",
                    params={"duration": 120},
                    next=["end"],
                ),
                FlowNodeDef(id="end", type=FlowNodeType.END, name="充电完成"),
            ],
        ),
        # 5. 异常处理
        FlowDefinition(
            id="tpl_error_handling",
            name="异常处理",
            description="AGV故障时的异常处理流程",
            nodes=[
                FlowNodeDef(id="start", type=FlowNodeType.START, name="异常触发", next=["assess"]),
                FlowNodeDef(
                    id="assess", type=FlowNodeType.CHECK, name="评估故障",
                    params={"condition": "error_severity == 'critical'"},
                    next_on_pass="emergency_stop",
                    next_on_fail="retry",
                ),
                FlowNodeDef(
                    id="emergency_stop", type=FlowNodeType.SCRIPT, name="紧急停止",
                    params={"handler": "emergency", "code": "agv.stop()"},
                    next=["notify"],
                ),
                FlowNodeDef(
                    id="retry", type=FlowNodeType.WAIT, name="等待重试",
                    params={"duration": 10},
                    next=["check_retry"],
                ),
                FlowNodeDef(
                    id="check_retry", type=FlowNodeType.CHECK, name="重试检查",
                    params={"condition": "retry_count < 3"},
                    next_on_pass="resume",
                    next_on_fail="notify",
                ),
                FlowNodeDef(
                    id="resume", type=FlowNodeType.SCRIPT, name="恢复执行",
                    params={"handler": "resume", "code": "agv.resume()"},
                    next=["end"],
                ),
                FlowNodeDef(
                    id="notify", type=FlowNodeType.SCRIPT, name="通知运维",
                    params={"handler": "notify", "code": "notify_operator(error_msg)"},
                    next=["end"],
                ),
                FlowNodeDef(id="end", type=FlowNodeType.END, name="处理完成"),
            ],
        ),
    ]


# Singleton
flow_engine = FlowEngine()

# Register standard templates
for template in get_standard_templates():
    flow_engine.register_flow(template)
