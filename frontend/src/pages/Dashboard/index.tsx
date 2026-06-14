/**
 * Dashboard - 系统总览仪表盘
 *
 * Shows key metrics, AGV status, task progress, and schedule visualization.
 */

import React, { useEffect, useState, useCallback, useRef } from 'react';
import {
  Row,
  Col,
  Card,
  Statistic,
  Typography,
  Progress,
  Tag,
  Space,
  Button,
  Empty,
  Spin,
  Table,
  Tooltip,
} from 'antd';
import {
  RobotOutlined,
  ThunderboltOutlined,
  ClockCircleOutlined,
  CheckCircleOutlined,
  DashboardOutlined,
  ReloadOutlined,
  PlayCircleOutlined,
  NodeIndexOutlined,
} from '@ant-design/icons';
import * as api from '../../services/api';
import { useStore } from '../../store/useStore';

const { Text, Title } = Typography;

const Dashboard: React.FC = () => {
  const {
    agvs, setAgvs,
    tasks, setTasks,
    scheduleResult, setScheduleResult,
    metrics, setMetrics,
    loading, setLoading,
  } = useStore();

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [scheduleRunning, setScheduleRunning] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading('dashboard', true);
    try {
      const [agvData, taskData, metricsData] = await Promise.all([
        api.getAgvStatuses(),
        api.getTasks(),
        api.getMetrics(),
      ]);
      setAgvs(agvData);
      setTasks(taskData);
      setMetrics(metricsData);
    } catch (e) {
      console.error('Dashboard fetch error:', e);
    } finally {
      setLoading('dashboard', false);
    }
  }, [setAgvs, setTasks, setMetrics, setLoading]);

  useEffect(() => { fetchData(); }, [fetchData]);

  // Auto-refresh every 5s
  useEffect(() => {
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, [fetchData]);

  // Draw AGV position canvas
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !agvs.length) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const W = canvas.width;
    const H = canvas.height;

    ctx.clearRect(0, 0, W, H);

    // Grid
    ctx.strokeStyle = 'rgba(24,144,255,0.06)';
    ctx.lineWidth = 1;
    for (let i = 0; i < W; i += 40) {
      ctx.beginPath(); ctx.moveTo(i, 0); ctx.lineTo(i, H); ctx.stroke();
    }
    for (let i = 0; i < H; i += 40) {
      ctx.beginPath(); ctx.moveTo(0, i); ctx.lineTo(W, i); ctx.stroke();
    }

    // Draw AGVs
    agvs.forEach((agv) => {
      const x = ((agv.x + 5) / 50) * W;
      const y = ((agv.y + 5) / 55) * H;

      // Glow effect
      ctx.beginPath();
      ctx.arc(x, y, 12, 0, Math.PI * 2);
      const color = agv.status === 'executing' ? '#52c41a' :
                     agv.status === 'moving' ? '#1890ff' :
                     agv.status === 'charging' ? '#faad14' : '#666';
      ctx.fillStyle = color + '30';
      ctx.fill();

      // AGV dot
      ctx.beginPath();
      ctx.arc(x, y, 6, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.strokeStyle = '#fff';
      ctx.lineWidth = 2;
      ctx.stroke();

      // Label
      ctx.fillStyle = '#e0e0e0';
      ctx.font = '10px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(agv.id, x, y - 12);

      // Battery ring
      ctx.beginPath();
      ctx.arc(x, y, 10, -Math.PI / 2, -Math.PI / 2 + (agv.battery / 100) * Math.PI * 2);
      ctx.strokeStyle = agv.battery > 20 ? '#52c41a' : '#ff4d4f';
      ctx.lineWidth = 2;
      ctx.stroke();
    });
  }, [agvs]);

  const handleRunSchedule = async () => {
    setScheduleRunning(true);
    try {
      const result = await api.runSchedule();
      setScheduleResult(result);
    } catch (e) {
      console.error('Schedule run error:', e);
    } finally {
      setScheduleRunning(false);
    }
  };

  const activeAgvs = agvs.filter(a => a.status !== 'idle');
  const completedTasks = tasks.filter(t => t.status === 'completed').length;

  return (
    <Spin spinning={loading['dashboard']}>
      <div className="page-title">
        <DashboardOutlined style={{ marginRight: 8 }} />
        系统总览
      </div>

      {/* Stats Cards */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} sm={12} lg={6}>
          <Card className="stat-card" hoverable>
            <RobotOutlined className="stat-icon" />
            <Statistic
              title="AGV总数 / 活跃"
              value={agvs.length}
              suffix={
                <Text style={{ fontSize: 14, color: '#52c41a' }}>
                  / {activeAgvs.length} 活跃
                </Text>
              }
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card className="stat-card" hoverable>
            <ThunderboltOutlined className="stat-icon" />
            <Statistic
              title="任务总数 / 已完成"
              value={tasks.length}
              suffix={
                <Text style={{ fontSize: 14, color: '#52c41a' }}>
                  / {completedTasks} 完成
                </Text>
              }
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card className="stat-card" hoverable>
            <ClockCircleOutlined className="stat-icon" />
            <Statistic
              title="总完工时间 (Makespan)"
              value={scheduleResult?.makespan?.toFixed(1) ?? '--'}
              suffix="s"
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card className="stat-card" hoverable>
            <NodeIndexOutlined className="stat-icon" />
            <Statistic
              title="AGV利用率"
              value={scheduleResult?.metrics?.agv_utilization
                ? (scheduleResult.metrics.agv_utilization * 100).toFixed(1)
                : '--'}
              suffix="%"
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        {/* AGV Status List */}
        <Col xs={24} lg={12}>
          <Card
            title="AGV 状态"
            extra={
              <Space>
                <Button size="small" icon={<ReloadOutlined />} onClick={fetchData} />
                <Button
                  type="primary"
                  size="small"
                  icon={<PlayCircleOutlined />}
                  loading={scheduleRunning}
                  onClick={handleRunSchedule}
                >
                  执行调度
                </Button>
              </Space>
            }
          >
            {agvs.length === 0 ? (
              <Empty description="暂无AGV数据" />
            ) : (
              agvs.map((agv) => (
                <div key={agv.id} style={{
                  display: 'flex',
                  alignItems: 'center',
                  padding: '8px 0',
                  borderBottom: '1px solid rgba(255,255,255,0.04)',
                }}>
                  <RobotOutlined style={{
                    fontSize: 20,
                    color: agv.status === 'executing' ? '#52c41a' :
                           agv.status === 'moving' ? '#1890ff' : '#666',
                    marginRight: 12,
                  }} />
                  <div style={{ flex: 1 }}>
                    <div>
                      <Text strong>{agv.name || agv.id}</Text>
                      <Tag
                        color={agv.status === 'idle' ? 'default' :
                               agv.status === 'executing' ? 'success' :
                               agv.status === 'moving' ? 'processing' :
                               agv.status === 'charging' ? 'warning' : 'error'}
                        style={{ marginLeft: 8 }}
                      >
                        {agv.status === 'idle' ? '空闲' :
                         agv.status === 'executing' ? '执行中' :
                         agv.status === 'moving' ? '移动中' :
                         agv.status === 'charging' ? '充电中' : agv.status}
                      </Tag>
                    </div>
                    <div style={{ marginTop: 4 }}>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        位置: ({agv.x.toFixed(1)}, {agv.y.toFixed(1)})
                        {agv.current_task && ` | 任务: ${agv.current_task}`}
                      </Text>
                    </div>
                  </div>
                  <div style={{ width: 120 }}>
                    <Text style={{ fontSize: 12, color: '#a0a8c0', marginRight: 8 }}>
                      电量
                    </Text>
                    <Progress
                      percent={agv.battery}
                      size="small"
                      strokeColor={agv.battery > 30 ? '#52c41a' : '#ff4d4f'}
                      format={(p) => `${p}%`}
                      style={{ width: 70 }}
                    />
                  </div>
                </div>
              ))
            )}
          </Card>
        </Col>

        {/* AGV Position Map */}
        <Col xs={24} lg={12}>
          <Card title="AGV 位置可视化">
            <div className="map-canvas-wrapper" style={{ height: 300 }}>
              <canvas
                ref={canvasRef}
                width={500}
                height={300}
                style={{ width: '100%', height: '100%' }}
              />
            </div>
          </Card>
        </Col>
      </Row>

      {/* Schedule Result */}
      {scheduleResult && (
        <Card
          title="最新调度结果"
          style={{ marginTop: 16 }}
          extra={
            <Space>
              <Tag color="blue">算法耗时: {scheduleResult.algorithm_runtime_ms.toFixed(0)}ms</Tag>
              <Tag color="green">总成本: {scheduleResult.total_cost.toFixed(2)}</Tag>
            </Space>
          }
        >
          <Row gutter={[16, 16]}>
            <Col span={24}>
              <Table
                dataSource={scheduleResult.assignments}
                rowKey="agv_id"
                pagination={false}
                size="small"
                columns={[
                  { title: 'AGV', dataIndex: 'agv_id', key: 'agv_id',
                    render: (v: string) => <Tag color="blue">{v}</Tag> },
                  { title: '任务', dataIndex: 'task_id', key: 'task_id' },
                  { title: '路径', dataIndex: 'path', key: 'path',
                    render: (p: string[]) => (
                      <Text style={{ fontSize: 11, fontFamily: 'monospace' }}>
                        {p.join(' → ')}
                      </Text>
                    ) },
                  { title: '路径成本', dataIndex: 'path_cost', key: 'path_cost',
                    render: (v: number) => v.toFixed(2) },
                  { title: '开始(s)', dataIndex: 'start_time', key: 'start_time',
                    render: (v: number) => v.toFixed(1) },
                  { title: '结束(s)', dataIndex: 'end_time', key: 'end_time',
                    render: (v: number) => v.toFixed(1) },
                  { title: '等待(s)', dataIndex: 'wait_times', key: 'wait_times',
                    render: (w: number[]) => w.length > 0
                      ? w.reduce((a,b) => a+b, 0).toFixed(1)
                      : '-' },
                ]}
              />
            </Col>
          </Row>

          {/* Metrics */}
          <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
            {Object.entries(scheduleResult.metrics).map(([key, value]) => (
              <Col xs={12} sm={6} key={key}>
                <Card size="small" style={{ background: 'rgba(24,144,255,0.03)' }}>
                  <Statistic
                    title={key.replace(/_/g, ' ')}
                    value={typeof value === 'number' ? value.toFixed(2) : String(value)}
                    valueStyle={{ fontSize: 16, color: '#e0e0e0' }}
                  />
                </Card>
              </Col>
            ))}
          </Row>
        </Card>
      )}
    </Spin>
  );
};

export default Dashboard;
