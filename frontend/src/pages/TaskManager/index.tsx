/**
 * TaskManager - 任务管理页面
 */

import React, { useEffect, useState, useCallback } from 'react';
import {
  Card, Table, Button, Space, Tag, Modal, Form, Input, InputNumber,
  Select, DatePicker, message, Empty, Spin, Typography, Row, Col, Statistic,
} from 'antd';
import {
  PlusOutlined, ReloadOutlined, UnorderedListOutlined,
  PlayCircleOutlined, FilterOutlined,
} from '@ant-design/icons';
import * as api from '../../services/api';
import { useStore } from '../../store/useStore';
import type { AgvTask } from '../../services/api';
import dayjs from 'dayjs';

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
  const { tasks, setTasks, scheduleResult, setScheduleResult, loading, setLoading } = useStore();
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [batchModalOpen, setBatchModalOpen] = useState(false);
  const [form] = Form.useForm();
  const [scheduleRunning, setScheduleRunning] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string | null>(null);

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
        <Col xs={8}>
          <Card size="small">
            <Statistic title="总任务" value={tasks.length} valueStyle={{ color: '#e0e0e0' }} />
          </Card>
        </Col>
        <Col xs={8}>
          <Card size="small">
            <Statistic title="待处理" value={pendingCount} valueStyle={{ color: '#faad14' }} />
          </Card>
        </Col>
        <Col xs={8}>
          <Card size="small">
            <Statistic title="已完成" value={completedCount} valueStyle={{ color: '#52c41a' }} />
          </Card>
        </Col>
      </Row>

      <Card
        title={`任务列表 (${filteredTasks.length})`}
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
        <Table
          dataSource={filteredTasks}
          columns={columns}
          rowKey="id"
          size="small"
          pagination={{ pageSize: 15 }}
          scroll={{ y: 400 }}
          locale={{ emptyText: <Empty description="暂无任务" /> }}
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
