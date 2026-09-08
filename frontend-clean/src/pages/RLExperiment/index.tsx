/**
 * RL 实验与 A/B 测试页面 — 体现 Phase 8 能力
 *
 * 功能:
 *  1. RL 模型状态查看
 *  2. A/B 测试启动与结果对比
 *  3. 自适应调度参数配置
 *  4. 分布式调度监控
 */

import React, { useState, useEffect, useCallback } from 'react';
import {
  Card, Row, Col, Button, Form, Input, InputNumber, Select, Table, Tag, Space,
  message, Statistic, Descriptions, Progress, Empty, Divider, Typography, Badge,
} from 'antd';
import {
  ExperimentOutlined, PlayCircleOutlined, ReloadOutlined, BulbOutlined,
  ClusterOutlined, RobotOutlined, BarChartOutlined, TrophyOutlined,
} from '@ant-design/icons';
import {
  startABTest, getABTestResult, getRLModelStatus,
  submitDistributedOrder, getDistributedResults,
  startDistributedWorker, stopDistributedWorker,
  ABTestResult,
} from '../../services/advancedApi';

const { Text } = Typography;

const RLExperimentPage: React.FC = () => {
  const [abForm] = Form.useForm();
  const [abResult, setAbResult] = useState<ABTestResult | null>(null);
  const [rlStatus, setRlStatus] = useState<{ loaded: boolean; model_type: string; model_path: string | null } | null>(null);
  const [distResults, setDistResults] = useState<Record<string, unknown>[]>([]);
  const [workerRunning, setWorkerRunning] = useState(false);
  const [loading, setLoading] = useState(false);

  const refreshAll = useCallback(async () => {
    setLoading(true);
    try {
      const [rl, dist] = await Promise.all([
        getRLModelStatus().catch(() => null),
        getDistributedResults(20).catch(() => null),
      ]);
      setRlStatus(rl);
      if (dist) setDistResults(dist.results);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refreshAll(); }, [refreshAll]);

  // 启动 A/B 测试
  const handleStartAB = async () => {
    try {
      const values = await abForm.validateFields();
      await startABTest({
        test_name: values.test_name,
        strategy_a: values.strategy_a,
        strategy_b: values.strategy_b,
        traffic_split: values.traffic_split || 0.5,
      });
      message.success(`A/B 测试 "${values.test_name}" 已启动`);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '启动失败');
    }
  };

  // 查询 A/B 测试结果
  const handleQueryAB = async () => {
    try {
      const values = abForm.getFieldsValue();
      const result = await getABTestResult(values.test_name);
      if (!result) {
        setAbResult(null);
        message.warning(`测试 "${values.test_name}" 不存在或尚未启动`);
        return;
      }
      // 确保必填字段存在，防止后续渲染 undefined 访问
      setAbResult({
        test_name: result.test_name || '',
        strategy_a: result.strategy_a || '',
        strategy_b: result.strategy_b || '',
        samples_a: result.samples_a ?? 0,
        samples_b: result.samples_b ?? 0,
        metrics_a: result.metrics_a || {},
        metrics_b: result.metrics_b || {},
        winner: result.winner || null,
      });
      message.success('结果已获取');
    } catch (e: any) {
      setAbResult(null); // 异常时清空，避免残留脏数据
      message.error(e?.response?.data?.detail || e?.message || '查询失败');
    }
  };

  // 提交分布式订单
  const handleSubmitOrder = async () => {
    try {
      const result = await submitDistributedOrder({
        test_order: true,
        timestamp: new Date().toISOString(),
      });
      if (result.success) {
        message.success(`订单已提交: ${result.message_id}`);
        refreshAll();
      }
    } catch {
      message.error('提交失败 (Redis 可能未连接)');
    }
  };

  // Worker 控制
  const handleWorkerToggle = async () => {
    try {
      if (workerRunning) {
        await stopDistributedWorker();
        setWorkerRunning(false);
        message.success('Worker 已停止');
      } else {
        await startDistributedWorker();
        setWorkerRunning(true);
        message.success('Worker 已启动');
      }
    } catch {
      message.error('操作失败');
    }
  };

  // 自适应调度权重
  const [adaptiveWeights, setAdaptiveWeights] = useState({
    distance: 0.35, wait_time: 0.25, buffer_space: 0.20, battery: 0.20,
  });

  return (
    <div>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="RL 模型状态"
              valueRender={() => (
                <Badge
                  status={rlStatus?.loaded ? 'success' : 'warning'}
                  text={rlStatus?.loaded ? '已加载' : '未加载'}
                />
              )}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="模型类型" value={rlStatus?.model_type || '-'} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="分布式 Worker"
              valueRender={() => (
                <Badge
                  status={workerRunning ? 'processing' : 'default'}
                  text={workerRunning ? '运行中' : '已停止'}
                />
              )}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="队列结果数" value={distResults.length} />
          </Card>
        </Col>
      </Row>

      <Card title={<><RobotOutlined /> RL 模型状态</>} size="small" style={{ marginBottom: 16 }}>
        <Descriptions column={3} size="small">
          <Descriptions.Item label="模型状态">
            <Badge status={rlStatus?.loaded ? 'success' : 'warning'}
              text={rlStatus?.loaded ? '已加载' : '未加载 (降级为启发式)'} />
          </Descriptions.Item>
          <Descriptions.Item label="模型类型">{rlStatus?.model_type || '-'}</Descriptions.Item>
          <Descriptions.Item label="模型路径">{rlStatus?.model_path || '-'}</Descriptions.Item>
        </Descriptions>
        <Divider style={{ margin: '12px 0' }} />
        <Space>
          <Button icon={<ReloadOutlined />} onClick={refreshAll} loading={loading}>刷新</Button>
          <Text type="secondary" style={{ fontSize: 12 }}>
            注: 未安装 PyTorch 时自动降级为贪心启发式, 安装后可加载 DQN/PPO 模型
          </Text>
        </Space>
      </Card>

      <Row gutter={16}>
        <Col span={12}>
          <Card title={<><ExperimentOutlined /> A/B 测试</>} size="small">
            <Form form={abForm} layout="vertical" initialValues={{
              test_name: 'mip_vs_rl', strategy_a: 'mip', strategy_b: 'rl', traffic_split: 0.5,
            }}>
              <Form.Item name="test_name" label="测试名称" rules={[{ required: true }]}>
                <Input placeholder="mip_vs_rl" />
              </Form.Item>
              <Row gutter={8}>
                <Col span={8}>
                  <Form.Item name="strategy_a" label="策略 A">
                    <Select options={[
                      { label: 'MIP (精确求解)', value: 'mip' },
                      { label: 'Hungarian (匈牙利)', value: 'hungarian' },
                      { label: 'Greedy (贪心)', value: 'greedy' },
                      { label: 'RL (强化学习)', value: 'rl' },
                    ]} />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item name="strategy_b" label="策略 B">
                    <Select options={[
                      { label: 'MIP (精确求解)', value: 'mip' },
                      { label: 'Hungarian (匈牙利)', value: 'hungarian' },
                      { label: 'Greedy (贪心)', value: 'greedy' },
                      { label: 'RL (强化学习)', value: 'rl' },
                    ]} />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item name="traffic_split" label="A 组流量">
                    <InputNumber min={0} max={1} step={0.1} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
              </Row>
              <Space>
                <Button type="primary" icon={<PlayCircleOutlined />} onClick={handleStartAB}>启动测试</Button>
                <Button icon={<BarChartOutlined />} onClick={handleQueryAB}>查询结果</Button>
              </Space>
            </Form>

            {abResult && typeof abResult === 'object' && abResult.test_name && (
              <div style={{ marginTop: 16 }}>
                <Divider style={{ margin: '8px 0' }} />
                <Descriptions column={2} size="small" title="A/B 测试结果">
                  <Descriptions.Item label="测试名称">{abResult.test_name || '-'}</Descriptions.Item>
                  <Descriptions.Item label="胜者">
                    {(abResult.winner && abResult.winner !== '' && abResult.winner !== 'undetermined') ? (
                      <Tag color="gold" icon={<TrophyOutlined />}>{abResult.winner}</Tag>
                    ) : (
                      <Tag>待定 (样本量不足: A={abResult.samples_a}, B={abResult.samples_b})</Tag>
                    )}
                  </Descriptions.Item>
                  <Descriptions.Item label="策略 A 样本数">{abResult.samples_a}</Descriptions.Item>
                  <Descriptions.Item label="策略 B 样本数">{abResult.samples_b}</Descriptions.Item>
                </Descriptions>
                <Row gutter={16}>
                  <Col span={12}>
                    <Card type="inner" title={`策略 A: ${abResult.strategy_a || '-'}`} size="small">
                      {abResult.metrics_a && Object.entries(abResult.metrics_a).map(([k, v]) => (
                        <div key={k}>
                          <Text type="secondary">{k}: </Text>
                          <Text strong>{typeof v === 'number' ? v.toFixed(2) : v}</Text>
                        </div>
                      ))}
                      {Object.keys(abResult.metrics_a).length === 0 && <Text type="secondary">暂无数据</Text>}
                    </Card>
                  </Col>
                  <Col span={12}>
                    <Card type="inner" title={`策略 B: ${abResult.strategy_b || '-'}`} size="small">
                      {abResult.metrics_b && Object.entries(abResult.metrics_b).map(([k, v]) => (
                        <div key={k}>
                          <Text type="secondary">{k}: </Text>
                          <Text strong>{typeof v === 'number' ? v.toFixed(2) : v}</Text>
                        </div>
                      ))}
                      {Object.keys(abResult.metrics_b).length === 0 && <Text type="secondary">暂无数据</Text>}
                    </Card>
                  </Col>
                </Row>
              </div>
            )}
          </Card>
        </Col>

        <Col span={12}>
          <Card title={<><BulbOutlined /> 自适应调度配置</>} size="small" style={{ marginBottom: 16 }}>
            <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 12 }}>
              神经网络评估 4 个关键因素: AGV-任务距离 / 任务等待时间 / 缓冲区空间 / AGV 电量
            </Text>
            {Object.entries(adaptiveWeights).map(([key, val]) => (
              <div key={key} style={{ marginBottom: 8 }}>
                <Row justify="space-between">
                  <Text>{key}</Text>
                  <Text type="secondary">{(val * 100).toFixed(0)}%</Text>
                </Row>
                <Progress
                  percent={val * 100}
                  size="small"
                  strokeColor={key === 'distance' ? '#1890ff' : key === 'wait_time' ? '#52c41a' : key === 'buffer_space' ? '#faad14' : '#ff4d4f'}
                />
              </div>
            ))}
            <Divider style={{ margin: '8px 0' }} />
            <Text type="secondary" style={{ fontSize: 11 }}>
              高负载时增加等待时间权重; 高拥塞时增加距离权重; 系统自动动态调整
            </Text>
          </Card>

          <Card title={<><ClusterOutlined /> 分布式调度</>} size="small">
            <Space style={{ marginBottom: 12 }}>
              <Button
                type={workerRunning ? 'default' : 'primary'}
                danger={workerRunning}
                icon={<PlayCircleOutlined />}
                onClick={handleWorkerToggle}
              >
                {workerRunning ? '停止 Worker' : '启动 Worker'}
              </Button>
              <Button icon={<ReloadOutlined />} onClick={refreshAll}>刷新</Button>
              <Button onClick={handleSubmitOrder}>提交测试订单</Button>
            </Space>
            <Table
              dataSource={distResults.map((r, i) => ({ key: i, ...r }))}
              columns={[
                { title: '订单 ID', dataIndex: 'order_id', ellipsis: true },
                { title: '结果 ID', dataIndex: 'result_id', ellipsis: true },
                { title: '分配数', dataIndex: 'assignments' },
                { title: 'Makespan', dataIndex: 'makespan', render: (v: number) => v?.toFixed(1) },
                { title: '完成时间', dataIndex: 'completed_at', ellipsis: true },
              ]}
              pagination={{ pageSize: 5 }}
              size="small"
              locale={{ emptyText: <Empty description="暂无结果 (需 Redis + Worker)" /> }}
            />
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default RLExperimentPage;
