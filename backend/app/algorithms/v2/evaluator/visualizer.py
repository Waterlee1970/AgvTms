"""
结果可视化器 - 生成图表和报告
================================

支持的输出:
  1. 雷达图 (Radar Chart) - 多算法多维度对比
  2. 柱状图 (Bar Chart) - 总分/单维度对比
  3. 折线图 (Line Chart) - 不同规模下性能趋势
  4. 热力图 (Heatmap) - 算法 × 场景矩阵
  5. HTML 完整报告
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


class ResultsVisualizer:
    """
    结果可视化器
    
    生成前端可直接使用的图表数据格式
    """

    def __init__(self):
        self._color_palette = [
            "#1890ff", "#52c41a", "#faad14", "#f5222d", "#722ed1",
            "#13c2c2", "#eb2f96", "#fa8c16", "#a0d911", "#2f54eb",
        ]

    def _get_color(self, index: int) -> str:
        return self._color_palette[index % len(self._color_palette)]

    # ==================== 雷达图数据 ====================

    def radar_chart_data(self, comparison_report) -> Dict[str, Any]:
        """生成雷达图数据（单场景多算法）"""
        data = comparison_report.radar_data()
        datasets = data.get("datasets", [])
        if isinstance(datasets, dict):
            for i, (key, ds) in enumerate(datasets.items()):
                if isinstance(ds, dict):
                    ds["color"] = self._get_color(i)
        elif isinstance(datasets, list):
            for i, ds in enumerate(datasets):
                if isinstance(ds, dict):
                    ds["color"] = self._get_color(i)
        return {
            "type": "radar",
            "title": f"算法能力对比 - {comparison_report.scenario_name}",
            "data": data,
        }

    def radar_multi_scenario(self, reports) -> Dict[str, Any]:
        """生成多场景雷达图平均数据"""
        # 收集所有维度分数并取平均
        dim_names = ["efficiency", "quality", "resource", "realtime", "robustness"]
        dim_labels = {"efficiency": "效率", "quality": "质量", "resource": "资源利用",
                      "realtime": "实时性能", "robustness": "鲁棒性"}
        
        algo_avg: Dict[str, Dict[str, float]] = {}
        for report in reports:
            for name, card in report.results.items():
                algo_avg.setdefault(name, {})
                for dname in dim_names:
                    ds = card.dimension_scores.get(dname)
                    val = ds.normalized if ds else 0.0
                    algo_avg[name].setdefault(dname, []).append(val)

        datasets = []
        for i, (name, dims) in enumerate(algo_avg.items()):
            avg_dims = {dname: sum(vals)/len(vals) for dname, vals in dims.items()}
            datasets.append({
                "name": name,
                "values": [round(avg_dims.get(dn, 0), 1) for dn in dim_names],
                "color": self._get_color(i),
            })

        return {
            "type": "radar",
            "title": "多场景综合能力对比 (均值)",
            "data": {
                "labels": ["效率", "质量", "资源利用", "实时性能", "鲁棒性"],
                "datasets": datasets,
            },
        }

    # ==================== 柱状图数据 ====================

    def bar_chart_total_scores(self, batch_result) -> Dict[str, Any]:
        """总分柱状图"""
        scores = batch_result.overall_rankings
        algorithms = list(scores.keys())
        values = [round(v, 1) for v in scores.values()]

        return {
            "type": "bar",
            "title": "算法综合评分排名",
            "data": {
                "labels": algorithms,
                "datasets": [{
                    "name": "综合得分",
                    "values": values,
                    "colors": [self._get_color(i) for i in range(len(algorithms))],
                }],
            },
        }

    def bar_chart_dimension(self, reports, dimension: str) -> Dict[str, Any]:
        """单维度柱状图"""
        algo_scores: Dict[str, List[float]] = {}
        for report in reports:
            for name, card in report.results.items():
                ds = card.dimension_scores.get(dimension)
                if ds:
                    algo_scores.setdefault(name, []).append(ds.score)

        algos = list(algo_scores.keys())
        avg = [round(sum(s)/len(s), 1) for s in algo_scores.values()]

        return {
            "type": "bar",
            "title": f"「{dimension}」维度评分对比",
            "data": {
                "labels": algos,
                "datasets": [{"name": dimension, "values": avg}],
            },
        }

    # ==================== 趋势折线图 ====================

    def trend_by_scale(self, reports) -> Dict[str, Any]:
        """按场景规模(AGV数量)分析性能趋势"""
        scale_data: Dict[int, Dict[str, float]] = {}
        for report in reports:
            n_agv = report.scenario_metadata.get("agvs", 0)
            scale_data.setdefault(n_agv, {})
            for name, card in report.results.items():
                scale_data[n_agv][name] = card.total_score

        sorted_scales = sorted(scale_data.keys())
        all_algos = set()
        for sd in scale_data.values():
            all_algos.update(sd.keys())

        datasets = []
        for i, algo in enumerate(sorted(all_algos)):
            values = [round(scale_data.get(s, {}).get(algo, 0), 1) for s in sorted_scales]
            datasets.append({"name": algo, "values": values, "color": self._get_color(i)})

        return {
            "type": "line",
            "title": "性能随AGV规模变化趋势",
            "data": {
                "labels": [f"{s} AGV" for s in sorted_scales],
                "datasets": datasets,
            },
        }

    # ==================== 热力图数据 ====================

    def heatmap_algorithm_matrix(self, batch_result) -> Dict[str, Any]:
        """
        算法×场景 矩阵热力图
        
        行=算法，列=场景类型，值=平均分
        """
        type_stats = batch_result.scenario_type_stats
        algos = list(batch_result.overall_rankings.keys())
        types = list(type_stats.keys())

        matrix = []
        for algo in algos:
            row = []
            for stype in types:
                val = round(type_stats.get(stype, {}).get(algo, 0), 1)
                row.append(val)
            matrix.append(row)

        return {
            "type": "heatmap",
            "title": "算法-场景适配度热力图",
            "data": {
                "x_labels": types,
                "y_labels": algos,
                "matrix": matrix,
            },
        }

    # ==================== HTML 报告生成 ====================

    def generate_html_report(self, batch_result) -> str:
        """
        生成完整的 HTML 可视化报告
        
        包含: 表格 + 图表占位符 + 分析结论
        """
        radar = self.radar_multi_scenario(batch_result.scenario_reports) \
                 if hasattr(batch_result, 'scenario_reports') else {"data":{"labels":[],"datasets":[]}}
        bars = self.bar_chart_total_scores(batch_result)
        heatmap = self.heatmap_algorithm_matrix(batch_result)

        # 排行榜表格行
        table_rows = ""
        for rank, (name, score) in enumerate(batch_result.overall_rankings.items(), 1):
            best_type = next((t for t, b in batch_result.best_by_scenario_type.items()
                             if b == name), "-")
            table_rows += f"""
            <tr>
                <td>{rank}</td>
                <td><strong>{name}</strong></td>
                <td>{score:.1f}</td>
                <td>{best_type}</td>
            </tr>"""

        # 建议列表
        rec_items = "".join(f"<li>{r}</li>" for r in batch_result.recommendations)

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AgvTms 算法评估报告</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
               background: #f5f7fa; color: #333; line-height: 1.6; padding: 20px; }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        h1 {{ text-align: center; color: #1890ff; margin-bottom: 10px; }}
        .subtitle {{ text-align: center; color: #666; margin-bottom: 30px; }}
        .card {{ background: white; border-radius: 12px; padding: 24px; margin-bottom: 24px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
        .card h2 {{ border-left: 4px solid #1890ff; padding-left: 12px; margin-bottom: 16px; }}
        table {{ width: 100%; border-collapse: collapse; }}
        th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #eee; }}
        th {{ background: #fafafa; font-weight: 600; }}
        tr:hover {{ background: #f9fbff; }}
        .rank-1 {{ color: #f5222d; font-weight: bold; }}
        .rank-2 {{ color: #fa8c16; font-weight: bold; }}
        .rank-3 {{ color: #faad14; font-weight: bold; }}
        .chart-row {{ display: flex; gap: 24px; flex-wrap: wrap; }}
        .chart-box {{ flex: 1; min-width: 400px; height: 400px; }}
        .rec-box {{ background: #e6f7ff; border: 1px solid #91d5ff; border-radius: 8px;
                   padding: 16px; margin-top: 16px; }}
        .rec-box h3 {{ color: #0050b3; margin-bottom: 8px; }}
        .tag {{ display: inline-block; padding: 2px 8px; border-radius: 4px;
               font-size: 12px; margin-right: 4px; }}
        .tag-a {{ background: #f6ffed; color: #52c41a; border: 1px solid #b7eb8f; }}
        .tag-b {{ background: #fff7e6; color: #fa8c16; border: 1px solid #ffd591; }}
        .tag-c {{ background: #fff1f0; color: #f5222d; border: 1px solidffa393; }}
        footer {{ text-align: center; color: #999; margin-top: 40px; padding: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🚀 AgvTms 多算法评估报告</h1>
        <p class="subtitle">AGV+TMS 混合调度系统 | 多维度评价体系 | 自动化基准测试</p>

        <!-- 综合排行榜 -->
        <div class="card">
            <h2>🏆 综合排行榜</h2>
            <table>
                <thead>
                    <tr><th>排名</th><th>算法</th><th>平均分</th><th>最擅长的场景类型</th></tr>
                </thead>
                <tbody>{table_rows}
                </tbody>
            </table>
        </div>

        <!-- 图表区域 -->
        <div class="card">
            <h2>📊 可视化分析</h2>
            <div class="chart-row">
                <div class="chart-box">
                    <canvas id="radarChart"></canvas>
                </div>
                <div class="chart-box">
                    <canvas id="barChart"></canvas>
                </div>
            </div>
        </div>

        <!-- 各场景详细结果 -->
        <div class="card">
            <h2>📋 分场景详细结果</h2>
            {self._generate_detail_tables_html(batch_result)}
        </div>

        <!-- 建议 -->
        <div class="card">
            <h2>💡 使用建议</h2>
            <div class="rec-box">
                <h3>基于测试结果的推荐策略</h3>
                <ul>{rec_items}</ul>
            </div>
        </div>

        <footer>
            <p>Generated by AgvTms Evaluator v2.0 | 多算法兼容评价系统</p>
        </footer>
    </div>

    <script>
        // 雷达图
        const radarData = {json.dumps(radar.get('data', {'labels':[],'datasets':[]}), ensure_ascii=False)};
        new Chart(document.getElementById('radarChart'), {{
            type: 'radar',
            data: {{
                labels: radarData.labels || ['效率','质量','资源','实时性','鲁棒性'],
                datasets: (radarData.datasets || []).map((ds, i) => ({{
                    label: ds.name || '',
                    data: ds.values || [],
                    borderColor: '{self._color_palette[0]}',
                    backgroundColor: '{self._color_palette[0]}20',
                    pointRadius: 4,
                }}))
            }},
            options: {{ responsive: true, maintainAspectRatio: false,
                        scales: {{ r: {{ beginAtZero: true, max: 100 }} }} }}
        }});

        // 柱状图
        const barData = {json.dumps(bars.get('data', {'labels':[],'datasets':[]}), ensure_ascii=False)};
        new Chart(document.getElementById('barChart'), {{
            type: 'bar',
            data: {{
                labels: barData.labels || [],
                datasets: [{{
                    label: '综合得分',
                    data: barData.datasets?.[0]?.values || [],
                    backgroundColor: ['#1890ff','#52c41a','#faad14','#f5222d','#722ed1',
                                       '#13c2c2','#eb2f96','#fa8c16'],
                    borderRadius: 6,
                }}]
            }},
            options: {{ responsive: true, maintainAspectRatio: false,
                        scales: {{ y: {{ beginAtZero: true, max: 100 }} }} }}
        }});
    </script>
</body>
</html>"""
        return html

    def _generate_detail_tables_html(self, batch_result) -> str:
        """生成各场景详细结果表格"""
        tables = ""
        for report in batch_result.scenario_reports[:5]:  # 最多显示前5个场景
            rows = ""
            for name, card in sorted(report.results.items(), key=lambda x: -x[1].total_score):
                grade_cls = f"rank-{min(card.rank, 3)}"
                rows += f"""<tr>
                    <td class="{grade_cls}">#{card.rank}</td>
                    <td>{card.algorithm_display_name or name}</td>
                    <td><strong>{card.total_score:.1f}</strong></td>
                    <td><span class="tag tag-{'A' if card.grade.startswith('A') else 'B' if card.grade.startswith('B') else 'C'}">{card.grade}</span></td>
                    <td>{card.raw_metrics.get('compute_time_ms', 0):.1f}ms</td>
                    <td>{card.raw_metrics.get('num_tasks_assigned', 0)}</td>
                </tr>"""

            tables += f"""
            <h3 style="margin:16px 0 8px;">{report.scenario_name}
                <small style="color:#999;font-weight:normal;">
                    ({report.scenario_metadata.get('agvs',0)} AGVs, {report.scenario_metadata.get('tasks',0)} Tasks)
                </small>
            </h3>
            <table>
                <thead><tr><th>#</th><th>算法</th><th>总分</th><th>等级</th><th>耗时</th><th>分配数</th></tr></thead>
                <tbody>{rows}</tbody>
            </table>"""

        return tables

    def export_json(self, batch_result, filepath=None) -> str:
        """导出JSON格式数据"""
        json_str = batch_result.to_dict()
        if isinstance(json_str, dict):
            import json as _json
            json_str = _json.dumps(json_str, indent=2, ensure_ascii=False)
        if filepath:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(json_str)
        return json_str
