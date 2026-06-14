/**
 * ConveyorConfig - 输送线配置页面
 */

import React, { useEffect, useState, useCallback } from 'react';
import {
  Card, Table, Button, Space, Tag, Empty, Spin, Typography,
  Row, Col, Statistic, Modal, Form, Input, InputNumber, Select,
  message, Progress, Tooltip, Divider,
} from 'antd';
import {
  NodeIndexOutlined, PlusOutlined, ReloadOutlined, EditOutlined,
  TruckOutlined, SwapOutlined, MergeCellsOutlined, InfoCircleOutlined,
} from '@ant-design/icons';
import * as api from '../../services/api';
import { useStore } from '../../store/useStore';
import type { ConveyorSegment, ConveyorTask } from '../../services/api';

const { Text } = Typography;

const ConveyorConfig: React.FC = () => {
  const { conveyorSegments, setConveyorSegments, conveyorTasks, setConveyorTasks, loading, setLoading } = useStore();
  const [addModalOpen, setAddModalOpen] = useState(false);
  const [form] = Form.useForm();

  const fetchSegments = useCallback(async () => {
    setLoading('conveyor', true);
    try {
      const data = await api.getConveyorSegments();
      setConveyorSegments(data);
    } catch (e) {
      console.error('Fetch conveyor segments error:', e);
    } finally {
      setLoading('conveyor', false);
    }
  }, [setConveyorSegments, setLoading]);

  useEffect(() => { fetchSegments(); }, [fetchSegments]);

  const handleAddSegment = async () => {
    try {
      const values = await form.validateFields();
      message.info('输送段添加功能通过地图配置中的"添加边"实现，请前往地图配置页面添加 is_conveyor=true 的边');
      setAddModalOpen(false);
    } catch { /* validation error */ }
  };

  const columns = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 120,
      render: (v: string) => <Text code>{v}</Text> },
    { title: '名称', dataIndex: 'name', key: 'name' },
    { title: '起点', dataIndex: 'from_node', key: 'from_node', width: 90,
      render: (v: string) => <Tag color="purple">{v}</Tag> },
    { title: '终点', dataIndex: 'to_node', key: 'to_node', width: 90,
      render: (v: string) => <Tag color="magenta">{v}</Tag> },
    {
      title: '速度(m/s)', dataIndex: 'speed', key: 'speed', width: 90,
      render: (v: number) => v.toFixed(2),
    },
    {
      title: '长度(m)', dataIndex: 'length', key: 'length', width: 80,
      render: (v: number) => v.toFixed(1),
    },
    {
      title: '耗时(s)', key: 'duration', width: 80,
      render: (_: unknown, r: ConveyorSegment) => (r.length / r.speed).toFixed(1),
    },
    {
      title: '方向', dataIndex: 'direction', key: 'direction', width: 80,
      render: (d: string) => (
        <Tag color={d === 'bidirectional' ? 'green' : 'blue'}>
          {d === 'bidirectional' ? '双向' : '单向'}
        </Tag>
      ),
    },
  ];

  // 三种任务类型统计
  const ctAgvOnly = conveyorTasks.filter(t => t.task_type === 'agv_only');
  const ctConvOnly = conveyorTasks.filter(t => t.task_type === 'conveyor_only');
  const ctMixed = conveyorTasks.filter(t => t.task_type === 'mixed');
  const totalCTasks = conveyorTasks.length;

  // Timeline visualization
  const timelineRef = React.useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = timelineRef.current;
    if (!canvas || !conveyorSegments.length) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const W = canvas.width;
    const H = canvas.height;
    ctx.fillStyle = '#0a0e27';
    ctx.fillRect(0, 0, W, H);

    const barHeight = 30;
    const gap = 10;
    const marginLeft = 160;

    // Draw segment bars
    conveyorSegments.forEach((seg, i) => {
      const y = 20 + i * (barHeight + gap);
      const duration = seg.length / seg.speed;
      const barWidth = Math.min(duration * 10, W - marginLeft - 20);

      // Label
      ctx.fillStyle = '#a0a8c0';
      ctx.font = '11px sans-serif';
      ctx.textAlign = 'right';
      ctx.fillText(seg.name || seg.id, marginLeft - 10, y + barHeight / 2 + 4);

      // Bar
      ctx.fillStyle = 'rgba(114,46,209,0.3)';
      ctx.fillRect(marginLeft, y, barWidth, barHeight);
      ctx.strokeStyle = 'rgba(114,46,209,0.6)';
      ctx.lineWidth = 1;
      ctx.strokeRect(marginLeft, y, barWidth, barHeight);

      // Time label
      ctx.fillStyle = '#e0e0e0';
      ctx.font = '10px monospace';
      ctx.textAlign = 'left';
      ctx.fillText(`${duration.toFixed(1)}s`, marginLeft + barWidth + 8, y + barHeight / 2 + 4);
    });
  }, [conveyorSegments]);

  return (
    <Spin spinning={loading['conveyor']}>
      <div className="page-title">
        <NodeIndexOutlined style={{ marginRight: 8 }} />
        输送线配置
        <Space style={{ float: 'right' }}>
          <Button icon={<ReloadOutlined />} onClick={fetchSegments}>刷新</Button>
        </Space>
      </div>

      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={6}>
          <Card size="small">
            <Statistic
              title="输送段总数"
              value={conveyorSegments.length}
              valueStyle={{ color: '#722ed1' }}
            />
          </Card>
        </Col>
        <Col xs={6}>
          <Card size="small">
            <Statistic
              title="总长度"
              value={conveyorSegments.reduce((s, seg) => s + seg.length, 0).toFixed(1)}
              suffix="m"
              valueStyle={{ color: '#e0e0e0' }}
            />
          </Card>
        </Col>
        <Col xs={6}>
          <Card size="small">
            <Statistic
              title="平均速度"
              value={conveyorSegments.length > 0
                ? (conveyorSegments.reduce((s, seg) => s + seg.speed, 0) / conveyorSegments.length).toFixed(2)
                : 0}
              suffix="m/s"
              valueStyle={{ color: '#1890ff' }}
            />
          </Card>
        </Col>
        <Col xs={6}>
          <Card size="small" style={{ background: 'rgba(114,46,209,0.03)' }}>
            <Statistic
              title={<span>输送线任务 <InfoCircleOutlined /></span>}
              value={totalCTasks}
              valueStyle={{ color: '#eb2f96' }}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={14}>
          <Card title={`输送段列表 (${conveyorSegments.length})`} size="small">
            <Table
              dataSource={conveyorSegments}
              columns={columns}
              rowKey="id"
              size="small"
              pagination={false}
              scroll={{ y: 350 }}
              locale={{ emptyText: <Empty description="暂无输送段数据" /> }}
            />
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card title="输送线时间轴" size="small">
            {conveyorSegments.length === 0 ? (
              <Empty description="暂无数据" />
            ) : (
              <div className="map-canvas-wrapper" style={{ height: Math.max(200, conveyorSegments.length * 45) }}>
                <canvas
                  ref={timelineRef}
                  width={600}
                  height={Math.max(200, conveyorSegments.length * 45)}
                  style={{ width: '100%', height: '100%' }}
                />
              </div>
            )}
          </Card>
        </Col>
      </Row>

      {/* Info Card - 包含三种任务类型说明 */}
      <Card size="small" style={{ marginTop: 16, background: 'rgba(114,46,209,0.03)' }}>
        {/* 任务类型分布（如果有任务） */}
        {totalCTasks > 0 && (
          <>
            <Row gutter={[16, 16]} style={{ marginBottom: 12 }}>
              <Col xs={8}>
                <Statistic
                  title={<span><TruckOutlined /> 纯AGV任务</span>}
                  value={ctAgvOnly.length}
                  suffix={`/ ${totalCTasks}`}
                  valueStyle={{ color: '#1890ff', fontSize: 16 }}
                />
                <Progress
                  percent={totalCTasks > 0 ? (ctAgvOnly.length / totalCTasks) * 100 : 0}
                  size="small"
                  strokeColor="#1890ff"
                  showInfo={false}
                  style={{ marginTop: 4 }}
                />
              </Col>
              <Col xs={8}>
                <Statistic
                  title={<span><SwapOutlined /> 纯输送线任务</span>}
                  value={ctConvOnly.length}
                  suffix={`/ ${totalCTasks}`}
                  valueStyle={{ color: '#722ed1', fontSize: 16 }}
                />
                <Progress
                  percent={totalCTasks > 0 ? (ctConvOnly.length / totalCTasks) * 100 : 0}
                  size="small"
                  strokeColor="#722ed1"
                  showInfo={false}
                  style={{ marginTop: 4 }}
                />
              </Col>
              <Col xs={8}>
                <Statistic
                  title={<span><MergeCellsOutlined /> 混合长程任务</span>}
                  value={ctMixed.length}
                  suffix={`/ ${totalCTasks}`}
                  valueStyle={{ color: '#eb2f96', fontSize: 16 }}
                />
                <Progress
                  percent={totalCTasks > 0 ? (ctMixed.length / totalCTasks) * 100 : 0}
                  size="small"
                  strokeColor="#eb2f96"
                  showInfo={false}
                  style={{ marginTop: 4 }}
                />
              </Col>
            </Row>
            <Divider style={{ margin: '8px 0' }} />
          </>
        )}
        <Text style={{ color: '#a0a8c0' }}>
          输送线系统说明：输送线通过地图配置中的边来定义。在添加边时，将 is_conveyor 设置为 true，
          并设置较低的速度限制（如 0.5 m/s）来模拟传送带。
        </Text>
        {totalCTasks === 0 && (
          <div style={{ marginTop: 12 }}>
            <Text type="secondary">
              支持三种输送线任务类型：
            </Text>
            <ul style={{ color: '#a0a8c0', fontSize: 12, paddingLeft: 20, marginTop: 4 }}>
              <li><Tag color="blue">纯AGV(agv_only)</Tag>：AGV直接搬运，不涉及输送线，约37.5%</li>
              <li><Tag color="purple">纯输送线(conveyor_only)</Tag>：物料自动转运到出口后等待消除，无需AGV，约37.5%</li>
              <li><Tag color="magenta">混合长程(mixed)</Tag>：AGV取货→输送线转运→AGV接货→送到目的地，5阶段协同，25%</li>
            </ul>
          </div>
        )}
        {totalCTasks > 0 && (
          <Space size="middle" wrap style={{ marginTop: 8 }}>
            <Tooltip title="AGV直接搬运，不涉及输送线"><Tag color="blue">agv_only: {ctAgvOnly.length}</Tag></Tooltip>
            <Tooltip title="物料自动转运，无需AGV参与"><Tag color="purple">conveyor_only: {ctConvOnly.length}</Tag></Tooltip>
            <Tooltip title="AGV→输送线→AGV协同（5阶段长程）"><Tag color="magenta">mixed: {ctMixed.length}</Tag></Tooltip>
          </Space>
        )}
      </Card>
    </Spin>
  );
};

export default ConveyorConfig;
