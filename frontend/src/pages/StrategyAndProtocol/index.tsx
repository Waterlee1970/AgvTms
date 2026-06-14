/**
 * 策略与协议管理页面 — 体现 Phase 5+7 能力
 *
 * 功能:
 *  1. 可插拔策略切换 (CostFunction / Dispatcher / Router)
 *  2. 调度循环控制 (启动/停止/状态)
 *  3. 多协议适配器管理 (OPC UA / MQTT / Modbus / VDA5050)
 *  4. VDA5050 Order 构建器
 */

import React, { useState, useEffect, useCallback } from 'react';
import {
  Card, Row, Col, Select, Button, Tag, Space, message, Tabs, Table, Switch,
  Statistic, Descriptions, Input, InputNumber, Form, Divider, Badge, Typography,
} from 'antd';
import {
  SettingOutlined, ThunderboltOutlined, ApiOutlined, PlayCircleOutlined,
  PauseCircleOutlined, ReloadOutlined, BuildOutlined, CheckCircleOutlined,
} from '@ant-design/icons';
import {
  getStrategyInfo, setStrategy, StrategyInfo,
  getSchedulerLoopStatus, startSchedulerLoop, stopSchedulerLoop, SchedulerLoopStatus,
  getAdapters, startAdapter, stopAdapter, AdapterInfo,
  buildVda5050Order, getVda5050Schema,
} from '../../services/advancedApi';

const { Text } = Typography;

const StrategyAndProtocolPage: React.FC = () => {
  const [strategy, setStrategyState] = useState<StrategyInfo | null>(null);
  const [loopStatus, setLoopStatus] = useState<SchedulerLoopStatus | null>(null);
  const [adapterInfo, setAdapterInfo] = useState<AdapterInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('strategy');

  // VDA5050 Order 构建器状态
  const [orderForm] = Form.useForm();
  const [orderResult, setOrderResult] = useState<Record<string, unknown> | null>(null);
  const [vdaSchema, setVdaSchema] = useState<Record<string, unknown> | null>(null);

  const refreshAll = useCallback(async () => {
    setLoading(true);
    try {
      const [s, l, a] = await Promise.all([
        getStrategyInfo().catch(() => null),
        getSchedulerLoopStatus().catch(() => null),
        getAdapters().catch(() => null),
      ]);
      setStrategyState(s);
      setLoopStatus(l);
      setAdapterInfo(a);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refreshAll(); }, [refreshAll]);

  // 策略切换
  const handleStrategyChange = async (type: 'cost_function' | 'dispatcher' | 'router', value: string) => {
    try {
      await setStrategy({ [type]: value });
      message.success(`策略已切换: ${type} → ${value}`);
      refreshAll();
    } catch {
      message.error('策略切换失败');
    }
  };

  // 调度循环控制
  const handleLoopToggle = async () => {
    try {
      if (loopStatus?.is_running) {
        await stopSchedulerLoop();
        message.success('调度循环已停止');
      } else {
        await startSchedulerLoop();
        message.success('调度循环已启动');
      }
      refreshAll();
    } catch {
      message.error('操作失败');
    }
  };

  // 适配器启动/停止
  const handleAdapterToggle = async (name: string, isRunning: boolean) => {
    try {
      if (isRunning) {
        await stopAdapter(name);
        message.success(`适配器 ${name} 已停止`);
      } else {
        const config = name === 'opcua' ? { mode: 'simulation', num_sim_agvs: 10 } :
                       name === 'mqtt' ? { mode: 'simulation', num_sim_agvs: 8 } :
                       name === 'modbus' ? { mode: 'simulation', num_sim_agvs: 5 } :
                       name === 'vda5050' ? {} : {};
        await startAdapter(name, config);
        message.success(`适配器 ${name} 已启动`);
      }
      refreshAll();
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '操作失败');
    }
  };

  // VDA5050 Order 构建
  const handleBuildOrder = async () => {
    try {
      const values = await orderForm.validateFields();
      const result = await buildVda5050Order({
        order_id: values.order_id,
        path: values.path.split(',').map((s: string) => s.trim()).filter(Boolean),
        max_speed: values.max_speed || 1.5,
        serial_number: values.serial_number || '',
      });
      setOrderResult(result);
      message.success('VDA5050 Order 构建成功');
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '构建失败');
    }
  };

  const handleLoadSchema = async () => {
    try {
      const schema = await getVda5050Schema();
      setVdaSchema(schema);
    } catch {
      message.error('获取 Schema 失败');
    }
  };

  // 适配器协议标签颜色
  const protocolColor: Record<string, string> = {
    'opc-ua': 'blue', mqtt: 'green', 'modbus-tcp': 'orange', vda5050: 'purple',
  };

  // Tab: 策略管理
  const strategyTab = (
    <div>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={8}>
          <Card title={<><SettingOutlined /> 成本函数 (CostFunction)</>} size="small">
            <Space direction="vertical" style={{ width: '100%' }}>
              <Select
                style={{ width: '100%' }}
                value={strategy?.cost_functions.current}
                onChange={v => handleStrategyChange('cost_function', v)}
                options={strategy?.cost_functions.available.map(n => ({
                  label: n, value: n,
                }))}
              />
              <Text type="secondary" style={{ fontSize: 12 }}>
                5 种: distance / time / weighted / energy / priority
              </Text>
            </Space>
          </Card>
        </Col>
        <Col span={8}>
          <Card title={<><ThunderboltOutlined /> 分派策略 (Dispatcher)</>} size="small">
            <Space direction="vertical" style={{ width: '100%' }}>
              <Select
                style={{ width: '100%' }}
                value={strategy?.dispatchers.current}
                onChange={v => handleStrategyChange('dispatcher', v)}
                options={strategy?.dispatchers.available.map(n => ({
                  label: n, value: n,
                }))}
              />
              <Text type="secondary" style={{ fontSize: 12 }}>
                3 种: greedy (贪心) / hungarian (匈牙利) / mip (精确求解)
              </Text>
            </Space>
          </Card>
        </Col>
        <Col span={8}>
          <Card title={<><ApiOutlined /> 路由策略 (Router)</>} size="small">
            <Space direction="vertical" style={{ width: '100%' }}>
              <Select
                style={{ width: '100%' }}
                value={strategy?.routers.current}
                onChange={v => handleStrategyChange('router', v)}
                options={strategy?.routers.available.map(n => ({
                  label: n, value: n,
                }))}
              />
              <Text type="secondary" style={{ fontSize: 12 }}>
                3 种: astar (双向A*) / sipp (安全区间) / dstar (增量重规划)
              </Text>
            </Space>
          </Card>
        </Col>
      </Row>

      <Card title="调度循环控制 (SchedulerLoop)" size="small" style={{ marginBottom: 16 }}>
        <Row gutter={16} align="middle">
          <Col span={4}>
            <Statistic
              title="状态"
              valueRender={() => (
                <Badge
                  status={loopStatus?.is_running ? 'processing' : 'default'}
                  text={loopStatus?.is_running ? '运行中' : '已停止'}
                />
              )}
            />
          </Col>
          <Col span={4}>
            <Statistic title="总分派次数" value={loopStatus?.stats.total_dispatches || 0} />
          </Col>
          <Col span={4}>
            <Statistic title="订单处理数" value={loopStatus?.stats.total_orders_processed || 0} />
          </Col>
          <Col span={4}>
            <Statistic title="重调度次数" value={loopStatus?.stats.total_redispatches || 0} />
          </Col>
          <Col span={4}>
            <Statistic title="故障处理数" value={loopStatus?.stats.total_failures_handled || 0} />
          </Col>
          <Col span={4}>
            <Space>
              <Button
                type="primary"
                danger={loopStatus?.is_running}
                icon={loopStatus?.is_running ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
                onClick={handleLoopToggle}
                loading={loading}
              >
                {loopStatus?.is_running ? '停止' : '启动'}
              </Button>
              <Button icon={<ReloadOutlined />} onClick={refreshAll} />
            </Space>
          </Col>
        </Row>
      </Card>

      <Card title="策略说明" size="small">
        <Descriptions column={1} size="small">
          <Descriptions.Item label="可插拔架构">
            参考 openTCS 设计，支持运行时切换调度策略，无需重启服务
          </Descriptions.Item>
          <Descriptions.Item label="事件驱动">
            调度循环基于事件总线，新订单到达/AGV空闲/故障恢复自动触发重调度
          </Descriptions.Item>
          <Descriptions.Item label="A/B 测试">
            可同时运行两种策略对比效果，支持 RL vs MIP 自动判定胜者
          </Descriptions.Item>
        </Descriptions>
      </Card>
    </div>
  );

  // Tab: 适配器管理
  const adapterTab = (
    <div>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card size="small">
            <Statistic title="已注册适配器" value={adapterInfo?.registered.length || 0} suffix="种" />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="运行中适配器" value={Object.keys(adapterInfo?.running.adapters || {}).length} suffix="个" />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="管理车辆数" value={adapterInfo?.running.total_vehicles || 0} suffix="台" />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Button icon={<ReloadOutlined />} onClick={refreshAll} block>刷新</Button>
          </Card>
        </Col>
      </Row>

      <Card title="多协议适配器管理" size="small">
        <Table
          dataSource={adapterInfo?.registered.map(name => ({
            key: name,
            name,
            protocol: adapterInfo?.running.adapters[name]?.protocol || '-',
            connected: adapterInfo?.running.adapters[name]?.connected || false,
            vehicle_count: adapterInfo?.running.adapters[name]?.vehicle_count || 0,
          })) || []}
          columns={[
            { title: '适配器', dataIndex: 'name', render: (v: string) => <Tag color={protocolColor[v] || 'default'}>{v}</Tag> },
            { title: '协议', dataIndex: 'protocol' },
            {
              title: '状态', dataIndex: 'connected',
              render: (v: boolean) => <Badge status={v ? 'success' : 'default'} text={v ? '已连接' : '未连接'} />,
            },
            { title: '车辆数', dataIndex: 'vehicle_count' },
            {
              title: '操作', render: (_, record) => (
                <Switch
                  checked={record.connected}
                  onChange={() => handleAdapterToggle(record.name, record.connected)}
                  checkedChildren="运行"
                  unCheckedChildren="停止"
                />
              ),
            },
          ]}
          pagination={false}
          size="small"
        />
      </Card>

      <Card title="车辆路由表 (多品牌混合调度)" size="small" style={{ marginTop: 16 }}>
        <Table
          dataSource={Object.entries(adapterInfo?.running.vehicle_routes || {}).map(([vid, adapter], i) => ({
            key: i, vehicle_id: vid, adapter,
          }))}
          columns={[
            { title: '车辆 ID', dataIndex: 'vehicle_id' },
            { title: '所属适配器', dataIndex: 'adapter', render: (v: string) => <Tag color={protocolColor[v] || 'default'}>{v}</Tag> },
          ]}
          pagination={{ pageSize: 10 }}
          size="small"
        />
      </Card>
    </div>
  );

  // Tab: VDA5050 Order 构建器
  const vda5050Tab = (
    <div>
      <Row gutter={16}>
        <Col span={12}>
          <Card title={<><BuildOutlined /> VDA5050 Order 构建器</>} size="small">
            <Form form={orderForm} layout="vertical" initialValues={{
              order_id: 'ORD-001', path: 'N1, N2, N3, N4', max_speed: 1.5, serial_number: 'AGV001',
            }}>
              <Form.Item name="order_id" label="订单 ID" rules={[{ required: true }]}>
                <Input placeholder="ORD-001" />
              </Form.Item>
              <Form.Item name="path" label="路径节点 (逗号分隔)" rules={[{ required: true }]}>
                <Input placeholder="N1, N2, N3, N4" />
              </Form.Item>
              <Form.Item name="max_speed" label="最大速度 (m/s)">
                <InputNumber min={0.1} max={10} step={0.1} style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item name="serial_number" label="AGV 序列号">
                <Input placeholder="AGV001" />
              </Form.Item>
              <Button type="primary" icon={<BuildOutlined />} onClick={handleBuildOrder} block>
                构建 VDA5050 Order JSON
              </Button>
            </Form>
          </Card>
        </Col>
        <Col span={12}>
          <Card
            title="Order JSON 输出"
            size="small"
            extra={<Button size="small" icon={<CheckCircleOutlined />} onClick={handleLoadSchema}>查看 Schema</Button>}
          >
            {orderResult ? (
              <pre style={{
                background: '#0a0e27', color: '#52c41a', padding: 12, borderRadius: 8,
                maxHeight: 400, overflow: 'auto', fontSize: 12,
              }}>
                {JSON.stringify(orderResult, null, 2)}
              </pre>
            ) : (
              <Text type="secondary">构建 Order 后在此显示 JSON</Text>
            )}
          </Card>
          {vdaSchema && (
            <Card title="VDA5050 v2.0 Schema" size="small" style={{ marginTop: 16 }}>
              <pre style={{
                background: '#0a0e27', color: '#1890ff', padding: 12, borderRadius: 8,
                maxHeight: 300, overflow: 'auto', fontSize: 11,
              }}>
                {JSON.stringify(vdaSchema, null, 2)}
              </pre>
            </Card>
          )}
        </Col>
      </Row>
      <Divider />
      <Card title="VDA5050 完整实现说明" size="small">
        <Descriptions column={1} size="small">
          <Descriptions.Item label="版本">VDA5050 v2.0</Descriptions.Item>
          <Descriptions.Item label="消息类型">Order / State / InstantAction / Connection</Descriptions.Item>
          <Descriptions.Item label="Order 字段">headerId, version, manufacturer, serialNumber, timestamp, orderId, orderUpdateId, nodes[], edges[]</Descriptions.Item>
          <Descriptions.Item label="State 字段">14 个标准字段 (agvPosition, batteryState, actionStates, errors, warnings...)</Descriptions.Item>
          <Descriptions.Item label="动作类型">pickPosition, dropPosition, initPosition, charge, wait, customAction</Descriptions.Item>
          <Descriptions.Item label="即时动作">stop, cancelOrder, start</Descriptions.Item>
        </Descriptions>
      </Card>
    </div>
  );

  return (
    <div>
      <Tabs activeKey={activeTab} onChange={setActiveTab} items={[
        { key: 'strategy', label: <><SettingOutlined /> 策略管理</>, children: strategyTab },
        { key: 'adapter', label: <><ApiOutlined /> 协议适配器</>, children: adapterTab },
        { key: 'vda5050', label: <><BuildOutlined /> VDA5050</>, children: vda5050Tab },
      ]} />
    </div>
  );
};

export default StrategyAndProtocolPage;
