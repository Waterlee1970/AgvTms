# AGV-TMS Performance Benchmark Suite v2.3

> **Phase 4.0 P2-03** — 性能压测 & 优化基线

## 快速开始

```bash
cd backend/benchmarks

# 安装依赖
pip install locust requests

# 运行 smoke test (Web UI 模式)
locust -f locustfile.py --host http://localhost:8000

# 一键运行 (Headless)
chmod +x run_benchmark.sh
./run_benchmark.sh -s medium --headless
```

## 目录结构

```
benchmarks/
├── locustfile.py                    # 主压测脚本 (3类用户角色)
├── run_benchmark.sh                 # 一键执行脚本
├── BASELINE_REPORT.md               # 基线报告模板 (目标指标)
├── README.md                        # 本文件
├── scenarios/                       # 场景数据生成
│   ├── generate_agv_scenario.py     # AGV 车队数据生成器
│   ├── generate_task_scenario.py    # 任务流数据生成器
│   └── large_scale_agv_simulation.py # 大规模仿真引擎 (P2-05)
└── results/                         # 输出目录 (运行后生成)
    └── .gitkeep
```

## 用户角色模型

| 角色 | 权重 | 行为模式 | 对应真实场景 |
|------|------|----------|-------------|
| **AgvMonitorUser** | 70% | 高频只读: AGV列表/详情/地图/输送线/调度结果 | 前端监控大屏 |
| **TaskOperatorUser** | 20% | 任务CRUD+调度触发+WMS订单 | WMS上位机 |
| **AdminUser** | 10% | 配置变更/AGV状态更新/评测运行/重置 | 运维工作站 |

### API 覆盖表

| 端点 | 方法 | 角色 | 频率权重 | 说明 |
|------|------|------|---------|------|
| `/api/v1/agvs` | GET | Monitor | 25% | AGV列表刷新 |
| `/api/v1/agvs/{id}` | GET | Monitor | 15% | AGV详情 |
| `/api/v1/tasks` | GET | Monitor+Op | 12% | 任务列表 |
| `/api/v1/tasks` | POST | Operator | 30% | 创建任务 |
| `/api/v1/schedule/trigger` | POST | Operator | 25% | 触发调度 |
| `/api/v1/conveyors` | GET | Monitor | 8% | 输送线状态 |
| `/api/v1/map/nodes` | GET | Monitor | 10% | 地图节点 |
| `/api/healthz` | GET | Monitor/Admin | 8% | 健康检查 |
| `/api/wms/order` | POST | Operator | 15% | WMS订单 |
| `/api/v1/algorithms/config` | PUT | Admin | 20% | 算法配置 |

## 规模预设

| Preset | Users | Duration | AGVs | Tasks | 用途 |
|--------|-------|----------|------|-------|------|
| `small` | 10 | 2min | 10 | 50 | Smoke test |
| `medium` | 50 | 5min | 50 | 200 | 标准基线 |
| `large` | 150 | 8min | 100 | 500 | Load test |
| `xlarge` | 300 | 10min | 200 | 1000 | Stress test |
| `stress` | 500 | 15min | 500 | 2000 | Extreme pressure |

## 使用方式

### Web UI 模式（推荐首次使用）
```bash
locust -f locustfile.py --host http://localhost:8000
# 浏览器打开 http://localhost:8089
```

### Headless 模式（CI/CD）
```bash
# 基础用法
locust -f locustfile.py --headless \
  -u 100 -r 10 \
  --run-time 5m \
  --csv results/baseline \
  --host http://localhost:8000

# 一键脚本
./run_benchmark.sh -s large --headless -H http://192.168.1.100:8000
```

### 自定义参数
```bash
./run_benchmark.sh \
  --users 200 --time 10 \
  -H http://target-server:8000 \
  --output-dir results/custom_run
```

## 场景数据生成

### AGV 场景
```bash
python scenarios/generate_agv_scenario.py --list-presets
python scenarios/generate_agv_scenario.py --preset medium
python scenarios/generate_agv_scenario.py --size 100 --seed 42 --output my_agvs.json
```

### 任务场景  
```bash
python scenarios/generate_task_scenario.py --size-preset large --pending-only
python scenarios/generate_task_scenario.py --tasks 500 --ratio 0.3
```

### 大规模模拟 (P2-05)
```bash
python scenarios/large_scale_agv_simulation.py list-presets
python scenarios/large_scale_agv_simulation.py run --preset medium_logistics
python scenarios/large_scale_agv_simulation.py run --num-agvs 100 --duration 15
```

**预设场景**: `small_warehouse`(20) / `medium_logistics`(50) / `large_distribution`(100) / `factory_production`(80) / `full_performance_lab`(30)

## 结果解读

### 关键指标定义

| 指标 | 定义 | 目标值 |
|------|------|--------|
| **P50** | 50%请求的响应时间 | < 50ms |
| **P99** | 99%请求的响应时间 | < 200ms |
| **RPS** | 每秒请求数(吞吐) | > 1000 |
| **Error %** | 失败请求占比 | < 0.1% |
| **Avg Response** | 平均响应时间 | < 30ms |

### Locust HTML 报告内容
- 请求统计表 (每个API端点一行)
- 响应时间分布图
- 并发用户 vs 响应时间曲线
- RPS 时间序列图
- 失败请求数量与类型

### 结果文件
运行后 `results/TIMESTAMP/` 目录:
- `report.html` — 可视化报告
- `baseline_stats.csv` — 统计数据
- `baseline_failures.csv` — 失败明细
- `summary.md` — Markdown摘要

## CI/CD 集成示例 (GitHub Actions)

```yaml
name: Performance Benchmark
on: [push to main]
jobs:
  benchmark:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -r requirements-test.txt locust
      - run: cd backend && uvicorn app.main:app &
      - run: sleep 10 && cd benchmarks && ./run_benchmark.sh -s small --headless
      - uses: actions/upload-artifact@v4
        with: { path: benchmarks/results/, name: benchmark-results }
```

## FAQ

**Q: 连接被拒绝(Connection refused)?**  
A: 确保后端服务已启动: `cd backend && uvicorn app.main:app --port 8000`

**Q: 如何只测试特定 API?**  
A: 编辑 `locustfile.py` 中对应 User 类的 `@task` 权重或注释掉不需要的任务方法

**Q: 如何调整用户比例?**  
A: 修改各 User 类的 `weight` 属性（确保总权重=100）

**Q: 数据注入失败?**  
A: 使用 `--no-inject` 跳过注入，或先确认后端服务正常响应

**Q: 如何分析瓶颈?**  
A: 查看 `BASELINE_REPORT.md` 第5节"瓶颈分析模板"，结合 Locust HTML 报告的 Slowest 页面
