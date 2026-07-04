#!/bin/bash
#
# AGV-TMS 一键性能基准测试脚本 — v2.3 Phase 4.0 P2-03
#
# 用法:
#   ./run_benchmark.sh [OPTIONS]
#
# Options:
#   -s, --size PRESET     场景规模: small|medium|large|xlarge|stress (default: medium)
#   -H, --host URL        目标服务地址 (default: http://localhost:8000)
#   -u, --users NUM       最大并发用户数 (default: auto by preset)
#   -t, --time MINUTES    运行时长分钟 (default: auto by preset)
#   --no-inject           跳过数据注入步骤 (使用已有数据)
#   --headless            无头模式 (不打开浏览器UI)
#   --output-dir DIR      输出目录 (default: results/TIMESTAMP)
#   -h, --help            显示帮助
#
# 示例:
#   ./run_benchmark.sh                          # 中等规模默认
#   ./run_benchmark.sh -s small --no-inject     # 小规模不注入
#   ./run_benchmark.sh -s large -H http://192.168.1.100:8000
#   ./run_benchmark.sh -s stress -u 500 -t 10   # 压力测试
#

set -euo pipefail

# ==================== 颜色输出 ====================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'  # No Color

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "\n${BLUE}======> $*${NC}"; }

# ==================== 默认配置 ====================
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BENCH_DIR="$SCRIPT_DIR"
SCENARIOS_DIR="$BENCH_DIR/scenarios"
RESULTS_DIR=""

HOST_URL="${BENCH_HOST:-http://localhost:8000}"
SCENE_SIZE="medium"
NO_INJECT=false
HEADLESS_FLAG=""
USERS=""
RUN_TIME=""

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")

# ==================== 规模预设 ====================
declare -A SIZE_CONFIG
SIZE_CONFIG=(
    [small]="users:10,time:2,agvs:10,tasks:50"
    [medium]="users:50,time:5,agvs:50,tasks:200"
    [large]="users:150,time:8,agvs:100,tasks:500"
    [xlarge]="users:300,time:10,agvs:200,tasks:1000"
    [stress]="users:500,time:15,agvs:500,tasks:2000"
)

# ==================== 参数解析 ====================
usage() {
    cat << 'EOF'
AGV-TMS Performance Benchmark v2.3

Usage: ./run_benchmark.sh [OPTIONS]

Options:
  -s, --size PRESET     Scenario size: small|medium|large|xlarge|stress
  -H, --host URL        Target service URL (default: http://localhost:8000)
  -u, --users NUM       Max concurrent users (overrides preset)
  -t, --time MINUTES    Duration in minutes (overrides preset)
  --no-inject           Skip data injection step
  --headless            Run without browser UI
  --output-dir DIR      Custom output directory
  -h, --help            Show this help

Presets:
  small   →  10 users,  2 min,   10 AGVs,    50 tasks    (quick smoke test)
  medium  →  50 users,  5 min,   50 AGVs,   200 tasks    (standard baseline)
  large   → 150 users,  8 min,  100 AGVs,   500 tasks    (load test)
  xlarge  → 300 users, 10 min,  200 AGVs,  1000 tasks    (stress test)
  stress  → 500 users, 15 min,  500 AGVs,  2000 tasks    (extreme pressure)
EOF
}

while [[ $# -gt 0 ]]; do
    case $1 in
        -s|--size) SCENE_SIZE="$2"; shift 2;;
        -H|--host) HOST_URL="$2"; shift 2;;
        -u|--users) USERS="$2"; shift 2;;
        -t|--time) RUN_TIME="$2"; shift 2;;
        --no-inject) NO_INJECT=true; shift;;
        --headless) HEADLESS_FLAG="--headless"; shift;;
        --output-dir) RESULTS_DIR="$2"; shift 2;;
        -h|--help) usage; exit 0;;
        *) log_error "Unknown option: $1"; usage; exit 1;;
    esac
done

# 解析规模配置
IFS=',' read -ra CONFIG_PARTS <<< "${SIZE_CONFIG[$SCENE_SIZE]:-${SIZE_CONFIG[medium]}}"
for part in "${CONFIG_PARTS[@]}"; do
    key="${part%%:*}"
    val="${part##*:}"
    case $key in
        users)  [ -z "$USERS" ]  && USERS="$val"  ;;
        time)   [ -z "$RUN_TIME" ] && RUN_TIME="$val" ;;
    esac
done

RESULTS_DIR="${RESULTS_DIR:-$BENCH_DIR/results/$TIMESTAMP}"
mkdir -p "$RESULTS_DIR"

# ==================== Banner ====================
cat << EOF

╔═══════════════════════════════════════════════════════════╗
║                                                           ║
║   AGV-TMS Performance Benchmark v2.3                       ║
║   Phase 4.0 P2-03                                         ║
║                                                           ║
║   Target:   ${HOST_URL}
║   Size:     ${SCENE_SIZE}
║   Users:    ${USERS}
║   Duration: ${RUN_TIME}min
║   Output:   ${RESULTS_DIR}
║                                                           ║
╚═══════════════════════════════════════════════════════════╝
EOF

# ==================== Step 1: 依赖检查 ====================
step_1_dependencies() {
    log_step "Step 1/6: Checking dependencies..."
    
    local missing=0
    
    command -v python3 >/dev/null 2>&1 || { log_error "python3 not found"; missing=$((missing+1)); }
    command -v locust >/dev/null 2>&1 || { log_warn "locust not found, installing..."; pip install -q locust || missing=$((missing+1)); }
    
    # 检查 Python requests 库
    python3 -c "import requests" 2>/dev/null || { log_warn "requests not found, installing..."; pip install -q requests; }
    
    if [ $missing -gt 0 ]; then
        log_error "Missing dependencies, aborting."
        exit 1
    fi
    
    # 验证 locustfile 存在
    if [ ! -f "$BENCH_DIR/locustfile.py" ]; then
        log_error "locustfile.py not found at $BENCH_DIR/"
        exit 1
    fi
    
    log_info "All dependencies OK ✓"
}

# ==================== Step 2: 服务检测 ====================
step_2_service_check() {
    log_step "Step 2/6: Checking target service..."
    
    local max_retries=10
    local retry_interval=3
    local health_url="${HOST_URL%/}/api/healthz"
    
    for i in $(seq 1 $max_retries); do
        log_info "Attempt $i/$max_retries: GET $health_url"
        
        if python3 -c "
import requests, sys
try:
    r = requests.get('$health_url', timeout=5)
    print(f'Status: {r.status_code}')
    if r.status_code == 200:
        print(r.text[:200])
        sys.exit(0)
    else:
        sys.exit(1)
except Exception as e:
    print(f'Error: {e}')
    sys.exit(1)
" 2>/dev/null; then
            log_info "Service is healthy ✓"
            return 0
        fi
        
        log_warn "Service not ready, waiting ${retry_interval}s..."
        sleep $retry_interval
    done
    
    log_error "Service unavailable after $max_retries retries"
    log_info "Make sure the backend is running: cd backend && uvicorn app.main:app --port 8000"
    exit 1
}

# ==================== Step 3: 数据清理 ====================
step_3_cleanup() {
    log_step "Step 3/6: Cleaning old data..."
    
    local reset_url="${HOST_URL%/}/api/schedule/reset"
    
    python3 -c "
import requests
try:
    r = requests.post('$reset_url', timeout=10)
    print(f'Reset: {r.status_code} - {r.text[:100]}')
except Exception as e:
    print(f'Reset warning: {e}')
" 2>/dev/null || true
    
    log_info "Cleanup done ✓"
}

# ==================== Step 4: 数据注入 ====================
step_4_inject_data() {
    if [ "$NO_INJECT" = true ]; then
        log_step "Step 4/6: Skipping data injection (--no-inject)"
        return 0
    fi
    
    log_step "Step 4/6: Injecting test data..."
    
    # 注入 AGV 数据
    if [ -f "$SCENARIOS_DIR/generate_agv_scenario.py" ]; then
        log_info "Generating AGV scenario data..."
        python3 "$SCENARIOS_DIR/generate_agv_scenario.py" \
            --preset "$SCENE_SIZE" \
            --seed 42 \
            --output "$RESULTS_DIR/agv_scenario.json" \
            --inject-url "$HOST_URL" \
            2>&1 | tail -5 || log_warn "AGV injection had issues"
    fi
    
    # 注入任务数据
    if [ -f "$SCENARIOS_DIR/generate_task_scenario.py" ]; then
        log_info "Generating task scenario data..."
        python3 "$SCENARIOS_DIR/generate_task_scenario.py" \
            --size-preset "$SCENE_SIZE" \
            --seed 42 \
            --output "$RESULTS_DIR/task_scenario.json" \
            --inject-url "$HOST_URL" \
            2>&1 | tail -5 || log_warn "Task injection had issues"
    fi
    
    log_info "Data injection done ✓"
}

# ==================== Step 5: 执行压测 ====================
step_5_run_benchmark() {
    log_step "Step 5/6: Running Locust benchmark..."
    log_info "Configuration:"
    log_info "  Host:       $HOST_URL"
    log_info "  Users:      $USERS"
    log_info "  Spawn rate: $((USERS/5))/sec (auto)"
    log_info "  Duration:   ${RUN_TIME}min"
    log_info "  Headless:   ${HEADLESS_FLAG:-no (Web UI)}"
    
    local CSV_PREFIX="$RESULTS_DIR/baseline"
    local HTML_REPORT="$RESULTS_DIR/report.html"
    
    local LOCUST_CMD=(
        locust
        -f "$BENCH_DIR/locustfile.py"
        --host "$HOST_URL"
        --users "$USERS"
        --spawn-rate "$((USERS < 20 ? USERS : USERS / 5))"
        --run-time "${RUN_TIME}m"
        --csv "$CSV_PREFIX"
        --html "$HTML_REPORT"
        $HEADLESS_FLAG
    )
    
    log_info "Running: ${LOCUST_CMD[*]}"
    
    if "${LOCUST_CMD[@]}" 2>&1; then
        log_info "Benchmark completed successfully ✓"
        log_info "HTML Report: $HTML_REPORT"
        log_info "CSV Data: ${CSV_PREFIX}_*.csv"
    else
        log_error "Benchmark failed with exit code $?"
        exit 1
    fi
}

# ==================== Step 6: 结果汇总 ====================
step_6_summary() {
    log_step "Step 6/6: Generating summary report..."
    
    local SUMMARY_FILE="$RESULTS_DIR/summary.md"
    
    cat > "$SUMMARY_FILE" << HEADER
# AGV-TMS Performance Benchmark Summary

**Date**: $(date '+%Y-%m-%d %H:%M:%S')
**Size**: \`${SCENE_SIZE}\`
**Host**: \`${HOST_URL}\`
**Config**: ${USERS} users, ${RUN_TIME}min

## Results

HEADER

    # 尝试从 CSV 文件提取关键指标
    local STATS_CSV="${RESULTS_DIR}/baseline_stats.csv"
    if [ -f "$STATS_CSV" ]; then
        echo '```' >> "$SUMMARY_FILE"
        cat "$STATS_CSV" >> "$SUMMARY_FILE"
        echo '```' >> "$SUMMARY_FILE"
    fi
    
    # 尝试提取关键数字
    python3 << 'PYEOF' >> "$SUMMARY_FILE" 2>/dev/null || true
import csv, os, glob

stats_file = os.environ.get("STATS_CSV", "")
results_dir = os.environ.get("RESULTS_DIR", "")

try:
    # Find and parse stats
    csv_files = glob.glob(os.path.join(results_dir, "*_stats.csv"))
    if csv_files:
        with open(csv_files[0]) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            
            total_req = sum(int(r.get('Num Requests', 0)) for r in rows)
            total_fail = sum(int(r.get('Num Failures', 0)) for r in rows)
            
            avg_rt_rows = [r for r in rows if r.get('Type') == 'Aggregate']
            if avg_rt_rows:
                r = avg_rt_rows[0]
                print(f"\n### Aggregate Metrics\n")
                print(f"| Metric | Value |")
                print(f"|--------|-------|")
                print(f"| Total Requests | {total_req} |")
                print(f"| Failures | {total_fail} ({total_fail/max(total_req,1)*100:.2f}%) |")
                print(f"| Avg Response Time | {float(r.get('Average Response Time', 0)):.1f} ms |")
                print(f"| Min Response Time | {float(r.get('Min Response Time', 0)):.1f} ms |")
                print(f"| Max Response Time | {float(r.get('Max Response Time', 0)):.1f} ms |")
                print()
                
                # Check against targets
                p50 = float(r.get('50%', 0))
                p99_val = float(r.get('99%', 0))
                rps = float(r.get('Requests/s', 0))
                
                print(f"\n### Target Check\n")
                print(f"| Target | Value | Status |")
                print(f"|--------|-------|--------|")
                print(f"| P50 < 50ms | {p50:.1f}ms | {'✅ PASS' if p50 < 50 else '❌ FAIL'} |")
                print(f"| P99 < 200ms | {p99_val:.1f}ms | {'✅ PASS' if p99_val < 200 else '❌ FAIL'} |")
                print(f"| RPS > 1000 | {rps:.0f} | {'✅ PASS' if rps > 1000 else '❌ FAIL'} |")
except Exception as e:
    pass
PYEOF

    export STATS_CSV="$STATS_CSV" RESULTS_DIR="$RESULTS_DIR"

    cat >> "$SUMMARY_FILE" << FOOTER

## Files Generated
- HTML Report: \`report.html\`
- Statistics: \`baseline_stats.csv\`
- Failures: \`baseline_failures.csv\`
- Exceptions: \`baseline_exceptions.csv\`
- This summary: \`summary.md\`

---
*Generated by AGV-TMS Benchmark Suite v2.3*
FOOTER

    log_info "Summary report: $SUMMARY_FILE"
    
    # 打印摘要到控制台
    echo ""
    echo "========================================"
    echo "  BENCHMARK COMPLETE"
    echo "========================================"
    echo "  Results: $RESULTS_DIR"
    echo "========================================"
}

# ==================== 主流程 ====================
main() {
    mkdir -p "$RESULTS_DIR"
    
    step_1_dependencies
    step_2_service_check
    step_3_cleanup
    step_4_inject_data
    step_5_run_benchmark
    step_6_summary
}

main "$@"
