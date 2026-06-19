# AGV-TMS v1.8 路线图 & 下一步行动

> **当前基线**: P0+P1 完成 | 前后端一致性 **91.6%** | 代码覆盖 9 文件 / ~1665 行
> **生成时间**: 2026-06-20 | 分支: `1.8`

---

## 📅 Phase 1: 本周 (Week 1-2) — 后端对接测试

### 目标
验证前端 V2 功能与实际后端 API 的端到端连通性，确保零阻断性缺陷。

### 前置条件
```bash
# 终端 1: 启动后端
cd backend && uvicorn app.main:app --reload --port 8000

# 终端 2: 启动前端
cd frontend && npm run dev
```

### 验证清单

| # | 页面 URL | 验证项 | 预期结果 | 优先级 |
|---|----------|--------|----------|--------|
| 1 | `http://localhost:5173/algorithm-config` | 切换到 **[V2 工业级算法]** Tab | 显示 Theta*/ECBS/SIPP/D* 配置卡片 | P0 |
| 2 | `http://localhost:5173/algorithm-config` | 修改 V2 参数 → 点击保存 | 调用 `PUT /api/v2/advanced/hybrid-scheduler/config` | P0 |
| 3 | `http://localhost:5173/dashboard` | 查看 **[混合调度引擎控制]** 区 | 模式选择 + 交通管制开关可见 | P0 |
| 4 | `http://localhost:5173/dashboard` | 点击「执行混合调度」按钮 | 调用 `POST /api/v2/advanced/hybrid-schedule` | P0 |
| 5 | `http://localhost:5173/agv-monitor` | 切换到 **[🚦 交通管制]** Tab | 死锁检测卡片 + 区域锁定表 | P0 |
| 6 | `http://localhost:5173/agv-monitor` | 点击「检测死锁」按钮 | 调用 `GET /api/v2/advanced/deadlock/check` | P1 |
| 7 | `http://localhost:5173/vehicles` | 车型管理页面完整加载 | 6 种默认车型 CRUD 正常 | P1 |
| 8 | `http://localhost:5173/digital-twin` | WS 状态徽章显示 🟢/🔴 | 自动连接 `/api/v2/digital-twin/ws/agv-status` | P1 |

### 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| 后端 V2 端点未实现 | 中 | 高 | 前端已有 fallback → V1 逻辑 |
| CORS 跨域问题 | 低 | 中 | 检查 backend `main.py` 的 CORS middleware |
| WebSocket 握手失败 | 中 | 中 | 检查 `digital_twin_ws.py` 路由注册 |
| 环境依赖缺失 (Redis/Prom) | 低 | 低 | docker-compose 一键启动 |

### 完成标准
- [ ] 所有 P0 项通过（无 console 报错）
- [ ] Network Tab 无 5xx/4xx（除预期的 fallback）
- [ ] 截图存入 `docs/screenshots/week1-validation/`

---

## 📡 Phase 2: Month 2 — WebSocket 联动深化

### 目标
将 DigitalTwin3D 组件从模拟模式升级为真实 30FPS 数据驱动。

### 任务分解

#### 2.1 DigitalTwin3D WS 接入
```
文件: frontend/src/components/DigitalTwin3D.tsx (新建)
依赖: digitalTwinWsManager (已存在于 unifiedApi.ts)
```
**改动要点：**
1. 导入 `digitalTwinWsManager` 单例
2. 在 `useEffect` 中订阅 `'update'` 事件
3. 将 WS 推送的 `UnifiedAgvStatus[]` 映射为 Three.js Mesh 位置/姿态
4. 实现 requestAnimationFrame 驱动的平滑插值（lerp）

#### 2.2 后端 WS 推送增强
```
文件: backend/app/api/digital_twin_ws.py (已存在)
```
**确认事项：**
- [ ] 推送频率 = 30 FPS（interval ≈ 33ms）
- [ ] payload 结构匹配 `UnifiedAgvStatus`（id, x, y, yaw, speed, status, battery）
- [ ] 心跳机制 (ping/pong) 防止连接断开

#### 2.3 性能基线建立
```bash
# 使用 Chrome DevTools Performance 录制
# 指标目标:
#   - FPS ≥ 28 (100 AGV 场景)
#   - GC Pause < 10ms
#   - 内存增长 < 50MB/hour
```

### 完成标准
- [ ] 100 AGV 场景下稳定 30FPS 动画
- [ ] WS 断开自动降级为模拟模式
- [ ] `docs/month2-ws-integration-report.md`

---

## 🎬 Month 2 下半 — Demo 与压测

### 2.4 录制混合调度 Demo 视频
- **场景**: 100 AGV × HybridScheduler (Auto模式, Theta*↔ECBS 自动切换)
- **时长**: 3-5 分钟
- **内容**: 
  - Dashboard 启动调度 → 算法选择动画
  - AGVMonitor 死锁检测演示
  - DigitalTwin 3D 实时动画
  - 交通管制区域高亮
- **输出**: `docs/demo-v1.8-hybrid-scheduler.mp4`

### 2.5 性能压测报告
```
工具: locust / k6
场景:
  - 并发用户: 10/50/100
  - AGV 规模: 50/100/200/500
  - 持续时间: 30min
```

| 指标 | 当前基线 | 目标值 | 测量方法 |
|------|----------|--------|----------|
| API P99 延迟 | TBD | < 200ms | Prometheus histogram |
| WS 消息延迟 | TBD | < 50ms | 端到端时间戳差 |
| 内存占用 (500AGV) | TBD | < 2GB | Docker stats |
| CPU 利用率 | TBD | < 80% (8核) | top/htop |

**输出**: `docs/perf-stress-test-report-month2.md`

---

## 🧠 Q2 (Month 6) — 战略里程碑

### 6.1 RL 强化学习 A/B 测试集成

**前置**: 后端 RL Agent API 框架已存在（需确认位置）
```
可能的文件:
  backend/app/algorithms/v2/rl_agent.py  (待创建或已存在)
  backend/app/api/v2/rl/                (API 路由)
```

**实施步骤：
1. 创建 `frontend/src/pages/RlExperiment/index.tsx` — A/B 测试控制台
2. 对比指标: 
   - 调度完成时间
   - 路径长度总和
   - 碰撞次数
   - 能耗估算
3. 统计显著性检验 (t-test / Mann-Whitney U)

### 6.2 3D 数字孪生完整版发布

**功能清单：**
- [ ] 基于 DigitalTwin3D 的完整工厂建模
- [ ] 点云/SLAM 地图导入
- [ ] AGV 轨迹回放 (历史数据)
- [ ] 热力图 (拥堵/热点区域)
- [ ] 多视角切换 (自由/跟随/上帝视角)
- [ ] VR/AR 输出 (可选)

**技术选型建议：**
- 3D 引擎: Three.js (现有) → 或升级 to React-Three-Fiber
- 状态管理: Zustand (现有) + Immer for immutable updates
- 后端渲染: 若性能不足可考虑 server-side rendering

---

## 📊 优先级矩阵 (MoSCoW)

| 阶段 | 任务 | 优先级 | 依赖 | 工时预估 |
|------|------|--------|------|----------|
| W1-2 | 后端对接测试 | **Must** | 环境搭建 | 2-3 天 |
| M2 | WS 30FPS 联动 | **Should** | W1-2 通过 | 5-7 天 |
| M2 | Demo 视频录制 | **Should** | WS 联动完成 | 1 天 |
| M2 | 压测报告 | **Could** | 稳定版本 | 3-5 天 |
| M6 | RL A/B 测试 | **Won't** (Now) | 数据积累 | 2-3 周 |
| M6 | 3D 孪生完整版 | **Won't** (Now) | 全部 above | 4-6 周 |

---

## 🔗 相关文档索引

| 文档 | 路径 | 说明 |
|------|------|------|
| P0 完成报告 | `docs/P0修复完成报告-前端内核一致性校准.md` | V2算法暴露详情 |
| P1 完成报告 | `docs/P1修复完成报告-统一API_车型管理_WS集成.md` | 统一API/车型/WS |
| P0+P1 深度报告 | `docs/AGV-TMS_Phase5.5_P0-P1_深度实施报告.md` | 完整技术细节 |
| 一致性校准报告 | `docs/前端-内核一致性校准报告.md` | 91.6% 评分依据 |
| 竞品对标 | `docs/海康RCS与极智嘉RMS深度技术对标分析.md` | 市场参考 |

---

## ✅ 快速启动检查清单

- [ ] Git commit 当前所有变更 (`git add -A && git commit -m "feat: P0+P1 complete"`)
- [ ] 创建 Week1 分支 (`git checkout -b feature/week1-backend-test`)
- [ ] 确认 docker-compose 可一键拉起全部依赖
- [ ] 安装 Postman 或使用 curl 测试 V2 API 端点
- [ ] 截图记录初始状态作为 baseline

---

*本文档应随项目进展持续更新，建议每完成一个 Phase 进行一次 review。*
