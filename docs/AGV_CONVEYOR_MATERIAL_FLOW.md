# AGV-输送线物料协同仿真

## 核心概念

**物料在AGV和输送线两种模式之间切换移动**，而非AGV单纯在输送线行驶。

### 完整5阶段流转模型

```
工作站A ──[AGV搬运]──▶ 输送线入口 ──[自动转运]──▶ 输送线出口 ──[AGV接货]──▶ 工作站B
  (阶段1:AGV取货)   (阶段2:放料)   (阶段3:转运)    (阶段4:接货)   (阶段5:送达)
```

## 修改的文件和核心变更

### 1. `scenarios.py` - 场景生成器
- **`conveyor_tasks` 结构增强**: 新增 `agv_pickup_node_id`, `conveyor_entry_node_id`, `conveyor_exit_node_id`, `agv_dropoff_node_id` 四个关键节点
- **`phase` 字段**: 跟踪每个流转任务的当前阶段 (`agv_pickup` → `conveyor_loading` → `conveyor_transit` → `agv_receiving` → `agv_dropoff` → `completed`)
- **`estimated_conveyor_time`**: 预估输送线传输时间

### 2. `simulator.py` - 调度仿真器（主要改动）

#### 新增数据结构
- **`MaterialPhaseEnum`**: 物料流转的6个阶段枚举
- **`MaterialOnConveyor`**: 追踪每件物料在输送线上的状态（material_id, task_id, 进度, 状态）
- **新事件类型**: `MATERIAL_ON_CONVEYOR`, `MATERIAL_PICKUP_FROM_CONVEYOR`, `CONVEYOR_TRANSIT_START/END`

#### 核心仿真逻辑
1. **`_preassign_conveyor_tasks()`**: 初始化时为空闲AGV预分配输送线任务
2. **`_simulate_conveyor_transit()`**: 每步更新输送线上物料的转运进度（考虑速度、负载、排队效应）
3. **`_check_conveyor_loading()`**: 阶段2：AGV到达入口后放下物料，创建 MaterialOnConveyor 对象
4. **`_dispatch_single_receiving_agv()`**: 阶段3→4转换时分配唯一AGV去出口接货（防止重复分配）
5. **`_check_final_dropoff()`**: 阶段5：AGV到达最终目的地后标记任务完成

#### 关键技术细节
- **路径走完立即处理**: 当 AGV 的 path_len<=1 时立即触发阶段检查逻辑，不等待 progress>=1.0
- **task_id 格式兼容**: 正确处理 `conv_TASKID` 和 `conv_receive_TASKID` 两种格式
- **防重复分配**: 通过 `_task_phase` 状态检查和 existing AGV 检查避免多AGV重复接同一任务

### 3. `metrics.py` - 指标评估
已有字段: `conveyor_utilization`, `conveyor_tasks_completed`, `conveyor_jam_count`
新增: `_estimate_makespan()` 已考虑输送线传输时间

## 验证结果

```
场景: 6x8网格, 2个流转任务, 3000步仿真

CTASK-0001 完整生命周期:
  step=   0: [阶段1] AGV-003 前往 N_002_003 取物料
  step=  84: [阶段1→2] 取完物料, 前往输送线入口 N_002_000 放料  
  step= 336: [阶段2→3] 物料放到入口(MAT_0001), 开始转运
  step= 365: [阶段3→4] 物料到达出口N_005_000, AGV前往接货
  step= 376: [阶段4→5] 接走物料, 送往最终目的地
  step= 639: ✅ 任务完成! 物料经 输送线转运→AGV接货→送达目的地
```
