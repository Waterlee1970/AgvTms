# AGV-TMS v2.4 技术优势强化、稳定可靠性提升与测试体系建设方案

> 版本：v2.4 分支（commit `51837b7f`）  
> 编制日期：2026-07-15  
> 范围：后端服务、前端应用、工业通信层、算法引擎、数字孪生、DevOps 与测试体系

---

## 1. 现状诊断与核心差距

### 1.1 现状总览

| 维度 | 当前状态 | 成熟度 |
|------|---------|--------|
| 架构 | 前后端分离，FastAPI + React，PostgreSQL + Redis | ★★★★☆ |
| 算法引擎 | V1（ACO/SA/NLP）+ V2（MIP/A*/SIPP/CBS/RL/预测/评测）双轨并行 | ★★★★☆ |
| 工业通信 | MQTT 3.1.1/5.0、VDA5050、Sparkplug B、OPC UA、Modbus TCP 已接入 | ★★★☆☆ |
| 稳定性 | 具备断路器、重试、限流、健康聚合、主备切换框架 | ★★★☆☆ |
| 可观测性 | 结构化日志、Prometheus 指标、OpenTelemetry 追踪（可选） | ★★★☆☆ |
| 单元测试 | 后端 pytest 32 个测试文件，前端 vitest + Playwright | ★★★☆☆ |
| 压力测试 | k6_load_test.py 等少数脚本，未成体系 | ★★☆☆☆ |
| 工程化 | requirements.txt 与代码依赖不一致，大量可选导入降级 | ★★☆☆☆ |

### 1.2 关键技术债务

1. **依赖管理不一致**：代码中使用了 `aiokafka`、`influxdb-client`、`prometheus-client`、`structlog`、`tenacity` 等库，但 `backend/requirements.txt` 未完整声明，生产环境易出现 `ImportError` 降级。
2. **可选导入过多**：核心能力（日志、监控、InfluxDB、Kafka）大量使用 `try/except ImportError` 降级，导致功能缺失时静默失败，难以排查。
3. **测试覆盖不均衡**：算法与适配器测试较全，但核心服务层（schedule_service、task_service）、数字孪生、WebSocket 长连接、MQTT 桥接压力场景覆盖不足。
4. **压力测试未成体系**：缺少基线指标、SLI/SLO 定义、持续性能回归机制。
5. **配置与密钥管理粗放**：多处依赖环境变量，缺乏配置校验、敏感信息脱敏、运行时热更新能力。
6. **前端测试薄弱**： vitest 已配置，但实际测试文件稀少，核心组件与 3D 数字孪生缺少单元/集成测试。
7. **缺少混沌与容灾演练**：未引入故障注入、网络分区、Broker 宕机、数据库主从切换等演练机制。

---

## 2. 强化技术优势方案

### 2.1 算法引擎深度优化

| 方向 | 目标 | 关键动作 |
|------|------|----------|
| 算法评测体系产品化 | 将 `backend/app/algorithms/v2/evaluator/` 升级为可配置评测平台 | 支持自定义 KPI、A/B 测试、历史对比、可视化报告 |
| V1/V2 调度融合 | 统一调度入口，按场景自动选择算法 | 建设策略路由（Rule-based + ML 推荐），避免双轨并行带来的维护负担 |
| 实时动态重调度 | 支持 500+ 任务/分钟的动态插入与重规划 | 引入增量规划（Incremental Planning）与热路径缓存 |
| 电力计量行业模型 | 建立计量表位、检定工位、缓存区、RGV/AGV 协同的业务模型 | 与算法层解耦，形成行业知识库 |
| 数字孪生联动 | 算法结果实时驱动 3D 仿真 | 统一事件协议，降低仿真与实车间延迟至 < 200ms |

### 2.2 工业通信协议栈补强

| 协议 | 当前状态 | 强化目标 |
|------|---------|----------|
| MQTT | v2.4 已大幅增强（VDA5050、Sparkplug B、桥接、ACL、持久化） | 增加 QoS 一致性校验、百万级消息压测、Broker 集群切换 |
| Modbus TCP | 已有适配器 | 补齐 Modbus RTU over TCP、Profinet/OPC UA 统一抽象、PLC 故障码映射 |
| VDA5050 | topic 完整 | 实现订单状态机完整校验、即时动作（InstantAction）并发控制 |
| OPC UA | 已有适配器 | 增加订阅组管理、断线续传、证书双向认证 |
| Kafka | 代码存在但依赖缺失 | 补齐 requirements、事件 schema 治理、死信队列（DLQ） |

### 2.3 数据平台与智能决策

- **时序数据基座**：补齐 InfluxDB 依赖，统一 AGV 轨迹、电池、设备状态写入规范，建立 retention policy 与连续查询。
- **实时计算层**：引入 Kafka Streams / Flink（轻量版）处理 AGV 状态聚合、异常检测。
- **预测性维护**：基于 `predictive/` 模块扩展电池衰减、关键部件寿命、轨道拥堵预测模型。
- **AI 决策闭环**：RL 调度器与真实环境数据闭环，建立在线学习与离线仿真的沙箱机制。

### 2.4 前端与数字孪生体验升级

- **组件库化**：将 Ant Design 业务组件沉淀为内部组件库，提升复用率。
- **3D 性能优化**：Three.js 场景 LOD、实例化渲染、WebWorker 加载，支撑 1000+ 设备同时在线。
- **低代码编排**：React Flow 流程图支持拖拽生成任务模板，降低业务配置门槛。

---

## 3. 系统稳定可靠性提升方案

### 3.1 韧性工程（Resilience Engineering）

| 能力 | 当前实现 | 提升动作 |
|------|---------|----------|
| 断路器 | `CircuitBreaker` 类 | 与 `tenacity` 统一，支持半开状态按比例放行、按异常类型区分熔断策略 |
| 重试 | `retry` 装饰器 | 引入抖动（jitter）、超时预算（timeout budget）、幂等键校验 |
| 限流 | 本地令牌桶 | 增加分布式 Redis 限流、按 API/用户/设备维度限流 |
| 降级 | Fallback 链 | 建立全局降级开关中心，支持手动与自动触发，记录降级轨迹 |
| 隔离 | 无 | 引入舱壁模式（Bulkhead），隔离算法计算、设备通信、WebSocket 推送线程池 |

### 3.2 可观测性体系

- **日志**：统一 JSON 结构化日志，全链路 Trace ID / Span ID 注入，敏感字段脱敏。
- **指标**：补齐 Prometheus 依赖，定义核心 SLI（任务吞吐量、AGV 利用率、API P99、MQTT 消息延迟）。
- **链路追踪**：OpenTelemetry 接入 FastAPI、SQLAlchemy、Redis、MQTT、Kafka 调用链。
- **告警**：基于 Grafana Alerting 配置多级别告警（P0：调度中断；P1：单设备离线；P2：性能衰减）。
- **健康检查**：完善 `/health`、`/ready`、`/live` 语义，聚合 DB、Redis、MQTT、Kafka、外部 PLC 健康状态。

### 3.3 数据一致性与容灾

- **数据库**：引入读写分离、连接池监控、慢查询告警、定期 `pg_dump` 备份 + PITR。
- **Redis**：启用 AOF + RDB 持久化，部署 Sentinel/Cluster，避免缓存单点。
- **消息持久化**：MQTT 离线消息、Kafka 多副本、InfluxDB  retention 策略。
- **分布式锁**：基于 Redlock 保证任务分配、资源抢占的一致性。
- **多活/异地灾备**：制定 RPO < 5min、RTO < 30min 的目标方案，关键状态跨区同步。

### 3.4 配置与发布稳定性

- **配置中心**：引入 Consul / Nacos / etcd 管理运行时配置，支持热更新。
- **环境一致性**：统一 Docker Compose、K8s、本地开发环境，使用 Helm Chart 管理多环境发布。
- **蓝绿/金丝雀发布**：K8s Ingress 按权重灰度，配合健康检查自动回滚。
- **Secrets 管理**：Vault / Sealed Secrets 管理数据库密码、MQTT 证书、API Key。

### 3.5 安全加固

- **通信安全**：MQTT/OPC UA 强制 TLS 1.3 + 双向证书；API 启用 OAuth2 / JWT + RBAC。
- **输入校验**：所有外部接口启用 Pydantic v2 严格校验与防护（SQL 注入、 oversized payload）。
- **安全测试**：引入 SAST、依赖漏洞扫描（Bandit、Safety、Trivy）。

---

## 4. 单元测试与压力测试提升方案

### 4.1 当前测试体系问题

- 后端测试文件 32 个，但缺少统一覆盖率基线。
- 前端 vitest 配置齐全，实际测试文件稀少。
- 压力测试脚本孤立，未与 CI 集成。
- 缺少 Mock Server 与契约测试。
- 测试标记（markers）未充分利用，回归测试效率低。

### 4.2 单元测试提升计划

#### 4.2.1 后端单元测试

| 模块 | 目标覆盖率 | 重点测试场景 |
|------|-----------|--------------|
| API 路由层 | 85% | 参数校验、权限控制、异常响应、幂等性 |
| Services 核心逻辑 | 80% | task_service、schedule_service、vehicle_service 状态机 |
| 算法模块 | 80% | ACO/SA/MIP/A*/CBS/RL 边界条件与收敛性 |
| 适配器层 | 85% | MQTT/OPC UA/Modbus/EthernetIP 协议编解码、断线重连 |
| 韧性组件 | 90% | 断路器状态转换、限流阈值、降级链 |
| 数字孪生 | 75% | WebSocket 状态同步、3D 场景序列化 |

**关键动作**：

1. 完善 `conftest.py`：增加 `mock_kafka`、`mock_influxdb`、`mock_opcua_server`、`mock_plc`。
2. 统一使用 `AsyncMock` 与 `pytest-asyncio`，避免真实网络/数据库依赖。
3. 引入 `factory-boy` / `pydantic-factories` 生成海量测试数据。
4. 制定“每个 bug 必须附带回归测试”的规约。

#### 4.2.2 前端单元测试

| 模块 | 目标覆盖率 | 重点场景 |
|------|-----------|----------|
| 通用组件 | 80% | 表单、表格、图表、按钮交互 |
| 状态管理（Zustand） | 75% | 任务状态、车辆状态、告警状态流转 |
| API 服务层 | 80% | Axios 拦截器、错误处理、重试 |
| 数字孪生 3D | 60% | 场景加载、对象选择、相机控制 |
| 流程图编排 | 75% | 节点拖拽、连线校验、模板保存 |

**关键动作**：

1. 使用 `@testing-library/react` + `jsdom` 覆盖主要页面。
2. 对 Three.js 场景使用 `react-three-fiber` 的测试渲染器。
3. 建立 MSW（Mock Service Worker）统一拦截后端 API。

#### 4.2.3 契约测试

- 使用 Pact 或 OpenAPI-driven 测试，保证前后端、TMS 与 WMS/MES/ERP 接口契约稳定。
- 将 OpenAPI schema 作为 CI 门禁，禁止未通过 schema 校验的提交。

### 4.3 集成测试提升计划

| 范围 | 工具 | 内容 |
|------|------|------|
| API 集成 | pytest + TestClient (httpx) | 覆盖所有 REST/WS 端点，验证状态码与数据一致性 |
| 数据库集成 | pytest + testcontainers | PostgreSQL、Redis 真实容器，验证迁移与事务 |
| MQTT 集成 | mosquitto + pytest-asyncio | 发布/订阅、QoS、保留消息、遗嘱消息 |
| OPC UA 集成 | python-opcua test server | 节点读写、订阅、断线重连 |
| Kafka 集成 | testcontainers-kafka | 生产者/消费者、Consumer Group Rebalance |
| 端到端 | Playwright | 关键用户旅程：登录 → 创建任务 → 调度监控 → 告警处理 |

### 4.4 压力测试与性能测试体系

#### 4.4.1 性能基线定义

| 指标 | 目标值 | 说明 |
|------|--------|------|
| API P99 延迟 | < 200ms | 任务创建、状态查询 |
| 并发任务调度 | ≥ 500 任务/分钟 | 混合 AGV/RGV/输送线 |
| MQTT 消息吞吐 | ≥ 10,000 msg/s | 单 Broker，1000+ AGV 在线 |
| WebSocket 并发 | ≥ 5,000 连接 | 数字孪生监控端 |
| 调度算法响应 | < 1s | 100 节点、50 AGV 场景 |
| 数据库连接池 | ≤ 80% 使用率 | 峰值下 |

#### 4.4.2 压力测试工具链

| 工具 | 用途 |
|------|------|
| Locust / k6 | HTTP/WebSocket API 压力测试 |
| mqtt-stresser / emqtt_bench | MQTT Broker 压力测试 |
| pytest-benchmark / pytest-stress | Python 算法与函数级基准测试 |
| Grafana k6 Cloud / self-hosted | 压测执行与报告 |
| JMeter / Gatling | 复杂场景与 ERP/MES 对接模拟 |

#### 4.4.3 压力测试场景设计

1. **正常负载基线测试**：按设计容量的 50%、80%、100% 运行 30 分钟，采集 SLI。
2. **峰值冲击测试**：3 分钟内突增至 3 倍正常流量，观察限流、队列、降级行为。
3. **长时间 soak 测试**：72 小时稳定运行，检测内存泄漏、连接泄漏、日志膨胀。
4. **断网/故障注入**：
   - MQTT Broker 单节点宕机
   - PostgreSQL 主从切换
   - Redis 节点故障
   - 算法服务 OOM 重启
   - 50% AGV 同时离线
5. **混合工业协议压测**：同时运行 MQTT + OPC UA + Modbus 设备，验证 AdapterManager 调度稳定性。

#### 4.4.4 性能回归机制

- 每次 PR 合并前执行基准测试（smoke）。
- 每周执行全量性能测试，生成趋势图。
- 性能衰减超过 10% 自动阻塞发布。

### 4.5 测试基础设施

- **CI/CD 集成**：
  - GitHub Actions / GitLab CI 增加 `test-unit`、`test-integration`、`test-e2e`、`test-performance` 阶段。
  - 单元测试 < 5min；集成测试 < 15min；E2E < 30min；压力测试按周执行。
- **测试环境管理**：
  - 使用 Docker Compose 一键拉起 `test-stack`（DB、Redis、MQTT、Kafka、InfluxDB）。
  - 测试数据使用 `factory-boy` + 迁移脚本初始化，每次测试后清理。
- **覆盖率门禁**：
  - 新增代码行覆盖率 ≥ 70%。
  - 核心模块（services、adapters、algorithms、resilience）≥ 80%。
- **测试报告**：
  - pytest-html、Allure、Coverage HTML 报告自动上传。
  - 压力测试报告包含 Latency Distribution、Throughput Trend、Error Rate、Resource Utilization。

---

## 5. 重点任务计划

### 5.1 总体路线图

| 阶段 | 周期 | 主题 | 主要产出 |
|------|------|------|----------|
| Phase 1 | 4 周 | 工程化还债与测试基线 | 依赖锁定、CI 门禁、单元测试覆盖率达到 60% |
| Phase 2 | 6 周 | 稳定性与韧性加固 | 统一韧性框架、可观测性上线、容灾演练 |
| Phase 3 | 6 周 | 性能与压力测试体系 | 性能基线、压力测试平台、回归机制 |
| Phase 4 | 8 周 | 技术优势深化 | 算法融合、协议栈补齐、数字孪生升级 |
| Phase 5 | 持续 | 持续运营与优化 | 每周性能回归、每季度灾备演练、SLI/SLO 运营 |

### 5.2 Phase 1：工程化还债与测试基线（第 1-4 周）

| 周 | 任务 | 负责人 | 验收标准 |
|----|------|--------|----------|
| 1 | 梳理并补齐 `requirements.txt` 与 `requirements-dev.txt`；移除冗余可选导入，核心依赖强制声明 | 后端架构 | `pip install` 后无 ImportError 降级 |
| 1 | 引入 `pip-tools` / `poetry` 锁定依赖版本 | 后端架构 | 生成 `requirements.lock` |
| 2 | 建立后端测试目录规范与命名约定；补充缺失的 mock fixtures | 测试工程师 | conftest 覆盖所有外部依赖 |
| 2 | 为 schedule_service、task_service、vehicle_service 编写单元测试 | 后端开发 | 核心服务覆盖率 ≥ 60% |
| 3 | 为 MQTT/OPC UA/Modbus 适配器补齐单元测试 | 工业通信组 | 适配器覆盖率 ≥ 70% |
| 3 | 前端补充 vitest 基础测试与 MSW 配置 | 前端开发 | 核心页面至少 1 个测试 |
| 4 | 接入 CI（GitHub Actions/GitLab CI），配置单元测试、lint、类型检查门禁 | DevOps | 每次 PR 自动执行并产出报告 |
| 4 | 建立代码覆盖率基线（pytest-cov + vitest coverage） | 测试工程师 | CI 中展示覆盖率趋势 |

### 5.3 Phase 2：稳定性与韧性加固（第 5-10 周）

| 周 | 任务 | 负责人 | 验收标准 |
|----|------|--------|----------|
| 5 | 统一韧性框架：整合断路器、重试、限流、降级为装饰器/上下文管理器 | 后端架构 | 全系统统一调用方式 |
| 5 | 引入分布式限流（Redis Token Bucket） | 后端架构 | 压测验证 10k req/s 限流准确 |
| 6 | 完善健康检查 `/health`、`/ready`、`/live` 与聚合器 | 后端开发 | K8s probe 可识别各组件状态 |
| 6 | 补齐 Prometheus 依赖与核心指标埋点 | 可观测组 | Grafana 可查看核心 SLI |
| 7 | OpenTelemetry 全链路追踪接入 | 可观测组 | 关键接口链路可追踪 |
| 7 | 日志脱敏与审计日志规范 | 安全组 | 敏感字段不出现在日志 |
| 8 | 数据库连接池监控、慢查询告警、备份策略 | DBA/DevOps | 备份可恢复演练通过 |
| 8 | Redis Sentinel/Cluster 部署方案 | DevOps | 单节点故障不影响服务 |
| 9 | Kafka/InluxDB 依赖补齐与 DLQ 实现 | 后端开发 | 事件不丢失 |
| 9 | 配置中心选型与接入（Nacos/etcd） | 架构组 | 配置热更新 ≤ 10s |
| 10 | 首次容灾演练：模拟 DB 主从切换、MQTT Broker 故障 | SRE | RTO/RPO 达到阶段目标 |

### 5.4 Phase 3：性能与压力测试体系（第 11-16 周）

| 周 | 任务 | 负责人 | 验收标准 |
|----|------|--------|----------|
| 11 | 定义 SLI/SLO，建立性能基线文档 | 架构+测试 | 基线通过评审 |
| 11 | 部署 Locust/k6 压力测试环境 | 测试工程师 | 可运行基础压测 |
| 12 | 设计并实施 API 压力测试场景 | 测试工程师 | 产出 P99/吞吐量/错误率报告 |
| 12 | MQTT Broker 压力测试（10k msg/s） | 工业通信组 | Broker 集群切换无消息丢失 |
| 13 | 算法性能基准测试（pytest-benchmark） | 算法组 | 100 节点场景 < 1s |
| 13 | WebSocket 并发测试（5k 连接） | 前端+后端 | 数字孪生稳定 |
| 14 | 72 小时 soak 测试与内存泄漏检测 | SRE | 无内存/连接泄漏 |
| 14 | 故障注入工具链（Chaos Mesh / toxiproxy） | SRE | 可自动注入网络延迟/分区 |
| 15 | 性能测试与 CI 集成（周度回归） | DevOps | 每次报告自动归档 |
| 15 | 性能衰减自动门禁（>10% 阻塞发布） | DevOps | CI 配置生效 |
| 16 | 全链路压测演练：模拟 1000 AGV 在线 | 全团队 | 达到设计容量目标 |

### 5.5 Phase 4：技术优势深化（第 17-24 周）

| 周 | 任务 | 负责人 | 验收标准 |
|----|------|--------|----------|
| 17-18 | V1/V2 调度融合与策略路由 | 算法组 | 单一调度入口，按场景自动选择 |
| 17-18 | 算法评测平台产品化 | 算法组 | 支持 A/B 测试与可视化 |
| 19-20 | Modbus RTU over TCP、Profinet 抽象层 | 工业通信组 | 新 PLC 接入周期 < 1 天 |
| 19-20 | PLC 故障码映射与诊断 | 工业通信组 | 故障可定位到具体工位 |
| 21-22 | 电力计量行业业务模型库 | 业务组 | 计量、检定、缓存区模型上线 |
| 21-22 | 预测性维护模型增强 | 算法组 | 电池/拥堵预测准确率 ≥ 80% |
| 23-24 | 数字孪生性能优化（LOD、实例化） | 前端组 | 1000+ 设备 60fps |
| 23-24 | 低代码任务模板编排 | 前端组 | 业务人员可配置任务模板 |

### 5.6 Phase 5：持续运营（长期）

| 频率 | 活动 | 负责人 |
|------|------|--------|
| 每日 | 查看核心 SLI 仪表盘、处理 P0/P1 告警 | SRE |
| 每周 | 执行性能回归测试，更新趋势图 | 测试工程师 |
| 每两周 | 代码覆盖率复盘，补充缺失测试 | 测试工程师 |
| 每月 | 依赖漏洞扫描与安全补丁 | 安全组 |
| 每季度 | 全链路容灾演练与复盘 | SRE |
| 每年 | 压测容量规划与架构升级 | 架构组 |

---

## 6. 关键指标与成功标准

| 类别 | 指标 | 当前值 | 目标值（6 个月） |
|------|------|--------|-----------------|
| 测试 | 后端单元测试覆盖率 | ~45% | ≥ 75% |
| 测试 | 前端单元测试覆盖率 | < 20% | ≥ 60% |
| 测试 | 集成测试通过率 | 不稳定 | ≥ 95% |
| 测试 | 压力测试自动化率 | 0% | ≥ 80% |
| 稳定性 | 生产环境可用性 | 未统计 | ≥ 99.9% |
| 稳定性 | P0 故障平均恢复时间（MTTR） | 未统计 | < 15min |
| 性能 | API P99 延迟 | 未基线 | < 200ms |
| 性能 | 调度吞吐量 | 未基线 | ≥ 500 任务/分钟 |
| 工程化 | requirements 与代码一致率 | ~60% | 100% |
| 工程化 | CI 门禁通过率 | 未接入 | ≥ 90% |

---

## 7. 风险与应对

| 风险 | 影响 | 应对措施 |
|------|------|----------|
| 业务需求挤压技术债偿还时间 | 计划延期 | 将 20% 迭代带宽固定用于工程化与测试 |
| 压力测试环境资源不足 | 无法真实模拟 | 使用云弹性资源，按周预约 |
| 依赖升级引入兼容性问题 | 功能回退 | 使用依赖锁定 + 灰度发布 + 自动化兼容性测试 |
| 算法重构影响现有业务 | 调度异常 | 保留 V1 兜底，A/B 验证后再切换 |
| 团队测试能力不足 | 测试质量差 | 引入测试教练、代码评审强化测试用例 |

---

## 8. 附录：推荐工具链清单

| 类别 | 工具 | 说明 |
|------|------|------|
| 后端测试 | pytest、pytest-asyncio、pytest-cov、pytest-xdist、pytest-benchmark、factory-boy | 单元与基准测试 |
| 前端测试 | vitest、@testing-library/react、jsdom、MSW、Playwright | 组件与 E2E 测试 |
| 集成测试 | testcontainers、httpx、aiomqtt、python-opcua | 容器化集成 |
| 压力测试 | k6、Locust、mqtt-stresser、emqtt_bench、Gatling | 多协议压测 |
| 可观测性 | Prometheus、Grafana、OpenTelemetry、Loki/ELK、Jaeger | 指标、日志、追踪 |
| 韧性 | tenacity、circuitbreaker、pybreaker、Chaos Mesh | 熔断与混沌 |
| 依赖管理 | poetry、pip-tools | 依赖锁定 |
| CI/CD | GitHub Actions、GitLab CI、ArgoCD | 持续集成与发布 |
| 安全 | Bandit、Safety、Trivy、SonarQube | 代码与镜像安全 |

---

*本方案基于 AGV-TMS v2.4 分支代码现状编制，建议每季度根据业务发展与技术成熟度进行一次修订。*
