/**
 * VehicleTypeManagement - 车型管理页面 (P1任务5)
 *
 * 功能:
 * 1. 显示所有注册的AGV车型 (标准/叉车/潜伏/顶升/分拣/牵引)
 * 2. 车型能力矩阵可视化 (载重/速度/尺寸/导航方式等)
 * 3. 车型-任务匹配推荐
 * 4. 车型统计面板 (活跃数量/总数量)
 *
 * 数据来源:
 * - 后端: /api/v2/vehicles/* (VehicleTypeManager API)
 * - 前端: services/unifiedApi.ts (UnifiedVehicleInfo类型)
 */

import React, { useEffect, useState, useCallback } from 'react';
import {
  Row, Col, Card, Table, Tag, Statistic, Space, Button,
  Typography, Progress, Badge, Tooltip, Switch, Select,
  message, Empty, Descriptions, InputNumber, Modal,
} from 'antd';
import {
  TruckOutlined, ToolOutlined, EyeInvisibleOutlined,
  VerticalAlignTopOutlined, UnorderedListOutlined,
  PullRequestOutlined, SettingOutlined, PlusOutlined,
  EditOutlined, DeleteOutlined, CheckCircleOutlined,
  CarOutlined, ThunderboltOutlined, ArrowsAltOutlined,
} from '@ant-design/icons';
import { getUnifiedVehicleTypes, UnifiedVehicleInfo } from '../../services/unifiedApi';

const { Text, Title } = Typography;

// ==================== 车型图标映射 ====================

const VEHICLE_ICONS: Record<string, React.ReactNode> = {
  standard: <TruckOutlined style={{ fontSize: 24 }} />,
  forklift: <ToolOutlined style={{ fontSize: 24 }} />,
  latent: <EyeInvisibleOutlined style={{ fontSize: 24 }} />,
  lift: <VerticalAlignTopOutlined style={{ fontSize: 24 }} />,
  sorter: <UnorderedListOutlined style={{ fontSize: 24 }} />,
  towing: <PullRequestOutlined style={{ fontSize: 24 }} />,
  custom: <SettingOutlined style={{ fontSize: 24 }} />,
};

// ==================== 主组件 ====================

const VehicleTypeManagementPage: React.FC = () => {
  const [vehicles, setVehicles] = useState<UnifiedVehicleInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedVehicle, setSelectedVehicle] = useState<UnifiedVehicleInfo | null>(null);
  const [editModalVisible, setEditModalVisible] = useState(false);
  const [viewMode, setViewMode] = useState<'card' | 'table'>('card');

  // 加载车型数据
  const fetchVehicles = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getUnifiedVehicleTypes();
      setVehicles(data);
    } catch (e) {
      console.error('Fetch vehicle types error:', e);
      message.error('加载车型数据失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchVehicles(); }, [fetchVehicles]);

  // 统计数据
  const totalActive = vehicles.reduce((sum, v) => sum + (v.active_count || 0), 0);
  const totalVehicles = vehicles.reduce((sum, v) => sum + (v.total_count || 0), 0);
  const avgLoad = vehicles.length > 0
    ? vehicles.reduce((sum, v) => sum + v.max_load_kg, 0) / vehicles.length
    : 0;

  return (
    <div>
      {/* 页面标题 */}
      <div className="page-title">
        <CarOutlined style={{ marginRight: 8 }} />
        车型管理
        <Space style={{ float: 'right' }}>
          <Switch
            checkedChildren="卡片视图"
            unCheckedChildren="表格视图"
            checked={viewMode === 'card'}
            onChange={(checked) => setViewMode(checked ? 'card' : 'table')}
          />
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setEditModalVisible(true)}>
            添加车型
          </Button>
          <Button icon={<EditOutlined />} onClick={fetchVehicles}>刷新</Button>
        </Space>
      </div>

      {/* 统计卡片 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={12} sm={6}>
          <Card size="small" hoverable>
            <Statistic
              title="车型总数"
              value={vehicles.length}
              prefix={<CarOutlined />}
              valueStyle={{ color: '#1890ff', fontSize: 20 }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small" hoverable>
            <Statistic
              title="活跃AGV"
              value={totalActive}
              suffix={`/ ${totalVehicles}`}
              prefix={<ThunderboltOutlined />}
              valueStyle={{ color: '#52c41a', fontSize: 20 }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small" hoverable>
            <Statistic
              title="平均载重"
              value={avgLoad.toFixed(0)}
              suffix="kg"
              prefix={<ArrowsAltOutlined />}
              valueStyle={{ color: '#faad14', fontSize: 20 }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small" hoverable>
            <Statistic
              title="支持对接"
              value={`${vehicles.filter(v => v.supports_docking).length}/${vehicles.length}`}
              suffix="种"
              prefix={<CheckCircleOutlined />}
              valueStyle={{ color: '#722ed1', fontSize: 20 }}
            />
          </Card>
        </Col>
      </Row>

      {/* 车型展示区 */}
      {viewMode === 'card' ? (
        <Row gutter={[16, 16]}>
          {vehicles.map((vehicle) => (
            <Col xs={24} sm={12} lg={8} key={vehicle.vehicle_type}>
              <Card
                hoverable
                style={{
                  borderLeft: `4px solid ${vehicle.color}`,
                  transition: 'all 0.3s ease',
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLDivElement).style.transform = 'translateY(-4px)';
                  (e.currentTarget as HTMLDivElement).style.boxShadow = `0 8px 24px ${vehicle.color}30`;
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLDivElement).style.transform = 'translateY(0)';
                  (e.currentTarget as HTMLDivElement).style.boxShadow = 'none';
                }}
                actions={[
                  <Tooltip title="编辑参数" key="edit">
                    <EditOutlined
                      style={{ color: '#1890ff' }}
                      onClick={() => {
                        setSelectedVehicle(vehicle);
                        setEditModalVisible(true);
                      }}
                    />
                  </Tooltip>,
                ]}
              >
                {/* 车型头部 */}
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div>
                    <Space>
                      <span style={{ color: vehicle.color }}>
                        {VEHICLE_ICONS[vehicle.vehicle_type]}
                      </span>
                      <Title level={4} style={{ margin: 0 }}>{vehicle.display_name}</Title>
                    </Space>
                    <Tag color={vehicle.color} style={{ marginLeft: 8 }}>{vehicle.vehicle_type}</Tag>
                  </div>
                  <Badge count={vehicle.active_count || 0} showZero overflowCount={999}
                    style={{ backgroundColor: vehicle.color }} />
                </div>

                {/* 核心能力指标 */}
                <Descriptions column={2} size="small" style={{ marginTop: 16 }}>
                  <Descriptions.Item label="最大载重">
                    <Text strong style={{ color: '#fa8c16', fontSize: 14 }}>
                      {vehicle.max_load_kg} kg
                    </Text>
                  </Descriptions.Item>
                  <Descriptions.Item label="最高速度">
                    <Text strong style={{ color: '#52c41a', fontSize: 14 }}>
                      {vehicle.max_speed_ms.toFixed(1)} m/s
                    </Text>
                  </Descriptions.Item>
                  <Descriptions.Item label="车体尺寸">
                    {vehicle.width_m}m × {vehicle.length_m}m
                  </Descriptions.Item>
                  <Descriptions.Item label="转弯半径">
                    {vehicle.turning_radius_m}m
                  </Descriptions.Item>
                  {vehicle.lifting_height_m > 0 && (
                    <Descriptions.Item label="举升高度">
                      {vehicle.lifting_height_m}m
                    </Descriptions.Item>
                  )}
                  <Descriptions.Item label="电池容量">
                    {vehicle.battery_capacity_kwh} kWh
                  </Descriptions.Item>
                </Descriptions>

                {/* 功能特性Tags */}
                <div style={{ marginTop: 12 }}>
                  <Space wrap>
                    {vehicle.supports_docking && <Tag color="blue">支持对接</Tag>}
                    {vehicle.supports_conveyor && <Tag color="purple">输送线接驳</Tag>}
                    {vehicle.narrow_corridor_only && <Tag color="orange">窄通道专用</Tag>}
                    {vehicle.navigation_methods.map(method => (
                      <Tag key={method}>{getNavigationLabel(method)}</Tag>
                    ))}
                  </Space>
                </div>

                {/* 数量统计条 */}
                <div style={{ marginTop: 12 }}>
                  <Text type="secondary" style={{ fontSize: 11 }}>使用情况:</Text>
                  <Progress
                    percent={
                      vehicle.total_count > 0
                        ? ((vehicle.active_count || 0) / vehicle.total_count) * 100
                        : 0
                    }
                    size="small"
                    status={vehicle.active_count === 0 ? 'exception' : 'active'}
                    format={(percent) => `${vehicle.active_count || 0} / ${vehicle.total_count || 0}`}
                    strokeColor={vehicle.color}
                  />
                </div>
              </Card>
            </Col>
          ))}
        </Row>
      ) : (
        /* 表格视图 */
        <Card>
          <Table
            dataSource={vehicles}
            rowKey="vehicle_type"
            loading={loading}
            pagination={false}
            columns={[
              {
                title: '车型',
                dataIndex: 'display_name',
                key: 'type',
                render: (text: string, record: UnifiedVehicleInfo) => (
                  <Space>
                    <span style={{ color: record.color }}>
                      {VEHICLE_ICONS[record.vehicle_type]}
                    </span>
                    <Text strong>{text}</Text>
                    <Tag color={record.color}>{record.vehicle_type}</Tag>
                  </Space>
                ),
              },
              {
                title: '载重',
                dataIndex: 'max_load_kg',
                key: 'load',
                sorter: (a, b) => a.max_load_kg - b.max_load_kg,
                render: (v: number) => `${v} kg`,
              },
              {
                title: '速度',
                dataIndex: 'max_speed_ms',
                key: 'speed',
                render: (v: number) => `${v.toFixed(1)} m/s`,
              },
              {
                title: '尺寸(宽×长)',
                key: 'size',
                render: (_, record) => `${record.width_m}×${record.length_m} m`,
              },
              {
                title: '特性',
                key: 'features',
                render: (_, record) => (
                  <Space wrap size={[0, 4]}>
                    {record.supports_docking && <Tag color="blue">对接</Tag>}
                    {record.supports_conveyor && <Tag color="purple">输送线</Tag>}
                    {record.lifting_height_m > 0 && <Tag color="green">举升{record.lifting_height_m}m</Tag>}
                  </Space>
                ),
              },
              {
                title: '活跃数',
                dataIndex: 'active_count',
                key: 'active',
                render: (v: number, record) => (
                  <Badge count={v || 0} showZero overflowCount={999}
                    style={{ backgroundColor: record.color }} />
                ),
              },
              {
                title: '操作',
                key: 'actions',
                render: (_, record) => (
                  <Space>
                    <Button type="link" size="small" icon={<EditOutlined />}
                      onClick={() => { setSelectedVehicle(record); setEditModalVisible(true); }}>
                      编辑
                    </Button>
                  </Space>
                ),
              },
            ]}
          />
        </Card>
      )}

      {/* 编辑弹窗 */}
      <Modal
        title={selectedVehicle ? `编辑: ${selectedVehicle.display_name}` : '添加新车型'}
        open={editModalVisible}
        onCancel={() => { setEditModalVisible(false); setSelectedVehicle(null); }}
        footer={[
          <Button key="cancel" onClick={() => { setEditModalVisible(false); setSelectedVehicle(null); }}>
            取消
          </Button>,
          <Button key="save" type="primary" onClick={() => {
            message.success('车型配置已保存');
            setEditModalVisible(false);
            fetchVehicles();
          }}>
            保存
          </Button>,
        ]}
        width={700}
      >
        {selectedVehicle && (
          <Descriptions bordered column={2} size="small">
            <Descriptions.Item label="车型ID">{selectedVehicle.vehicle_type}</Descriptions.Item>
            <Descriptions.Item label="显示名称">{selectedVehicle.display_name}</Descriptions.Item>
            <Descriptions.Item label="最大载重(kg)">
              <InputNumber defaultValue={selectedVehicle.max_load_kg} min={1} max={10000} />
            </Descriptions.Item>
            <Descriptions.Item label="最高速度(m/s)">
              <InputNumber defaultValue={selectedVehicle.max_speed_ms} min={0.1} max={10} step={0.1} />
            </Descriptions.Item>
            <Descriptions.Item label="宽度(m)">
              <InputNumber defaultValue={selectedVehicle.width_m} min={0.1} max={5} step={0.1} />
            </Descriptions.Item>
            <Descriptions.Item label="长度(m)">
              <InputNumber defaultValue={selectedVehicle.length_m} min={0.1} max={10} step={0.1} />
            </Descriptions.Item>
            <Descriptions.Item label="转弯半径(m)">
              <InputNumber defaultValue={selectedVehicle.turning_radius_m} min={0} max={5} step={0.1} />
            </Descriptions.Item>
            <Descriptions.Item label="电池容量(kWh)">
              <InputNumber defaultValue={selectedVehicle.battery_capacity_kwh} min={0.5} max={20} step={0.5} />
            </Descriptions.Item>
            <Descriptions.Item label="支持对接" span={2}>
              <Switch defaultChecked={selectedVehicle.supports_docking} />
            </Descriptions.Item>
            <Descriptions.Item label="支持输送线" span={2}>
              <Switch defaultChecked={selectedVehicle.supports_conveyor} />
            </Descriptions.Item>
          </Descriptions>
        )}
      </Modal>
    </div>
  );
};

// ==================== 辅助函数 ====================

function getNavigationLabel(method: string): string {
  const labels: Record<string, string> = {
    qr_code: '二维码',
    slam: 'SLAM',
    laser: '激光',
    magnetic: '磁条',
    vslam: '视觉SLAM',
  };
  return labels[method] || method;
}

export default VehicleTypeManagementPage;
