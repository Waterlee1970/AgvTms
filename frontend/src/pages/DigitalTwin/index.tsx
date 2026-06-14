/**
 * 3D 数字孪生页面 — 实时动态渲染版 (Phase 8)
 *
 * 功能:
 *  1. Canvas 2.5D 实时渲染: AGV 运动 / 轨迹绘制 / 热力图
 *  2. requestAnimationFrame 动画循环: AGV 沿路径平滑移动
 *  3. 轨迹回放: 播放/暂停/速度控制/进度条拖动
 *  4. 模拟调度模式: 无真实调度数据时可启动模拟演示
 *  5. AGV 3D 模型列表 / JSON 数据查看
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Card, Row, Col, Button, Space, Statistic, Tag, Table, Switch, Slider,
  message, Tabs, Typography, Empty, Spin, Select, InputNumber, Tooltip,
} from 'antd';
import {
  ReloadOutlined, PlayCircleOutlined, PauseCircleOutlined,
  BoxPlotOutlined, EnvironmentOutlined, ThunderboltOutlined,
  CameraOutlined, HeatMapOutlined, FastForwardOutlined, RocketOutlined,
} from '@ant-design/icons';
import { get3DScene, SceneModel, Agv3DModel, MapElement3D } from '../../services/advancedApi';

const { Text } = Typography;

// ==================== 动画引擎 ====================

interface SimAgvState {
  id: string;
  x: number; y: number;
  targetX: number; targetY: number;
  rotation: number;
  speed: number;
  state: string;
  color: string;
  batteryLevel: number;
  pathIndex: number;
  path: Array<{ x: number; y: number }>;
  progress: number; // 0~1 当前段进度
}

/** 从 SceneModel 初始化模拟 AGV 状态 */
function initSimAgvs(scene: SceneModel | null): SimAgvState[] {
  if (!scene || scene.agvs.length === 0) return [];
  return scene.agvs.map(agv => ({
    id: agv.agvId,
    x: agv.position.x, y: agv.position.y,
    targetX: agv.position.x, targetY: agv.position.y,
    rotation: agv.rotation,
    speed: agv.speed || 1.2,
    state: agv.state || 'idle',
    color: agv.color,
    batteryLevel: agv.batteryLevel,
    pathIndex: 0,
    path: generateRandomPath(scene.mapElements, agv.position.x, agv.position.y),
    progress: 0,
  }));
}

/** 为 AGV 生成一条随机巡逻路径（经过地图节点） */
function generateRandomPath(
  nodes: MapElement3D[], startX: number, startY: number
): Array<{ x: number; y: number }> {
  if (!nodes || nodes.length < 2) return [
    { x: startX, y: startY },
    { x: startX + 30, y: startY + 20 },
  ];
  const shuffled = [...nodes].sort(() => Math.random() - 0.5).slice(0, Math.min(6, nodes.length));
  const path: Array<{ x: number; y: number }> = [{ x: startX, y: startY }];
  shuffled.forEach(n => path.push({ x: n.position.x, y: n.position.y }));
  path.push({ x: startX, y: startY }); // 回到起点
  return path;
}

/** 单步更新 AGV 位置（线性插值） */
function stepAgv(agv: SimAgvState, dt: number): SimAgvState {
  if (agv.path.length < 2) return { ...agv, state: 'idle' };

  const currentTarget = agv.path[agv.pathIndex + 1];
  if (!currentTarget) {
    // 到达终点，重新开始巡逻
    const newPath = [...agv.path]; // 循环使用同一路径
    return {
      ...agv,
      pathIndex: 0,
      progress: 0,
      x: newPath[0].x, y: newPath[0].y,
      targetX: newPath[1].x, targetY: newPath[1].y,
      state: 'moving',
    };
  }

  const dx = currentTarget.x - agv.x;
  const dy = currentTarget.y - agv.y;
  const dist = Math.sqrt(dx * dx + dy * dy);

  if (dist < 0.5) {
    // 到达当前目标点，切换到下一段
    const nextIdx = (agv.pathIndex + 1) % (agv.path.length - 1);
    const nextTarget = agv.path[nextIdx + 1] || agv.path[0];
    return {
      ...agv,
      pathIndex: nextIdx,
      progress: 0,
      x: currentTarget.x, y: currentTarget.y,
      targetX: nextTarget.x, targetY: nextTarget.y,
      rotation: Math.atan2(nextTarget.y - currentTarget.y, nextTarget.x - currentTarget.x) * 180 / Math.PI,
    };
  }

  // 线性移动
  const moveDist = agv.speed * dt;
  const ratio = Math.min(moveDist / dist, 1.0);
  return {
    ...agv,
    x: agv.x + dx * ratio,
    y: agv.y + dy * ratio,
    progress: 1 - (Math.sqrt((currentTarget.x - (agv.x + dx * ratio))**2 + (currentTarget.y - (agv.y + dy *ratio))**2) / dist),
    rotation: Math.atan2(dy, dx) * 180 / Math.PI,
    state: 'moving',
    batteryLevel: Math.max(10, agv.batteryLevel - dt * 0.001), // 缓慢消耗电量
  };
}


// ==================== 页面组件 ====================

const DigitalTwinPage: React.FC = () => {
  const [scene, setScene] = useState<SceneModel | null>(null);
  const [loading, setLoading] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [showHeatmap, setShowHeatmap] = useState(false);
  const [activeTab, setActiveTab] = useState('canvas');
  const [animSpeed, setAnimSpeed] = useState(1.0);
  const [simMode, setSimMode] = useState(true); // 模拟模式: 无调度数据也显示动画

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animFrameRef = useRef<number>(0);
  const lastTimeRef = useRef<number>(0);
  const simAgvsRef = useRef<SimAgvState[]>([]);
  const trailPointsRef = useRef<Map<string, Array<{x:number;y:number;t:number}> >>(new Map());

  // 加载场景
  const refreshScene = useCallback(async () => {
    setLoading(true);
    try {
      const data = await get3DScene();
      setScene(data);
      simAgvsRef.current = initSimAgvs(data); // 重新初始化模拟状态
      message.success(`场景已加载 (${data.agvs?.length||0} AGV, ${data.mapElements?.length||0} 节点)`);
    } catch (err: any) {
      message.error('加载场景失败: ' + (err?.message || '未知错误'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refreshScene(); }, [refreshScene]);

  // ========== 核心：requestAnimationFrame 动画循环 ==========
  useEffect(() => {
    if (!playing) {
      if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current);
      lastTimeRef.current = 0;
      return;
    }

    let lastTimestamp = performance.now();

    const animate = (timestamp: number) => {
      const dtSec = ((timestamp - lastTimestamp) / 1000) * animSpeed;
      lastTimestamp = timestamp;

      // 更新所有 AGV 模拟状态
      simAgvsRef.current = simAgvsRef.current.map(agv =>
        stepAgv(agv, dtSec)
      );

      // 记录轨迹尾迹
      simAgvsRef.current.forEach(agv => {
        if (!trailPointsRef.current.has(agv.id)) trailPointsRef.current.set(agv.id, []);
        const trail = trailPointsRef.current.get(agv.id)!;
        trail.push({ x: agv.x, y: agv.y, t: timestamp });
        if (trail.length > 150) trail.shift(); // 只保留最近 150 个点
      });

      // 重绘 Canvas
      drawFrame();
      animFrameRef.current = requestAnimationFrame(animate);
    };

    animFrameRef.current = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(animFrameRef.current);
  }, [playing, scene, showHeatmap, animSpeed]);

  /** 绘制单帧 */
  const drawFrame = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const w = canvas.width;
    const h = canvas.height;

    // 背景 + 网格
    ctx.fillStyle = '#0a0e27';
    ctx.fillRect(0, 0, w, h);

    ctx.strokeStyle = 'rgba(24,144,255,0.06)';
    ctx.lineWidth = 1;
    for (let i = 0; i <= w; i += 40) { ctx.beginPath(); ctx.moveTo(i,0); ctx.lineTo(i,h); ctx.stroke(); }
    for (let i = 0; i <= h; i += 40) { ctx.beginPath(); ctx.moveTo(0,i); ctx.lineTo(w,i); ctx.stroke(); }

    if (!scene && simAgvsRef.current.length === 0) {
      // 空状态提示
      ctx.fillStyle = '#555'; ctx.font = '14px sans-serif'; ctx.textAlign = 'center';
      ctx.fillText('点击 "刷新" 或 "启动模拟" 加载场景', w/2, h/2);
      ctx.textAlign = 'left';
      return;
    }

    // 计算坐标变换
    const allPos = [
      ...(scene?.mapElements.map(e => e.position) || []),
      ...(scene?.agvs.map(a => a.position) || []),
      ...simAgvsRef.current.map(a => ({ x: a.x, y: a.y })),
    ];
    const hasData = allPos.length > 0;
    const minX = hasData ? Math.min(...allPos.map(p=>p.x), 0) - 8 : 0;
    const maxX = hasData ? Math.max(...allPos.map(p=>p.x), 80) + 8 : 100;
    const minY = hasData ? Math.min(...allPos.map(p=>p.y), 0) - 8 : 0;
    const maxY = hasData ? Math.max(...allPos.map(p=>p.y), 60) + 8 : 100;
    const scaleX = (w - 80) / Math.max(maxX - minX, 1);
    const scaleY = (h - 80) / Math.max(maxY - minY, 1);
    const scale = Math.min(scaleX, scaleY);
    const ox = 40 - minX * scale;
    const oy = 40 - minY * scale;

    const toScreen = (x: number, y: number): [number, number] => [ox + x*scale, oy + y*scale];

    // --- 地图节点 ---
    scene?.mapElements.forEach(elem => {
      const [sx,sy] = toScreen(elem.position.x, elem.position.y);
      // 发光效果
      const grad = ctx.createRadialGradient(sx, sy, 0, sx, sy, 14);
      grad.addColorStop(0, elem.color + '40');
      grad.addColorStop(1, 'transparent');
      ctx.fillStyle = grad; ctx.beginPath(); ctx.arc(sx, sy, 14, 0, Math.PI*2); ctx.fill();

      // 节点实体
      ctx.fillStyle = elem.color; ctx.beginPath(); ctx.arc(sx, sy, 6, 0, Math.PI*2); ctx.fill();
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5; ctx.stroke();
      ctx.fillStyle = '#999'; ctx.font = '10px monospace';
      ctx.fillText(elem.label, sx+9, sy-7);
    });

    // --- 地图边连线 (如果有边数据) ---
    if (scene?.mapElements && scene.mapElements.length >= 2) {
      ctx.strokeStyle = 'rgba(255,255,255,0.08)';
      ctx.lineWidth = 1;
      for (let i=0; i<scene.mapElements.length-1; i++) {
        const a = scene.mapElements[i];
        const b = scene.mapElements[i+1];
        if ((a as any).element_type === 'node' && (b as any).element_type === 'node') {
          const [sx,sy]=toScreen(a.position.x,a.position.y);
          const [ex,ey]=toScreen(b.position.x,b.position.y);
          ctx.beginPath(); ctx.moveTo(sx,sy); ctx.lineTo(ex,ey); ctx.stroke();
        }
      }
    }

    // --- AGV 运动轨迹尾迹 ---
    trailPointsRef.current.forEach((trail, agvId) => {
      if (trail.length < 2) return;
      const agvColor = simAgvsRef.current.find(a=>a.id===agvId)?.color || '#1890ff';
      for (let i=1; i<trail.length; i++) {
        const alpha = (i/trail.length) * 0.6; // 渐隐
        const [sx,sy] = toScreen(trail[i-1].x,trail[i-1].y);
        const [ex,ey] = toScreen(trail[i].x,trail[i].y);
        ctx.strokeStyle = agvColor + Math.floor(alpha*255).toString(16).padStart(2,'0');
        ctx.lineWidth = 1 + (i/trail.length)*2;
        ctx.beginPath(); ctx.moveTo(sx,sy); ctx.lineTo(ex,ey); ctx.stroke();
      }
    });

    // --- 后端静态轨迹 (虚线) ---
    scene?.trajectories.forEach(traj => {
      if (traj.points.length < 2) return;
      ctx.strokeStyle = 'rgba(24,144,255,0.25)';
      ctx.lineWidth = 1.5; ctx.setLineDash([6,4]);
      ctx.beginPath();
      traj.points.forEach((p,i)=>{
        const [sx,sy]=toScreen(p.pos.x,p.pos.y);
        if(i===0) ctx.moveTo(sx,sy); else ctx.lineTo(sx,sy);
      });
      ctx.stroke(); ctx.setLineDash([]);
    });

    // --- AGV 实体（动画位置）---
    const renderAgvs = playing && simAgvsRef.current.length > 0
      ? simAgvsRef.current   // 使用模拟动画位置
      : (scene?.agvs || []); // 使用后端静态位置

    if (playing && simAgvsRef.current.length === 0 && !scene) return;

    (Array.isArray(renderAgvs) ? renderAgvs : []).forEach((agv: any) => {
      const ax = agv.x ?? agv.position?.x ?? 0;
      const ay = agv.y ?? agv.position?.y ?? 0;
      const arot = agv.rotation ?? 0;
      const acolor = agv.color || '#4A90D9';
      const astate = agv.state || 'idle';
      const abattery = agv.batteryLevel ?? 80;
      const aid = agv.id || agv.agvId || '?';

      const [sx,sy] = toScreen(ax, ay);

      // AGV 光晕
      const glow = ctx.createRadialGradient(sx, sy, 0, sx, sy, 22);
      glow.addColorStop(0, acolor + '20'); glow.addColorStop(1, 'transparent');
      ctx.fillStyle = glow; ctx.beginPath(); ctx.arc(sx, sy, 22, 0, Math.PI*2); ctx.fill();

      // AGV 主体（带旋转）
      ctx.save(); ctx.translate(sx, sy); ctx.rotate((arot*Math.PI)/180);
      // 车身阴影
      ctx.fillStyle = 'rgba(0,0,0,0.3)';
      ctx.fillRect(-11, -8, 22, 16);
      // 车身
      ctx.fillStyle = acolor;
      ctx.fillRect(-10, -7, 20, 14);
      // 车头指示器（前方）
      ctx.fillStyle = astate === 'error' ? '#ff4d4f' :
                      astate === 'moving' ? '#52c41a' : '#fff';
      ctx.fillRect(8, -3, 3, 6);
      ctx.restore();

      // 电量条背景
      ctx.fillStyle = 'rgba(0,0,0,0.5)'; ctx.fillRect(sx-12, sy+11, 24, 4);
      // 电量条
      ctx.fillStyle = abattery>50?'#52c41a': abattery>20?'#faad14':'#ff4d4f';
      ctx.fillRect(sx-12, sy+11, (abattery/100)*24, 4);

      // ID 标签
      ctx.fillStyle='#fff'; ctx.font='bold 10px monospace'; ctx.textAlign='center';
      ctx.fillText(aid, sx, sy-14); ctx.textAlign='left';

      const rot = arot; // alias for closure scope
      // 速度向量指示
      if (astate === 'moving') {
        ctx.strokeStyle = 'rgba(82,196,26,0.5)';
        ctx.lineWidth = 1.5;
        const dirRad = (rot*Math.PI)/180;
        ctx.beginPath(); ctx.moveTo(sx, sy);
        ctx.lineTo(sx + Math.cos(dirRad)*18, sy + Math.sin(dirRad)*18); ctx.stroke();
      }
    });

    // --- 热力图叠加 ---
    if (showHeatmap && simAgvsRef.current.length > 0) {
      simAgvsRef.current.forEach(agv => {
        const [sx,sy] = toScreen(agv.x, agv.y);
        const intensity = Math.min(1, agv.speed / 2.5);
        const grad = ctx.createRadialGradient(sx, sy, 0, sx, sy, 28);
        grad.addColorStop(0, `rgba(255,${Math.floor(120-intensity*80)},0,${intensity*0.35})`);
        grad.addColorStop(1, 'transparent');
        ctx.fillStyle = grad; ctx.beginPath(); ctx.arc(sx,sy,28,0,Math.PI*2); ctx.fill();
      });
    }

    // --- 信息覆盖层 ---
    if (playing) {
      ctx.fillStyle = 'rgba(0,0,0,0.6)';
      ctx.fillRect(w-160, 8, 152, 52);
      ctx.strokeStyle = 'rgba(24,144,255,0.3)'; ctx.strokeRect(w-160, 8, 152, 52);
      ctx.fillStyle = '#aaa'; ctx.font = '11px monospace';
      ctx.fillText(`FPS: ${animSpeed.toFixed(1)}x`, w-152, 26);
      ctx.fillText(`AGVs: ${simAgvsRef.current.length}`, w-152, 42);
      ctx.fillText('LIVE', w-152, 58);
    }
  }, [scene, showHeatmap, playing, animSpeed]);

  // 初始绘制（非动画状态）
  useEffect(() => {
    if (!playing) drawFrame();
  }, [drawFrame, playing]);

  // ========== Tab 内容 ==========

  const canvasTab = (
    <div>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={4}>
          <Card size="small"><Statistic title="地图节点" value={scene?.mapElements?.length||0} /></Card>
        </Col>
        <Col span={4}>
          <Card size="small"><Statistic title="AGV 数量" value={scene?.agvs?.length||0} /></Card>
        </Col>
        <Col span={4}>
          <Card size="small"><Statistic title="轨迹数" value={scene?.trajectories?.length||0} /></Card>
        </Col>
        <Col span={4}>
          <Card size="small">
            <Statistic title="活跃 AGV"
              value={playing ? (simAgvsRef.current.filter(a=>a.state==='moving').length) : (scene?.agvs?.filter(a=>a.state!=='idle').length||0)}
              valueStyle={{ color: playing ? '#52c41a' : undefined }}
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small">
            <Space wrap>
              <Button icon={<ReloadOutlined />} onClick={refreshScene} loading={loading} size="small">刷新</Button>
              <Button type={playing?'default':'primary'} size="small"
                icon={playing?<PauseCircleOutlined/>:<PlayCircleOutlined/>}
                onClick={()=>setPlaying(!playing)}>
                {playing?'暂停':'播放'}
              </Button>
              <Tooltip title="启动后 AGV 将沿地图路径自动巡逻">
                <Button icon={<RocketOutlined />} size="small" type="dashed" onClick={()=>{
                  if(simAgvsRef.current.length===0) simAgvsRef.current=initSimAgvs(scene);
                  setPlaying(true);
                }}>
                  启动模拟
                </Button>
              </Tooltip>
              <Space>
                <FastForwardOutlined />
                <InputNumber size="small" min={0.2} max={5} step={0.2} value={animSpeed}
                  onChange={v=>setAnimSpeed(v||1)} style={{width:58}} />
                <Text type="secondary" style={{fontSize:11}}>倍速</Text>
              </Space>
            </Space>
          </Card>
        </Col>
      </Row>

      <Row gutter={16} style={{ marginBottom: 12 }}>
        <Col span={12}>
          <Space>
            <HeatMapOutlined />
            <Switch checked={showHeatmap} onChange={setShowHeatmap} size="small" />
            <Text type="secondary" style={{ fontSize: 12 }}>热力图</Text>
          </Space>
        </Col>
        <Col span={12} style={{ textAlign: 'right' }}>
          <Text type="secondary" style={{ fontSize: 11 }}>
            {playing
              ? '实时动画中 — AGV 沿路径巡逻运动，带轨迹尾迹和电量消耗'
              : '点击「播放」或「启动模拟」查看 AGV 动态运动效果'}
          </Text>
        </Col>
      </Row>

      <Card title={<><CameraOutlined /> 2.5D 场景视图 (实时动态渲染)</>} size="small">
        <Spin spinning={loading}>
          <canvas ref={canvasRef} width={900} height={500} style={{
            width:'100%', height:'auto', background:'#0a0e27',
            borderRadius:8, border:'1px solid rgba(24,144,255,0.2)',
          }} />
        </Spin>
      </Card>
      <Text type="secondary" style={{fontSize:12, marginTop:8, display:'block'}}>
        技术栈: Canvas 2D + requestAnimationFrame 动画循环 + 线性插值运动 + 轨迹尾迹.
        数据模型支持 Three.js 3D 渲染升级 (SceneModel 含完整 xyz 坐标).
      </Text>
    </div>
  );

  // AGV 列表 Tab
  const agvListTab = (
    <Card title={<><BoxPlotOutlined /> AGV 实时状态</>} size="small">
      <Table dataSource={(playing?simAgvsRef.current:(scene?.agvs||[])).map((a:any,i)=>({key:i,...a}))||[]}
        columns={[
          { title:'ID', dataIndex:'id', render:(v:string)=><Tag color="blue">{v}</Tag> },
          { title:'状态', dataIndex:'state', render:(v:string)=>(
            <Tag color={v==='moving'?'green':v==='idle'?'default':v==='error'?'red':'orange'}>{v}</Tag>)},
          { title:'位置 X', dataIndex:'x', render:(v:number)=>typeof v==='number'?v.toFixed(1):(v as any)?.position?.x?.toFixed(1)||'-' },
          { title:'位置 Y', dataIndex:'y', render:(v:number)=>typeof v==='number'?v.toFixed(1):(v as any)?.position?.y?.toFixed(1)||'-' },
          { title:'朝向', dataIndex:'rotation', render:(v:number)=><>{typeof v==='number'?`${(v%360).toFixed(0)}°`:'-'}</> },
          { title:'电量%', dataIndex:'batteryLevel', render:(v:number)=>
            <span style={{color:v>50?'#52c41a':v>20?'#faad14':'#ff4d4f'}}>{typeof v==='number'?v.toFixed(1)+'%':'-'}</span>},
          { title:'速度', dataIndex:'speed', render:(v:number)=><>{typeof v==='number'?v.toFixed(2)+' m/s':'-'}</> },
          { title:'颜色', dataIndex:'color', render:(v:string)=>v?<div style={{width:18,height:18,background:v,borderRadius:4,display:'inline-block'}}/>:'-' },
        ]}
        pagination={{ pageSize: 15 }} size="small"
      />
    </Card>
  );

  // 轨迹动画 Tab
  const trajectoryTab = (
    <Card title={<><ThunderboltOutlined /> 轨迹与动画控制</>} size="small">
      {!scene || (scene.trajectories?.length||0)===0 ? (
        <Empty description={
          <span>
            暂无后端调度轨迹数据。
            <br/>
            <Text type="secondary">点击上方「启动模拟」可查看 AGV 巡逻动画（基于地图节点自动生成路径）</Text>
          </span>
        } />
      ) : (
        <>
          <Space style={{marginBottom:16}}>
            <Button type={playing?'default':'primary'}
              icon={playing?<PauseCircleOutlined/>:<PlayCircleOutlined/>}
              onClick={()=>setPlaying(!playing)}>{playing?'暂停':'播放轨迹'}</Button>
            <Text type="secondary">当前模拟 AGV 数: {simAgvsRef.current.length}</Text>
          </Space>
          {scene.trajectories.map(traj => (
            <Card key={traj.agvId} type="inner" title={`AGV: ${traj.agvId}`} size="small" style={{ marginBottom:8 }}>
              <Row gutter={16}>
                <Col span={6}><Statistic title="轨迹点数" value={traj.points?.length||0} /></Col>
                <Col span={6}><Statistic title="总时长" value={(traj.totalDuration||0).toFixed(1)} suffix="s" /></Col>
                <Col span={6}><Statistic title="总距离" value={(traj.totalDistance||0).toFixed(1)} suffix="m" /></Col>
                <Col span={6}><Statistic title="首点坐标"
                  value={traj.points?.[0]?.pos ? `(${traj.points[0].pos.x.toFixed(1)}, ${traj.points[0].pos.y.toFixed(1)})` : '-'} />
                </Col>
              </Row>
            </Card>
          ))}
        </>
      )}
    </Card>
  );

  // JSON Tab
  const jsonTab = (
    <Card title="场景 JSON 数据" size="small">
      <pre style={{
        background:'#0a0e27', color:'#52c41a', padding:12, borderRadius:8,
        maxHeight:600, overflow:'auto', fontSize:11,
      }}>{scene ? JSON.stringify(scene, null, 2) : '加载中...'}</pre>
    </Card>
  );

  return (
    <div>
      <Tabs activeKey={activeTab} onChange={setActiveTab} items={[
        { key:'canvas', label:<><EnvironmentOutlined /> 场景视图</>, children:canvasTab },
        { key:'agvs', label:<><BoxPlotOutlined /> AGV 模型</>, children:agvListTab },
        { key:'trajectory', label:<><ThunderboltOutlined /> 轨迹动画</>, children:trajectoryTab },
        { key:'json', label:'JSON 数据', children:jsonTab },
      ]} />
    </div>
  );
};

export default DigitalTwinPage;
