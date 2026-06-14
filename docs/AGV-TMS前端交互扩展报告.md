# AGV-TMS 前端交互扩展 — 适配内核架构能力

> **实施日期**: 2026-06-19  
> **目标**: Web 前端交互界面体现算法、协议、能力提升，扩展功能适配 Phase A-D + Phase 5-8 内核架构

---

## 一、新增功能总览

| 新增页面 | 路由 | 体现能力 | 对应 Phase |
|---------|------|---------|-----------|
| **策略与协议** | `/strategy` | 可插拔策略切换 + 调度循环 + 多协议适配器 + VDA5050 | Phase 5 + 7 |
| **3D 数字孪生** | `/digital-twin` | 2.5D 场景渲染 + AGV 3D 模型 + 轨迹动画 + 热力图 | Phase 8 |
| **RL 实验** | `/rl-experiment` | RL 模型状态 + A/B 测试 + 自适应调度 + 分布式监控 | Phase 6 + 8 |

**新增文件**: 4 个 (1 API 服务 + 3 页面)  
**修改文件**: 1 个 (App.tsx 菜单+路由)  
**新增 API 对接**: 19 个后端端点

---

## 二、策略与协议页面 (`/strategy`)

### 三个 Tab 功能

#### Tab 1: 策略管理
- **CostFunction 切换** — 5 种成本函数下拉选择 (distance/time/weighted/energy/priority)
- **Dispatcher 切换** — 3 种分派策略 (greedy/hungarian/mip)
- **Router 切换** — 3 种路由策略 (astar/sipp/dstar)
- **调度循环控制** — 启动/停止按钮 + 实时统计 (分派次数/订单数/重调度数/故障处理数)
- **策略说明** — 可插拔架构/事件驱动/A/B 测试说明

#### Tab 2: 协议适配器
- **适配器统计** — 已注册数/运行中数/管理车辆数
- **适配器管理表** — 4 种适配器 (OPC UA/MQTT/Modbus/VDA5050) 的启动/停止开关
- **车辆路由表** — 多品牌混合调度的 AGV→适配器映射

#### Tab 3: VDA5050
- **Order 构建器** — 输入路径节点 → 生成完整 VDA5050 v2.0 Order JSON
- **JSON 输出** — 实时显示构建结果
- **Schema 查看** — VDA5050 v2.0 完整字段说明
- **实现说明** — 消息类型/字段/动作类型/即时动作

---

## 三、3D 数字孪生页面 (`/digital-twin`)

### 四个 Tab 功能

#### Tab 1: 场景视图
- **2.5D Canvas 渲染** — 俯视图实时渲染地图节点、AGV 位置、路径轨迹
- **统计卡片** — 地图节点数/AGV 数量/轨迹数/活跃 AGV
- **控制按钮** — 刷新/播放暂停/热力图开关
- **AGV 可视化** — 彩色方块 + 电量条 + ID 标签 + 状态标签
- **轨迹渲染** — 虚线路径 + 热力图叠加 (速度强度)

#### Tab 2: AGV 模型
- **3D 模型列表** — AGV ID/状态/位置/电量/速度/载货/动画/颜色
- **状态颜色** — idle(蓝)/moving(绿)/charging(黄)/error(红)

#### Tab 3: 轨迹动画
- **播放控制** — 播放/暂停 + 进度滑块
- **轨迹详情** — 每个 AGV 的点数/总时长/总距离/当前位置
- **逐帧显示** — 当前帧的 AGV 坐标

#### Tab 4: JSON 数据
- **完整场景 JSON** — SceneModel 结构化数据查看 (供 Three.js 渲染)

---

## 四、RL 实验页面 (`/rl-experiment`)

### 四大功能模块

#### 1. RL 模型状态
- 模型加载状态 (已加载/未加载降级)
- 模型类型 (DQN/PPO)
- 模型路径
- PyTorch 可用性提示

#### 2. A/B 测试
- **启动表单** — 测试名称/策略 A/策略 B/流量比例
- **策略选项** — MIP/Hungarian/Greedy/RL
- **结果展示** — 胜者标签 + 样本数 + 各策略指标对比

#### 3. 自适应调度配置
- **4 因子权重** — 距离/等待时间/缓冲区/电量 (进度条可视化)
- **动态调整说明** — 高负载/高拥塞时的自动权重调整逻辑

#### 4. 分布式调度监控
- **Worker 控制** — 启动/停止分布式调度 Worker
- **订单提交** — 提交测试订单到消息队列
- **结果表格** — 订单 ID/结果 ID/分配数/Makespan/完成时间

---

## 五、App.tsx 菜单重构

### 菜单分组
```
系统总览
地图配置
任务管理
AGV监控
输送线配置
──────────
算法引擎
  ├ 算法配置
  ├ 算法评测
  └ RL实验 / A/B测试
──────────
高级功能 (Phase 5-8)
  ├ 策略与协议
  └ 3D 数字孪生
```

### 顶栏增强
- **Phase 5-8 标签** — 蓝色 Tag 标识高级功能
- **动态版本号** — 从后端 `/api/v2/system/info` 获取

---

## 六、API 服务扩展 (`advancedApi.ts`)

新增 19 个 API 函数，对接后端 `/api/v2/advanced/*`：

| Phase | API 函数 | 端点 |
|-------|---------|------|
| 5 | `getStrategyInfo` / `setStrategy` | GET/PUT `/strategy` |
| 5 | `startSchedulerLoop` / `stopSchedulerLoop` / `getSchedulerLoopStatus` | `/scheduler-loop/*` |
| 6 | `submitDistributedOrder` / `getDistributedResults` | `/distributed/*` |
| 6 | `startDistributedWorker` / `stopDistributedWorker` | `/distributed/worker/*` |
| 7 | `buildVda5050Order` / `getVda5050Schema` | `/vda5050/*` |
| 7 | `getAdapters` / `startAdapter` / `stopAdapter` | `/adapters/*` |
| 8 | `get3DScene` / `getAgvTrajectory3D` | `/digital-twin/*` |
| 8 | `startABTest` / `getABTestResult` / `getRLModelStatus` | `/ab-test/*` / `/rl/*` |

---

## 七、验证结果

### Lint 检查
```
✓ advancedApi.ts — 0 errors
✓ StrategyAndProtocol/index.tsx — 0 errors
✓ DigitalTwin/index.tsx — 0 errors
✓ RLExperiment/index.tsx — 0 errors
✓ App.tsx — 0 errors
```

### TypeScript 编译
```
✓ 新增文件全部通过 tsc --noEmit (0 errors)
  (仅 AlgorithmBenchmark 有预存的 tooltipFormatter 警告, 非本次引入)
```

### 前端预览
```
✓ http://localhost:3000/strategy — 策略与协议页面可访问
✓ http://localhost:3000/digital-twin — 3D 数字孪生页面可访问
✓ http://localhost:3000/rl-experiment — RL 实验页面可访问
```

---

## 八、前后端能力映射

| 后端能力 (Phase) | 前端交互 | 交互方式 |
|----------------|---------|---------|
| CostFunction 5 种 (Phase C) | 下拉选择切换 | Select |
| Dispatcher 3 种 (Phase C) | 下拉选择切换 | Select |
| Router 3 种 (Phase C) | 下拉选择切换 | Select |
| SchedulerLoop (Phase D) | 启动/停止 + 统计 | Button + Statistic |
| 4 种适配器 (Phase B+7) | 开关启停 | Switch |
| 多品牌车辆路由 (Phase B) | 路由表展示 | Table |
| VDA5050 完整 Order (Phase 7) | 构建器 + JSON 输出 | Form + pre |
| 3D 场景数据 (Phase 8) | Canvas 2.5D 渲染 | Canvas |
| 轨迹动画 (Phase 8) | 播放/暂停/滑块 | Button + Slider |
| 热力图 (Phase 8) | 开关叠加 | Switch |
| RL 模型状态 (Phase 8) | 状态展示 | Badge + Descriptions |
| A/B 测试 (Phase 8) | 启动 + 结果对比 | Form + Card |
| 自适应权重 (Phase 8) | 进度条可视化 | Progress |
| 分布式 Worker (Phase 6) | 启停 + 结果表 | Button + Table |
| 消息队列 (Phase 6) | 订单提交 + 结果 | Button + Table |
