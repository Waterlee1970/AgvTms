/**
 * ThreeDigitalTwin — React 封装的 3D 数字孪生组件
 *
 * 功能:
 *   - Three.js 场景渲染 (via React Three Fiber)
 *   - AGV 实时 3D 模型展示
 *   - 轨迹回放动画
 *   - 热力图叠加
 *   - 多视角控制 (自由/俯视/跟随)
 *   - 性能监控 HUD
 *   - 2D Canvas 降级方案
 *
 * @example
 * ```tsx
 * <ThreeDigitalTwin
 *   nodes={mapNodes}
 *   edges={mapEdges}
 *   agvs={agvStates}
 *   trajectories={trajectoryData}
 *   heatmap={heatmapData}
 *   mode="3d"
 * />
 * ```
 */

import React, { useRef, useEffect, useCallback, useState, useMemo, Suspense } from 'react';
import { Canvas } from '@react-three/fiber';
import { OrbitControls, PerspectiveCamera, Environment, Html, Stats } from '@react-three/drei';
import * as THREE from 'three';

import { SceneEngine, MapNode3D, MapEdge3D, AgvState3D, TrajectoryPoint3D, HeatmapCell, SceneEngineOptions } from './SceneEngine';

// ==================== 类型定义 ====================

interface ThreeDigitalTwinProps {
  /** 地图节点数据 */
  nodes?: MapNode3D[];
  /** 地图边数据 */
  edges?: MapEdge3D[];
  /** AGV 状态数组 */
  agvs?: AgvState3D[];
  /** 轨迹数据 (按 AGV ID 索引) */
  trajectories?: Record<string, TrajectoryPoint3D[]>;
  /** 热力图数据 */
  heatmap?: HeatmapCell[];
  /** 渲染模式: '3d' | '2d' | 'auto' */
  mode?: '3d' | '2d' | 'auto';
  /** 是否显示性能统计 */
  showStats?: boolean;
  /** 是否显示控制面板 UI */
  showControls?: boolean;
  /** 聚焦的 AGV ID */
  focusAgvId?: string;
  /** 视角模式: 'free' | 'top' | 'follow' | 'isometric' */
  viewMode?: 'free' | 'top' | 'follow' | 'isometric';
  /** 场景引擎配置选项 */
  engineOptions?: SceneEngineOptions;
  /** 数据更新回调 */
  onDataUpdate?: (data: any) => void;
  /** AGV 点击事件回调 */
  onAgvClick?: (agvId: string) => void;
  /** 自定义样式 */
  style?: React.CSSProperties;
  className?: string;
}

// ==================== 子组件 ====================

/** AGV 模型组件 (React Three Fiber 集成) */
function AgvModel({ agv, onClick }: { agv: AgvState3D; onClick?: () => void }) {
  const meshRef = useRef<THREE.Group>(null);
  const [hovered, setHovered] = useState(false);

  // 状态颜色映射
  const stateColor = useMemo(() => {
    switch (agv.state) {
      case 'idle': return '#00ff88';
      case 'moving': return '#00aaff';
      case 'charging': return '#ffaa00';
      case 'error': return '#ff3333';
      default: return '#888888';
    }
  }, [agv.state]);

  return (
    <group
      ref={meshRef}
      position={[agv.x, 0.3, agv.y]}
      rotation-y={-agv.theta + Math.PI / 2}
      onClick={onClick}
      onPointerOver={() => setHovered(true)}
      onPointerOut={() => setHovered(false)}
    >
      {/* 车身 */}
      <mesh castShadow receiveShadow>
        <boxGeometry args={[1.2, 0.4, 0.8]} />
        <meshStandardMaterial
          color={stateColor}
          emissive={stateColor}
          emissiveIntensity={hovered ? 0.6 : 0.15}
          metalness={0.5}
          roughness={0.3}
        />
      </mesh>

      {/* 车顶驾驶舱 */}
      <mesh position={[-0.1, 0.55, 0]}>
        <boxGeometry args={[0.7, 0.3, 0.65]} />
        <meshStandardMaterial
          color="#334455"
          transparent
          opacity={0.8}
          roughness={0.5}
        />
      </mesh>

      {/* 前叉 */}
      <mesh position={[0.7, 0.12, 0.18]}>
        <boxGeometry args={[0.05, 0.25, 0.6]} />
        <meshStandardMaterial color="#ffcc00" metalness={0.8} roughness={0.2} />
      </mesh>
      <mesh position={[0.7, 0.12, -0.18]}>
        <boxGeometry args={[0.05, 0.25, 0.6]} />
        <meshStandardMaterial color="#ffcc00" metalness={0.8} roughness={0.2} />
      </mesh>

      {/* 前照灯 */}
      <pointLight
        position={[0.62, 0.3, 0]}
        intensity={agv.state === 'moving' ? 3 : 0.5}
        distance={6}
        color="#ffffdd"
      />

      {/* 状态指示灯 */}
      <mesh position={[0, 0.75, 0]}>
        <sphereGeometry args={[0.08, 12, 12]} />
        <meshBasicMaterial
          color={stateColor}
          toneMapped={false}
        />
      </mesh>

      {/* 标签 */}
      <Html
        position={[0, 1.4, 0]}
        center
        distanceFactor={12}
        style={{ pointerEvents: 'none', userSelect: 'none' }}
      >
        <div
          className="agv-label"
          style={{
            background: 'rgba(20, 25, 45, 0.9)',
            border: `1px solid ${stateColor}`,
            borderRadius: 4,
            padding: '2px 6px',
            fontSize: 11,
            fontWeight: 600,
            color: '#fff',
            whiteSpace: 'nowrap',
            pointerEvents: 'none',
            boxShadow: `0 0 8px ${stateColor}40`,
          }}
        >
          {agv.id.slice(-4)}
          <span style={{ marginLeft: 4, fontSize: 9, opacity: 0.7 }}>
            {Math.round(agv.speed * 100) / 100} m/s
          </span>
        </div>
      </Html>

      {/* 速度向量箭头 */}
      <arrowHelper
        args={[
          new THREE.Vector3(Math.cos(agv.theta), 0, Math.sin(agv.theta)),
          new THREE.Vector3(0, 0.25, 0),
          0.4 + Math.min(agv.speed / 2, 1) * 0.6,
          0x00ffaa,
          0.18,
          0.1,
        ]}
      />
    </group>
  );
}

/** 地图节点组件 */
function NodeMesh({ node }: { node: MapNode3D }) {
  let color = '#4a90d9';
  let height = 0.4;

  switch (node.nodeType) {
    case 'pickup':
      color = '#ff9800'; height = 0.8; break;
    case 'dropoff':
      color = '#4caf50'; height = 0.8; break;
    case 'charging':
      color = '#e91e63'; height = 1.0; break;
  }

  return (
    <group position={[node.x, height / 2, node.y]}>
      {/* 节点柱体 */}
      <mesh castShadow>
        <cylinderGeometry args={[0.25, 0.35, height, 12]} />
        <meshStandardMaterial
          color={color}
          emissive={color}
          emissiveIntensity={0.3}
          metalness={0.3}
          roughness={0.6}
        />
      </mesh>
      {/* 发光光环 */}
      <mesh rotation-x={-Math.PI / 2} position-y={-height / 2 + 0.02}>
        <ringGeometry args={[0.4, 0.55, 24]} />
        <meshBasicMaterial
          color={color}
          transparent
          opacity={0.4}
          side={THREE.DoubleSide}
        />
      </mesh>
      {/* 节点标签 */}
      <Html center distanceFactor={15} position={[0, height + 0.5, 0]}>
        <div style={{
          background: 'rgba(0,0,0,0.7)',
          color: '#fff',
          padding: '1px 5px',
          borderRadius: 3,
          fontSize: 10,
          whiteSpace: 'nowrap',
        }}>
          {node.label || node.id.slice(-3)}
        </div>
      </Html>
    </group>
  );
}

/** 边连线组件 */
function EdgeLine({ edge, nodes }: { edge: MapEdge3D; nodes: MapNode3D[] }) {
  const src = nodes.find(n => n.id === edge.source);
  const tgt = nodes.find(n => n.id === edge.target);
  if (!src || !tgt) return null;

  const points = useMemo(() => [
    new THREE.Vector3(src.x, 0.08, src.y),
    new THREE.Vector3(tgt.x, 0.08, tgt.y),
  ], [src.x, src.y, tgt.x, tgt.y]);

  return (
    <line>
      <bufferGeometry>
        <bufferAttribute
          attach="attributes-position"
          count={points.length}
          array={new Float32Array(points.flatMap(p => [p.x, p.y, p.z]))}
          itemSize={3}
        />
      </bufferGeometry>
      <lineBasicMaterial color="#3a3a5c" transparent opacity={0.5} />
    </line>
  );
}

/** 热力图单元格组件 */
function HeatmapCellMesh({ cell }: { cell: HeatmapCell }) {
  const intensity = cell.value;
  const color = new THREE.Color().setHSL((1 - intensity) * 0.66, 1, 0.5).getStyle();

  return (
    <mesh rotation-x={-Math.PI / 2} position={[cell.x, 0.03, cell.y]}>
      <circleGeometry args={[0.8, 16]} />
      <meshBasicMaterial
        color={color}
        transparent
        opacity={intensity * 0.6}
        side={THREE.DoubleSide}
      />
    </mesh>
  );
}

/** 场景内容组件 */
function SceneContent({
  nodes = [],
  edges = [],
  agvs = [],
  heatmap = [],
  viewMode = 'free',
  focusAgvId,
  onAgvClick,
}: Pick<ThreeDigitalTwinProps, 'nodes' | 'edges' | 'agvs' | 'heatmap' | 'viewMode' | 'focusAgvId' | 'onAgvClick'>) {
  // 计算相机位置基于视角模式
  const getCameraPosition = useCallback(() => {
    if (!nodes.length) return [30, 40, 30] as const;

    switch (viewMode) {
      case 'top':
        return [0, 60, 0.1] as const;
      case 'isometric':
        return [35, 35, 35] as const;
      case 'follow': {
        const focused = agvs.find(a => a.id === focusAgvId) || agvs[0];
        if (focused) return [focused.x + 10, 15, focused.z ?? focused.y + 10] as const;
        return [30, 40, 30] as const;
      }
      case 'free':
      default:
        return [30, 40, 30] as const;
    }
  }, [viewMode, nodes.length, agvs, focusAgvId]);

  const camPos = getCameraPosition();

  return (
    <>
      {/* 相机 */}
      <PerspectiveCamera makeDefault position={camPos} fov={60} near={0.1} far={500} />

      {/* 控制器 */}
      <OrbitControls
        enableDamping
        dampingFactor={0.08}
        minDistance={5}
        maxDistance={150}
        maxPolarAngle={Math.PI / 2.1}
      />

      {/* 环境光 */}
      <ambientLight intensity={0.6} />
      <hemisphereLight args={['#ffffff', '#444466', 0.5]} />
      <directionalLight
        position={[20, 40, 20]}
        intensity={0.8}
        castShadow
        shadow-mapSize-width={2048}
        shadow-mapSize-height={2048}
        shadow-camera-near={0.5}
        shadow-camera-far={100}
        shadow-camera-left={-30}
        shadow-camera-right={30}
        shadow-camera-top={30}
        shadow-camera-bottom={-30}
      />

      {/* 点光源氛围 */}
      <pointLight position={[-15, 15, -15]} intensity={0.5} color="#4488ff" />
      <pointLight position={[15, 10, 15]} intensity={0.5} color="#ff8844" />

      {/* 网格地面 */}
      <gridHelper args={[100, 50, '#2d2d44', '#2d2d44']} position-y={-0.01} />

      {/* 地面平面 */}
      {nodes.length > 1 && (() => {
        const xs = nodes.map(n => n.x), ys = nodes.map(n => n.y);
        const w = Math.max(...xs) - Math.min(...xs) + 10;
        const h = Math.max(...ys) - Math.min(...ys) + 10;
        return (
          <mesh rotation-x={-Math.PI / 2} position-y={-0.02} receiveShadow>
            <planeGeometry args={[w, h]} />
            <meshStandardMaterial color="#22223a" transparent opacity={0.85} />
          </mesh>
        );
      })()}

      {/* 地图节点 */}
      {nodes.map(node => (
        <NodeMesh key={`node-${node.id}`} node={node} />
      ))}

      {/* 边连线 */}
      {edges.map(edge => (
        <EdgeLine key={`edge-${edge.id}`} edge={edge} nodes={nodes} />
      ))}

      {/* 热力图 */}
      {heatmap.map((cell, i) => (
        <HeatmapCellMesh key={`heat-${i}`} cell={cell} />
      ))}

      {/* AGV 模型 */}
      {agvs.map(agv => (
        <AgvModel
          key={`agv-${agv.id}`}
          agv={agv}
          onClick={() => onAgvClick?.(agv.id)}
        />
      ))}
    </>
  );
}

/** 2D Canvas 降级组件 */
function Fallback2D({ nodes, edges, agvs }: Pick<ThreeDigitalTwinProps, 'nodes' | 'edges' | 'agvs'>) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const W = canvas.width, H = canvas.height;

    // 清空
    ctx.fillStyle = '#1a1a2e';
    ctx.fillRect(0, 0, W, H);

    // 绘制网格
    ctx.strokeStyle = '#2d2d44';
    ctx.lineWidth = 0.5;
    for (let i = 0; i <= W; i += 30) { ctx.beginPath(); ctx.moveTo(i, 0); ctx.lineTo(i, H); ctx.stroke(); }
    for (let j = 0; j <= H; j += 30) { ctx.beginPath(); ctx.moveTo(0, j); ctx.lineTo(W, j); ctx.stroke(); }

    // 绘制边
    ctx.strokeStyle = '#3a3a5c';
    ctx.lineWidth = 1.5;
    edges?.forEach(e => {
      const s = nodes?.find(n => n.id === e.source), t = nodes?.find(n => n.id === e.target);
      if (s && t) { ctx.beginPath(); ctx.moveTo(s.x * 10 + W/2, s.y * 10 + H/2); ctx.lineTo(t.x * 10 + W/2, t.y * 10 + H/2); ctx.stroke(); }
    });

    // 绘制节点
    nodes?.forEach(n => {
      ctx.fillStyle = n.nodeType === 'pickup' ? '#ff9800' : n.nodeType === 'dropoff' ? '#4caf50' : '#4a90d9';
      ctx.beginPath();
      ctx.arc(n.x * 10 + W/2, n.y * 10 + H/2, 6, 0, Math.PI * 2);
      ctx.fill();
    });

    // 绘制AGV
    agvs?.forEach(a => {
      const colors: Record<string, string> = { idle: '#00ff88', moving: '#00aaff', charging: '#ffaa00', error: '#ff3333' };
      ctx.fillStyle = colors[a.state] || '#888';
      ctx.shadowBlur = 12;
      ctx.shadowColor = colors[a.state] || '#888';
      ctx.beginPath();
      ctx.arc(a.x * 10 + W/2, a.y * 10 + H/2, 8, 0, Math.PI * 2);
      ctx.fill();
      ctx.shadowBlur = 0;

      // 标签
      ctx.fillStyle = '#fff';
      ctx.font = '10px Arial';
      ctx.fillText(a.id.slice(-4), a.x * 10 + W/2 + 10, a.y * 10 + H/2 + 3);
    });

    // 提示文字
    ctx.fillStyle = 'rgba(255,255,255,0.3)';
    ctx.font = '14px Arial';
    ctx.textAlign = 'center';
    ctx.fillText('2D Fallback Mode — WebGL not available or 2D selected', W/2, H - 20);

  }, [nodes, edges, agvs]);

  return (
    <canvas
      ref={canvasRef}
      width={800}
      height={600}
      style={{ width: '100%', height: '100%', display: 'block' }}
    />
  );
}

// ==================== 主组件 ====================

const ThreeDigitalTwin: React.FC<ThreeDigitalTwinProps> = ({
  nodes = [],
  edges = [],
  agvs = [],
  trajectories = {},
  heatmap = [],
  mode = 'auto',
  showStats = false,
  showControls = true,
  focusAgvId,
  viewMode = 'free',
  engineOptions,
  onAgvClick,
  style,
  className,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const [renderMode, setRenderMode] = useState<'3d' | '2d'>('2d');
  const [fps, setFps] = useState(60);

  // 自动检测 WebGL 支持
  useEffect(() => {
    if (mode === '2d') {
      setRenderMode('2d');
      return;
    }
    try {
      const c = document.createElement('canvas');
      const hasGL = !!(
        c.getContext('webgl2') || c.getContext('webgl') || c.getContext('experimental-webgl')
      );
      setRenderMode(mode === '3d' || (mode === 'auto' && hasGL) ? '3d' : '2d');
    } catch {
      setRenderMode('2d');
    }
  }, [mode]);

  // FPS 监控
  useEffect(() => {
    if (renderMode !== '3d') return;
    let frames = 0;
    let lastTime = performance.now();
    const interval = setInterval(() => {
      const now = performance.now();
      frames++;
      if (now - lastTime >= 1000) {
        setFps(frames);
        frames = 0;
        lastTime = now;
      }
    }, 100);
    return () => clearInterval(interval);
  }, [renderMode]);

  return (
    <div ref={containerRef} className={className} style={{ width: '100%', height: '100%', position: 'relative', ...style }}>
      {/* 控制面板 */}
      {showControls && renderMode === '3d' && (
        <div style={{
          position: 'absolute',
          top: 12,
          left: 12,
          zIndex: 10,
          background: 'rgba(15, 20, 40, 0.85)',
          backdropFilter: 'blur(8px)',
          borderRadius: 8,
          padding: '10px 14px',
          color: '#e0e6f0',
          fontSize: 12,
          fontFamily: 'monospace',
          userSelect: 'none',
          border: '1px solid rgba(74, 144, 217, 0.25)',
          boxShadow: '0 4px 24px rgba(0,0,0,0.4)',
        }}>
          <div style={{ fontWeight: 700, marginBottom: 6, color: '#4a90d9', letterSpacing: 1 }}>
            🤖 3D Digital Twin
          </div>
          <div style={{ opacity: 0.8, lineHeight: 1.8 }}>
            <div>📍 AGVs: <span style={{ color: '#00ffaa' }}>{agvs.length}</span></div>
            <div>🗺️ Nodes: <span style={{ color: '#ff9800' }}>{nodes.length}</span></div>
            <div>🔗 Edges: <span style={{ color: '#888' }}>{edges.length}</span></div>
            <div>⚡ FPS: <span style={{ color: fps > 50 ? '#00ff88' : fps > 30 ? '#ffaa00' : '#ff3333' }}>{fps}</span></div>
            <div>👁️ View: <span style={{ color: '#4a90d9' }}>{viewMode}</span></div>
          </div>
        </div>
      )}

      {/* WebGL 渲染 */}
      {renderMode === '3d' ? (
        <Canvas
          shadows
          gl={{ antialias: true, alpha: false, powerPreference: 'high-performance' }}
          dpr={[1, 2]}
          style={{ background: '#1a1a2e' }}
        >
          <color attach="background" args={['#1a1a2e']} />
          <fog attach="fog" args={['#1a1a2e', 50, 200]} />

          <Suspense fallback={
            <Html center>
              <div style={{ color: '#fff', fontSize: 16 }}>Loading 3D Engine...</div>
            </Html>
          }>
            <SceneContent
              nodes={nodes}
              edges={edges}
              agvs={agvs}
              heatmap={heatmap}
              viewMode={viewMode}
              focusAgvId={focusAgvId}
              onAgvClick={onAgvClick}
            />
          </Suspense>

          {showStats && <Stats />}
        </Canvas>
      ) : (
        /* 2D 降级 */
        <Fallback2D nodes={nodes} edges={edges} agvs={agvs} />
      )}
    </div>
  );
};

export default ThreeDigitalTwin;
export type { ThreeDigitalTwinProps };
