#!/usr/bin/env node
/**
 * AGV-TMS .NET/C# Mock Backend (端口 5000)
 * 用于多语言后端集成测试
 */

const http = require('http');
const url = require('url');
const PORT = process.env.PORT || 5000;
const LABEL = process.env.BACKEND_LABEL || '.NET';

const MOCK_AGVS = [
  { id: 'AGV001', name: `AGV-1号 [${LABEL}]`, x: 0, y: 0, battery: 96, status: 'executing', current_task: 'T001', current_node: 'N_P00' },
  { id: 'AGV002', name: `AGV-2号 [${LABEL}]`, x: 12, y: 0, battery: 89, status: 'idle', current_task: null, current_node: 'N_P01' },
];

function corsHeaders() {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Content-Type': 'application/json; charset=utf-8',
    'X-Backend': LABEL,
  };
}

const server = http.createServer((req, res) => {
  const parsed = url.parse(req.url, true);
  const method = req.method;
  const pathname = parsed.pathname;
  
  Object.entries(corsHeaders()).forEach(([k, v]) => res.setHeader(k, v));
  if (method === 'OPTIONS') { res.writeHead(204); res.end(); return; }
  
  console.log(`[${LABEL}] ${method} ${pathname}`);

  let body = '';
  req.on('data', c => body += c);
  req.on('end', () => {
    let result;
    try {
      const parsedBody = body ? JSON.parse(body) : {};
      
      if (pathname === '/api/agv/status' && method === 'GET')
        result = { code: 200, data: MOCK_AGVS, backend: LABEL.toLowerCase() };
      else if (pathname === '/api/schedule/run' && method === 'POST')
        result = { code: 200, data: { id: `${LABEL.toLowerCase()}-${Date.now()}`, assignments: MOCK_AGVS.slice(0,1).map(a => ({ agv_id: a.id, task_id: a.current_task, path: [a.current_node], path_cost: 25.5 })), total_cost: 850.5, makespan: 65.3, metrics: { total_makespan: 65.3, total_agv_travel_distance: 120, task_completion_rate: 1.0 }, algorithm_runtime_ms: 38 }, backend: LABEL.toLowerCase() };
      else if (pathname === '/api/map/graph' && method === 'GET')
        result = { code: 200, data: { nodes: [{ id: 'N_P00', name: 'P00', x: 0, y: 0, type: 'pickup' }], edges: [{ from_node: 'N_P00', to_node: 'N_P01', distance: 12 }], name: `Map [${LABEL}]`, version: 1 }, backend: LABEL.toLowerCase() };
      else if (pathname === '/api/v2/test/gateway-status' && method === 'GET')
        result = { code: 200, data: { initialized: true, registered_services: 2, circuit_breakers: {} }, backend: LABEL.toLowerCase() };
      else if (pathname === '/api/v2/events/publish' && method === 'POST')
        result = { code: 200, data: { success: true, event_id: `${LABEL.toLowerCase()}-evt-${Date.now().toString(16)}` }, backend: LABEL.toLowerCase() };
      else if (pathname === '/health')
        result = { status: 'healthy', backend: LABEL.toLowerCase(), port: PORT };
      else {
        res.writeHead(404); res.end(JSON.stringify({ error: 'Not Found', backend: LABEL })); return;
      }
      
      res.writeHead(200); res.end(JSON.stringify(result));
    } catch (e) { res.writeHead(500); res.end(JSON.stringify({ error: e.message })); }
  });
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`\n🔵 ${LABEL} Mock Backend running:`);
  console.log(`   Local:  http://localhost:${PORT}`);
  console.log(`   Health: http://localhost:${PORT}/health\n`);
});
