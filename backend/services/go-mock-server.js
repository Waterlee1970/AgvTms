#!/usr/bin/env node
/**
 * AGV-TMS Go/Gin Mock Backend (端口 9000)
 */

const http = require('http');
const url = require('url');
const PORT = process.env.PORT || 9000;
const LABEL = 'Go';

function corsHeaders() {
  return { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type', 'Content-Type': 'application/json; charset=utf-8' };
}

const server = http.createServer((req, res) => {
  const parsed = url.parse(req.url, true);
  const method = req.method;
  const pathname = parsed.pathname;
  
  Object.entries(corsHeaders()).forEach(([k, v]) => res.setHeader(k, v));
  if (method === 'OPTIONS') { res.writeHead(204); res.end(); return; }
  console.log(`[Go] ${method} ${pathname}`);
  
  let body = '';
  req.on('data', c => body += c);
  req.on('end', () => {
    try {
      if (pathname === '/api/agv/status' && method === 'GET')
        res.end(JSON.stringify({ code: 200, data: [{ id: 'AGV001', name: 'AGV-1号 [Go]', x: 0, y: 0, battery: 99, status: 'executing', current_task: 'T001' }], backend: 'go' }));
      else if (pathname === '/api/schedule/run' && method === 'POST')
        res.end(JSON.stringify({ code: 200, data: { id: `go-${Date.now()}`, assignments: [{ agv_id: 'AGV001', task_id: 'T001', path: ['N_P00'] }], total_cost: 500, makespan: 40, metrics: {}, algorithm_runtime_ms: 10 }, backend: 'go' }));
      else if (pathname === '/api/map/graph' && method === 'GET')
        res.end(JSON.stringify({ code: 200, data: { nodes: [], edges: [], name: 'Map [Go]', version: 1 }, backend: 'go' }));
      else if (pathname === '/api/v2/test/gateway-status' && method === 'GET')
        res.end(JSON.stringify({ code: 200, data: { initialized: true, registered_services: 1 }, backend: 'go' }));
      else if (pathname === '/api/v2/events/publish' && method === 'POST')
        res.end(JSON.stringify({ code: 200, data: { success: true, event_id: `go-evt-${Date.now().toString(16)}` }, backend: 'go' }));
      else if (pathname === '/health')
        res.end(JSON.stringify({ status: 'healthy', backend: 'go', port: PORT }));
      else { res.writeHead(404); res.end(JSON.stringify({ error: 'Not Found' })); return; }
      res.writeHead(200);
    } catch(e) { res.writeHead(500); res.end(JSON.stringify({ error: e.message })); }
  });
});

server.listen(PORT, '0.0.0.0', () => console.log(`\n🟢 Go Mock Backend running: http://localhost:${PORT}\n`));
