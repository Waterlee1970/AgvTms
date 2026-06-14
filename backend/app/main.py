"""
FastAPI Application Entry Point.

AGV-TMS: Flexible Logistics Scheduling System
Hybrid scheduling with ACO + SA + NLP
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router as routes_router
from .api.evaluator_api import router as evaluator_router

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="AGV-TMS 柔性物流调度系统",
    description="""
## AGV + 输送线混合调度系统

核心功能：
- **蚁群算法 (ACO)**: 多AGV路径规划
- **模拟退火 (SA)**: 任务最优分配
- **非线性规划 (NLP)**: 输送线任务排序
- **混合调度引擎**: 整合三种算法，解决AGV与固定输送线协同调度

对标：海康威视RCS、博士输送线、罗克韦尔APS
    """,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(routes_router)  # /api/* (核心业务API)
app.include_router(evaluator_router)  # /api/v2/evaluator/* (算法评测API)


@app.get("/")
async def root():
    return {
        "name": "AGV-TMS 柔性物流调度系统",
        "version": "1.0.0",
        "docs": "/docs",
        "algorithms": ["ACO", "SA", "NLP", "HYBRID"],
    }


@app.get("/health")
async def health_check():
    return {"status": "healthy", "algorithms_loaded": True}
