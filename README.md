# AGV-TMS 柔性物流调度系统

> 混合调度原型系统：AGV柔性路径规划 + 固定输送线任务统筹

## 系统架构

```
┌─────────────────────────────────────────────────────┐
│                    Frontend (React)                   │
│  Dashboard │ MapConfig │ TaskManager │ AgvMonitor    │
│  ConveyorConfig │ AlgorithmConfig                    │
│         Ant Design Pro / OrangX 风格                  │
├─────────────────────────────────────────────────────┤
│                  FastAPI REST + WebSocket              │
├─────────────────────────────────────────────────────┤
│              混合调度引擎 (Hybrid Scheduler)           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐          │
│  │ 蚁群算法  │  │ 模拟退火  │  │ 非线性规划│          │
│  │  (ACO)   │  │  (SA)    │  │  (NLP)   │          │
│  │ 路径规划  │  │ 任务分配  │  │ 输送线排序│          │
│  └──────────┘  └──────────┘  └──────────┘          │
└─────────────────────────────────────────────────────┘
```

## 核心算法

### 1. 蚁群算法 (ACO) - AGV路径规划
- 信息素矩阵初始化和蒸发
- 概率式路径构建：P_ij = (τ_ij^α · η_ij^β) / Σ(τ_il^α · η_il^β)
- 多AGV碰撞避免（时间窗口预留）
- 参数：蚂蚁数、α、β、蒸发率、迭代次数

### 2. 模拟退火 (SA) - 任务分配优化
- 贪心初始解生成
- 邻域操作：任务交换、重新分配、顺序调整
- Metropolis接受准则：P(accept) = exp(-ΔE/T)
- 指数冷却：T_k = T_0 · α^k

### 3. 非线性规划 (NLP) - 输送线排序
- SLSQP求解器 (scipy.optimize.minimize)
- 目标：最小化总完工时间 + 能耗
- 约束：容量限制、任务优先级、无重叠
- 决策变量：各任务在各输送段上的开始时间

### 4. 混合调度引擎
- Phase 1: SA → 任务到AGV分配
- Phase 2: ACO → AGV路径规划
- Phase 3: NLP → 输送线任务排序
- Phase 4: 冲突检测与解决
- Phase 5: 统一调度输出

## 对标产品

| 功能 | 本系统 | 海康威视RCS | 博士输送线 | 罗克韦尔APS |
|------|--------|------------|-----------|------------|
| AGV路径规划 | ACO | A* / 时间窗 | - | - |
| 任务分配 | SA | 贪心+规则 | 规则引擎 | 线性规划 |
| 输送线排序 | NLP | - | 固定时序 | 混合整数规划 |
| 混合调度 | ACO+SA+NLP | 分层调度 | PLC控制 | APS引擎 |

## 快速启动

### 后端

```bash
cd backend
pip install -r requirements.txt
python run.py
# API文档: http://localhost:8000/docs
```

### 前端

```bash
cd frontend
npm install
npm run dev
# 界面: http://localhost:3000
```

## 项目结构

```
AgvTms/
├── backend/
│   ├── app/
│   │   ├── algorithms/        # 算法引擎
│   │   │   ├── aco.py         # 蚁群算法
│   │   │   ├── sa.py          # 模拟退火
│   │   │   ├── nlp.py         # 非线性规划
│   │   │   └── hybrid.py      # 混合调度引擎
│   │   ├── api/
│   │   │   └── routes.py      # API路由 + WebSocket
│   │   ├── models/
│   │   │   └── schemas.py     # 数据模型
│   │   ├── services/
│   │   │   ├── map_service.py
│   │   │   └── schedule_service.py
│   │   └── main.py            # FastAPI应用入口
│   ├── requirements.txt
│   └── run.py                 # 启动脚本
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Dashboard/     # 系统总览
│   │   │   ├── MapConfig/     # 地图配置
│   │   │   ├── TaskManager/   # 任务管理
│   │   │   ├── AgvMonitor/    # AGV监控
│   │   │   ├── ConveyorConfig/# 输送线配置
│   │   │   └── AlgorithmConfig/# 算法配置
│   │   ├── services/api.ts
│   │   ├── store/useStore.ts
│   │   ├── App.tsx
│   │   └── main.tsx
│   └── package.json
└── README.md
```

## API 端点

| 方法 | 路径 | 描述 |
|------|------|------|
| POST | /api/schedule/run | 触发混合调度 |
| GET | /api/schedule/result/{id} | 获取调度结果 |
| GET | /api/agv/status | 获取所有AGV状态 |
| GET | /api/map/graph | 获取完整地图 |
| POST | /api/map/node | 添加地图节点 |
| POST | /api/tasks | 创建任务 |
| GET | /api/tasks | 获取任务列表 |
| PUT | /api/algorithm/config | 更新算法配置 |
| GET | /api/metrics | 获取系统指标 |
| WS | /ws/schedule/live | 实时调度推送 |

## 技术栈

- **前端**: React 18 + TypeScript + Ant Design 5 + Zustand + Vite
- **后端**: Python 3.10+ + FastAPI + Pydantic v2
- **算法**: NumPy + SciPy + NetworkX
- **通信**: REST API + WebSocket
