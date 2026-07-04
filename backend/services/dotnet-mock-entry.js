#!/usr/bin/env dotnet-script
// AGV-TMS .NET Mock Backend (端口 5000)
// 使用简单的 HTTP 监听器模拟 REST API

// 注意: 这是一个独立脚本，不需要完整 .NET 项目
// 运行方式: node dotnet-mock-server.js (复用 Node.js 实现)

console.log("=== .NET Mock Backend ===");
console.log("端口 5000 - 复用 Node.js 模式");
console.log("启动 /Users/water/Documents/AgvTms/backend/services/dotnet-mock-server.js");

// 直接用 Node.js 启动 .NET mock
require('child_process').fork(__dirname + '/dotnet-mock-server.js', [], {
  env: { ...process.env, PORT: '5000', BACKEND_LABEL: '.NET/C#' }
});
