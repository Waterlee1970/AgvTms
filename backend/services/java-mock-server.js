#!/usr/bin/env node
/**
 * AGV-TMS Java/Spring Boot Mock Backend (端口 8080)
 */

const http = require('http');
const url = require('url');
const PORT = process.env.PORT || 8080;
const LABEL = 'Java';

const MOCK_AGVS = [
  { id: 'AGV001', name: `AGV-1号 [${LABEL}]`, x: 0, y: 0, battery: 94, status: 'executing', current_task: 'T100', current_node: 'N_P00' },
  { id: 'AGV002', name: `AGV-2号 [${LABEL}]`, x: 24, y: 0, battery: 87, status: 'executing', current_task: 'T101', current_node: 'N_P02' },
];

function corsHeaders() {
  return { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type', 'Content-Type': 'application/json; charset=utf-8', 'X-Backend': LABEL };
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
      const b = body ? JSON.parse(body) : {};
      if (pathname === '/api/agv/status' && method === 'GET')
        result = { code: 200, data: MOCK_AGVS, backend: 'java' };
      else if (pathname === '/api/schedule/run' && method === 'POST')
        result = { code: 200, data: { id: `java-${Date.now()}`, assignments: MOCK_AGVS.map(a => ({ agv_id: a.id, task_id: a.current_task, path: [a.current_node], path_cost: 30.2 })), total_cost: 920.0, makespan: 72.5, metrics: { total_makespan: 72.5, total_agv_travel_distance: 180.5 }, algorithm_runtime_ms: 55 }, backend: 'java' };
      else if (pathname === '/api/map/graph' && method === 'GET')
        result = { code: 200, data: { nodes: [{ id: 'N_P00', name: 'P00', x: 0, y: 0 }], edges: [], name: 'Map [Java]', version: 1 }, backend: 'java' };
      else if (pathname === '/api/v2/test/gateway-status' && method === 'GET')
        result = { code: 200, data: { initialized: true, registered_services: 4, circuit_breakers: {} }, backend: 'java' };
      else if (pathname === '/api/v2/events/publish' && method === 'POST')
        result = { code: 200, data: { success: true, event_id: `java-evt-${Date.now().toString(16)}` }, backend: 'java' };
      else if (pathname === '/health')
        result = { status: 'healthy', backend: 'java', port: PORT };
      else { res.writeHead(404); res.end(JSON.stringify({ error: 'Not Found' })); return; }
      res.writeHead(200); res.end(JSON.stringify(result));
    } catch(e) { res.writeHead(500); res.end(JSON.stringify({ error: e.message })); }
  });
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`\n🟠 Java Mock Backend running:`);
  console.log(`   Local:  http://localhost:${PORT}`);
  console.log(`   Health: http://localhost:${PORT}/health\n`);
});
