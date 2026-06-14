/**
 * AlgorithmConfig - 算法参数配置页面
 *
 * Configure ACO, SA, NLP, and Hybrid algorithm parameters.
 */

import React, { useEffect, useState, useCallback } from 'react';
import {
  Row, Col, Card, Typography, Slider, InputNumber, Button,
  Space, Switch, Select, message, Spin, Divider, Tag, Tooltip,
  Empty, Statistic, Tabs,
} from 'antd';
import {
  SettingOutlined, SaveOutlined, ReloadOutlined,
  ThunderboltOutlined, ExperimentOutlined, DashboardOutlined,
  BulbOutlined, RocketOutlined, FireOutlined,
  TruckOutlined, SwapOutlined, MergeCellsOutlined,
} from '@ant-design/icons';
import * as api from '../../services/api';
import { useStore } from '../../store/useStore';
import type { AlgorithmConfig } from '../../services/api';

const { Text, Title } = Typography;

const defaultConfig: AlgorithmConfig = {
  aco: { num_ants: 50, alpha: 1.0, beta: 2.0, evaporation_rate: 0.1, iterations: 100, q0: 0.5 },
  sa: { initial_temp: 1000, cooling_rate: 0.95, iterations: 500, min_temp: 0.01 },
  nlp: { solver: 'SLSQP', tolerance: 1e-6, max_iter: 1000, verbose: false },
  hybrid: { aco_weight: 0.4, sa_weight: 0.3, nlp_weight: 0.3, strategy: 'sequential' },
};

const presets: Record<string, { label: string; icon: React.ReactNode; config: Record<string, Record<string, unknown>>; desc: string }> = {
  speed: {
    label: '速度优先', icon: <RocketOutlined />,
    desc: '优化计算速度，适用于实时性要求高的场景',
    config: {
      aco: { num_ants: 30, iterations: 50 },
      sa: { iterations: 200, initial_temp: 500 },
      nlp: { max_iter: 500, tolerance: 1e-4 },
      hybrid: { aco_weight: 0.6, sa_weight: 0.2, nlp_weight: 0.2, strategy: 'parallel' },
    },
  },
  energy: {
    label: '能耗优化', icon: <BulbOutlined />,
    desc: '最小化能源消耗，适合长时间运行场景',
    config: {
      aco: { num_ants: 100, iterations: 200, evaporation_rate: 0.05 },
      sa: { iterations: 1000, cooling_rate: 0.98, initial_temp: 2000 },
      nlp: { max_iter: 2000, tolerance: 1e-8 },
      hybrid: { aco_weight: 0.2, sa_weight: 0.3, nlp_weight: 0.5, strategy: 'sequential' },
    },
  },
  balanced: {
    label: '均衡模式', icon: <DashboardOutlined />,
    desc: '在速度和能耗之间取得平衡',
    config: {
      aco: { num_ants: 50, iterations: 100 },
      sa: { iterations: 500, cooling_rate: 0.95 },
      nlp: { max_iter: 1000, tolerance: 1e-6 },
      hybrid: { aco_weight: 0.4, sa_weight: 0.3, nlp_weight: 0.3, strategy: 'sequential' },
    },
  },
};

const AlgorithmConfigPage: React.FC = () => {
  const { algorithmConfig, setAlgorithmConfig, loading, setLoading } = useStore();
  const [config, setConfig] = useState<AlgorithmConfig>(algorithmConfig || defaultConfig);
  const [activePreset, setActivePreset] = useState<string | null>(null);

  const fetchConfig = useCallback(async () => {
    setLoading('algoConfig', true);
    try {
      const data = await api.getAlgorithmConfig();
      setAlgorithmConfig(data);
      setConfig(data);
    } catch { /* ignore */ }
    finally { setLoading('algoConfig', false); }
  }, [setAlgorithmConfig, setLoading]);

  useEffect(() => { fetchConfig(); }, [fetchConfig]);

  const handleSave = async () => {
    setLoading('algoSave', true);
    try {
      const result = await api.updateAlgorithmConfig(config);
      setAlgorithmConfig(result);
      message.success('算法配置已保存');
    } catch { message.error('保存失败'); }
    finally { setLoading('algoSave', false); }
  };

  const handlePreset = (key: string) => {
    const preset = presets[key];
    setActivePreset(key);
    setConfig(prev => ({
      aco: { ...prev.aco, ...preset.config?.aco },
      sa: { ...prev.sa, ...preset.config?.sa },
      nlp: { ...prev.nlp, ...preset.config?.nlp },
      hybrid: { ...prev.hybrid, ...preset.config?.hybrid },
    }));
    message.info(`已应用预设: ${preset.label}`);
  };

  const updateAco = (field: string, value: number) => {
    setConfig(prev => ({ ...prev, aco: { ...prev.aco, [field]: value } }));
    setActivePreset(null);
  };

  const updateSa = (field: string, value: number) => {
    setConfig(prev => ({ ...prev, sa: { ...prev.sa, [field]: value } }));
    setActivePreset(null);
  };

  const updateNlp = (field: string, value: unknown) => {
    setConfig(prev => ({ ...prev, nlp: { ...prev.nlp, [field]: value } }));
    setActivePreset(null);
  };

  const updateHybrid = (field: string, value: unknown) => {
    setConfig(prev => ({ ...prev, hybrid: { ...prev.hybrid, [field]: value } }));
    setActivePreset(null);
  };

  // ---- Render helpers ----

  const renderSlider = (
    label: string,
    value: number,
    min: number,
    max: number,
    step: number,
    onChange: (v: number | null) => void,
    tooltip?: string,
  ) => (
    <div style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
        <Tooltip title={tooltip}>
          <Text style={{ fontSize: 13, color: '#a0a8c0' }}>{label}</Text>
        </Tooltip>
        <InputNumber
          size="small"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={onChange}
          style={{ width: 80 }}
        />
      </div>
      <Slider
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(v) => onChange(v)}
      />
    </div>
  );

  const items = [
    {
      key: 'aco',
      label: <span><FireOutlined /> 蚁群算法 (ACO)</span>,
      children: (
        <div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            蚁群算法用于多AGV路径规划。蚂蚁通过信息素在图中寻找最优路径，α控制信息素权重，β控制启发式权重。
          </Text>
          <Divider style={{ margin: '12px 0' }} />
          {renderSlider('蚂蚁数量', config.aco.num_ants, 5, 500, 5, (v) => v && updateAco('num_ants', v), '每代蚂蚁数量，越多探索越充分但计算量越大')}
          {renderSlider('信息素权重 α', config.aco.alpha, 0, 5, 0.1, (v) => v && updateAco('alpha', v), '信息素的重要性，越大越依赖历史经验')}
          {renderSlider('启发式权重 β', config.aco.beta, 0, 5, 0.1, (v) => v && updateAco('beta', v), '启发式信息的重要性，越大越倾向短路径')}
          {renderSlider('蒸发率 ρ', config.aco.evaporation_rate, 0.01, 0.9, 0.01, (v) => v && updateAco('evaporation_rate', v), '信息素蒸发速率，控制探索与利用平衡')}
          {renderSlider('迭代次数', config.aco.iterations, 10, 1000, 10, (v) => v && updateAco('iterations', v), '算法最大迭代次数')}
          {renderSlider('贪心因子 q0', config.aco.q0, 0, 1, 0.05, (v) => v && updateAco('q0', v), '开发vs探索概率阈值')}
        </div>
      ),
    },
    {
      key: 'sa',
      label: <span><ExperimentOutlined /> 模拟退火 (SA)</span>,
      children: (
        <div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            模拟退火用于任务到AGV的最优分配。高温时接受较差解以跳出局部最优，降温后收敛到全局最优。
          </Text>
          <Divider style={{ margin: '12px 0' }} />
          {renderSlider('初始温度', config.sa.initial_temp, 1, 100000, 100, (v) => v && updateSa('initial_temp', v), '起始温度，越高探索越充分')}
          {renderSlider('冷却速率', config.sa.cooling_rate, 0.5, 0.999, 0.001, (v) => v && updateSa('cooling_rate', v), '每次迭代温度衰减因子')}
          {renderSlider('迭代次数', config.sa.iterations, 50, 10000, 50, (v) => v && updateSa('iterations', v), '算法最大迭代次数')}
          {renderSlider('最低温度', config.sa.min_temp, 1e-6, 100, 0.001, (v) => v && updateSa('min_temp', v), '温度低于此值时算法终止')}
        </div>
      ),
    },
    {
      key: 'nlp',
      label: <span><ThunderboltOutlined /> 非线性规划 (NLP)</span>,
      children: (
        <div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            非线性规划用于输送线任务排序优化。通过SLSQP求解器最小化总完工时间和能量消耗。
          </Text>
          <Divider style={{ margin: '12px 0' }} />
          <div style={{ marginBottom: 16 }}>
            <Text style={{ fontSize: 13, color: '#a0a8c0' }}>求解器</Text>
            <Select
              value={config.nlp.solver}
              onChange={(v) => updateNlp('solver', v)}
              style={{ width: '100%', marginTop: 4 }}
              options={[
                { value: 'SLSQP', label: 'SLSQP - 序列最小二乘规划' },
                { value: 'COBYLA', label: 'COBYLA - 约束优化线性近似' },
              ]}
            />
          </div>
          {renderSlider('容差', config.nlp.tolerance, 1e-12, 1e-2, 1e-6, (v) => v && updateNlp('tolerance', v), '收敛容差，越小精度越高')}
          {renderSlider('最大迭代', config.nlp.max_iter, 50, 10000, 50, (v) => v && updateNlp('max_iter', v), '最大迭代次数')}
          <div style={{ marginTop: 8 }}>
            <Space>
              <Text style={{ fontSize: 13, color: '#a0a8c0' }}>详细输出</Text>
              <Switch checked={config.nlp.verbose} onChange={(v) => updateNlp('verbose', v)} />
            </Space>
          </div>
        </div>
      ),
    },
    {
      key: 'hybrid',
      label: <span><SettingOutlined /> 混合策略</span>,
      children: (
        <div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            混合调度引擎权重配置。总成本 = w_aco × ACO成本 + w_sa × SA成本 + w_nlp × NLP成本。
          </Text>
          <Divider style={{ margin: '12px 0' }} />
          {renderSlider('ACO权重', config.hybrid.aco_weight, 0, 1, 0.05, (v) => v && updateHybrid('aco_weight', v), '蚁群算法在总成本中的权重')}
          {renderSlider('SA权重', config.hybrid.sa_weight, 0, 1, 0.05, (v) => v && updateHybrid('sa_weight', v), '模拟退火在总成本中的权重')}
          {renderSlider('NLP权重', config.hybrid.nlp_weight, 0, 1, 0.05, (v) => v && updateHybrid('nlp_weight', v), '非线性规划在总成本中的权重')}
          <div style={{ marginBottom: 16 }}>
            <Text style={{ fontSize: 13, color: '#a0a8c0' }}>执行策略</Text>
            <Select
              value={config.hybrid.strategy}
              onChange={(v) => updateHybrid('strategy', v)}
              style={{ width: '100%', marginTop: 4 }}
              options={[
                { value: 'sequential', label: '顺序执行 - 依次运行SA→ACO→NLP' },
                { value: 'parallel', label: '并行执行 - 同时运行后合并结果' },
              ]}
            />
          </div>
        </div>
      ),
    },
  ];

  return (
    <Spin spinning={loading['algoConfig']}>
      <div className="page-title">
        <SettingOutlined style={{ marginRight: 8 }} />
        算法配置
        <Space style={{ float: 'right' }}>
          <Button icon={<ReloadOutlined />} onClick={fetchConfig}>重置</Button>
          <Button
            type="primary"
            icon={<SaveOutlined />}
            loading={loading['algoSave']}
            onClick={handleSave}
          >
            保存配置
          </Button>
        </Space>
      </div>

      {/* Preset Cards */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        {Object.entries(presets).map(([key, preset]) => (
          <Col xs={24} sm={8} key={key}>
            <Card
              hoverable
              size="small"
              onClick={() => handlePreset(key)}
              style={{
                borderColor: activePreset === key ? '#1890ff' : undefined,
                cursor: 'pointer',
              }}
            >
              <Space>
                <span style={{ fontSize: 20, color: '#1890ff' }}>{preset.icon}</span>
                <div>
                  <Text strong>{preset.label}</Text>
                  <br />
                  <Text type="secondary" style={{ fontSize: 11 }}>{preset.desc}</Text>
                </div>
              </Space>
              {activePreset === key && (
                <Tag color="blue" style={{ float: 'right', marginTop: -24 }}>当前</Tag>
              )}
            </Card>
          </Col>
        ))}
      </Row>

      {/* Algorithm Parameter Tabs */}
      <Card>
        <Tabs items={items} />
      </Card>

      {/* Weight Summary */}
      <Card size="small" style={{ marginTop: 16 }}>
        <Row gutter={16}>
          <Col span={6}>
            <Statistic title="ACO权重" value={config.hybrid.aco_weight.toFixed(2)} valueStyle={{ color: '#ff7a45' }} />
          </Col>
          <Col span={6}>
            <Statistic title="SA权重" value={config.hybrid.sa_weight.toFixed(2)} valueStyle={{ color: '#1890ff' }} />
          </Col>
          <Col span={6}>
            <Statistic title="NLP权重" value={config.hybrid.nlp_weight.toFixed(2)} valueStyle={{ color: '#722ed1' }} />
          </Col>
          <Col span={6}>
            <Statistic title="策略" value={config.hybrid.strategy === 'sequential' ? '顺序' : '并行'} valueStyle={{ color: '#52c41a' }} />
          </Col>
        </Row>

        {/* 任务类型与算法映射说明 */}
        <Divider style={{ margin: '12px 0' }} />
        <div>
          <Text strong style={{ color: '#e0e0e0', fontSize: 13 }}>三种任务类型的算法调度策略</Text>
          <Row gutter={[12, 12]} style={{ marginTop: 8 }}>
            <Col xs={24} md={8}>
              <Card size="small" style={{ background: 'rgba(24,144,255,0.04)', borderLeft: '3px solid #1890ff' }}>
                <Text strong><TruckOutlined style={{ color: '#1890ff', marginRight: 6 }} />纯AGV任务</Text>
                <div style={{ fontSize: 11, color: '#a0a8c0', marginTop: 4 }}>
                  使用 <Text code>SA + ACO</Text><br/>
                  • SA：任务→AGV最优分配<br/>
                  • ACO：无碰撞AGV路径规划<br/>
                  • 不经过输送线边
                </div>
              </Card>
            </Col>
            <Col xs={24} md={8}>
              <Card size="small" style={{ background: 'rgba(114,46,209,0.04)', borderLeft: '3px solid #722ed1' }}>
                <Text strong><SwapOutlined style={{ color: '#722ed1', marginRight: 6 }} />纯输送线任务</Text>
                <div style={{ fontSize: 11, color: '#a0a8c0', marginTop: 4 }}>
                  使用 <Text code>NLP</Text><br/>
                  • 输送线段排序优化<br/>
                  • 容量约束与能量消耗<br/>
                  • 自动转运，无需AGV
                </div>
              </Card>
            </Col>
            <Col xs={24} md={8}>
              <Card size="small" style={{ background: 'rgba(235,47,150,0.04)', borderLeft: '3px solid #eb2f96' }}>
                <Text strong><MergeCellsOutlined style={{ color: '#eb2f96', marginRight: 6 }} />混合长程任务</Text>
                <div style={{ fontSize: 11, color: '#a0a8c0', marginTop: 4 }}>
                  使用 <Text code>Hybrid (SA+ACO+NLP)</Text><br/>
                  • Phase1-2: AGV取货+放料(SA+ACO)<br/>
                  • Phase3: 输送线转运(NLP)<br/>
                  • Phase4-5: AGV接货+卸货(SA+ACO)
                </div>
              </Card>
            </Col>
          </Row>
        </div>
      </Card>
    </Spin>
  );
};

export default AlgorithmConfigPage;
