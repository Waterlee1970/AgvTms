import React, { useState } from 'react';
import { Routes, Route, useNavigate, useLocation } from 'react-router-dom';
import { Layout, Menu, Typography, Badge, Avatar, Space, Button } from 'antd';
import {
  DashboardOutlined,
  EnvironmentOutlined,
  UnorderedListOutlined,
  RobotOutlined,
  NodeIndexOutlined,
  SettingOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  ApiOutlined,
  ThunderboltOutlined,
  ExperimentOutlined,
} from '@ant-design/icons';
import Dashboard from './pages/Dashboard';
import MapConfig from './pages/MapConfig';
import TaskManager from './pages/TaskManager';
import AgvMonitor from './pages/AgvMonitor';
import ConveyorConfig from './pages/ConveyorConfig';
import AlgorithmConfig from './pages/AlgorithmConfig';
import AlgorithmBenchmark from './pages/AlgorithmBenchmark';

const { Header, Sider, Content } = Layout;
const { Text } = Typography;

const menuItems = [
  { key: '/', icon: <DashboardOutlined />, label: '系统总览' },
  { key: '/map', icon: <EnvironmentOutlined />, label: '地图配置' },
  { key: '/tasks', icon: <UnorderedListOutlined />, label: '任务管理' },
  { key: '/agv', icon: <RobotOutlined />, label: 'AGV监控' },
  { key: '/conveyor', icon: <NodeIndexOutlined />, label: '输送线配置' },
  { key: '/algorithm', icon: <SettingOutlined />, label: '算法配置' },
  { key: '/benchmark', icon: <ExperimentOutlined />, label: '算法评测' },
];

const App: React.FC = () => {
  const [collapsed, setCollapsed] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();

  const selectedKey = '/' + (location.pathname.split('/')[1] || '');

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider
        trigger={null}
        collapsible
        collapsed={collapsed}
        width={220}
        style={{
          background: '#0f1535',
          borderRight: '1px solid rgba(24, 144, 255, 0.1)',
        }}
      >
        <div className="logo-container">
          <ApiOutlined className="logo-icon" />
          {!collapsed && <span className="logo-text">AGV-TMS</span>}
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selectedKey === '/' ? '/' : selectedKey]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
          style={{
            background: 'transparent',
            borderRight: 0,
          }}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            background: '#0a0e27',
            padding: '0 24px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            borderBottom: '1px solid rgba(24, 144, 255, 0.1)',
            height: 56,
          }}
        >
          <Space>
            <Button
              type="text"
              icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setCollapsed(!collapsed)}
              style={{ color: '#e0e0e0', fontSize: 16 }}
            />
            <Text style={{ color: '#e0e0e0', fontSize: 16, fontWeight: 600 }}>
              柔性物流调度系统
            </Text>
            <Badge status="processing" text={<Text style={{ color: '#52c41a', fontSize: 12 }}>系统运行中</Text>} />
          </Space>
          <Space size="middle">
            <ThunderboltOutlined style={{ color: '#1890ff', fontSize: 18 }} />
            <Text style={{ color: '#888', fontSize: 12 }}>v1.0.0</Text>
            <Avatar size="small" style={{ background: '#1890ff' }}>A</Avatar>
          </Space>
        </Header>
        <Content
          style={{
            margin: 16,
            padding: 20,
            background: '#0a0e27',
            borderRadius: 8,
            overflow: 'auto',
            minHeight: 'calc(100vh - 88px)',
          }}
        >
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/map" element={<MapConfig />} />
            <Route path="/tasks" element={<TaskManager />} />
            <Route path="/agv" element={<AgvMonitor />} />
            <Route path="/conveyor" element={<ConveyorConfig />} />
            <Route path="/algorithm" element={<AlgorithmConfig />} />
            <Route path="/benchmark" element={<AlgorithmBenchmark />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  );
};

export default App;
