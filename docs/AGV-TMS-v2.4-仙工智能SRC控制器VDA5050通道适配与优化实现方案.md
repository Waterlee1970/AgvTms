# AGV-TMS v2.4 · 结合仙工智能 SRC 控制器的 VDA5050 通道适配与优化实现方案

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v2.4.0 |
| 编制日期 | 2026-09-07 |
| 适用系统 | AGV-TMS v2.4 (Phase 5-8 车辆接入/适配器体系) |
| 适配对象 | 仙工智能 (SEER) SRC 系列核心控制器 |
| 对接标准 | VDA5050 v2.0 (MQTT) |
| 交付形态 | 代码适配器增强 + 方案文档 (本文) |
| 代码位置 | `AgvTms-2.4/backend/app/{protocols,adapters}/`，见 §3.2 |

---

## 1. 背景与目标

AGV-TMS 在"海康 RCS / 极智嘉 RMS 对标分析"路线中完成了第三方调度系统的
HTTP 级适配与协议画像设计。本方案延续该路线，把接入范围扩展到**仙工智能
(SEER) 的核心控制器层 (SRC)**，目标：

1. 在不依赖 SEER 调度平台的前提下，让 AGV-TMS 以**标准 VDA5050 v2.0 通道**
   直连 SRC 控制器驱动的机器人（叉车/AMR/料车等）；
2. 在既有 `Vda5050VehicleAdapter` 上按 SEER 约束做**增强而非新建适配器**，
   复用 Phase 7 的统一指令/路由/状态模型；
3. 补齐实际对接最容易被忽略的四个优化点：
   - **AGVId ↔ serialNumber 解耦映射**（TMS 内部车号 vs SRC 控制器序列号）；
   - **位置初始化/二次定位**（SRC 定位丢失后的上位机复位流程）；
   - **坐标系对齐**（SRC 地图与 TMS 世界地图的原点/旋转/比例差异）；
   - **指令语义修复**（原实现 `MOVE` 存在构造字段错位导致必然失败的问题）。

## 2. 仙工 SRC 控制器协议认知与接入决策

### 2.1 公开可确认的事实

依据仙工智能官方产品资料与开发者帮助中心：

- **SRC 系列核心控制器**（如 SRC-2000 等）是 SEER 自研的运动控制与导航大脑，
  向上可同时对接上层调度与用户系统；
- SRC **原生支持 VDA5050 v2.0**：机器人作为标准 VDA5050 AGV 通过 MQTT
  主题 `uagv/v2/{manufacturer}/{serialNumber}/{messageType}` 与上位机通信，
  支持 order / state / instantAction / connection / visualization 五类消息；
- SRC 同时提供**私有控制器 API（Robokit API）**，采用 TCP request/response
  （机器人作服务端），覆盖电池、里程、IO、运动等控制器消息，属于受控文档，
  需要厂商授权与固件版本匹配；
- VDA5050 的 `manufacturer` 与 `serialNumber` 取值来自 SRC 控制器出厂配置，
  `serialNumber` 通常带厂商序列号前缀。

### 2.2 三条接入路径对比与决策

| 路径 | 开放度 | 标准化 | 与 TMS 复用度 | 结论 |
| --- | --- | --- | --- | --- |
| A. SRC 私有控制器 API（TCP/串口 JSON request-response） | 需授权文档，逐固件版本对齐 | 低 | 低（每车型写编解码） | 不优先 |
| B. SEER 调度平台 OpenAPI（RoboView/RDS） | 需部署其调度软件 | 中 | 中（对标海康/极智嘉做法） | 备选 |
| C. SRC 原生 VDA5050 (MQTT) | 公开标准、SRC 官方支持 | 高 | 高（复用 `Vda5050VehicleAdapter`） | **采用** |

**决策**：采用路径 C——在既有 `Vda5050VehicleAdapter` 上增强，按 SEER 画像
配置下发/接收；私有控制器细节以 `TODO(SEER-field)` 标注并在配置中可校准。

### 2.3 现场接入待核对清单（随文档交付）

| # | 核对项 | 影响 |
| --- | --- | --- |
| 1 | SRC 控制器型号与固件版本是否支持 VDA5050 v2.0 | 决定协议能力 |
| 2 | 控制器出厂 `manufacturer` / `serialNumber` 实际取值 | 决定主题与消息头 |
| 3 | 生产 MQTT broker 地址/端口/账号 | 决定 live 通道 |
| 4 | 控制器地图（SRC map）与 TMS 地图间原点/旋转/比例标定 | 决定坐标系对齐参数 |
| 5 | 上位机侧位置初始化是否被固件允许（含安全策略） | 决定复位流程启用方式 |
| 6 | 故障字典（errorType/errorReferences 语义） | 决定错误翻译表质量 |

## 3. 方案设计

### 3.1 分层架构

```
TMS 调度/监控 (统一 VehicleCommand / VehicleStatus)
   │
   ▼
Vda5050VehicleAdapter (Phase 7 适配器，新增 vendor 画像支持)
   │  AGVId↔serialNumber · 坐标系对齐 · 位置初始化 · 错误翻译
   ▼
app/protocols/seer_src.py  ── SEER-SRC × VDA5050 厂商画像库 (纯函数/模型)
   ▼
app/protocols/vda5050.py + agv_simulator.py  (VDA5050 消息模型 / 车队仿真)
   ▼
MQTT Broker  (live: uagv/v2/... , 现场部署)  |  进程内仿真 (演示/测试)
```

### 3.2 文件改动清单

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/app/protocols/seer_src.py` | 新增 | SEER 厂商画像库：ID 映射、坐标变换、位置初始化报文、约束校验、错误翻译、动作映射 |
| `backend/app/protocols/agv_simulator.py` | 增强 | 仿真器支持初始位姿/航向、装载/卸货、充电回充、`start/resume/pause/stop/cancelOrder` 即时动作、厂商字段 |
| `backend/app/adapters/vda5050_vehicle_adapter.py` | 重构增强 | 支持 `vendor="seer"` 画像、本地车队自管、修复 `MOVE` 字段错位、补全指令语义、`get_profile_info()` |
| `backend/tests/test_seer_vda5050.py` | 新增 | 28 个单元测试（画像/变换/报文/仿真/适配器/管理器全链路） |
| `docs/AGV-TMS-v2.4-仙工智能SRC控制器VDA5050通道适配与优化实现方案.md(.docx)` | 新增 | 本文档 |

兼容性：不传 `vendor` 时行为与旧版一致（绑定全局 `fleet_simulator`），
存量调用不受影响。

### 3.3 SEER 厂商画像能力总览

| 能力 | 入口 | 说明 |
| --- | --- | --- |
| 画像识别 | `SeerVda5050Profile.from_vendor(vendor)` | 别名含 `seer/src/seer-src/src2000/仙工` 等 |
| AGVId↔序列号 | `agv_id_to_serial` / `serial_to_agv_id` | 前缀 `SRC-`，可配置，双向往返 |
| 坐标系对齐 | `CoordTransform` | SRC 地图→TMS 地图：平移/旋转(度)/缩放 |
| 位置初始化报文 | `build_init_position_order` / `..._instant_action` | `order`（默认）或 `instantAction` 两种模式 |
| 合规校验 | `validate_message_for_seer` | 下发前检查 manufacturer/version/headerId/序列号/坐标/动作集合 |
| 错误翻译 | `translate_error_states` | errorStates → 稳定错误码 + 中文描述 |
| 主题族 | `seer_topics` | 按画像拼装五类 VDA5050 主题 |
| 动作映射 | `SEER_NODE_ACTION_MAP` | LOAD/UNLOAD/CHARGE/INIT_POSITION ↔ pickPosition/dropPosition/charge/initPosition |

## 4. 关键实现细节

### 4.1 AGVId ↔ serialNumber 解耦映射

SEER 控制器序列号是机器人"物理身份证"，而 TMS 内部车号是业务路由键。
适配器维护 `vehicle_id → serialNumber` 映射，所有命令、状态、主题都经映射
解耦，避免把业务车号泄漏到 MQTT 消息头：

```
TMS 内部车号          SRC 控制器序列号           VDA5050 header/topic
  VEH-1    ──►        SRC-8F00A1     ──►   uagv/v2/SEER/SRC-8F00A1/order
  SEER-01  ──►        SRC-5F5F5F
```

已有序列号（以 `SRC-` 开头）原样透传；`serial_to_agv_id` 反向映射保证
统一状态里的 `vehicle_id` 与调度路由表一致。

### 4.2 SRC 地图 ↔ TMS 世界地图 坐标系对齐

两套地图常存在原点差、旋转差、比例差，若不做补偿，位置初始化与路径坐标
会整体偏置。实现为仿射变换：

```
p_tms = R(rotation_deg) · (scale · p_src) + (offset_x, offset_y)
```

- `CoordTransform.tms_to_src()` 用于把 TMS 坐标写入 VDA5050 order/初始化报文；
- `CoordTransform.src_to_tms()` 用于状态回传时把 SRC 位姿翻译为 TMS 世界坐标；
- 航向角同步补偿（`theta_tms = theta_src + rotation_deg`）；
- 未配置变换时为恒等变换（零开销）。

### 4.3 位置初始化 / 二次定位

`INIT_POSITION` 语义：SRC 定位丢失或首次上线时，上位机下发目标位姿
（可指定 `coordinate_frame: vda|tms`），适配器执行两步：

1. `sim.set_pose(x, y, theta)` 让通道内位姿生效（仿真即落地）；
2. 按画像 `init_position_mode` 构造**位置初始化报文**（默认 `order`，
   单节点 + `initPosition` 动作；备选 `instantAction`），
   报文体进入 `CommandResult.data.payload`，供 live 通道使用或审计。

> 现场注意：SRC 固件是否允许上位机侧位置复位、是否要求安全确认，
> 需在 §2.3 清单中确认后再启用自动化。

### 4.4 统一指令 ↔ VDA5050 映射矩阵（增强后）

| VehicleCommand | VDA5050 语义 | 说明（本次增强） |
| --- | --- | --- |
| `MOVE` | 单点 `order` | **修复**原构造字段错位问题；坐标来源=参数 x/y → `node_positions` 登记表 → 明确报错引导 |
| `INIT_POSITION` | `order`(initPosition) / `instantAction` | 新增，含坐标系转换 |
| `STOP` | `instantAction: stop` | 停车即冻结（`paused=True, driving=False`） |
| `RESUME` | `instantAction: start/resume` | 解除冻结 |
| `CANCEL_TASK` | `instantAction: cancelOrder` | 清空当前 order |
| `CHARGE` | 充电桩 `order`(charge 动作) / 原地充电 | 新增：可选 `station` 定点充电 |
| `LOAD/PICKUP` | `pickPosition` | 落地装载状态到状态模型 |
| `UNLOAD/DROPOFF` | `dropPosition` | 落地卸货状态 |
| `transport_order` | 完整多节点 `order` | 归一化节点/边，坐标变换可选 |

### 4.5 VDA5050 主题族（按画像拼装）

```
uagv/v2/{manufacturer}/{serialNumber}/order
uagv/v2/{manufacturer}/{serialNumber}/state
uagv/v2/{manufacturer}/{serialNumber}/connection
uagv/v2/{manufacturer}/{serialNumber}/instantAction
uagv/v2/{manufacturer}/{serialNumber}/visualization
```

`topic_base` 可配置，兼容 SEER 项目常用的自有主题前缀；live 部署时建议
`$share/agvtms/...` 订阅组。

### 4.6 状态统一与错误翻译

`get_status()` 把仿真/上报状态归一为 `VehicleStatus`：

- 状态机：error → charging → offline → moving → loading → idle；
- 位姿：SRC 坐标经变换输出 TMS 坐标，航向转角度；
- 错误：`translate_error_states` 输出**跨运行稳定的错误码**（zlib.crc32 派生，
  便于监控聚合）+ 中文描述；故障字典词条按官方文档校准（`TODO(SEER-field)`）。

## 5. 启动配置示例（API 层）

`POST /api/v2/advanced/adapters/start`，请求体示例：

```json
{
  "adapter_name": "vda5050",
  "config": {
    "vendor": "seer",
    "manufacturer": "SEER",
    "vehicle_ids": ["VEH-1", "VEH-2"],
    "vehicle_configs": {
      "VEH-1": { "serial": "SRC-8F00A1", "x": 1.0, "y": 2.0, "theta": 0.0, "battery": 88.0 },
      "VEH-2": { "serial": "SRC-8F00A2", "x": 10.0, "y": 10.0, "theta": 90.0 }
    },
    "node_positions": { "N042": { "x": 5.0, "y": 5.0 } },
    "mode": "simulation",
    "init_position_mode": "order",
    "map_transform": { "offset_x": 10.0, "offset_y": 20.0, "rotation_deg": 0.0, "scale": 1.0 },
    "topic_base": "uagv/v2"
  }
}
```

启动后可通过 `GET /api/v2/advanced/adapters`（已注册 + 运行信息）查看运行中的
适配器；画像详情（车辆映射 / 主题族 / 坐标变换）可调用
`Vda5050VehicleAdapter.get_profile_info()` 获取，便于"策略与协议/车辆接入"
类页面展示。

## 6. 验证结果

### 6.1 本次新增测试（全部通过）

`pytest tests/test_seer_vda5050.py` → **28 passed**

覆盖类别（示例）：

| 类别 | 测试点 |
| --- | --- |
| 画像与 ID 映射 | vendor 别名识别、前缀补齐/透传、双向往返 |
| 坐标系对齐 | 平移/旋转90°/缩放/逆变换往返/配置解析 |
| 报文构造 | 主题族、位置初始化 order/instantAction、合规校验告警、错误翻译稳定性 |
| 仿真器增强 | set_pose、装载/充电标志、stop/start/resume/cancel、厂商字段 |
| 适配器增强 | SEER 连接与车辆映射、通用兼容、MOVE 坐标校验与下发、位置初始化落地、装载/充电指令、transport order、TMS 坐标翻译 |
| 管理器链路 | 通过 `AdapterManager` 启动 `vda5050`(seer) → 路由 → 统一指令 → 状态 |

### 6.2 回归情况

- 既有 VDA5050/适配器相关契约测试不受影响；
- 存量测试中 mqtt/opcua 接口契约与 wk34 基础设施用例（kafka/influx/编译环境项）
  的失败为**改动前已存在**的问题，与本次 SEER 通道改动无关联，未在本次范围内处理。

## 7. 现场接入指引与实施任务规划

### 7.1 接入 Checklist

1. 确认 SRC 型号/固件 VDA5050 支持与 broker 参数（§2.3）；
2. 标定 SRC 地图与 TMS 地图：记录 2+ 公共点求旋转/平移/比例（或最小二乘），
   填入 `map_transform`；
3. 汇总在役车辆清单：TMS 车号、SRC 序列号、车位姿 → `vehicle_configs`；
4. `mode: "simulation"` 试跑命令/状态 → 核对状态机与错误翻译；
5. 切换 live（外接 broker + 工厂地图）小范围试点，观察位置初始化与充电指令；
6. 校准错误字典，将厂商文档词条回填 `SEER_ERROR_REFERENCE_TABLE`。

### 7.2 实施任务规划（建议拆分）

| # | 任务 | 产出 | 优先级 |
| --- | --- | --- | --- |
| 1 | SEER 厂商画像库与单元测试 | `seer_src.py` + 用例 | P0（已完成） |
| 2 | 适配器 SEER 画像增强与自测 | `vda5050_vehicle_adapter.py` | P0（已完成） |
| 3 | 仿真器增强（初始位姿/装载/充电/即时动作） | `agv_simulator.py` | P0（已完成） |
| 4 | live MQTT 通道接通（broker/订阅组/断线重连） | 现场联调 | P1 |
| 5 | 前端协议目录与车辆类型页展示 SEER 画像 | 前端配置页 | P1 |
| 6 | 错误字典/故障码按官方文档校准 | 配置文件 | P2 |
| 7 | 接入实际车型（叉车/AMR）试点与调优 | 现场报告 | P2 |

## 8. 风险与边界说明

- SRC 私有控制器 API（Robokit，TCP/串口）不在本实现范围，接入需厂商授权文档；
- VDA5050 位置初始化是否可被固件接受取决于现场固件版本与安全策略；
- `manufacturer` 实际出厂取值与序列号格式需以现场控制器为准，均可通过配置覆盖；
- 充电/装载动作语义基于 VDA5050 标准动作，具体到 SEER 车型的 actionType
  参数以现场联调为准。

## 9. 参考来源

- SEER 官网产品资料：SRC 系列核心控制器、RoboView 调度（开放能力说明）；
- SEER 帮助中心（TCP request/response 控制器 API 说明、VDA5050 上位机接入说明）；
- VDA5050 v2.0 标准（German Automotive Industry Association 发布）。
