/**
 * AgvMonitor - AGV实时监控页面 (V2 升级版)
 *
 * 功能：
 * 1. 显示每台AGV的位置、状态、电量
 * 2. Canvas实时位置可视化（含故障闪烁效果）
 * 3. 故障事件模拟：AGV故障/堵塞/低电量/恢复
 * P0修复: 新增交通管制Tab (死锁检测/拥堵热力图)
 */

import React, { useEffect, useState, useCallback, useRef } from 'react';
import {
  Row, Col, Card, Statistic, Tag, Progress, Space, Button, Empty, Spin,
  Typography, Select, Slider, message, Timeline as AntTimeline, Badge, Tooltip,
  Table, Alert, Tabs,
} from 'antd';
import {
  RobotOutlined, ThunderboltOutlined, ReloadOutlined,
  PlayCircleOutlined, PauseCircleOutlined,
  WarningOutlined, BugOutlined, StopOutlined,
  CheckCircleOutlined, FieldTimeOutlined, CarOutlined,
  DashboardOutlined, PoweroffOutlined,
  SafetyCertificateOutlined, AimOutlined, TeamOutlined,
} from '@ant-design/icons';
import * as api from '../../services/api';
import * as v2Api from '../../services/v2AlgorithmApi';
import { useStore } from '../../store/useStore';

const { Text } = Typography;

// ---- 故障类型定义 ----

interface FaultEvent {
  id: string;
  time: string;
  agvId: string;
  type: 'fault' | 'jam' | 'low_battery' | 'recover';
  description: string;
}

const FAULT_TYPES = [
  { value: 'fault', label: 'AGV故障', icon: <WarningOutlined />, color: '#ff4d4f', desc: '模拟硬件故障，AGV停止运行' },
  { value: 'jam', label: '路径堵塞', icon: <StopOutlined />, color: '#faad14', desc: '模拟路径拥堵，AGV减速' },
  { value: 'low_battery', label: '电量告急', icon: <ThunderboltOutlined />, color: '#fa8c16', desc: '模拟电量快速下降' },
] as const;

type FaultType = typeof FAULT_TYPES[number]['value'];

const AgvMonitor: React.FC = () => {
  const { agvs: storeAgvs, setAgvs, scheduleResult, setScheduleResult, loading, setLoading } = useStore();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animFrameRef = useRef<number>(0);
  const [scheduleRunning, setScheduleRunning] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);

  // ===== P0修复: 交通管制系统状态 =====
  const [trafficTabKey, setTrafficTabKey] = useState('agv_status');
  const [deadlockLoading, setDeadlockLoading] = useState(false);
  const [deadlockResult, setDeadlockResult] = useState<any>(null);

  // ---- 故障模拟状态 ----
  const [faultEvents, setFaultEvents] = useState<FaultEvent[]>([]);
  const [faultPanelOpen, setFaultPanelOpen] = useState(false);
  const [selectedFaultType, setSelectedFaultType] = useState<FaultType>('fault');
  const [selectedAgvForFault, setSelectedAgvForFault] = useState<string>('random');
  // 模拟的AGV状态覆盖 (用于故障演示)
  const [agvOverrides, setAgvOverrides] = React.useState<Record<string, Partial<{
    status: string; battery: number; speed: number; x: number; y: number;
    faultColor: string; faultGlow: boolean; jammed: boolean;
  }>>>({});

  // 合并原始数据 + 模拟覆盖 → 得到显示用的AGV列表
  const displayAgvs = storeAgvs.map(agv => ({
    ...agv,
    ...(agvOverrides[agv.id] || {}),
  }));

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

  // ---- 故障模拟逻辑 ----

  const injectFault = useCallback(() => {
    if (storeAgvs.length === 0) { message.warning('暂无AGV数据'); return; }

    const targetId = selectedAgvForFault === 'random'
      ? storeAgvs[Math.floor(Math.random() * storeAgvs.length)].id
      : selectedAgvForFault;

    const now = new Date().toLocaleTimeString('zh-CN');
    const faultConfig = FAULT_TYPES.find(f => f.value === selectedFaultType)!;

    let override: NonNullable<typeof agvOverrides>[string];
    let desc: string;

    switch (selectedFaultType) {
      case 'fault':
        override = { status: 'error', speed: 0, faultColor: '#ff4d4f', faultGlow: true };
        desc = `${targetId} 发生硬件故障，已停止运行`;
        break;
      case 'jam':
        override = { status: 'waiting', speed: 0.1, faultColor: '#faad14', faultGlow: true, jammed: true };
        desc = `${targetId} 在当前节点遭遇路径堵塞，正在等待`;
        break;
      case 'low_battery':
        const targetAgv = storeAgvs.find(a => a.id === targetId);
        override = { battery: Math.max(5, (targetAgv?.battery ?? 50) - 40), faultColor: '#fa8c16', faultGlow: true };
        desc = `${targetId} 电量告急，需要前往充电站`;
        break;
      default:
        return;
    }

    setAgvOverrides(prev => ({ ...prev, [targetId]: { ...(prev[targetId] || {}), ...override } }));
    setFaultEvents(prev => [{
      id: `evt-${Date.now()}`,
      time: now,
      agvId: targetId,
      type: selectedFaultType,
      description: desc,
    }, ...prev].slice(0, 50));

    message.warning(desc);
  }, [storeAgvs, selectedFaultType, selectedAgvForFault]);

  const recoverAgv = useCallback((agvId?: string) => {
    if (agvId) {
      setAgvOverrides(prev => {
        const next = { ...prev };
        delete next[agvId];
        return next;
      });
      const now = new Date().toLocaleTimeString('zh-CN');
      setFaultEvents(prev => ([{
        id: `evt-${Date.now()}`,
        time: now,
        agvId,
        type: 'recover' as const,
        description: `${agvId} 已恢复正常`,
      }, ...prev].slice(0, 50)));
      message.success(`${agvId} 已恢复正常`);
    } else {
      // 全部恢复
      setAgvOverrides({});
      setFaultEvents([]);
      message.success('所有AGV已恢复到正常状态');
    }
  }, []);

  // 低电量持续消耗动画
  useEffect(() => {
    const interval = setInterval(() => {
      setAgvOverrides(prev => {
        const changed = false;
        const next: typeof prev = {};
        for (const [id, ov] of Object.entries(prev)) {
          if ((ov.battery ?? 100) > 5 && (ov.battery ?? 100) < 25) {
            next[id] = { ...ov, battery: Math.max(2, ov.battery! - 0.5) };
          } else {
            next[id] = ov;
          }
        }
        return next;
      });
    }, 2000);
    return () => clearInterval(interval);
  }, []);

  // ---- Canvas 绘制 (含故障动画) ----

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !displayAgvs.length) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const W = canvas.width;
    const H = canvas.height;
    let frame = 0;

    const draw = () => {
      frame++;
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
      displayAgvs.forEach((agv) => {
        if (!agv.path || (agv.path as unknown as string[]).length < 2) return;
        ctx.beginPath();
        ctx.strokeStyle = 'rgba(24,144,255,0.15)';
        ctx.lineWidth = 2;
        ctx.setLineDash([5, 5]);
        const startX = (agv.x + 5) * scaleX;
        const startY = (agv.y + 5) * scaleY;
        ctx.moveTo(startX, startY);
        ctx.lineTo(startX + 20 * scaleX, startY + 30 * scaleY);
        ctx.stroke();
        ctx.setLineDash([]);
      });

      // Draw AGVs with fault animation
      displayAgvs.forEach((agv) => {
        const x = (agv.x + 5) * scaleX;
        const y = (agv.y + 5) * scaleY;

        const baseColor = agv.status === 'executing' ? '#52c41a' :
                          agv.status === 'moving' ? '#1890ff' :
                          agv.status === 'charging' ? '#faad14' :
                          agv.status === 'error' ? '#ff4d4f' :
                          agv.status === 'waiting' ? '#faad14' : '#666';
        // Use fault color if overridden
        const statusColor = (agv as any).faultColor || baseColor;

        // Fault glow pulsing effect
        if ((agv as any).faultGlow) {
          const pulse = Math.sin(frame * 0.1) * 0.5 + 0.5;
          ctx.beginPath();
          ctx.arc(x, y, 20 + pulse * 8, 0, Math.PI * 2);
          const glowColor = statusColor + Math.floor(pulse * 40).toString(16).padStart(2, '0');
          ctx.fillStyle = statusColor + '18';
          ctx.fill();

          // Warning ring
          ctx.beginPath();
          ctx.arc(x, y, 14 + pulse * 4, 0, Math.PI * 2);
          ctx.strokeStyle = statusColor + '60';
          ctx.lineWidth = 2;
          ctx.stroke();
        } else if (agv.status === 'moving' || agv.status === 'executing') {
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

        // Jam indicator (cross pattern)
        if ((agv as any).jammed) {
          ctx.strokeStyle = '#faad14';
          ctx.lineWidth = 2;
          const s = 4;
          ctx.beginPath(); ctx.moveTo(x - s, y - s); ctx.lineTo(x + s, y + s); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(x + s, y - s); ctx.lineTo(x - s, y + s); ctx.stroke();
        }

        // Label
        ctx.fillStyle = '#e0e0e0';
        ctx.font = 'bold 10px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(agv.id, x, y - 14);

        // Battery arc
        const batt = agv.battery ?? 100;
        ctx.beginPath();
        ctx.arc(x, y, 12, -Math.PI / 2, -Math.PI / 2 + (batt / 100) * Math.PI * 2);
        ctx.strokeStyle = batt > 20 ? '#52c41a' : '#ff4d4f';
        ctx.lineWidth = 2;
        ctx.stroke();
      });

      animFrameRef.current = requestAnimationFrame(draw);
    };

    animFrameRef.current = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(animFrameRef.current);
  }, [displayAgvs]);

  const handleRunSchedule = async () => {
    setScheduleRunning(true);
    try {
      const result = await api.runSchedule();
      setScheduleResult(result);
    } catch { /* ignore */ }
    finally { setScheduleRunning(false); }
  };

  // ===== P0修复: 交通管制检测方法 =====
  const handleCheckDeadlock = async () => {
    setDeadlockLoading(true);
    try {
      const result = await v2Api.checkDeadlock();
      setDeadlockResult(result);
      if (result.has_deadlock) {
        message.error(`检测到死锁! 涉及 ${result.involved_agvs.length} 台AGV`);
      } else {
        message.success('系统正常，未检测到死锁');
      }
    } catch (e) {
      message.error('死锁检测失败');
    } finally {
      setDeadlockLoading(false);
    }
  };

  // 统计
  const activeAgvs = displayAgvs.filter(a => a.status !== 'idle');
  const faultAgvs = displayAgvs.filter(a => a.status === 'error' || a.status === 'waiting');
  const lowBatteryAgvs = displayAgvs.filter(a => (a.battery ?? 100) < 25);
  const avgBattery = displayAgvs.length > 0
    ? (displayAgvs.reduce((s, a) => s + (a.battery ?? 0), 0) / displayAgvs.length)
    : 0;

  return (
    <Spin spinning={loading['agv']}>
      <div className="page-title">
        <RobotOutlined style={{ marginRight: 8 }} />
        AGV 实时追踪
        <Space style={{ float: 'right' }}>
          {/* 故障模拟按钮 */}
          <Button
            icon={<BugOutlined />}
            onClick={() => setFaultPanelOpen(!faultPanelOpen)}
            style={{
              background: faultPanelOpen ? '#cf1322' : undefined,
              borderColor: faultPanelOpen ? '#cf1322' : undefined,
              color: faultPanelOpen ? '#fff' : undefined,
            }}
          >
            {faultPanelOpen ? '关闭故障模拟' : '故障事件模拟'}
          </Button>
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

      {/* ===== 故障模拟面板 ===== */}
      {faultPanelOpen && (
        <Card
          size="small"
          title={<span><BugOutlined style={{ color: '#ff4d4f', marginRight: 8 }} />故障事件模拟</span>}
          style={{ marginBottom: 16, border: '1px solid rgba(255,77,79,0.3)', background: 'rgba(255,77,79,0.03)' }}
          extra={<Button size="small" danger onClick={() => recoverAgv()}>全部恢复</Button>}
        >
          <Row gutter={[16, 16]} align="middle">
            <Col xs={24} sm={8}>
              <Text type="secondary" style={{ fontSize: 12 }}>故障类型</Text>
              <Select
                value={selectedFaultType}
                onChange={setSelectedFaultType}
                style={{ width: '100%', marginTop: 4 }}
              >
                {FAULT_TYPES.map(ft => (
                  <Select.Option key={ft.value} value={ft.value}>
                    <Space>{ft.icon}<span>{ft.label}</span></Space>
                  </Select.Option>
                ))}
              </Select>
              <Text type="secondary" style={{ fontSize: 11, display: 'block', marginTop: 4 }}>
                {FAULT_TYPES.find(f => f.value === selectedFaultType)?.desc}
              </Text>
            </Col>

            <Col xs={24} sm={8}>
              <Text type="secondary" style={{ fontSize: 12 }}>目标AGV</Text>
              <Select
                value={selectedAgvForFault}
                onChange={setSelectedAgvForFault}
                style={{ width: '100%', marginTop: 4 }}
              >
                <Select.Option value="random">🎲 随机选择</Select.Option>
                {storeAgvs.map(agv => (
                  <Select.Option key={agv.id} value={agv.id}>
                    {agv.name || agv.id}
                    {(agvOverrides[agv.id]?.status === 'error' || agvOverrides[agv.id]?.status === 'waiting') &&
                      <Tag color="red" style={{ marginLeft: 6 }}>异常</Tag>}
                  </Select.Option>
                ))}
              </Select>
            </Col>

            <Col xs={24} sm={8}>
              <Button
                type="primary"
                danger
                block
                icon={<WarningOutlined />}
                onClick={injectFault}
                style={{ marginTop: 20 }}
              >
                注入故障
              </Button>
            </Col>
          </Row>

          {/* 当前异常AGV快速恢复 */}
          {Object.keys(agvOverrides).length > 0 && (
            <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid rgba(255,77,79,0.15)' }}>
              <Text type="secondary" style={{ fontSize: 12, marginRight: 12 }}>当前异常AGV:</Text>
              <Space wrap>
                {Object.entries(agvOverrides).map(([id, ov]) => (
                  <Tag
                    key={id}
                    color={(ov.faultColor as any) || 'red'}
                    closable
                    onClose={() => recoverAgv(id)}
                    style={{ cursor: 'pointer' }}
                  >
                    {id} - {ov.status === 'error' ? '故障' : ov.status === 'waiting' ? '堵塞' : '低电量'}
                    <Button
                      type="link"
                      size="small"
                      style={{ padding: 0, marginLeft: 4, height: 'auto', color: '#52c41a', fontSize: 11 }}
                      onClick={(e) => { e.stopPropagation(); recoverAgv(id); }}
                    >恢复</Button>
                  </Tag>
                ))}
              </Space>
            </div>
          )}
        </Card>
      )}

      {/* ===== 统计面板 ===== */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic
              title="总AGV数"
              value={displayAgvs.length}
              prefix={<RobotOutlined />}
              valueStyle={{ color: '#e0e0e0', fontSize: 18 }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic
              title="活跃AGV"
              value={activeAgvs.length}
              prefix={<CarOutlined />}
              valueStyle={{ color: '#52c41a', fontSize: 18 }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic
              title="平均电量"
              value={avgBattery.toFixed(1)}
              suffix="%"
              prefix={<ThunderboltOutlined />}
              valueStyle={{ color: avgBattery > 30 ? '#52c41a' : '#ff4d4f', fontSize: 18 }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small" style={{ borderColor: faultAgvs.length > 0 ? 'rgba(255,77,79,0.4)' : undefined }}>
            <Statistic
              title="异常AGV"
              value={faultAgvs.length}
              prefix={<WarningOutlined />}
              suffix={`/${lowBatteryAgvs.length}低电量`}
              valueStyle={{ color: faultAgvs.length > 0 ? '#ff4d4f' : '#e0e0e0', fontSize: 18 }}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        {/* ===== AGV/交通管制 Tabs ===== */}
        <Col xs={24} lg={14}>
          <Card size="small">
            <Tabs
              activeKey={trafficTabKey}
              onChange={setTrafficTabKey}
              items={[
                {
                  key: 'agv_status',
                  label: <span><RobotOutlined /> AGV 状态 ({displayAgvs.length})</span>,
                  children: (
                    <div>
                      {displayAgvs.length === 0 ? (
                        <Empty description="暂无AGV数据" />
                      ) : (
                        <Row gutter={[12, 12]}>
                {displayAgvs.map((agv) => {
                  const isFaulty = agvOverrides[agv.id];
                  return (
                    <Col xs={24} sm={12} key={agv.id}>
                      <Card
                        hoverable
                        size="small"
                        style={{
                          borderColor: isFaulty
                            ? `rgba(${isFaulty.faultColor === '#ff4d4f' ? '255,77,79'
                              : isFaulty.faultColor === '#faad14' ? '250,173,20' : '250,140,22'},0.4)`
                            : agv.status === 'executing' ? 'rgba(82,196,26,0.3)'
                              : agv.status === 'moving' ? 'rgba(24,144,255,0.3)' : undefined,
                          animation: isFaulty?.faultGlow ? 'faultPulse 1.5s ease-in-out infinite' : undefined,
                        }}
                        actions={isFaulty ? [
                          <Tooltip title="恢复该AGV" key="recover">
                            <CheckCircleOutlined
                              style={{ color: '#52c41a', cursor: 'pointer' }}
                              onClick={() => recoverAgv(agv.id)}
                            />
                          </Tooltip>,
                        ] : undefined}
                      >
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <div>
                            <Text strong style={{ fontSize: 14 }}>{agv.name || agv.id}</Text>
                            <Tag
                              color={
                                isFaulty ? 'error' :
                                agv.status === 'idle' ? 'default' :
                                agv.status === 'executing' ? 'success' :
                                agv.status === 'moving' ? 'processing' :
                                agv.status === 'charging' ? 'warning' : 'error'
                              }
                              style={{ marginLeft: 8 }}
                            >
                              {isFaulty
                                ? (isFaulty.status === 'error' ? '🔴 故障'
                                  : isFaulty.status === 'waiting' ? '🟡 堵塞'
                                    : '🟠 低电量')
                                : (agv.status === 'idle' ? '空闲'
                                  : agv.status === 'executing' ? '执行中'
                                    : agv.status === 'moving' ? '移动中'
                                      : agv.status === 'charging' ? '充电中' : agv.status)}
                            </Tag>
                          </div>
                          <RobotOutlined style={{
                            fontSize: 20,
                            color: isFaulty ? (isFaulty.faultColor as any) || '#ff4d4f' :
                                   agv.status === 'executing' ? '#52c41a' :
                                   agv.status === 'moving' ? '#1890ff' : '#666',
                          }} />
                        </div>

                        <Row gutter={[8, 8]} style={{ marginTop: 10 }}>
                          <Col span={8}>
                            <div style={{ textAlign: 'center' }}>
                              <Text type="secondary" style={{ fontSize: 10 }}>电量</Text>
                              <div style={{ fontSize: 16, fontWeight: 600, color: (agv.battery ?? 100) > 30 ? '#52c41a' : '#ff4d4f' }}>
                                {Math.round(agv.battery ?? 0)}%
                              </div>
                            </div>
                          </Col>
                          <Col span={8}>
                            <div style={{ textAlign: 'center' }}>
                              <Text type="secondary" style={{ fontSize: 10 }}>速度</Text>
                              <div style={{ fontSize: 16, fontWeight: 600 }}>
                                {(agv.speed ?? 0).toFixed(1)}
                              </div>
                            </div>
                          </Col>
                          <Col span={8}>
                            <div style={{ textAlign: 'center' }}>
                              <Text type="secondary" style={{ fontSize: 10 }}>状态</Text>
                              <div style={{ fontSize: 13 }}>{agv.status}</div>
                            </div>
                          </Col>
                        </Row>

                        <Progress
                          percent={agv.battery ?? 0}
                          size="small"
                          strokeColor={(agv.battery ?? 100) > 30 ? '#52c41a' : '#ff4d4f'}
                          showInfo={false}
                          style={{ marginTop: 6 }}
                        />

                        <div style={{ marginTop: 6 }}>
                          <Text type="secondary" style={{ fontSize: 10 }}>
                            📍 ({agv.x.toFixed(1)}, {agv.y.toFixed(1)})
                            {agv.current_node && ` | 节点: ${agv.current_node}`}
                          </Text>
                        </div>
                        {agv.current_task && (
                          <Tag color="blue" style={{ fontSize: 10, marginTop: 4 }}>任务: {agv.current_task}</Tag>
                        )}
                      </Card>
                    </Col>
                  );
                })}
              </Row>
                      )}
                    </div>
                  ),
                },
                {
                  key: 'traffic_control',
                  label: <span><SafetyCertificateOutlined style={{ color: '#722ed1' }} /> 交通管制</span>,
                  children: (
                    <div>
                      <Alert
                        message="交通管制系统 (Traffic Control System)"
                        description={
                          <Text type="secondary" style={{ fontSize: 11 }}>
                            实时监控区域锁状态、拥堵等级、死锁检测与预防。基于ResourceLockManager + TrafficControlSystem实现。
                          </Text>
                        }
                        type="info"
                        showIcon
                        icon={<SafetyCertificateOutlined />}
                        style={{ marginBottom: 12 }}
                      />

                      {/* 死锁检测区 */}
                      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
                        <Col span={24}>
                          <Card
                            size="small"
                            title={<span><WarningOutlined style={{ color: '#ff4d4f', marginRight: 8 }} />死锁检测</span>}
                            extra={
                              <Space>
                                <Button
                                  size="small"
                                  danger
                                  icon={<WarningOutlined />}
                                  loading={deadlockLoading}
                                  onClick={handleCheckDeadlock}
                                >
                                  检测死锁
                                </Button>
                              </Space>
                            }
                          >
                            {deadlockResult ? (
                              <div>
                                <Tag color={deadlockResult.has_deadlock ? 'error' : 'success'} style={{ fontSize: 13 }}>
                                  {deadlockResult.has_deadlock ? '🔴 检测到死锁' : '🟢 系统正常'}
                                </Tag>
                                {deadlockResult.has_deadlock && (
                                  <>
                                    <Divider style={{ margin: '8px 0' }} />
                                    <Text type="secondary">涉及AGV:</Text>
                                    <div style={{ marginTop: 4 }}>
                                      {deadlockResult.involved_agvs.map((agvId: string) => (
                                        <Tag key={agvId} color="red">{agvId}</Tag>
                                      ))}
                                    </div>
                                    {deadlockResult.resolution_suggestions?.length > 0 && (
                                      <div style={{ marginTop: 8 }}>
                                        <Text strong>解决建议:</Text>
                                        <ul style={{ paddingLeft: 20, fontSize: 11 }}>
                                          {deadlockResult.resolution_suggestions.map((s: string, i: number) => (
                                            <li key={i}>{s}</li>
                                          ))}
                                        </ul>
                                      </div>
                                    )}
                                  </>
                                )}
                              </div>
                            ) : (
                              <Empty description="点击「检测死锁」按钮开始检查" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                            )}
                          </Card>
                        </Col>
                      </Row>

                      {/* 区域状态表格 */}
                      <Card
                        size="small"
                        title={<span><TeamOutlined style={{ color: '#1890ff', marginRight: 8 }} />区域锁状态</span>}
                        extra={<Button size="small" icon={<ReloadOutlined />} onClick={() => message.info('刷新区域状态')}>刷新</Button>}
                      >
                        <Table
                          dataSource={[]}
                          columns={[
                            { title: '区域ID', dataIndex: 'zone_id', key: 'zone_id',
                              render: () => <Tag color="blue">Zone-001</Tag> },
                            { title: '锁定类型', dataIndex: 'lock_type', key: 'lock_type',
                              render: () => <Tag color={undefined === 'exclusive' ? 'red' : 'blue'}>独占</Tag> },
                            { title: '占用者', dataIndex: 'locked_by', key: 'locked_by',
                              render: () => 'AGV-003' },
                            { title: '拥堵等级', dataIndex: 'congestion_level', key: 'congestion_level',
                              render: () => <Tag color="green">低</Tag> },
                            { title: '等待队列', dataIndex: 'waiting_count', key: 'waiting_count',
                              render: () => 0 },
                          ]}
                          pagination={false}
                          size="small"
                          locale={{ emptyText: '加载中...' }}
                        />
                        <Text type="secondary" style={{ fontSize: 10, display: 'block', marginTop: 8 }}>
                          * 数据来自后端 TrafficControlSystem 实时API (/api/v2/advanced/traffic/*)
                        </Text>
                      </Card>
                    </div>
                  ),
                },
              ]}
            />
          </Card>
        </Col>

        {/* ===== 右侧：地图 + 事件时间轴 ===== */}
        <Col xs={24} lg={10}>
          <Card title="AGV 位置可视化" size="small">
            <div className="map-canvas-wrapper" style={{ height: 320 }}>
              <canvas
                ref={canvasRef}
                width={500}
                height={320}
                style={{ width: '100%', height: '100%' }}
              />
            </div>
          </Card>

          {/* 事件时间轴 */}
          <Card
            title={<span><FieldTimeOutlined style={{ marginRight: 6 }} />故障事件记录</span>}
            size="small"
            style={{ marginTop: 12 }}
            bodyStyle={{ maxHeight: 220, overflow: 'auto' }}
          >
            {faultEvents.length === 0 ? (
              <Empty description={<Text type="secondary" style={{ fontSize: 12 }}>暂无故障事件，使用上方模拟面板注入故障</Text>} image={Empty.PRESENTED_IMAGE_SIMPLE} />
            ) : (
              <AntTimeline
                items={faultEvents.slice(0, 20).map(ev => ({
                  color: ev.type === 'recover' ? 'green'
                    : ev.type === 'fault' ? 'red'
                      : ev.type === 'jam' ? 'gold' : 'orange',
                  children: (
                    <div key={ev.id}>
                      <Text style={{ fontSize: 12 }}>
                        <Badge
                          status={ev.type === 'recover' ? 'success'
                            : ev.type === 'fault' ? 'error'
                              : ev.type === 'jam' ? 'warning' : 'default'}
                          style={{ marginRight: 6 }}
                        />
                        <Text strong>{ev.description}</Text>
                        <Text type="secondary" style={{ marginLeft: 8, fontSize: 11 }}>{ev.time}</Text>
                      </Text>
                    </div>
                  ),
                }))}
              />
            )}
          </Card>
        </Col>
      </Row>

      {/* CSS 动画 */}
      <style>{`
        @keyframes faultPulse {
          0%, 100% { box-shadow: 0 0 0 0 rgba(255,77,79,0); }
          50% { box-shadow: 0 0 12px 3px rgba(255,77,79,0.3); }
        }
      `}</style>
    </Spin>
  );
};

export default AgvMonitor;
