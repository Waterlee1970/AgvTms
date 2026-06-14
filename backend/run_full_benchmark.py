#!/usr/bin/env python3
"""
AgvTms AG+TMS 混合场景多算法兼容性评测验证脚本
生成完整报告 (Markdown + JSON)
"""
import sys, time, json
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.algorithms.v2.evaluator.registry import get_registry
from app.algorithms.v2.evaluator.scenarios import ScenarioGenerator
from app.algorithms.v2.evaluator.runner import ScenarioRunner, EvaluatorConfig

def main():
    print('='*70)
    print(' AgvTms AG+TMS 混合场景多算法兼容性评测')
    print(f' 时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print('='*70)

    SCENARIOS = [
        ('small_warehouse', '小型仓库基准(8x12网格, 5AGV, 15任务)', False),
        ('medium_warehouse', '中型仓库标准(15x20, 15AGV, 40任务)', False),
        ('factory_floor', '工厂车间+输送线(20x25, 20AGV, 50任务)', True),
        ('mixed_agv_tms', '★ AGV+TMS混合核心(20x28, 20AGV, 45任务+15CTask)', True),
        ('stress_test', '极限压力测试(15x20, 50AGV, 150任务)', False),
    ]

    config = EvaluatorConfig(verbose=False, weights={
        'efficiency': 0.35, 'quality': 0.25, 'resource': 0.20,
        'realtime': 0.15, 'robustness': 0.05,
    })
    runner = ScenarioRunner(config=config)

    all_reports = []
    start_time = time.time()

    for idx, (preset_name, desc, is_mixed) in enumerate(SCENARIOS):
        print(f'\n[{idx+1}/{len(SCENARIOS)}] {desc}...', end=' ', flush=True)
        
        try:
            gen = ScenarioGenerator(seed=42 + idx * 100)
            scene = gen.generate(preset_name=preset_name)
            report = runner.run_comparison(scene)
            all_reports.append(report)
            
            wscore = report.results[report.winner].total_score
            print(f'✅ 获胜: {report.winner} ({wscore:.1f}分)')
        except Exception as e:
            print(f'❌ 错误: {str(e)[:60]}')

    elapsed = time.time() - start_time
    
    # === 分析结果 ===
    valid_reports = [r for r in all_reports if r.results and r.rankings]
    
    # 综合排名
    overall = {}
    for r in valid_reports:
        for name, card in r.results.items():
            if card.total_score > 5 and not card.error_info:
                overall.setdefault(name, []).append(card.total_score)

    avg_scores = {n: sum(s)/len(s) for n,s in overall.items()}
    sorted_avg = sorted(avg_scores.items(), key=lambda x: -x[1])
    
    # 兼容性统计
    compat = {}
    for r in valid_reports:
        for name, card in r.results.items():
            compat.setdefault(name, {'ok':0,'fail':0,'scores':[]})
            if card.total_score > 5:
                compat[name]['ok'] += 1
                compat[name]['scores'].append(card.total_score)
            else:
                compat[name]['fail'] += 1
    
    # 维度冠军
    dim_winners = {}
    for dk, dn in [('efficiency','效率'),('quality','质量'),('resource','资源利用'),
                    ('realtime','实时性能'),('robustness','鲁棒性')]:
        best_algo, best_score = '', 0
        for r in valid_reports:
            for name, card in r.results.items():
                ds = card.dimension_scores.get(dk)
                if ds and ds.score > best_score:
                    best_algo, best_score = name, ds.score
        dim_winners[dn] = (best_algo, best_score)

    # === 输出报告 ===
    lines = []
    lines.append('# AgvTms 多算法兼容性与评测验证报告\n')
    lines.append(f'> **版本**: v2.1 | **日期**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} | **耗时**: {elapsed:.1f}s\n')
    
    # 1. 执行摘要
    lines.append('## 1. 执行摘要\n')
    if sorted_avg:
        w = sorted_avg[0]
        grade = "A+" if w[1]>=90 else "A" if w[1]>=80 else "B+" if w[1]>=70 else "B"
        lines.append(f'- 🏆 **综合最佳**: `{w[0]}` (**{w[1]:.1f}分**, **{grade}**)')
        lines.append(f'- 测试场景: {len(valid_reports)}种 (含{sum(m for _,_,m in SCENARIOS)}个AGV+TMS混合场景)')
    lines.append('')
    
    # 2. 排名表
    lines.append('## 2. 综合排行榜\n')
    lines.append('| 排名 | 算法 | 均分 | 最高 | 最低 | 成功率 |')
    lines.append('|:----:|------|:----:|:----:|:----:|:------:|')
    for i,(name,avg) in enumerate(sorted_avg,1):
        scs = compat.get(name,{'scores':[],'ok':0,'fail':0})
        scores = scs['scores']
        hi = max(scores) if scores else 0
        lo = min(scores) if scores else 0
        rate = scs['ok']/(scs['ok']+scs['fail'])*100
        icon = ['🥇','🥈','🥉'][i-1] if i<=3 else f'{i}'
        lines.append(f'| {icon} | **{name}** | **{avg:.1f}** | {hi:.1f} | {lo:.1f} | {rate:.0f}% |')
    lines.append('')

    # 3. 场景详情
    lines.append('## 3. 各场景评测详情\n')
    for i,r in enumerate(valid_reports):
        lines.append(f'### 3.{i+1}. {r.scenario_name}\n')
        meta = r.scenario_metadata
        is_mixed_scene = any(
            (m and str(m) in str(r.scenario_type).lower()) or 'mixed' in str(r.scenario_type).lower()
            for _,_,m in SCENARIOS
        )
        mixed_tag = ' ⚡ **混合场景(含输送线)**' if is_mixed_scene else ''
        lines.append(f'> 类型:`{r.scenario_type}` | 节点:{meta.get("nodes","?")} 边:{meta.get("edges","?")} '
                     f'AGV:{meta.get("agvs","?")} 任务:{meta.get("tasks","?")}{mixed_tag}')
        lines.append('')
        lines.append('| # | 算法 | 分数 | 等级 | 效率 | 质量 | 资源 | 实时 | 鲁棒 |')
        lines.append('|:-:|------|:----:|:----:|:----:|:----:|:----:|:----:|:----:|')
        
        class _D: score=0
        
        for j,(name,score) in enumerate(r.rankings[:6],1):
            c = r.results[name]
            d = c.dimension_scores
            mk = '🏆' if j==1 else ''
            de=d.get('efficiency') or _D(); dq=d.get('quality') or _D()
            dr=d.get('resource') or _D(); dt=d.get('realtime') or _D(); db=d.get('robustness') or _D()
            lines.append(f'| #{j} | **{c.algorithm_display_name}{mk}** | **{score:.1f}** | '
                        f'**{c.grade}** | {de.score:.0f} | {dq.score:.0f} | {dr.score:.0f} | '
                        f'{dt.score:.0f} | {db.score:.0f} |')
        lines.append(f'\n> 💡 {r.summary}\n')

    # 4. 维度分析
    lines.append('## 4. 五维能力专项分析\n')
    lines.append('| 维度 | 冠军算法 | 得分 |')
    lines.append('|------|----------|:----:|')
    for dn,(algo,score) in dim_winners.items():
        lines.append(f'| **{dn}** | **{algo}** | **{score:.1f}** |')
    lines.append('')

    # 5. 兼容性
    lines.append('## 5. 兼容性与稳定性\n')
    lines.append('| 算法 | 成功 | 失败 | 成功率 | 平均分 |')
    lines.append('|------|:----:|:----:|:------:|:------:|')
    for name,stats in compat.items():
        ok=stats['ok']; fail=stats['fail']; total=ok+fail
        rate=ok/total*100 if total>0 else 0
        avg=sum(stats['scores'])/len(stats['scores']) if stats['scores'] else 0
        st='✅稳定' if rate>=95 else '⚠️可用' if rate>=75 else '❌不稳定'
        lines.append(f'| {name} | {ok} | {fail} | {rate:.0f}% | {avg:.1f} ({st}) |')
    lines.append('')

    # 6. 建议
    lines.append('## 6. 结论与建议\n')
    if sorted_avg:
        top = sorted_avg[0]
        lines.append(f'- ✅ **默认推荐**: `{top[0]}` ({top[1]:.1f}分)')
        
        # 场景特定推荐
        recs = []
        for r in valid_reports:
            w = r.winner; s = r.results[w].total_score
            if w != top[0]: recs.append((r.scenario_type, w, s))
        
        unique_recs = list(set(recs))
        if unique_recs:
            lines.append('\n### 按场景类型推荐\n')
            for t,a,s in sorted(unique_recs):
                lines.append(f'- **[`{t}`]** → `{a}` ({s:.1f}分)')

    lines.append('\n---\n*报告由 AgvTms Benchmark Suite 自动生成*\n')

    # === 保存文件 ===
    out_dir = Path('benchmark_results')
    out_dir.mkdir(exist_ok=True)
    
    md_path = out_dir / 'benchmark_report_v2.md'
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    
    json_data = {
        'metadata': {
            'generated_at': datetime.now().isoformat(),
            'elapsed_seconds': round(elapsed, 1),
            'scenarios_tested': len(valid_reports),
        },
        'overall_rankings': dict(sorted_avg),
        'dimension_champions': {k: {'algorithm': v[0], 'score': round(v[1], 2)} for k,v in dim_winners.items()},
        'compatibility_matrix': {
            k: {
                'success': v['ok'], 'failure': v['fail'],
                'rate': round(v['ok']/(v['ok']+v['fail'])*100, 1) if (v['ok']+v['fail'])>0 else 0,
                'avg_score': round(sum(v['scores'])/len(v['scores']), 2) if v['scores'] else 0,
            } for k,v in compat.items()
        },
    }
    
    json_path = out_dir / 'benchmark_data_v2.json'
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)

    print(f'\n{"="*70}')
    print(f' ✅ 报告已保存:')
    print(f'   📄 Markdown: {md_path}')
    print(f'   📊 JSON:     {json_path}')
    print(f'   ⏱ 总耗时: {elapsed:.1f}s')
    if sorted_avg:
        print(f'   🏆 最佳算法: {sorted_avg[0][0]} ({sorted_avg[0][1]:.1f}分)')
    
    return md_path, json_path

if __name__ == '__main__':
    main()
