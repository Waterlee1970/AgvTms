/**
 * AgvMonitor - AGV实时监控页面
 */

import React, { useEffect, useState, useCallback, useRef } from 'react';
import {
  Row, Col, Card, Statistic, Tag, Progress, Space, Button, Empty, Spin, Typography,
} from 'antd';
import {
  RobotOutlined, ThunderboltOutlined, ReloadOutlined,
  PlayCircleOutlined, PauseCircleOutlined,
} from '@ant-design/icons';
import * as api from '../../services/api';
import { useStore } from '../../store/useStore';

const { Text } = Typography;

const AgvMonitor: React.FC = () => {
  const { agvs, setAgvs, scheduleResult, setScheduleResult, loading, setLoading } = useStore();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [scheduleRunning, setScheduleRunning] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);

  const fetchAgvs = useCallback(async () => {
    setLoading('agv', true);
    try {
      const data = await api.getAgvStatuses();
      setAgvs(data);
    } catch (e) {
      console.error('Fetch AGVs error:', e);
    } finally {
      setLoading('agv', false);
    }
  }, [setAgvs, setLoading]);

  useEffect(() => { fetchAgvs(); }, [fetchAgvs]);

  // Auto refresh
  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(fetchAgvs, 2000);
    return () => clearInterval(interval);
  }, [autoRefresh, fetchAgvs]);

  // Draw AGV path map
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !agvs.length) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const W = canvas.width;
    const H = canvas.height;

    ctx.fillStyle = 'rgba(10,14,39,0.95)';
    ctx.fillRect(0, 0, W, H);

    // Grid
    ctx.strokeStyle = 'rgba(24,144,255,0.04)';
    ctx.lineWidth = 1;
    for (let i = 0; i < W; i += 30) {
      ctx.beginPath(); ctx.moveTo(i, 0); ctx.lineTo(i, H); ctx.stroke();
    }
    for (let i = 0; i < H; i += 30) {
      ctx.beginPath(); ctx.moveTo(0, i); ctx.lineTo(W, i); ctx.stroke();
    }

    const scaleX = W / 50;
    const scaleY = H / 55;

    // Draw paths
    agvs.forEach((agv) => {
      if (!agv.path || agv.path.length < 2) return;
      ctx.beginPath();
      ctx.strokeStyle = 'rgba(24,144,255,0.15)';
      ctx.lineWidth = 2;
      ctx.setLineDash([5, 5]);
      // Simplified: draw from AGV position to path end
      const startX = (agv.x + 5) * scaleX;
      const startY = (agv.y + 5) * scaleY;
      const endX = (agv.x + 20) * scaleX;
      const endY = (agv.y + 30) * scaleY;
      ctx.moveTo(startX, startY);
      ctx.lineTo(endX, endY);
      ctx.stroke();
      ctx.setLineDash([]);
    });

    // Draw AGVs
    agvs.forEach((agv) => {
      const x = (agv.x + 5) * scaleX;
      const y = (agv.y + 5) * scaleY;

      const statusColor = agv.status === 'executing' ? '#52c41a' :
                           agv.status === 'moving' ? '#1890ff' :
                           agv.status === 'charging' ? '#faad14' :
                           agv.status === 'error' ? '#ff4d4f' : '#666';

      // Glow
      if (agv.status === 'moving' || agv.status === 'executing') {
        ctx.beginPath();
        ctx.arc(x, y, 16, 0, Math.PI * 2);
        ctx.fillStyle = statusColor + '15';
        ctx.fill();
      }

      // Body
      ctx.beginPath();
      ctx.arc(x, y, 8, 0, Math.PI * 2);
      ctx.fillStyle = statusColor;
      ctx.fill();
      ctx.strokeStyle = '#fff';
      ctx.lineWidth = 2;
      ctx.stroke();

      // Label
      ctx.fillStyle = '#e0e0e0';
      ctx.font = 'bold 10px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(agv.id, x, y - 14);

      // Battery arc
      ctx.beginPath();
      ctx.arc(x, y, 12, -Math.PI / 2, -Math.PI / 2 + (agv.battery / 100) * Math.PI * 2);
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
    } catch { /* ignore */ }
    finally { setScheduleRunning(false); }
  };

  return (
    <Spin spinning={loading['agv']}>
      <div className="page-title">
        <RobotOutlined style={{ marginRight: 8 }} />
        AGV 实时监控
        <Space style={{ float: 'right' }}>
          <Button
            icon={autoRefresh ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
            onClick={() => setAutoRefresh(!autoRefresh)}
          >
            {autoRefresh ? '暂停刷新' : '开始刷新'}
          </Button>
          <Button icon={<ReloadOutlined />} onClick={fetchAgvs}>刷新</Button>
          <Button type="primary" icon={<PlayCircleOutlined />}
            loading={scheduleRunning} onClick={handleRunSchedule}>
            执行调度
          </Button>
        </Space>
      </div>

      {/* AGV Cards */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        {agvs.length === 0 ? (
          <Col span={24}><Empty description="暂无AGV数据" /></Col>
        ) : (
          agvs.map((agv) => (
            <Col xs={24} sm={12} lg={6} key={agv.id}>
              <Card
                hoverable
                size="small"
                style={{
                  borderColor: agv.status === 'executing' ? 'rgba(82,196,26,0.3)' :
                               agv.status === 'moving' ? 'rgba(24,144,255,0.3)' :
                               agv.status === 'error' ? 'rgba(255,77,79,0.3)' : undefined,
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div>
                    <Text strong style={{ fontSize: 15 }}>{agv.name || agv.id}</Text>
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
                  <RobotOutlined style={{
                    fontSize: 24,
                    color: agv.status === 'executing' ? '#52c41a' :
                           agv.status === 'moving' ? '#1890ff' : '#666',
                  }} />
                </div>

                <Row gutter={8} style={{ marginTop: 12 }}>
                  <Col span={12}>
                    <Statistic
                      title="电量"
                      value={agv.battery}
                      suffix="%"
                      valueStyle={{ fontSize: 16, color: agv.battery > 30 ? '#52c41a' : '#ff4d4f' }}
                    />
                  </Col>
                  <Col span={12}>
                    <Statistic
                      title="速度"
                      value={agv.speed?.toFixed(1) || '0.0'}
                      suffix="m/s"
                      valueStyle={{ fontSize: 16 }}
                    />
                  </Col>
                </Row>

                <div style={{ marginTop: 8 }}>
                  <Progress
                    percent={agv.battery}
                    size="small"
                    strokeColor={agv.battery > 30 ? '#52c41a' : '#ff4d4f'}
                    showInfo={false}
                  />
                </div>

                <div style={{ marginTop: 8 }}>
                  <Text type="secondary" style={{ fontSize: 11 }}>
                    位置: ({agv.x.toFixed(1)}, {agv.y.toFixed(1)})
                    {agv.current_node && ` | 节点: ${agv.current_node}`}
                  </Text>
                </div>
                {agv.current_task && (
                  <div style={{ marginTop: 4 }}>
                    <Tag color="blue" style={{ fontSize: 10 }}>任务: {agv.current_task}</Tag>
                  </div>
                )}
              </Card>
            </Col>
          ))
        )}
      </Row>

      {/* AGV Path Map */}
      <Card title="AGV 路径可视化" size="small">
        <div className="map-canvas-wrapper" style={{ height: 400 }}>
          <canvas
            ref={canvasRef}
            width={700}
            height={400}
            style={{ width: '100%', height: '100%' }}
          />
        </div>
      </Card>

      {/* Utilization Stats */}
      {agvs.length > 0 && (
        <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
          <Col span={6}>
            <Card size="small">
              <Statistic
                title="总AGV数"
                value={agvs.length}
                valueStyle={{ color: '#e0e0e0' }}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic
                title="活跃AGV"
                value={agvs.filter(a => a.status !== 'idle').length}
                valueStyle={{ color: '#52c41a' }}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic
                title="平均电量"
                value={agvs.length > 0 ? (agvs.reduce((s, a) => s + a.battery, 0) / agvs.length).toFixed(1) : 0}
                suffix="%"
                valueStyle={{ color: '#faad14' }}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic
                title="执行中任务"
                value={agvs.filter(a => a.current_task).length}
                valueStyle={{ color: '#1890ff' }}
              />
            </Card>
          </Col>
        </Row>
      )}
    </Spin>
  );
};

export default AgvMonitor;
