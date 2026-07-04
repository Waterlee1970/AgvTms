#!/usr/bin/env node
/**
 * AGV-TMS Node.js Mock Backend (端口 3001)
 * 用于多语言后端集成测试
 */

const http = require('http');
const url = require('url');

const PORT = 3001;

// 模拟 AGV 数据
const MOCK_AGVS = [
  { id: 'AGV001', name: 'AGV-1号 [Node.js]', x: 0, y: 0, battery: 95, status: 'executing', current_task: 'T053', current_node: 'N_P00' },
  { id: 'AGV002', name: 'AGV-2号 [Node.js]', x: 12, y: 0, battery: 88, status: 'executing', current_task: 'T062', current_node: 'N_P01' },
  { id: 'AGV003', name: 'AGV-3号 [Node.js]', x: 24, y: 0, battery: 72, status: 'idle', current_task: null, current_node: 'N_P02' },
];

// CORS 头
function corsHeaders(req) {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Content-Type': 'application/json',
  };
}

// 路由处理
const routes = {
  // TC-001: AGV 状态查询
  'GET /api/agv/status': () => ({ code: 200, data: MOCK_AGVS, backend: 'nodejs' }),
  
  // TC-002: 任务调度
  'POST /api/schedule/run': (body) => ({
    code: 200,
    data: {
      id: `node-${Date.now()}`,
      assignments: MOCK_AGVS.filter(a => a.status === 'executing').map(a => ({
        agv_id: a.id,
        task_id: a.current_task,
        path: [a.current_node],
        path_cost: Math.random() * 50 + 10,
        start_time: Date.now(),
        end_time: Date.now() + 60000,
      })),
      total_cost: Math.random() * 1000 + 500,
      makespan: Math.random() * 100 + 50,
      metrics: {
        total_makespan: 85.5,
        total_agv_travel_distance: 234.8,
        total_conveyor_energy: 0,
        agv_utilization: 0.78,
        task_completion_rate: 1.0,
        avg_task_wait_time: 2.3,
        collision_count: 0,
        conveyor_throughput: 0,
      },
      algorithm_runtime_ms: 45,
    },
    backend: 'nodejs',
  }),

  // TC-003: 地图图元
  'GET /api/map/graph': () => ({
    code: 200,
    data: {
      nodes: [{ id: 'N_P00', name: 'P00', x: 0, y: 0, type: 'pickup' }],
      edges: [{ from_node: 'N_P00', to_node: 'N_P01', distance: 12, direction: 'bidirectional' }],
      name: 'Demo Map (Node.js)',
      version: 1,
    },
    backend: 'nodejs',
  }),

  // TC-004: Gateway 状态
  'GET /api/v2/test/gateway-status': () => ({
    code: 200,
    data: { initialized: true, registered_services: 3, circuit_breakers: {} },
    backend: 'nodejs',
  }),

  // TC-005: Kafka 事件
  'POST /api/v2/events/publish': (body) => ({
    code: 200,
    data: { success: true, event_id: `node-evt-${Date.now().toString(16)}` },
    backend: 'nodejs',
  }),

  // 健康检查
  'GET /health': () => ({ status: 'healthy', backend: 'nodejs', port: PORT }),
};

const server = http.createServer((req, res) => {
  const parsed = url.parse(req.url, true);
  const method = req.method;
  const pathname = parsed.pathname;
  const routeKey = `${method} ${pathname}`;

  // 设置 CORS
  const headers = corsHeaders(req);
  Object.entries(headers).forEach(([k, v]) => res.setHeader(k, v));

  // OPTIONS 预检
  if (method === 'OPTIONS') {
    res.writeHead(204);
    res.end();
    return;
  }

  console.log(`[Node.js] ${method} ${pathname}`);

  // 路由匹配
  let body = '';
  req.on('data', chunk => body += chunk);
  req.on('end', () => {
    try {
      const handler = routes[routeKey];
      if (handler) {
        const parsedBody = body ? JSON.parse(body) : {};
        const result = typeof handler === 'function' ? handler(parsedBody) : handler;
        res.writeHead(200);
        res.end(JSON.stringify(result));
      } else {
        res.writeHead(404);
        res.end(JSON.stringify({ error: 'Not Found', route: routeKey }));
      }
    } catch (e) {
      res.writeHead(500);
      res.end(JSON.stringify({ error: e.message }));
    }
  });
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`\n🟢 Node.js Mock Backend running:`);
  console.log(`   Local:   http://localhost:${PORT}`);
  console.log(`   Health:  http://localhost:${PORT}/health`);
  console.log(`   Time:    ${new Date().toLocaleString()}\n`);
});
