"""
AGV-TMS Backend Server Entry Point — Phase 5: 工程化加固

Usage:
    python run.py                    # Start on default port 8000 (dev)
    python run.py --port 9000        # Start on custom port
    python run.py --reload           # Development mode with auto-reload
    python run.py --production       # Production mode (Gunicorn + workers)
    python run.py --workers 8        # Specify worker count
    python run.py --health-only      # Run only health check (for K8s liveness)
"""

import argparse
import os
import sys
import multiprocessing

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def get_worker_count(default: int = None) -> int:
    """根据 CPU 核心数自动确定 worker 数量."""
    if default:
        return default
    
    cpu_count = multiprocessing.cpu_count()
    # 生产环境: 2*CPU + 1, 开发环境: min(4, CPU)
    env = os.getenv("ENVIRONMENT", "development")
    
    if env == "production":
        return min(cpu_count * 2 + 1, 16)  # 上限16 workers
    else:
        return min(4, max(1, cpu_count))


def start_dev_server(host: str, port: int, reload: bool = False):
    """开发模式: uvicorn 单进程 + 自动重载."""
    import uvicorn
    
    print(f"""
╔══════════════════════════════════════════════════════════╗
║         AGV-TMS 柔性物流调度系统 v1.8.0                   ║
║                                                          ║
║  Mode: Development (Dev)                                  ║
║  Workers: 1 (auto-reload enabled)                         ║
║  API Docs: http://{host}:{port}/docs                       ║
║  WebSocket: ws://{host}:{port}/ws/schedule/live            ║
╚══════════════════════════════════════════════════════════╝
    """)
    
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
        access_log=True,
        timeout_keep_alive=65,
    )


def start_production_server(host: str, port: int, workers: int):
    """生产模式: Gunicorn + Uvicorn workers."""
    try:
        import gunicorn.app.base as gbase
        
        class StandaloneApplication(gbase.BaseApplication):
            def __init__(self, app_uri, options=None):
                self.options = options or {}
                self.application_uri = app_uri
                super().__init__()
                
            def load_config(self):
                for key, value in self.options.items():
                    if key in self.cfg.settings and value is not None:
                        self.cfg.set(key.lower(), value)
                        
                self.cfg.set("default_proc_name", "agvtms-server")
                
            def load(self):
                from app.main import app
                return app
        
        options = {
            "bind": f"{host}:{port}",
            "workers": workers,
            "worker_class": "uvicorn.workers.UvicornWorker",
            "worker-tmp-dir": "/dev/shm",
            "timeout": 120,
            "keepalive": 5,
            "graceful-timeout": 30,
            "max-requests": 10000,
            "max-requests-jitter": 500,
            "preload_app": True,
            "accesslog": "-",
            "errorlog": "-",
            "log-level": "info",
        }
        
        print(f"""
╔══════════════════════════════════════════════════════════╗
║         AGV-TMS 柔性物流调度系统 v1.8.0                   ║
║                                                          ║
║  Mode: Production (Prod)                                  ║
║  Workers: {workers} (CPU cores: {multiprocessing.cpu_count()})                      ║
║  API 文档: http://{host}:{port}/docs                       ║
║  Health:   http://{host}:{port}/health                     ║
╚══════════════════════════════════════════════════════════╝
        """)
        
        StandaloneApplication("app.main:app", options).run()
        
    except ImportError:
        # Fallback to uvicorn if gunicorn not installed
        print("⚠️ Gunicorn not installed, falling back to uvicorn multi-worker mode")
        import uvicorn
        uvicorn.run(
            "app.main:app",
            host=host,
            port=port,
            workers=workers,
            log_level="info",
            access_log=True,
        )


def health_check_only(port: int = 8000):
    """仅运行健康检查端点 (用于 K8s liveness probe)."""
    import uvicorn
    from fastapi import FastAPI, Response
    
    health_app = FastAPI()
    
    @health_app.get("/health")
    async def health():
        return {"status": "ok"}
    
    @health_app.get("/ready")
    async def ready():
        # 这里可以添加更详细的就绪检查
        return {"status": "ready"}
    
    uvicorn.run(
        health_app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
    )


def main():
    parser = argparse.ArgumentParser(
        description="AGV-TMS Backend Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run.py --reload              Dev mode with auto-reload
  python run.py --production          Prod mode (auto workers)
  python run.py --workers 8 --prod    Custom worker count
  python run.py --health-only         Health check only (K8s)
        """
    )
    
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"), help="Host to bind")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")), help="Port to bind")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload (dev)")
    parser.add_argument("-w", "--workers", type=int, default=None, help="Number of worker processes")
    parser.add_argument("--production", "--prod", action="store_true", dest="production", help="Production mode")
    parser.add_argument("--health-only", action="store_true", help="Run only health check endpoint")
    args = parser.parse_args()

    # 设置环境变量
    os.environ.setdefault("ENVIRONMENT", "production" if args.production else "development")
    
    if args.health_only:
        health_check_only(args.port)
    elif args.production or os.getenv("ENVIRONMENT") == "production":
        workers = args.workers or get_worker_count()
        start_production_server(args.host, args.port, workers)
    else:
        start_dev_server(args.host, args.port, args.reload)


if __name__ == "__main__":
    main()
