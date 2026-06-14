"""
AGV-TMS Backend Server Entry Point.

Usage:
    python run.py              # Start on default port 8000
    python run.py --port 9000  # Start on custom port
    python run.py --reload     # Development mode with auto-reload
"""

import argparse
import uvicorn


def main():
    parser = argparse.ArgumentParser(description="AGV-TMS Backend Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    args = parser.parse_args()

    print(f"""
╔══════════════════════════════════════════════════════════╗
║         AGV-TMS 柔性物流调度系统 v1.0.0                    ║
║                                                          ║
║  算法引擎: 蚁群算法(ACO) + 模拟退火(SA) + 非线性规划(NLP)  ║
║  API 文档: http://{args.host}:{args.port}/docs                ║
║  WebSocket: ws://{args.host}:{args.port}/ws/schedule/live    ║
╚══════════════════════════════════════════════════════════╝
    """)

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
