/**
 * TaskManager - 任务管理页面
 */

import React, { useEffect, useState, useCallback } from 'react';
import {
  Card, Table, Button, Space, Tag, Modal, Form, Input, InputNumber,
  Select, DatePicker, message, Empty, Spin, Typography, Row, Col, Statistic,
  Tabs, Divider,
} from 'antd';
import {
  PlusOutlined, ReloadOutlined, UnorderedListOutlined,
  PlayCircleOutlined, FilterOutlined,
  TruckOutlined, SwapOutlined, MergeCellsOutlined,
} from '@ant-design/icons';
import * as api from '../../services/api';
import { useStore } from '../../store/useStore';
import type { AgvTask, ConveyorTask } from '../../services/api';
import dayjs from 'dayjs';

/** 任务类型配置 */
const conveyorTypeConfig: Record<string, { color: string; label: string; icon: React.ReactNode; desc: string }> = {
  agv_only:     { color: 'blue',    label: '纯AGV',      icon: <TruckOutlined />,       desc: 'AGV直接搬运，不涉及输送线' },
  conveyor_only:{ color: 'purple',  label: '纯输送线',   icon: <SwapOutlined />,        desc: '物料自动转运，无需AGV参与' },
  mixed:        { color: 'magenta', label: '混合长程',   icon: <MergeCellsOutlined />,  desc: 'AGV→输送线→AGV协同（5阶段）' },
};

const { Text } = Typography;

const statusColors: Record<string, string> = {
  pending: 'default',
  assigned: 'blue',
  in_progress: 'processing',
  completed: 'success',
  failed: 'error',
  cancelled: 'warning',
};

const statusLabels: Record<string, string> = {
  pending: '待分配',
  assigned: '已分配',
  in_progress: '执行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
};

const TaskManager: React.FC = () => {
  const { tasks, setTasks, conveyorTasks, setConveyorTasks, scheduleResult, setScheduleResult, loading, setLoading } = useStore();
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [batchModalOpen, setBatchModalOpen] = useState(false);
  const [form] = Form.useForm();
  const [scheduleRunning, setScheduleRunning] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string | null>(null);
  const [currentPage, setCurrentPage] = useState(1);

  const fetchTasks = useCallback(async () => {
    setLoading('tasks', true);
    try {
      const data = await api.getTasks();
      setTasks(data);
    } catch (e) {
      console.error('Fetch tasks error:', e);
    } finally {
      setLoading('tasks', false);
    }
  }, [setTasks, setLoading]);

  useEffect(() => { fetchTasks(); }, [fetchTasks]);

  const handleCreateTask = async () => {
    try {
      const values = await form.validateFields();
      const task = {
        ...values,
        deadline: values.deadline?.toISOString(),
      };
      await api.createTasks([task]);
      message.success('任务已创建');
      setCreateModalOpen(false);
      form.resetFields();
      fetchTasks();
    } catch { message.error('创建失败'); }
  };

  const handleBatchCreate = async () => {
    try {
      const values = await form.validateFields();
      const count = values.batch_count || 5;
      const tasks: AgvTask[] = [];
      for (let i = 0; i < count; i++) {
        tasks.push({
          pickup_node: `N_P0${i % 5}`,
          dropoff_node: `N_D0${(i + 2) % 5}`,
          priority: Math.floor(Math.random() * 10) + 1,
        });
      }
      await api.createTasks(tasks);
      message.success(`已批量创建 ${count} 个任务`);
      setBatchModalOpen(false);
      form.resetFields();
      fetchTasks();
    } catch { message.error('批量创建失败'); }
  };

  const handleRunSchedule = async () => {
    setScheduleRunning(true);
    try {
      const result = await api.runSchedule();
      setScheduleResult(result);
      message.success('调度执行完成');
    } catch {
      message.error('调度执行失败');
    } finally {
      setScheduleRunning(false);
    }
  };

  const filteredTasks = statusFilter
    ? tasks.filter(t => t.status === statusFilter)
    : tasks;

  // Reset to page 1 when filter changes
  useEffect(() => { setCurrentPage(1); }, [statusFilter]);

  const columns = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 80 },
    { title: '取货点', dataIndex: 'pickup_node', key: 'pickup_node', width: 90 },
    { title: '卸货点', dataIndex: 'dropoff_node', key: 'dropoff_node', width: 90 },
    {
      title: '优先级', dataIndex: 'priority', key: 'priority', width: 80,
      sorter: (a: AgvTask, b: AgvTask) => a.priority - b.priority,
      render: (p: number) => {
        const color = p >= 8 ? 'red' : p >= 5 ? 'orange' : 'blue';
        return <Tag color={color}>{p}</Tag>;
      },
    },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 90,
      render: (s: string) => <Tag color={statusColors[s]}>{statusLabels[s] || s}</Tag>,
    },
    { title: '分配AGV', dataIndex: 'assigned_agv', key: 'assigned_agv', width: 100,
      render: (v: string) => v ? <Tag color="blue">{v}</Tag> : '-' },
    {
      title: '创建时间', dataIndex: 'create_time', key: 'create_time', width: 160,
      render: (v: string) => v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '-',
    },
  ];

  const pendingCount = tasks.filter(t => t.status === 'pending').length;
  const completedCount = tasks.filter(t => t.status === 'completed').length;
  const inProgressCount = tasks.filter(t => t.status === 'in_progress').length;

  // 输送线任务统计
  const ctPendingCount = conveyorTasks.filter(t => t.status === 'pending').length;
  const ctCompletedCount = conveyorTasks.filter(t => t.status === 'completed').length;

  // 输送线任务表格列定义
  const conveyorColumns = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 100,
      render: (v: string) => <Text code>{v}</Text> },
    { title: '任务类型', dataIndex: 'task_type', key: 'task_type', width: 110,
      render: (t: string) => {
        const cfg = conveyorTypeConfig[t];
        return cfg ? <Tag color={cfg.color} icon={cfg.icon}>{cfg.label}</Tag> : <Tag>{t}</Tag>;
      },
    },
    { title: '优先级', dataIndex: 'priority', key: 'priority', width: 80,
      sorter: (a: ConveyorTask, b: ConveyorTask) => a.priority - b.priority,
      render: (p: number) => {
        const color = p >= 8 ? 'red' : p >= 5 ? 'orange' : 'blue';
        return <Tag color={color}>{p}</Tag>;
      },
    },
    { title: '状态', dataIndex: 'status', key: 'status', width: 90,
      render: (s: string) => <Tag color={statusColors[s]}>{statusLabels[s] || s}</Tag>,
    },
    { title: '数量', dataIndex: 'quantity', key: 'quantity', width: 60 },
    { title: '物品类型', dataIndex: 'item_type', key: 'item_type', width: 90 },
    { title: '当前阶段', dataIndex: 'phase', key: 'phase', width: 120,
      render: (p: string) => (
        <Tag style={{ fontSize: 11 }}>
          {p === 'agv_pickup' ? 'AGV取货' :
           p === 'conveyor_loading' ? '输送上料' :
           p === 'conveyor_transit' ? '输送转运' :
           p === 'agv_receiving' ? 'AGV接货' :
           p === 'agv_dropoff' ? 'AGV卸货' :
           p === 'agv_only_dropoff' ? 'AGV卸货(纯)' :
           p === 'completed' ? '已完成' : p}
        </Tag>
      ),
    },
    { title: '预计输送时间(s)', dataIndex: 'estimated_conveyor_time', key: 'estimated_conveyor_time', width: 130,
      render: (v: number) => v > 0 ? v.toFixed(1) : '-',
    },
  ];

  return (
    <Spin spinning={loading['tasks']}>
      <div className="page-title">
        <UnorderedListOutlined style={{ marginRight: 8 }} />
        任务管理
        <Space style={{ float: 'right' }}>
          <Button icon={<ReloadOutlined />} onClick={fetchTasks}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateModalOpen(true)}>
            创建任务
          </Button>
          <Button icon={<PlusOutlined />} onClick={() => { form.resetFields(); setBatchModalOpen(true); }}>
            批量创建
          </Button>
          <Button
            type="primary"
            icon={<PlayCircleOutlined />}
            loading={scheduleRunning}
            onClick={handleRunSchedule}
            style={{ background: '#52c41a', borderColor: '#52c41a' }}
          >
            执行调度
          </Button>
        </Space>
      </div>

      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={6}>
          <Card size="small">
            <Statistic title="普通任务总数" value={tasks.length} valueStyle={{ color: '#e0e0e0' }} />
          </Card>
        </Col>
        <Col xs={6}>
          <Card size="small">
            <Statistic title="待处理" value={pendingCount} valueStyle={{ color: '#faad14' }} />
          </Card>
        </Col>
        <Col xs={6}>
          <Card size="small">
            <Statistic title="已完成" value={completedCount} valueStyle={{ color: '#52c41a' }} />
          </Card>
        </Col>
        <Col xs={6}>
          <Card size="small" style={{ background: 'rgba(114,46,209,0.03)' }}>
            <Statistic
              title={<span>输送线任务 <TruckOutlined /></span>}
              value={conveyorTasks.length}
              suffix={ctCompletedCount > 0 ? <Text type="success" style={{ fontSize: 12 }}> / {ctCompletedCount}完成</Text> : ''}
              valueStyle={{ color: '#722ed1' }}
            />
          </Card>
        </Col>
      </Row>

      {/* 任务列表 - 使用 Tabs 分离普通任务和输送线任务 */}
      <Card
        title="任务管理"
        extra={
          <Select
            allowClear
            placeholder="筛选状态"
            style={{ width: 120 }}
            value={statusFilter}
            onChange={setStatusFilter}
            options={Object.entries(statusLabels).map(([k, v]) => ({ value: k, label: v }))}
          />
        }
      >
        <Tabs
          defaultActiveKey="normal"
          items={[
            {
              key: 'normal',
              label: (
                <span>
                  普通AGV任务 <Tag>{tasks.length}</Tag>
                </span>
              ),
              children: (
                <Table
                  dataSource={filteredTasks}
                  columns={columns}
                  rowKey="id"
                  size="small"
                  pagination={{
                    current: currentPage,
                    pageSize: 15,
                    total: filteredTasks.length,
                    showTotal: (total) => `共 ${total} 条`,
                    onChange: (page) => setCurrentPage(page),
                    showSizeChanger: false,
                  }}
                  scroll={{ y: 400 }}
                  locale={{ emptyText: <Empty description="暂无普通任务" /> }}
                />
              ),
            },
            {
              key: 'conveyor',
              label: (
                <span>
                  输送线协同任务 <Tag color="purple">{conveyorTasks.length}</Tag>
                </span>
              ),
              children: (
                <>
                  {/* 任务类型分布说明 */}
                  <div style={{ marginBottom: 12, display: 'flex', gap: 24, flexWrap: 'wrap' }}>
                    {Object.entries(conveyorTypeConfig).map(([key, cfg]) => {
                      const cnt = conveyorTasks.filter(t => t.task_type === key).length;
                      return (
                        <Tag key={key} color={cfg.color} icon={cfg.icon} style={{ padding: '4px 12px' }}>
                          {cfg.label}: {cnt}个 — {cfg.desc}
                        </Tag>
                      );
                    })}
                  </div>
                  <Table
                    dataSource={conveyorTasks}
                    columns={conveyorColumns}
                    rowKey="id"
                    size="small"
                    pagination={{
                      pageSize: 10,
                      showTotal: (total) => `共 ${total} 条输送线任务`,
                      showSizeChanger: false,
                    }}
                    scroll={{ y: 350 }}
                    locale={{ emptyText: <Empty description="暂无输送线任务（请在场景中启用输送线）" /> }}
                  />
                </>
              ),
            },
          ]}
        />
      </Card>

      {/* Create Task Modal */}
      <Modal
        title="创建任务"
        open={createModalOpen}
        onOk={handleCreateTask}
        onCancel={() => setCreateModalOpen(false)}
      >
        <Form form={form} layout="vertical">
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="pickup_node" label="取货点" rules={[{ required: true }]}>
                <Input placeholder="如 N_P00" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="dropoff_node" label="卸货点" rules={[{ required: true }]}>
                <Input placeholder="如 N_D03" />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="priority" label="优先级" initialValue={5}>
            <InputNumber min={1} max={10} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="deadline" label="截止时间">
            <DatePicker showTime style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>

      {/* Batch Create Modal */}
      <Modal
        title="批量创建任务"
        open={batchModalOpen}
        onOk={handleBatchCreate}
        onCancel={() => setBatchModalOpen(false)}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="batch_count" label="任务数量" initialValue={5}>
            <InputNumber min={1} max={50} style={{ width: '100%' }} />
          </Form.Item>
          <Text type="secondary">
            将随机生成取货点(N_P0x)和卸货点(N_D0x)的任务
          </Text>
        </Form>
      </Modal>

      {/* Schedule Result Summary */}
      {scheduleResult && (
        <Card title="调度结果摘要" size="small" style={{ marginTop: 16 }}>
          <Row gutter={16}>
            <Col span={6}><Statistic title="总成本" value={scheduleResult.total_cost.toFixed(2)} /></Col>
            <Col span={6}><Statistic title="Makespan" value={`${scheduleResult.makespan.toFixed(1)}s`} /></Col>
            <Col span={6}><Statistic title="AGV利用率" value={`${(scheduleResult.metrics.agv_utilization * 100).toFixed(1)}%`} /></Col>
            <Col span={6}><Statistic title="算法耗时" value={`${scheduleResult.algorithm_runtime_ms.toFixed(0)}ms`} /></Col>
          </Row>
        </Card>
      )}
    </Spin>
  );
};

export default TaskManager;
