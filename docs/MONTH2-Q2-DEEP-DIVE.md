# AGV-TMS v1.8+ 深度实施蓝图

> **适用阶段**: Month 2 (WS联动/Demo/压测) → Q2 (RL/3D孪生完整版)
> **前置条件**: P0+P1 已完成 (一致性 91.6%) | Week 1-2 后端对接测试通过
> **文档版本**: v2026.06.20 | 分支: `feature/month2-ws-integration`

---

## 📅 Part A: Month 2 — WebSocket 深度联动

### A.1 DigitalTwin3D.tsx 完整实现

#### 文件位置
```
frontend/src/components/DigitalTwin3D.tsx (新建, ~400 行)
```

#### 技术栈
- 3D 引擎: **Three.js** (r158+) + **@react-three/fiber** (React 绑定)
- 动画: `useFrame` hook (requestAnimationFrame 封装)
- 状态: `digitalTwinWsManager` (unifiedApi.ts 已导出单例)
- 插值: lerp (线性插值) + slerp (四元数旋转)

#### 核心架构
```typescript
// DigitalTwin3D.tsx 骨架结构
import { Canvas, useFrame } from '@react-three/fiber';
import { digitalTwinWsManager, UnifiedAgvStatus } from '../services/unifiedApi';

// ============ 类型定义 ============
interface AgvMeshRef {
  mesh: THREE.Mesh;           // AGV 3D 模型
  targetPos: [number, number, number];  // 目标位置 (from WS)
  currentPos: THREE.Vector3;  // 当前插值位置
  targetYaw: number;          // 目标航向角
  currentYaw: number;        // 当前插值角度
  status: 'idle' | 'moving' | 'charging' | 'error';
  agvId: string;
}

// ============ 主组件 ============
export default function DigitalTwin3D() {
  const [wsConnected, setWsConnected] = useState(false);
  const [agvDataMap, setAgvDataMap] = useState<Map<string, UnifiedAgvStatus>>(new Map());
  const agvRefs = useRef<Map<string, AgvMeshRef>>(new Map());

  // WS 生命周期管理
  useEffect(() => {
    // 连接成功回调
    const unsubConn = digitalTwinWsManager.on('connected', () => {
      setWsConnected(true);
    });

    // 断开回调 → 降级为模拟模式
    const unsubDisconn = digitalTwinWsManager.on('disconnected', () => {
      setWsConnected(false);
      enableSimulationMode();  // 见下文
    });

    // 核心: 30FPS 数据接收
    const unsubUpdate = digitalTwinWsManager.on('update', (data: UnifiedAgvStatus[]) => {
      const newMap = new Map(agvDataMap.current);
      data.forEach(agv => newMap.set(agv.id, agv));
      setAgvDataMap(newMap);  // 触发 re-render
    });

    // 启动连接
    digitalTwinWsManager.connect();

    return () => {
      unsubConn();
      unsubDisconn();
      unsubUpdate();
    };
  }, []);

  return (
    <div className="digital-twin-3d-container">
      {/* 状态栏 */}
      <StatusBar connected={wsConnected} fps={30} agvCount={agvDataMap.size} />

      {/* Three.js Canvas */}
      <Canvas
        camera={{ position: [50, 50, 50], fov: 60 }}
        shadows
        gl={{ antialias: true, alpha: false }}
      >
        <SceneLighting />
        <FactoryFloor /> {/* 工厂地面网格 */}
        
        {/* 动态 AGV 列表 */}
        {Array.from(agvDataMap.entries()).map(([id, agv]) => (
          <AgvEntity
            key={id}
            ref={(ref) => agvRefs.current.set(id, ref)}
            data={agv}
            wsConnected={wsConnected}
          />
        ))}

        <OrbitControls enableDamp />
      </Canvas>
    </div>
  );
}
```

#### AGV 实体组件 (关键动画逻辑)
```typescript
// AgvEntity.tsx — 单个 AGV 的 3D 表示与动画
function AgvEntity({ data, wsConnected }: Props) {
  const meshRef = useRef<THREE.Mesh>(null);

  // 每帧执行: requestAnimationFrame 循环 (~60fps)
  useFrame((state, delta) => {
    if (!meshRef.current) return;

    const LERP_SPEED = 5.0;  // 插值系数 (越大越快响应)
    
    // 位置平滑插值 (lerp)
    meshRef.current.position.lerp(
      new THREE.Vector3(data.x, 0, data.y),  // y=0 因为是 2D 地图投影到 3D
      Math.min(1, delta * LERP_SPEED)
    );

    // 航向角平滑过渡
    const targetRotation = -data.yaw;  // Three.js 坐标系转换
    meshRef.current.rotation.y = THREE.MathUtils.lerp(
      meshRef.current.rotation.y,
      targetRotation,
      Math.min(1, delta * LERP_SPEED)
    );

    // 状态颜色映射
    updateMaterialColor(meshRef.current, data.status);
  });

  return (
    <mesh ref={meshRef} castShadow>
      {/* AGV 几何体: 简化为 Box + 方向指示器 */}
      <boxGeometry args={[2, 1, 3]} />  // 长×高×宽 (米)
      <meshStandardMaterial color={getStatusColor(data.status)} />
      
      {/* 方向锥形箭头 */}
      <mesh position={[0, 0.6, 1.5]}>
        <coneGeometry args={[0.5, 1, 4]} />
        <meshStandardMaterial color="yellow" emissive="orange" />
      </mesh>
    </mesh>
  );
}

// 状态颜色映射函数
function getStatusColor(status: string): string {
  switch (status) {
    case 'idle': return '#52c41a';     // 绿色
    case 'moving': return '#1890ff';   // 蓝色
    case 'charging': return '#faad14'; // 橙色
    case 'error': return '#ff4d4f';    // 红色
    default: return '#999999';
  }
}
```

#### 降级模拟模式 (当 WS 断开时)
```typescript
function enableSimulationMode() {
  console.log('[DigitalTwin3D] WS disconnected, fallback to simulation mode');
  
  // 复用现有 DigitalTwin/index.tsx 中的模拟数据生成逻辑
  const SIM_AGV_COUNT = 20;
  let simTime = 0;
  
  const simInterval = setInterval(() => {
    simTime += 0.033;  // ~30fps
    
    const mockAgvs: UnifiedAgvStatus[] = Array.from({ length: SIM_AGV_COUNT }, (_, i) => ({
      id: `SIM-${i.toString().padStart(3, '0')}`,
      x: 25 * Math.sin(simTime + i * 0.5),
      y: 25 * Math.cos(simTime + i * 0.7),
      yaw: simTime + i,
      speed: 1.0 + Math.random() * 0.5,
      status: ['idle', 'moving', 'charging'][i % 3],
      battery: 100 - (simTime * 0.1) % 100,
    }));

    // 手动触发 update 事件 (与真实 WS 数据同一路径)
    digitalTwinWsManager.emit('update', mockAgvs);
  }, 33);  // 30fps

  // 返回清理函数
  return () => clearInterval(simInterval);
}
```

#### 性能优化清单
| 技术 | 目的 | 实现 |
|------|------|------|
| **InstancedMesh** | 批量渲染相同几何体 | 500 AGV 场景下 DrawCall 从 500→1 |
| **Frustum Culling** | 屏幕外不渲染 | Three.js 内置, 需确保 geometry.boundingSphere |
| **LOD (Level of Detail)** | 远距离降低面数 | 近处 1200 面, 远处 200 面 |
| **Web Worker** | 计算 offload | 将路径规划计算移至 Worker |
| **Object Pooling** | 避免 GC Pause | 预分配 600 个 AGV Mesh 对象 |

**InstancedMesh 示例（500 AGV 场景必备）：**
```typescript
// 替代单独的 mesh, 使用 InstancedMesh
const INSTANCED_MAX = 600;
const instancedMesh = useMemo(() => {
  const geometry = new BoxGeometry(2, 1, 3);
  const material = new MeshStandardMaterial();
  const mesh = new InstancedMesh(geometry, material, INSTANCED_MAX);
  mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);  // 标记为动态更新
  return mesh;
}, []);

// useFrame 中批量更新矩阵
useFrame((state, delta) => {
  const dummy = new THREE.Object3D();
  let idx = 0;
  
  for (const [id, agv] of agvDataMap) {
    if (idx >= INSTANCED_MAX) break;
    
    dummy.position.set(agv.x, 0, agv.y);
    dummy.rotation.y = -agv.yaw;
    dummy.updateMatrix();
    instancedMesh.setMatrixAt(idx++, dummy.matrix);
  }
  
  instancedMesh.instanceMatrix.needsUpdate = true;  // 一次性上传 GPU
});
```

---

### A.2 Demo 视频录制脚本

#### 录制工具链
```bash
# macOS 推荐方案
brew install obs   # OBS Studio (免费开源)

# 或使用命令行工具
brew install ffmpeg
# 录制命令:
# ffmpeg -f avfoundation -framerate 30 -i "1" -t 180 output.mp4
```

#### Demo 脚本 (时长: 3分30秒)

| 时间段 | 场景 | 操作要点 | 解说词 (可选) |
|--------|------|----------|---------------|
| **0:00-0:30** | 开场全景 | DigitalTwin3D 全局视角, 缓慢旋转 | "欢迎来到 AGV-TMS v1.8 数字孪生系统..." |
| **0:30-1:00** | Dashboard 调度启动 | 点击「执行混合调度」→ 选择 Auto 模式 | "演示混合调度引擎自动选择最优算法..." |
| **1:00-1:30** | 算法切换展示 | 切换 force_theta / force_ecbs 对比 | "≤3 台用 Theta*, >3 台自动升级 ECBS..." |
| **1:30-2:00** | AlgorithmConfig V2 | 打开 V2 Tab, 调整权重参数 | "Theta* 权重: 0.7, ECBS suboptimal: 1.2..." |
| **2:00-2:30** | AGVMonitor 交通管制 | 切换交通管制Tab → 检测死锁 | "实时死锁检测与区域锁定可视化..." |
| **2:30-3:00** | 3D 实时动画 | 回到 DigitalTwin, 放大跟随单个 AGV | "WebSocket 30FPS 实时驱动, 零延迟..." |
| **3:00-3:20** | 性能面板 | 打开 Chrome DevTools Performance | "100 AGV 稳定 28 FPS, 内存增长 <10MB/min..." |
| **3:20-3:30** | 结尾 | 显示项目 Logo + GitHub 地址 | "感谢观看, 开源在 github.com/xxx/agv-tms" |

#### 后台数据准备（模拟或录制回放）
```python
# backend/scripts/generate_demo_scenario.py
"""生成 100 AGV 混合调度场景的预录数据"""
import json
import random
from datetime import datetime

scenario = {
  "metadata": {
    "version": "1.8-demo",
    "recorded_at": datetime.now().isoformat(),
    "agv_count": 100,
    "duration_seconds": 210,  # 3.5 min
    "fps": 30
  },
  "algorithm_config": {
    "mode": "auto",
    "theta_star_weight": 0.7,
    "ecbs_suboptimality": 1.2,
    "traffic_control_enabled": True,
    "deadlock_prevention_enabled": True
  },
  "frames": []  # 6300 帧 (30fps × 210s)
}

# 生成预设轨迹 (可复现的伪随机)
random.seed(42)
for frame_idx in range(30 * 210):
  timestamp = frame_idx / 30.0
  agvs = []
  for i in range(100):
    # 每台 AGV 有独立的运动模式 (直线/圆弧/停止)
    phase = (timestamp + i * 0.1) % 60  # 60秒周期
    
    agvs.append({
      f"id": f"AGV-{i:03d}",
      f"x": 50 * math.sin(phase * 0.1 + i * 0.05),
      f"y": 50 * math.cos(phase * 0.08 + i * 0.03),
      f"yaw": phase * 0.5 + i,
      f"speed": 1.5 if (i % 3 != 0) else 0,  # 1/3 停止
      f"status": ["moving", "idle", "charging"][i % 3],
      f"battery": max(20, 100 - timestamp * 0.02 - i * 0.5)
    })
  
  scenario["frames"].append({"ts": timestamp, "agvs": agvs})

with open("docs/demo-scenario-100agv.json", "w") as f:
  json.dump(scenario, f, indent=2)

print(f"Generated {len(scenario['frames'])} frames for 100 AGVs")
```

#### 视频输出规格
```
格式: MP4 (H.264)
分辨率: 1920×1080 (1080p) 或 2560×1440 (2K)
帧率: 30 FPS
码率: 8 Mbps (高质量)
音频: AAC 128kbps (如有解说)
文件大小预估: ~180 MB (3.5 min)
```

---

### A.3 性能压测报告模板

#### 测试环境配置
```yaml
# docs/perf-test-environment.yml
environment:
  hardware:
    cpu: Intel Xeon E5-2680 v4 @ 2.40GHz (8 cores)
    ram: 32GB DDR4 ECC
    disk: NVMe SSD 1TB (IOPS > 100k)
  
  software:
    os: Ubuntu 22.04 LTS
    docker: Docker Compose v2.20.0
    python: 3.11.5
    node: v20.10.0
  
  services:
    - postgres:15 (主数据库)
    - redis:7 (缓存/WS PubSub)
    - prometheus:2 (监控)
    - grafana:10 (可视化)
```

#### 压测场景定义

```javascript
// docs/k6/scenarios/load_test.js
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

// 自定义指标
const ErrorRate = new Rate('errors');
const ApiLatency = Trend('api_latency_ms');

export const options = {
  stages: [
    { duration: '2m', target: 10 },   // 预热
    { duration: '5m', target: 50 },   // 正常负载
    { duration: '5m', target: 100 },  // 高负载
    { duration: '2m', target: 200 },  // 峰值压力
    { duration: '5m', target: 200 },  // 持续峰值
    { duration: '2m', target: 0 },    // 恢复
  ],
  thresholds: {
    http_req_duration: ['p(99)<200'],  // P99 < 200ms
    errors: ['rate<0.01'],              // 错误率 < 1%
  },
};

const AGV_SCALES = [50, 100, 200, 500];

export default function () {
  // 场景 1: 混合调度 API
  const schedulePayload = JSON.stringify({
    mode: 'auto',
    agv_ids: Array.from(
      { length: __ITER % 5 === 0 ? 500 : 100 },
      (_, i) => `AGV-${String(i).padStart(3, '0')}`
    ),
    tasks: generateTasks(__ITER % 10 + 5),
  });

  const scheduleRes = http.post(
    `${__ENV.BASE_URL}/api/v2/advanced/hybrid-schedule`,
    schedulePayload,
    { headers: { 'Content-Type': 'application/json' } }
  );
  
  ApiLatency.add(scheduleRes.timings.duration);
  ErrorRate.add(scheduleRes.status !== 200);
  
  check(scheduleRes, {
    'schedule status 200': (r) => r.status === 200,
    'schedule latency < 200ms': (r) => r.timings.duration < 200,
    'response has result': (r) => 
      JSON.parse(r.body)?.result?.selected_algorithm !== undefined,
  });

  sleep(1);  // QPS ≈ 并发数
}

function generateTasks(count) {
  // 生成随机任务 (取货点 → 卸货点)
  return Array.from({ length: count }, (_, i) => ({
    task_id: `TASK-${i}`,
    pickup: { x: Math.random() * 100, y: Math.random() * 100 },
    dropoff: { x: Math.random() * 100, y: Math.random() * 100 },
    priority: ['low', 'medium', 'high'][i % 3],
  }));
}
```

#### WebSocket 压力测试
```bash
# 使用 artillio 或自定义脚本同时打开多个 WS 连接
# docs/ws-stress-test.sh
#!/bin/bash

CONNECTIONS=(10 50 100 200 500)
AGV_COUNTS=(50 100 200 500)

for conn in "${CONNECTIONS[@]}"; do
  echo "=== Testing $conn concurrent WS connections ==="
  
  python3 scripts/bench_ws.py \
    --url "ws://localhost:8000/api/v2/digital-twin/ws/agv-status" \
    --connections $conn \
    --agvs ${AGV_COUNTS[$RANDOM % ${#AGV_COUNTS[@]}]} \
    --duration 60 \
    --report "docs/ws-bench-${conn}conn.json"
done
```

#### 结果收集模板
```markdown
## 压测报告: [日期]

### 环境信息
- CPU: ___ cores @ ___ GHz
- RAM: ___ GB
- 网络: ___ Gbps
- 后端版本: ___
- 数据库: PostgreSQL ___ / Redis ___

### 测试结果汇总表

| 场景 | 并发用户 | AGV 数量 | 平均延迟 | P99 延迟 | P99.9 延迟 | 吞吐量 (QPS) | 错误率 | CPU 占用 | 内存占用 |
|------|----------|----------|----------|----------|------------|--------------|--------|----------|----------|
| 暖机 | 10 | 50 | ms | ms | ms | /s | % | % | GB |
| 正常 | 50 | 100 | ms | ms | ms | /s | % | % | GB |
| 高负 | 100 | 200 | ms | ms | ms | /s | % | % | GB |
| 峰值 | 200 | 500 | ms | ms | ms | /s | % | % | GB |
| 极限 | 500 | 500 | ms | ms | ms | /s | % | % | GB |

### 关键发现
1. **瓶颈点**: ___ (DB查询/内存分配/GC锁/网络IO)
2. **优化建议**: ___
3. **对比基线**: 上次测试日期 ___ → 提升 ___%

### Grafana 截图
- [ ] CPU 利用率曲线
- [ ] 内存增长趋势
- [ ] API 延迟直方图 (Prometheus histogram)
- [ ] WS 消息延迟分布
- [ ] 数据库连接池状态
```

---

## 🧠 Part B: Q2 (Month 6) — 战略规划

### B.1 RL 强化学习 A/B 测试集成

#### 架构设计
```
┌─────────────┐    ┌──────────────┐    ┌─────────────────┐
│   前端 UI    │───▶│ RL 实验 API  │◀───│  RL Agent 服务   │
│  (A/B 切换)  │    │ /api/v2/rl/  │    │  (Python/Torch)  │
└─────────────┘    └──────────────┘    └─────────────────┘
       │                   │                      │
       ▼                   ▼                      ▼
  RlExperiment      实验配置/指标收集       训练推理引擎
  页面组件          (PostgreSQL)         (GPU 可选)
```

#### 后端 RL Agent API (待开发)
```python
# backend/app/api/v2/rl_experiment.py (新建)
"""
RL 强化学习 A/B 测试实验框架
- 实验管理: 创建/启停实验
- 流量分配: 按 percentage 分流到不同策略
- 指标收集: 实时记录调度质量指标
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Literal, Optional
import uuid
import random

router = APIRouter(prefix="/api/v2/rl/experiments", tags=["rl-experiment"])

class ExperimentCreate(BaseModel):
    name: str                           # 实验名称
    description: str                    # 描述
    algorithm_a: str = "hybrid_auto"    # 对照组 (现有算法)
    algorithm_b: str = "rl_agent"       # 实验组 (RL 算法)
    traffic_split: float = 0.5          # 流量分配 (0~1, 0.5=各50%)
    metrics: list[str] = [
        "completion_time",
        "total_path_length",
        "collision_count",
        "energy_consumption"
    ]
    min_samples: int = 1000             # 最小样本量 (统计显著性)
    significance_level: float = 0.05    # α=0.05

class ExperimentStatus(BaseModel):
    experiment_id: str
    name: str
    status: Literal["running", "paused", "completed"]
    current_samples_a: int              # 对照组样本数
    current_samples_b: int              # 实验组样本数
    results: Optional[dict] = None      # 最终结果 (完成后填充)

@router.post("/")
async def create_experiment(exp: ExperimentCreate):
    """创建新实验"""
    exp_id = str(uuid.uuid4())[:8]
    # 存入 Redis/PostgreSQL
    await redis.hset(f"rl:exp:{exp_id}", mapping={
        "name": exp.name,
        "status": "running",
        "traffic_split": exp.traffic_split,
        "algorithm_a": exp.algorithm_a,
        "algorithm_b": exp.algorithm_b,
        "metrics": json.dumps(exp.metrics),
        "created_at": datetime.now().isoformat()
    })
    return {"experiment_id": exp_id, "status": "created"}

@router.get("/{exp_id}/status")
async def get_experiment_status(exp_id: str):
    """获取实验进度和初步结果"""
    data = await redis.hgetall(f"rl:exp:{exp_id}")
    if not data:
        raise HTTPException(404, "Experiment not found")
    
    # 收集两组指标
    samples_a = await collect_metrics(exp_id, group="control")
    samples_b = await collect_metrics(exp_id, group="treatment")
    
    # 初步 t-test (可用 scipy.stats)
    p_value = calculate_significance(samples_a, samples_b)
    
    return ExperimentStatus(
        experiment_id=exp_id,
        name=data["name"],
        status=data["status"],
        current_samples_a=len(samples_a),
        current_samples_b=len(samples_b),
        results={"p_value": p_value, "significant": p_value < 0.05}
    )

@router.post("/{exp_id}/assign")
async def assign_algorithm(request: Request):
    """根据实验配置为当前请求分配算法 (流量分流核心)"""
    exp_id = request.headers.get("X-Experiment-ID")
    if not exp_id:
        return {"algorithm": "default"}  # 无实验时走默认逻辑
    
    data = await redis.hgetall(f"rl:exp:{exp_id}")
    split = float(data["traffic_split"])
    
    if random.random() < split:
        return {"algorithm": data["algorithm_b"], "group": "treatment"}
    else:
        return {"algorithm": data["algorithm_a"], "group": "control"}
```

#### 前端 RL 实验控制台
```typescript
// frontend/src/pages/RlExperiment/index.tsx (新建, ~350 行)
import React, { useState, useEffect } from 'react';
import { Card, Table, Button, Tag, Progress, Statistic, Row, Col } from 'antd';
import {
  PlayCircleOutlined,
  PauseCircleOutlined,
  BarChartOutlined,
  TrophyOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';

interface Experiment {
  id: string;
  name: string;
  status: 'running' | 'paused' | 'completed';
  algorithm_a: string;
  algorithm_b: string;
  traffic_split: number;
  samples_a: number;
  samples_b: number;
  p_value?: number;
  significant?: boolean;
  created_at: string;
}

export default function RlExperimentPage() {
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [loading, setLoading] = useState(false);

  // 创建新实验
  const handleCreateExp = async () => {
    Modal.confirm({
      title: '创建 RL A/B 测试',
      content: (
        <Form layout="vertical">
          <Form.Item label="实验名称">
            <Input placeholder="例: RL-v1 vs HybridScheduler" />
          </Form.Item>
          <Form.Item label="对照组算法">
            <Select defaultValue="hybrid_auto">
              <Option value="hybrid_auto">HybridScheduler (Auto)</Option>
              <Option value="theta_star">Pure Theta*</Option>
            </Select>
          </Form.Item>
          <Form.Item label="实验组算法">
            <Select defaultValue="rl_agent">
              <Option value="rl_agent">RL Agent (DQN/PPO)</Option>
              <Option value="rl_agent_v2">RL Agent v2 (Transformer)</Option>
            </Select>
          </Form.Item>
          <Form.Item label="流量分配">
            <Slider min={0} max={1} step={0.1} defaultValue={0.5} 
              marks={{ 0: '100% 对照', 0.5: '50/50', 1: '100% 实验'}} />
          </Form.Item>
        </Form>
      ),
      onOk: async (values) => {
        await api.post('/api/v2/rl/experiments/', values);
        fetchExperiments();  // 刷新列表
      }
    });
  };

  // 表格列定义
  const columns: ColumnsType<Experiment> = [
    { title: '实验 ID', dataIndex: 'id', width: 100 },
    { title: '名称', dataIndex: 'name', ellipsis: true },
    { 
      title: '状态', 
      dataIndex: 'status',
      render: (status) => (
        <Tag color={status === 'running' ? 'green' : status === 'paused' ? 'orange' : 'blue'}>
          {status === 'running' ? '运行中' : status === 'paused' ? '已暂停' : '已完成'}
        </Tag>
      )
    },
    { title: '对照算法', dataIndex: 'algorithm_a', render: (alg) => <Tag>{alg}</Tag> },
    { title: '实验算法', dataIndex: 'algorithm_b', render: (alg) => <Tag color="purple">{alg}</Tag> },
    { 
      title: '流量分配', 
      dataIndex: 'traffic_split',
      render: (split) => <Progress percent={split * 100} size="small" />
    },
    { title: '对照样本', dataIndex: 'samples_a' },
    { title: '实验样本', dataIndex: 'samples_b' },
    {
      title: '显著性',
      dataIndex: 'significant',
      render: (sig, record) => sig !== undefined 
        ? (sig ? <Tag icon={<TrophyOutlined />} color="gold">显著</Tag> : <Tag>不显著</Tag>)
        : '-'
    },
    {
      title: 'P-Value',
      dataIndex: 'p_value',
      render: (p) => p !== null ? p?.toFixed(4) : '-'
    },
  ];

  return (
    <div className="rl-experiment-page">
      <Card
        title="🧠 强化学习 A/B 测试平台"
        extra={
          <Button 
            type="primary" 
            icon={<PlayCircleOutlined />}
            onClick={handleCreateExp}
          >
            新建实验
          </Button>
        }
      >
        {/* 总览统计 */}
        <Row gutter={16} style={{ marginBottom: 24 }}>
          <Col span={6}>
            <Card><Statistic title="运行中实验" value={experiments.filter(e => e.status === 'running').length} /></Card>
          </Col>
          <Col span={6}>
            <Card><Statistic title="已完成实验" value={experiments.filter(e => e.status === 'completed').length} /></Card>
          </Col>
          <Col span={6}>
            <Card><Statistic title="总样本量" value={experiments.reduce((sum, e) => sum + e.samples_a + e.samples_b, 0)} /></Card>
          </Col>
          <Col span={6}>
            <Card><Statistic title="显著改进" value={experiments.filter(e => e.significant).length} suffix="/" 
              valueStyle={{ color: '#cf1322' }} /></Card>
          </Col>
        </Row>

        <Table
          dataSource={experiments}
          columns={columns}
          rowKey="id"
          loading={loading}
          pagination={{ pageSize: 10 }}
          expandable={{
            expandedRowRender: (record) => (
              <Card size="small" title="详细指标对比">
                {/* 此处嵌入 Chart.js / ECharts 图表 */}
                <RlMetricCharts experimentId={record.id} />
              </Card>
            )
          }}
        />
      </Card>
    </div>
  );
}
```

#### RL Agent 核心算法 (参考实现)
```python
# backend/app/algorithms/v2/rl_agent.py (待开发, ~500 行)
"""
基于 DQN (Deep Q-Network) 的 AGV 调度决策 Agent
State Space:   AGV 位置 + 任务队列 + 交通状态 (one-hot encoded, ~dim 256)
Action Space:  任务分配策略 (离散: 0~N 个可选任务)
Reward Signal: -时间步 -碰撞惩罚 -能耗 +完成奖励
Network:      MLP (256→128→64→ActionDim) + Dueling DQN + Prioritized Replay
"""

import torch
import torch.nn as nn
import numpy as np
from collections import deque
import random

class DQNAgent(nn.Module):
    def __init__(self, state_dim=256, action_dim=50, hidden_dim=128):
        super().__init__()
        # Dueling DQN 架构
        self.feature = nn.Sequential(
            nn.Linear(state_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU()
        )
        self.advantage = nn.Sequential(
            nn.Linear(hidden_dim // 2, 64),
            nn.ReLU(),
            nn.Linear(64, action_dim)
        )
        self.value = nn.Sequential(
            nn.Linear(hidden_dim // 2, 64),
            nn.ReLU(),
            nn.Linear(64, 1)
        )
        
    def forward(self, x):
        feat = self.feature(x)
        adv = self.advantage(feat)
        val = self.value(feat)
        # Dueling: Q(s,a) = V(s) + (A(s,a) - mean(A))
        return val + adv - adv.mean(dim=-1, keepdim=True)


class ReplayBuffer:
    def __init__(self, capacity=100000):
        self.buffer = deque(maxlen=capacity)
    
    def push(self, state, action, reward, next_state, done):
        self.buffer.append((state, action, reward, next_state, done))
    
    def sample(self, batch_size=64):
        batch = random.sample(self.buffer, batch_size)
        states, actions, rewards, next_states, dones = zip(*batch)
        return (np.array(states), np.array(actions), np.array(rewards),
                np.array(next_states), np.array(dones))

# 训练循环 (需离线训练后加载模型)
def train_rl_agent(episodes=10000, save_path='models/rl_agent.pt'):
    agent = DQNAgent()
    optimizer = torch.optim.Adam(agent.parameters(), lr=1e-4)
    buffer = ReplayBuffer()
    
    for ep in range(episodes):
        state = env.reset()
        total_reward = 0
        
        while not done:
            action = agent.select_action(state, eps=max(0.01, 1 - ep/1000))  # ε-greedy
            next_state, reward, done, env.step(action)
            
            buffer.push(state, action, reward, next_state, done)
            total_reward += reward
            
            # Experience Replay
            if len(buffer.buffer) > 1024:
                batch = buffer.sample(64)
                loss = compute_td_loss(agent, target_agent, batch)
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
        
        if ep % 100 == 0:
            print(f"Episode {ep}, Reward: {total_reward:.2f}")
            torch.save(agent.state_dict(), save_path)
```

---

### B.2 3D 数字孪生完整版

#### 功能路线图
```
Phase 2a (Month 3-4): 基础增强
├── 点云地图导入 (.pcd/.ply → Three.js Points)
├── SLAM 轨迹叠加显示
├── AGV 历史轨迹回放 (时间轴控制)
└── 多视角切换系统 (Free/Follow/God)

Phase 2b (Month 5): 分析层
├── 热力图渲染 (拥堵区域/热点)
├── 速度场可视化 (矢量箭头)
├── 能耗分布图 (颜色梯度)
└── 统计图表悬浮窗 (ECharts)

Phase 2c (Month 6): 高级功能
├── VR 输出 (WebXR API)
├── AR 标记 (AR.js / WebXR AR)
├── 多用户协作 (Colyseus.io WebSocket)
└── 导出视频/截图 (CCapture.js)
```

#### 点云导入模块
```typescript
// frontend/src/components/PointCloudViewer.tsx (新建)
import * as THREE from 'three';
import { PCDLoader } from 'three/examples/jsm/loaders/PCDLoader.js';

function PointCloudMap({ url }: { url: string }) {
  const pointsRef = useRef<THREE.Points>(null);

  useEffect(() => {
    const loader = new PCDLoader();
    loader.load(url, (points) => {
      // 点云材质优化
      const material = points.material as THREE.PointsMaterial;
      material.size = 0.3;           // 点大小
      material.color.setHex(0x888888);  // 灰色点云
      
      // 性能优化: 大点云需要降采样
      if (points.geometry.attributes.position.count > 1_000_000) {
        downsamplePointCloud(points.geometry, target=500000);
      }

      if (pointsRef.current) {
        pointsRef.current = points;
      }
    });
  }, [url]);

  return <primitive object={pointsRef.current} />;
}

// PCD 文件加载器 (如果 Three.js PCDLoader 不够用)
async function loadPCDCustom(url: string): Promise<THREE.BufferGeometry> {
  const response = await fetch(url);
  const text = await response.text();
  const lines = text.split('\n');
  
  let vertexCount = 0;
  const vertices: number[] = [];
  const colors: number[] = [];
  
  // 解析 PCD 格式 (简化版)
  for (const line of lines) {
    if (line.startsWith('DATA')) continue;
    if (line.startsWith('#') || line.trim() === '') continue;
    
    const parts = line.trim().split(/\s+/);
    if (parts.length >= 3) {
      vertices.push(parseFloat(parts[0]), parseFloat(parts[1]), parseFloat(parts[2]));
      if (parts.length >= 6) {
        colors.push(parseFloat(parts[3])/255, parseFloat(parts[4])/255, parseFloat(parts[5])/255);
      }
      vertexCount++;
    }
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
  if (colors.length > 0) {
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  }
  
  return geometry;
}
```

#### 热力图生成器 (拥堵分析)
```typescript
// frontend/src/utils/heatmapGenerator.ts (新建)
/**
 * 基于 AGV 历史位置数据生成热力图
 * 算法: 高斯核密度估计 (KDE)
 */
import * as THREE from 'three';

interface HeatmapConfig {
  gridSize: number;        // 网格分辨率 (如 100×100)
  sigma: number;           // 高斯核带宽
  domainSize: { x: number; y: number };  // 物理空间大小 (米)
  colorStops: [number, string][];  // 颜色梯度
}

const DEFAULT_CONFIG: HeatmapConfig = {
  gridSize: 100,
  sigma: 2.0,
  domainSize: { x: 100, y: 100 },
  colorStops: [
    [0.0, 'rgba(0,0,255,0)],       // 蓝 (冷)
    [0.25, 'rgba(0,255,255,0.5)'],
    [0.5, 'rgba(0,255,0,0.8)'],
    [0.75, 'rgba(255,255,0,0.9)'],
    [1.0, 'rgba(255,0,0,1)'],       // 红 (热)
  ]
};

export function generateHeatmap(
  positions: Array<{ x: number; y: number; weight?: number }>,
  config: Partial<HeatmapConfig> = {}
): THREE.Mesh {
  const cfg = { ...DEFAULT_CONFIG, ...config };
  const { gridSize, sigma, domainSize, colorStops } = cfg;

  // 初始化密度网格
  const density = new Float32Array(gridSize * gridSize);
  const cellSizeX = domainSize.x / gridSize;
  const cellSizeY = domainSize.y / gridSize;

  // KDE: 每个点贡献高斯分布
  const radius = Math.ceil(sigma * 3);  // 3σ 覆盖范围
  for (const pos of positions) {
    const gx = Math.floor(pos.x / cellSizeX);
    const gy = Math.floor(pos.y / cellSizeY);
    const w = pos.weight ?? 1.0;

    for (let dy = -radius; dy <= radius; dy++) {
      for (let dx = -radius; dx <= radius; dx++) {
        const nx = gx + dx, ny = gy + dy;
        if (nx < 0 || nx >= gridSize || ny < 0 || ny >= gridSize) continue;

        const distSq = dx*dx + dy*dy;
        const gaussian = Math.exp(-distSq / (2 * sigma*sigma));
        density[ny * gridSize + nx] += w * gaussian;
      }
    }
  }

  // 归一化
  const maxVal = Math.max(...density);
  if (maxVal > 0) {
    for (let i = 0; i < density.length; i++) density[i] /= maxVal;
  }

  // 生成纹理贴图
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = gridSize;
  const ctx = canvas.getContext('2d')!;
  const imageData = ctx.createImageData(gridSize, gridSize);

  for (let y = 0; y < gridSize; y++) {
    for (let x = 0; x < gridSize; x++) {
      const val = density[y * gridSize + x];
      const color = interpolateColor(colorStops, val);
      const idx = (y * gridSize + x) * 4;
      imageData.data[idx] = color.r;
      imageData.data[idx+1] = color.g;
      imageData.data[idx+2] = color.b;
      imageData.data[idx+3] = val * 200;  // 半透明
    }
  }
  ctx.putImageData(imageData, 0, 0);

  const texture = new THREE.CanvasTexture(canvas);
  texture.magFilter = THREE.LinearFilter;

  // 平面网格
  const geometry = new THREE.PlaneGeometry(domainSize.x, domainSize.y, gridSize-1, gridSize-1);
  const material = new THREE.MeshBasicMaterial({
    map: texture,
    transparent: true,
    side: THREE.DoubleSide,
    depthWrite: false,  // 不遮挡其他物体
  });

  return new THREE.Mesh(geometry, material);
}

// 颒色插值辅助
function interpolateColor(stops: [number, string][], t: number): { r: number; g: number; b: number } {
  let lower = stops[0], upper = stops[stops.length - 1];
  for (let i = 0; i < stops.length - 1; i++) {
    if (t >= stops[i][0] && t <= stops[i+1][0]) {
      lower = stops[i]; upper = stops[i+1];
      break;
    }
  }
  const range = upper[0] - lower[0];
  const factor = range === 0 ? 0 : (t - lower[0]) / range;
  // 解析 hex 颜色并 lerp (省略解析代码...)
  return lerpColor(parseColor(lower[1]), parseColor(upper[1]), factor);
}
```

#### VR 模块 (WebXR)
```typescript
// frontend/src/components/VRExperience.tsx (可选, Month 6)
import { XRButton, XRController } from '@react-three/xr';

function VRExperience() {
  return (
    <>
      <VRButton />
      <XRSpace>
        <XRController />
        {/* VR 手柄射线交互 */}
        <XRRay
          onSelect={(obj) => console.log('Selected:', obj)}
          onHover={() => {}}
        />
        
        {/* VR UI 面板 (固定在头部前方) */}
        <VRUIPanel position={[0, 1.6, -2]}>
          <Text>AGV Status Panel</Text>
          {agvs.map(agv => (
            <VRAgvStatus key={agv.id} agv={agv} />
          ))}
        </VRUIPanel>
      </XRSpace>
    </>
  );
}
```

---

## 📊 Part C: 资源需求与风险

### C.1 人力估算 (人月)

| 任务 | 前端工程师 | 后端工程师 | 算法工程师 | 测试/UI | 合计 |
|------|-----------|-----------|-----------|---------|------|
| M2: WS 联动 | 1.5 人月 | 0.5 人月 | - | 0.5 | 2.5 |
| M2: Demo 视频 | 0.5 人月 | - | - | 0.5 | 1.0 |
| M2: 压测报告 | 0.5 | 1.0 | - | 0.5 | 2.0 |
| Q2: RL 框架 | 1.0 | 1.0 | 2.0 | 0.5 | 4.5 |
| Q2: 3D 孪生完整版 | 2.0 | 0.5 | 0.5 | 1.0 | 4.0 |
| **总计** | **5.5** | **3.0** | **2.5** | **3.0** | **14.0** |

### C.2 硬件需求
| 用途 | 配置建议 | 租赁成本/月 |
|------|----------|------------|
| 开发测试 | 16 核 / 32GB / RTX 3060 | ¥2000 (云服务器) |
| RL 训练 | 32 核 / 64GB / A100 40GB | ¥15000 (GPU 云) |
| 压测集群 | 8× 8 核 VM (k6 distributed) | ¥3000 |
| VR 测试设备 | Meta Quest 3 / Apple Vision Pro | ¥3000 (采购) |

### C.3 风险登记册

| ID | 风险描述 | 概率 | 影响 | 应对策略 |
|----|---------|------|------|----------|
| R1 | RL Agent 收敛困难 (稀疏奖励) | 高 | 高 | 先用 imitation learning 预训练, 再 fine-tune |
| R2 | 500 AGV 下前端 FPS 崩溃 | 中 | 高 | 强制启用 InstancedMesh + LOD |
| R3 | WebXR 兼容性差 (浏览器限制) | 中 | 中 | 降级为桌面 3D 版本, VR 作为加分项 |
| R4 | 压测暴露 DB 瓶颈 | 中 | 中 | 引入 Redis 缓存层 + 读写分离 |
| R5 | 团队人力不足 (仅 1-2 人) | 高 | 高 | **砍掉 RL/VR**, 优先保证 WS+压测 |

### C.4 裁剪建议 (若资源受限)

**最低可行性版本 (MVP):**
- ✅ WS 30FPS 联动 (必须)
- ✅ 基础性能压测 (100 AGV)
- ⏸️ Demo 视频 (可延后)
- ❌ RL A/B 测试 (推迟到有专职算法工程师)
- ❌ VR 功能 (推迟到有硬件支持)
- ❌ 3D 孪生高级版 (保留基础 3D 即可)

---

## 📝 附录

### A. 相关文件索引
```
docs/
├── NEXT-STEPS-ROADMAP.md              # 总路线图 (本文档的上游)
├── MONTH2-Q2-DEEP-DIVE.md            # 本文档
├── demo-scenario-100agv.json          # Demo 录制数据
├── perf-stress-test-report-month2.md  # 压测报告 (待填写)
│
frontend/
├── src/
│   ├── components/
│   │   ├── DigitalTwin3D.tsx          # 3D 场景 (本月重点)
│   │   ├── PointCloudViewer.tsx       # 点云查看器
│   │   └── VRExperience.tsx           # VR 模块 (Q2)
│   ├── pages/
│   │   └── RlExperiment/index.tsx     # RL 实验控制台 (Q2)
│   ├── utils/
│   │   └── heatmapGenerator.ts        # 热力图工具
│   └── services/
│       └── unifiedApi.ts              # WS Manager (已存在)
│
backend/
├── app/
│   ├── api/
│   │   ├── v2/
│   │   │   └── rl_experiment.py       # RL API (Q2 待建)
│   │   └── digital_twin_ws.py         # WS 端点 (已存在)
│   └── algorithms/v2/
│       ├── rl_agent.py                # RL Agent (Q2 待建)
│       └── hybrid_scheduler.py        # 已存在
├── scripts/
│   ├── generate_demo_scenario.py      # Demo 数据生成
│   └── bench_ws.py                    # WS 压测脚本
└── tests/
    └── test_rl_agent.py               # RL 单元测试 (Q2)
```

### B. 技术选型对比
| 维度 | 当前技术 | 备选方案 | 建议 |
|------|----------|----------|------|
| 3D 渲染 | Three.js + R3F | Babylon.js | 保持 Three.js (生态更大) |
| 图表 | Ant Design Charts | ECharts | ECharts (热力图更强大) |
| 压测 | k6 | Locust | k6 (JS 生态一致) |
| RL 框架 | PyTorch + Stable-Baselines3 | TensorFlow + Ray RLlib | SB3 (轻量易上手) |
| WS 库 | 原生 WebSocket | Socket.IO | 原生 (减少依赖) |

---

*本文档应作为 Month 2/Q2 实施的技术权威参考, 每月 review 更新一次。*
