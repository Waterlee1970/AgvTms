/**
 * MapConfig - 地图配置页面
 *
 * Interactive node/edge editor for factory layout.
 */

import React, { useEffect, useState, useCallback } from 'react';
import {
  Row, Col, Card, Table, Button, Space, Modal, Form, Input,
  InputNumber, Select, Tag, message, Empty, Spin, Popconfirm, Typography,
} from 'antd';
import {
  PlusOutlined, DeleteOutlined, EditOutlined, SaveOutlined,
  EnvironmentOutlined, ReloadOutlined,
} from '@ant-design/icons';
import * as api from '../../services/api';
import { useStore } from '../../store/useStore';
import type { MapNode, MapEdge } from '../../services/api';

const { Text } = Typography;

const nodeTypeColors: Record<string, string> = {
  pickup: '#52c41a',
  dropoff: '#ff4d4f',
  charge: '#faad14',
  cross: '#1890ff',
  path: '#666',
  conveyor_in: '#722ed1',
  conveyor_out: '#eb2f96',
};

const nodeTypeLabels: Record<string, string> = {
  pickup: '取货点',
  dropoff: '卸货点',
  charge: '充电站',
  cross: '交叉口',
  path: '路径点',
  conveyor_in: '输送线入口',
  conveyor_out: '输送线出口',
};

const MapConfig: React.FC = () => {
  const { mapNodes, setMapNodes, mapEdges, setMapEdges, loading, setLoading } = useStore();
  const [nodeModalOpen, setNodeModalOpen] = useState(false);
  const [edgeModalOpen, setEdgeModalOpen] = useState(false);
  const [editingNode, setEditingNode] = useState<MapNode | null>(null);
  const [nodeForm] = Form.useForm();
  const [edgeForm] = Form.useForm();

  // Map canvas
  const canvasRef = React.useRef<HTMLCanvasElement>(null);

  const fetchMap = useCallback(async () => {
    setLoading('map', true);
    try {
      const graph = await api.getMapGraph();
      setMapNodes(graph.nodes);
      setMapEdges(graph.edges);
    } catch (e) {
      console.error('Fetch map error:', e);
    } finally {
      setLoading('map', false);
    }
  }, [setMapNodes, setMapEdges, setLoading]);

  useEffect(() => { fetchMap(); }, [fetchMap]);

  // Draw map
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !mapNodes.length) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const W = canvas.width;
    const H = canvas.height;
    ctx.clearRect(0, 0, W, H);

    // Grid
    ctx.strokeStyle = 'rgba(24,144,255,0.04)';
    ctx.lineWidth = 1;
    for (let i = 0; i < W; i += 40) {
      ctx.beginPath(); ctx.moveTo(i, 0); ctx.lineTo(i, H); ctx.stroke();
    }
    for (let i = 0; i < H; i += 40) {
      ctx.beginPath(); ctx.moveTo(0, i); ctx.lineTo(W, i); ctx.stroke();
    }

    const scaleX = W / 50;
    const scaleY = H / 55;

    // Draw edges
    mapEdges.forEach((edge) => {
      const fromNode = mapNodes.find(n => n.id === edge.from_node);
      const toNode = mapNodes.find(n => n.id === edge.to_node);
      if (!fromNode || !toNode) return;

      ctx.beginPath();
      ctx.moveTo((fromNode.x + 5) * scaleX, (fromNode.y + 5) * scaleY);
      ctx.lineTo((toNode.x + 5) * scaleX, (toNode.y + 5) * scaleY);
      ctx.strokeStyle = edge.is_conveyor ? 'rgba(114,46,209,0.4)' : 'rgba(24,144,255,0.2)';
      ctx.lineWidth = edge.is_conveyor ? 3 : 1;
      ctx.stroke();

      // Arrow for directed edges
      if (edge.direction !== 'bidirectional') {
        const mx = ((fromNode.x + toNode.x) / 2 + 5) * scaleX;
        const my = ((fromNode.y + toNode.y) / 2 + 5) * scaleY;
        ctx.fillStyle = 'rgba(24,144,255,0.5)';
        ctx.font = '14px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('→', mx, my);
      }
    });

    // Draw nodes
    mapNodes.forEach((node) => {
      const x = (node.x + 5) * scaleX;
      const y = (node.y + 5) * scaleY;
      const color = nodeTypeColors[node.type] || '#666';

      ctx.beginPath();
      ctx.arc(x, y, 6, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.strokeStyle = '#fff';
      ctx.lineWidth = 1.5;
      ctx.stroke();

      // Label
      ctx.fillStyle = '#e0e0e0';
      ctx.font = '9px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(node.id, x, y - 10);
    });
  }, [mapNodes, mapEdges]);

  // ---- Node CRUD ----

  const handleAddNode = () => {
    setEditingNode(null);
    nodeForm.resetFields();
    nodeForm.setFieldsValue({ type: 'path', capacity: 1, x: 0, y: 0 });
    setNodeModalOpen(true);
  };

  const handleEditNode = (node: MapNode) => {
    setEditingNode(node);
    nodeForm.setFieldsValue(node);
    setNodeModalOpen(true);
  };

  const handleSaveNode = async () => {
    try {
      const values = await nodeForm.validateFields();
      if (editingNode) {
        await api.updateMapNode(editingNode.id, values);
        message.success('节点已更新');
      } else {
        await api.addMapNode(values);
        message.success('节点已添加');
      }
      setNodeModalOpen(false);
      fetchMap();
    } catch (e) {
      if (e instanceof Error) message.error('操作失败');
    }
  };

  const handleDeleteNode = async (nodeId: string) => {
    try {
      await api.deleteMapNode(nodeId);
      message.success('节点已删除');
      fetchMap();
    } catch { message.error('删除失败'); }
  };

  // ---- Edge CRUD ----

  const handleAddEdge = () => {
    edgeForm.resetFields();
    edgeForm.setFieldsValue({ direction: 'bidirectional', distance: 10, speed_limit: 1.5, is_conveyor: false });
    setEdgeModalOpen(true);
  };

  const handleSaveEdge = async () => {
    try {
      const values = await edgeForm.validateFields();
      await api.addMapEdge(values);
      message.success('边已添加');
      setEdgeModalOpen(false);
      fetchMap();
    } catch { message.error('操作失败'); }
  };

  const handleDeleteEdge = async (edgeId: string) => {
    if (!edgeId) return;
    try {
      await api.deleteMapEdge(edgeId);
      message.success('边已删除');
      fetchMap();
    } catch { message.error('删除失败'); }
  };

  const nodeColumns = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 90 },
    { title: '名称', dataIndex: 'name', key: 'name' },
    { title: 'X', dataIndex: 'x', key: 'x', width: 70, render: (v: number) => v.toFixed(1) },
    { title: 'Y', dataIndex: 'y', key: 'y', width: 70, render: (v: number) => v.toFixed(1) },
    {
      title: '类型', dataIndex: 'type', key: 'type', width: 110,
      render: (t: string) => <Tag color={nodeTypeColors[t]}>{nodeTypeLabels[t] || t}</Tag>,
    },
    {
      title: '操作', key: 'action', width: 120,
      render: (_: unknown, record: MapNode) => (
        <Space size="small">
          <Button type="link" size="small" icon={<EditOutlined />}
            onClick={() => handleEditNode(record)} />
          <Popconfirm title="确定删除?" onConfirm={() => handleDeleteNode(record.id)}>
            <Button type="link" size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const edgeColumns = [
    { title: '起点', dataIndex: 'from_node', key: 'from_node', width: 100 },
    { title: '终点', dataIndex: 'to_node', key: 'to_node', width: 100 },
    { title: '距离(m)', dataIndex: 'distance', key: 'distance', width: 80,
      render: (v: number) => v.toFixed(1) },
    {
      title: '方向', dataIndex: 'direction', key: 'direction', width: 90,
      render: (d: string) => (
        <Tag color={d === 'bidirectional' ? 'green' : 'blue'}>
          {d === 'bidirectional' ? '双向' : d === 'forward' ? '正向' : '反向'}
        </Tag>
      ),
    },
    {
      title: '输送线', dataIndex: 'is_conveyor', key: 'is_conveyor', width: 80,
      render: (v: boolean) => v ? <Tag color="purple">是</Tag> : <Tag>否</Tag>,
    },
    {
      title: '操作', key: 'action', width: 80,
      render: (_: unknown, record: MapEdge) => (
        record.id ? (
          <Popconfirm title="确定删除?" onConfirm={() => handleDeleteEdge(record.id!)}>
            <Button type="link" size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        ) : null
      ),
    },
  ];

  return (
    <Spin spinning={loading['map']}>
      <div className="page-title">
        <EnvironmentOutlined style={{ marginRight: 8 }} />
        地图配置
        <Space style={{ float: 'right' }}>
          <Button icon={<ReloadOutlined />} onClick={fetchMap}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={handleAddNode}>添加节点</Button>
          <Button icon={<PlusOutlined />} onClick={handleAddEdge}>添加边</Button>
        </Space>
      </div>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={14}>
          <Card title="地图可视化" size="small">
            <div className="map-canvas-wrapper" style={{ height: 400 }}>
              <canvas
                ref={canvasRef}
                width={600}
                height={400}
                style={{ width: '100%', height: '100%' }}
              />
            </div>
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card title={`节点列表 (${mapNodes.length})`} size="small">
            <Table
              dataSource={mapNodes}
              columns={nodeColumns}
              rowKey="id"
              size="small"
              pagination={false}
              scroll={{ y: 300 }}
              locale={{ emptyText: <Empty description="暂无节点" /> }}
            />
          </Card>
        </Col>
      </Row>

      <Card title={`边列表 (${mapEdges.length})`} size="small" style={{ marginTop: 16 }}>
        <Table
          dataSource={mapEdges}
          columns={edgeColumns}
          rowKey={(r) => r.id || `${r.from_node}-${r.to_node}`}
          size="small"
          pagination={false}
          scroll={{ y: 300 }}
          locale={{ emptyText: <Empty description="暂无边" /> }}
        />
      </Card>

      {/* Node Modal */}
      <Modal
        title={editingNode ? '编辑节点' : '添加节点'}
        open={nodeModalOpen}
        onOk={handleSaveNode}
        onCancel={() => setNodeModalOpen(false)}
      >
        <Form form={nodeForm} layout="vertical">
          <Form.Item name="id" label="节点ID" rules={[{ required: true }]}>
            <Input disabled={!!editingNode} />
          </Form.Item>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="x" label="X坐标" rules={[{ required: true }]}>
                <InputNumber style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="y" label="Y坐标" rules={[{ required: true }]}>
                <InputNumber style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="type" label="类型" rules={[{ required: true }]}>
            <Select options={Object.entries(nodeTypeLabels).map(([k, v]) => ({ value: k, label: v }))} />
          </Form.Item>
          <Form.Item name="capacity" label="容量">
            <InputNumber min={1} max={10} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>

      {/* Edge Modal */}
      <Modal
        title="添加边"
        open={edgeModalOpen}
        onOk={handleSaveEdge}
        onCancel={() => setEdgeModalOpen(false)}
      >
        <Form form={edgeForm} layout="vertical">
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="from_node" label="起点节点ID" rules={[{ required: true }]}>
                <Select showSearch options={mapNodes.map(n => ({ value: n.id, label: `${n.id} (${n.name})` }))} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="to_node" label="终点节点ID" rules={[{ required: true }]}>
                <Select showSearch options={mapNodes.map(n => ({ value: n.id, label: `${n.id} (${n.name})` }))} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="distance" label="距离(m)" rules={[{ required: true }]}>
            <InputNumber min={0.1} step={0.5} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="direction" label="方向">
            <Select options={[
              { value: 'bidirectional', label: '双向' },
              { value: 'forward', label: '正向' },
              { value: 'backward', label: '反向' },
            ]} />
          </Form.Item>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="speed_limit" label="速度限制(m/s)">
                <InputNumber min={0.1} max={10} step={0.1} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="is_conveyor" label="是否为输送线" valuePropName="checked">
                <Select options={[{ value: true, label: '是' }, { value: false, label: '否' }]} />
              </Form.Item>
            </Col>
          </Row>
        </Form>
      </Modal>
    </Spin>
  );
};

export default MapConfig;
