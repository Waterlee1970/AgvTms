/**
 * Three.js 3D Scene Engine — AGV Digital Twin 核心渲染引擎
 *
 * 功能:
 *   - 场景初始化 (相机/灯光/渲染器/后处理)
 *   - 地图几何体构建 (节点/边/区域)
 *   - AGV 3D 模型管理
 *   - 轨迹可视化
 *   - 热力图叠加
 *   - 性能监控 (FPS/内存)
 *
 * 技术栈: Three.js r160+ / React Three Fiber v8
 */

import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';

// ==================== 类型定义 ====================

export interface MapNode3D {
  id: string;
  x: number;
  y: number;
  nodeType?: string; // 'pickup' | 'dropoff' | 'charging' | 'junction'
  label?: string;
}

export interface MapEdge3D {
  id: string;
  source: string;
  target: string;
  weight?: number;
  bidirectional?: boolean;
}

export interface AgvState3D {
  id: string;
  x: number;
  y: number;
  theta: number;       // 弧度, 朝向角度
  speed: number;
  battery: number;      // 0-100
  state: string;        // 'idle' | 'moving' | 'charging' | 'error' | 'loading'
  currentTask?: string;
  loadStatus: boolean;
  color?: string;
}

export interface TrajectoryPoint3D {
  x: number;
  y: number;
  z: number;
  timestamp: number;
}

export interface HeatmapCell {
  x: number;
  y: number;
  value: number;        // 0-1 强度
  label?: string;
}

export interface SceneEngineOptions {
  containerWidth?: number;
  containerHeight?: number;
  backgroundColor?: number;
  enableShadows?: boolean;
  enableBloom?: boolean;
  showGridHelper?: boolean;
  showAxesHelper?: boolean;
  ambientLightIntensity?: number;
}

// ==================== 颜色常量 ====================

const COLORS = {
  background: 0x1a1a2e,
  grid: 0x2d2d44,
  nodeDefault: 0x4a90d9,
  nodePickup: 0xff9800,
  nodeDropoff: 0x4caf50,
  nodeCharging: 0xe91e63,
  edgeDefault: 0x3a3a5c,
  agvIdle: '#00ff88',
  agvMoving: '#00aaff',
  agvCharging: '#ffaa00',
  agvError: '#ff3333',
  trajectory: 0x00ffff,
  heatmapLow: 0x0000ff,
  heatmapMid: 0x00ff00,
  heatmapHigh: 0xff0000,
};

// ==================== SceneEngine 主类 ====================

export class SceneEngine {
  // 核心 Three.js 对象
  scene!: THREE.Scene;
  camera!: THREE.PerspectiveCamera;
  renderer!: THREE.WebGLRenderer;
  controls!: OrbitControls;

  // 后处理
  composer!: EffectComposer;
  bloomPass!: UnrealBloomPass;

  // 场景对象组
  mapGroup!: THREE.Group;
  agvGroup!: THREE.Group;
  trajectoryGroup!: THREE.Group;
  heatmapGroup!: THREE.Group;
  overlayGroup!: THREE.Group;

  // 资源缓存
  private _agvMeshes: Map<string, THREE.Group> = new Map();
  private _nodeMeshes: Map<string, THREE.Mesh> = new Map();
  private _edgeMeshes: Map<string, THREE.Line> = new Map();
  private _trajectoryLines: Map<string, THREE.Line> = new Map();

  // 性能监控
  fpsHistory: number[] = [];
  frameCount: number = 0;
  lastFpsTime: number = 0;
  currentFps: number = 60;

  // 动画状态
  private _animationFrameId: number | null = null;
  private _clock: THREE.Clock = new THREE.Clock();
  isRunning: boolean = false;

  constructor(private options: SceneEngineOptions = {}) {
    this.init();
  }

  /**
   * 初始化场景所有组件
   */
  private init(): void {
    const width = this.options.containerWidth || window.innerWidth;
    const height = this.options.containerHeight || window.innerHeight;

    // --- Scene ---
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(this.options.backgroundColor || COLORS.background);
    this.scene.fog = new THREE.Fog(COLORS.background, 50, 200);

    // --- Camera ---
    this.camera = new THREE.PerspectiveCamera(60, width / height, 0.1, 1000);
    this.camera.position.set(30, 40, 30);
    this.camera.lookAt(0, 0, 0);

    // --- Renderer ---
    this.renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: false,
      powerPreference: 'high-performance',
    });
    this.renderer.setSize(width, height);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.shadowMap.enabled = this.options.enableShadows !== false;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.2;

    // --- Controls ---
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.minDistance = 5;
    this.controls.maxDistance = 150;
    this.controls.maxPolarAngle = Math.PI / 2.1; // 不允许进入地下
    this.controls.target.set(0, 0, 0);

    // --- Lights ---
    this.setupLights();

    // --- Groups ---
    this.mapGroup = new THREE.Group();
    this.mapGroup.name = 'map';
    this.agvGroup = new THREE.Group();
    this.agvGroup.name = 'agvs';
    this.trajectoryGroup = new THREE.Group();
    this.trajectoryGroup.name = 'trajectories';
    this.heatmapGroup = new THREE.Group();
    this.heatmapGroup.name = 'heatmap';
    this.overlayGroup = new THREE.Group();
    this.overlayGroup.name = 'overlays';

    this.scene.add(this.mapGroup, this.agvGroup, this.trajectoryGroup, this.heatmapGroup, this.overlayGroup);

    // --- Helpers ---
    if (this.options.showGridHelper) {
      const gridHelper = new THREE.GridHelper(100, 50, COLORS.grid, COLORS.grid);
      gridHelper.material.opacity = 0.3;
      gridHelper.material.transparent = true;
      this.scene.add(gridHelper);
    }

    if (this.options.showAxesHelper) {
      const axesHelper = new THREE.AxesHelper(10);
      this.scene.add(axesHelper);
    }

    // --- Post-processing ---
    this.setupPostProcessing();
  }

  /**
   * 设置灯光系统
   */
  private setupLights(): void {
    const intensity = this.options.ambientLightIntensity || 0.6;

    // Ambient Light
    const ambient = new THREE.AmbientLight(0xffffff, intensity);
    this.scene.add(ambient);

    // Hemisphere Light (天空/地面渐变)
    const hemi = new THREE.HemisphereLight(0xffffff, 0x444466, 0.5);
    this.scene.add(hemi);

    // Directional Light (主光源 + 阴影)
    const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
    dirLight.position.set(20, 40, 20);
    dirLight.castShadow = true;
    dirLight.shadow.mapSize.width = 2048;
    dirLight.shadow.mapSize.height = 2048;
    dirLight.shadow.camera.near = 0.5;
    dirLight.shadow.camera.far = 100;
    dirLight.shadow.camera.left = -30;
    dirLight.shadow.camera.right = 30;
    dirLight.shadow.camera.top = 30;
    dirLight.shadow.camera.bottom = -30;
    this.scene.add(dirLight);

    // Point lights for atmosphere
    const pointLight1 = new THREE.PointLight(0x4488ff, 0.5, 50);
    pointLight1.position.set(-15, 15, -15);
    this.scene.add(pointLight1);

    const pointLight2 = new THREE.PointLight(0xff8844, 0.5, 50);
    pointLight2.position.set(15, 10, 15);
    this.scene.add(pointLight2);
  }

  /**
   * 设置后处理效果 (Bloom 辉光)
   */
  private setupPostProcessing(): void {
    this.composer = new EffectComposer(this.renderer);

    const renderPass = new RenderPass(this.scene, this.camera);
    this.composer.addPass(renderPass);

    if (this.options.enableBloom !== false) {
      this.bloomPass = new UnrealBloomPass(
        new THREE.Vector2(this.renderer.domElement.width, this.renderer.domElement.height),
        0.6,   // strength
        0.4,   // radius
        0.85   // threshold
      );
      this.composer.addPass(this.bloomPass);
    }
  }

  /**
   * 构建 3D 地图几何体
   */
  buildMap(nodes: MapNode3D[], edges: MapEdge3D[]): void {
    this.clearMap();

    // 计算中心点用于偏移
    if (nodes.length > 0) {
      const centerX = nodes.reduce((s, n) => s + n.x, 0) / nodes.length;
      const centerY = nodes.reduce((s, n) => s + n.y, 0) / nodes.length;
      this.mapGroup.position.set(-centerX, 0, -centerY);
    }

    // 创建节点柱体
    nodes.forEach(node => {
      const mesh = this.createNodeMesh(node);
      this._nodeMeshes.set(node.id, mesh);
      this.mapGroup.add(mesh);
    });

    // 创建边管道
    edges.forEach(edge => {
      const line = this.createEdgeLine(edge, nodes);
      if (line) {
        this._edgeMeshes.set(edge.id, line);
        this.mapGroup.add(line);
      }
    });

    // 添加地面
    this.createGroundPlane(nodes);
  }

  /**
   * 创建节点网格 (带类型区分的发光柱体)
   */
  private createNodeMesh(node: MapNode3D): THREE.Mesh {
    let color: number;
    let height: number = 0.5;

    switch (node.nodeType) {
      case 'pickup':
        color = COLORS.nodePickup;
        height = 0.8;
        break;
      case 'dropoff':
        color = COLORS.nodeDropoff;
        height = 0.8;
        break;
      case 'charging':
        color = COLORS.nodeCharging;
        height = 1.0;
        break;
      default:
        color = COLORS.nodeDefault;
        height = 0.4;
    }

    const geometry = new THREE.CylinderGeometry(0.25, 0.35, height, 12);
    const material = new THREE.MeshStandardMaterial({
      color,
      emissive: color,
      emissiveIntensity: 0.3,
      metalness: 0.3,
      roughness: 0.6,
    });
    const mesh = new THREE.Mesh(geometry, material);
    mesh.position.set(node.x, height / 2, node.y);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    mesh.userData = { nodeId: node.id, type: node.nodeType };

    // 发光光环
    const ringGeo = new THREE.RingGeometry(0.4, 0.55, 24);
    const ringMat = new THREE.MeshBasicMaterial({
      color,
      side: THREE.DoubleSide,
      transparent: true,
      opacity: 0.4,
    });
    const ring = new THREE.Mesh(ringGeo, ringMat);
    ring.rotation.x = -Math.PI / 2;
    ring.position.set(node.x, 0.02, node.y);
    (mesh as THREE.Object3D).add(ring);

    return mesh;
  }

  /**
   * 创建边连线 (半透明管道)
   */
  private createEdgeLine(edge: MapEdge3D, nodes: MapNode3D[]): THREE.Line | null {
    const srcNode = nodes.find(n => n.id === edge.source);
    const tgtNode = nodes.find(n => n.id === edge.target);
    if (!srcNode || !tgtNode) return null;

    const points = [
      new THREE.Vector3(srcNode.x, 0.08, srcNode.y),
      new THREE.Vector3(tgtNode.x, 0.08, tgtNode.y),
    ];
    const geometry = new THREE.BufferGeometry().setFromPoints(points);
    const material = new THREE.LineBasicMaterial({
      color: COLORS.edgeDefault,
      transparent: true,
      opacity: 0.5,
      linewidth: 2,
    });
    return new THREE.Line(geometry, material);
  }

  /**
   * 创建地面平面
   */
  private createGroundPlane(nodes: MapNode3D[]): void {
    if (nodes.length < 2) return;

    const xs = nodes.map(n => n.x);
    const ys = nodes.map(n => n.y);
    const minX = Math.min(...xs) - 5;
    const maxX = Math.max(...xs) + 5;
    const minY = Math.min(...ys) - 5;
    const maxY = Math.max(...ys) + 5;

    const geometry = new THREE.PlaneGeometry(maxX - minX, maxY - minY);
    const material = new THREE.MeshStandardMaterial({
      color: 0x22223a,
      metalness: 0.1,
      roughness: 0.9,
      transparent: true,
      opacity: 0.85,
    });
    const plane = new THREE.Mesh(geometry, material);
    plane.rotation.x = -Math.PI / 2;
    plane.position.set((minX + maxX) / 2, -0.01, (minY + maxY) / 2);
    plane.receiveShadow = true;
    this.mapGroup.add(plane);
  }

  /**
   * 更新或创建 AGV 3D 模型
   */
  updateAgv(agv: AgvState3D): THREE.Group {
    let group = this._agvMeshes.get(agv.id);

    if (!group) {
      group = this.createAgvMesh(agv);
      this._agvMeshes.set(agv.id, group);
      this.agvGroup.add(group);
    }

    // 平滑插值位置
    const targetPos = new THREE.Vector3(agv.x, 0.3, agv.y);
    group.position.lerp(targetPos, 0.3);

    // 平滑旋转
    const targetRot = new THREE.Euler(0, -agv.theta + Math.PI / 2, 0);
    group.rotation.y += (targetRot.y - group.rotation.y) * 0.2;

    // 更新颜色基于状态
    this.updateAgvColor(group, agv.state, agv.battery);

    // 更新速度指示器
    this.updateSpeedIndicator(group, agv.speed);

    return group;
  }

  /**
   * 创建 AGV 3D 模型 (叉车造型)
   */
  private createAgvMesh(agv: AgvState3D): THREE.Group {
    const group = new THREE.Group();
    group.name = `agv-${agv.id}`;

    // 车身 (主体)
    const bodyGeo = new THREE.BoxGeometry(1.2, 0.4, 0.8);
    const bodyMat = new THREE.MeshStandardMaterial({
      color: new THREE.Color(agv.color || COLORS.agvMoving),
      metalness: 0.5,
      roughness: 0.3,
    });
    const body = new THREE.Mesh(bodyGeo, bodyMat);
    body.position.y = 0.25;
    body.castShadow = true;
    group.add(body);

    // 车顶 (驾驶舱)
    const cabinGeo = new THREE.BoxGeometry(0.7, 0.3, 0.65);
    const cabinMat = new THREE.MeshStandardMaterial({
      color: 0x334455,
      metalness: 0.3,
      roughness: 0.5,
      transparent: true,
      opacity: 0.8,
    });
    const cabin = new THREE.Mesh(cabinGeo, cabinMat);
    cabin.position.set(-0.1, 0.55, 0);
    group.add(cabin);

    // 前叉 (AGV 特征)
    const forkGeo = new THREE.BoxGeometry(0.05, 0.25, 0.6);
    const forkMat = new THREE.MeshStandardMaterial({ color: 0xffcc00, metalness: 0.8, roughness: 0.2 });
    const forkL = new THREE.Mesh(forkGeo, forkMat);
    forkL.position.set(0.7, 0.12, 0.18);
    group.add(forkL);
    const forkR = new THREE.Mesh(forkGeo, forkMat);
    forkR.position.set(0.7, 0.12, -0.18);
    group.add(forkR);

    // 前照灯 (LED 光晕)
    const lightGeo = new THREE.SphereGeometry(0.06, 8, 8);
    const lightMat = new THREE.MeshBasicMaterial({ color: 0xffffcc });
    const headlightL = new THREE.Mesh(lightGeo, lightMat);
    headlightL.position.set(0.62, 0.22, 0.32);
    group.add(headlightL);
    const headlightR = new THREE.Mesh(lightGeo, lightMat);
    headlightR.position.set(0.62, 0.22, -0.32);
    group.add(headlightR);

    // 点光源 (实际照明)
    const spotlight = new THREE.SpotLight(0xffffdd, 2, 8, Math.PI / 6, 0.5);
    spotlight.position.set(0.62, 0.3, 0);
    spotlight.target.position.set(3, 0, 0);
    group.add(spotlight);
    group.add(spotlight.target);

    // 轮子 (4个)
    const wheelGeo = new THREE.CylinderGeometry(0.12, 0.12, 0.08, 12);
    const wheelMat = new THREE.MeshStandardMaterial({ color: 0x222222, roughness: 0.9 });
    const wheelPositions = [
      [0.4, 0.12, 0.45], [0.4, 0.12, -0.45],
      [-0.4, 0.12, 0.45], [-0.4, 0.12, -0.45],
    ];
    wheelPositions.forEach(([x, y, z]) => {
      const wheel = new THREE.Mesh(wheelGeo, wheelMat);
      wheel.position.set(x, y, z);
      wheel.rotation.z = Math.PI / 2;
      group.add(wheel);
    });

    // 状态指示灯 (顶部)
    const statusLightGeo = new THREE.SphereGeometry(0.08, 12, 12);
    const statusLightMat = new THREE.MeshBasicMaterial({
      color: new THREE.Color(this.getStateColor(agv.state)),
    });
    const statusLight = new THREE.Mesh(statusLightGeo, statusLightMat);
    statusLight.position.set(0, 0.75, 0);
    statusLight.name = 'statusLight';
    group.add(statusLight);

    // ID 文字标签 (用 Sprite)
    const sprite = this.createTextSprite(`AGV-${agv.id.slice(-4)}`);
    sprite.position.set(0, 1.1, 0);
    sprite.name = 'label';
    group.add(sprite);

    // 速度向量箭头 (方向指示)
    const arrowDir = new THREE.Vector3(Math.cos(agv.theta), 0, Math.sin(agv.theta));
    const arrowOrigin = new THREE.Vector3(0, 0.25, 0);
    const arrowLength = 0.8;
    const arrowHelper = new THREE.ArrowHelper(
      arrowDir, arrowOrigin, arrowLength, 0x00ffaa, 0.2, 0.12
    );
    arrowHelper.name = 'directionArrow';
    group.add(arrowHelper);

    // 初始位置
    group.position.set(agv.x, 0.3, agv.y);
    group.rotation.y = -agv.theta + Math.PI / 2;

    return group;
  }

  /**
   * 更新 AGV 颜色和状态
   */
  private updateAgvColor(group: THREE.Group, state: string, battery: number): void {
    const stateColor = this.getStateColor(state);
    
    // 更新状态指示灯
    const statusLight = group.getObjectByName('statusLight') as THREE.Mesh;
    if (statusLight && statusLight.material instanceof THREE.MeshBasicMaterial) {
      statusLight.material.color.set(stateColor);
      
      // 低电量闪烁红色
      if (battery < 20) {
        statusLight.material.color.setHex(0xff0000);
        const pulse = Math.sin(Date.now() * 0.01) * 0.5 + 0.5;
        statusLight.material.opacity = 0.5 + pulse * 0.5;
        (statusLight.material as any).transparent = true;
      }
    }
  }

  /**
   * 更新速度向量指示器
   */
  private updateSpeedIndicator(group: THREE.Group, speed: number): void {
    const arrow = group.getObjectByName('directionArrow') as THREE.ArrowHelper;
    if (arrow) {
      // 速度越快箭头越长
      const scale = 0.4 + Math.min(speed / 2, 1) * 0.8;
      arrow.setLength(scale, 0.15 + scale * 0.2, 0.1);
    }
  }

  /**
   * 获取状态对应颜色
   */
  private getStateColor(state: string): string {
    switch (state) {
      case 'idle': return COLORS.agvIdle;
      case 'moving': return COLORS.agvMoving;
      case 'charging': return COLORS.agvCharging;
      case 'error': return COLORS.agvError;
      case 'loading': return '#ffaa00';
      default: return '#888888';
    }
  }

  /**
   * 绘制轨迹线
   */
  drawTrajectory(agvId: string, points: TrajectoryPoint3D[], color?: number): void {
    // 移除旧轨迹
    const oldLine = this._trajectoryLines.get(agvId);
    if (oldLine) {
      this.trajectoryGroup.remove(oldLine);
      oldLine.geometry.dispose();
      if (oldLine.material instanceof THREE.Material) oldLine.material.dispose();
    }

    if (points.length < 2) return;

    const vectorPoints = points.map(p => new THREE.Vector3(p.x, p.y + 0.15, p.z));
    const curve = new THREE.CatmullRomCurve3(vectorPoints);
    const tubeGeo = new THREE.TubeGeometry(curve, Math.min(points.length * 2, 100), 0.04, 6, false);
    const tubeMat = new THREE.MeshStandardMaterial({
      color: color || COLORS.trajectory,
      emissive: color || COLORS.trajectory,
      emissiveIntensity: 0.4,
      transparent: true,
      opacity: 0.7,
    });
    const tube = new THREE.Mesh(tubeGeo, tubeMat);
    tube.name = `trajectory-${agvId}`;
    this.trajectoryGroup.add(tube);
    this._trajectoryLines.set(agvId, tube as unknown as THREE.Line);
  }

  /**
   * 渲染热力图
   */
  renderHeatmap(cells: HeatmapCell[]): void {
    this.clearHeatmap();

    cells.forEach(cell => {
      const intensity = cell.value;
      const color = new THREE.Color().setHSL((1 - intensity) * 0.66, 1, 0.5);

      const geo = new THREE.CircleGeometry(0.8, 16);
      const mat = new THREE.MeshBasicMaterial({
        color,
        transparent: true,
        opacity: intensity * 0.6,
        side: THREE.DoubleSide,
      });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.rotation.x = -Math.PI / 2;
      mesh.position.set(cell.x, 0.03, cell.y);
      this.heatmapGroup.add(mesh);
    });
  }

  /**
   * 创建文字精灵 (ID 标签等)
   */
  private createTextSprite(text: string, parameters?: {
    fontsize?: number;
    textColor?: string;
    borderColor?: string;
    backgroundColor?: string;
  }): THREE.Sprite {
    const fontface = 'Arial';
    const fontsize = parameters?.fontsize || 28;
    const textColor = parameters?.textColor || 'rgba(255,255,255,0.95)';
    const borderColor = parameters?.borderColor || 'rgba(0,0,0,0.5)';
    const backgroundColor = parameters?.backgroundColor || 'rgba(30,30,60,0.8)';

    const canvas = document.createElement('canvas');
    const context = canvas.getContext('2d')!;
    context.font = `${fontsize}px ${fontface}`;
    const metrics = context.measureText(text);
    const textWidth = metrics.width;
    canvas.width = Math.ceil(textWidth + 16);
    canvas.height = Math.ceil(fontsize * 1.4);

    context.font = `${fontsize}px ${fontface}`;
    context.fillStyle = backgroundColor;
    context.roundRect(0, 0, canvas.width, canvas.height, 4);
    context.fill();
    context.strokeStyle = borderColor;
    context.lineWidth = 2;
    context.stroke();
    context.fillStyle = textColor;
    context.textAlign = 'center';
    context.textBaseline = 'middle';
    context.fillText(text, canvas.width / 2, canvas.height / 2);

    const texture = new THREE.CanvasTexture(canvas);
    texture.needsUpdate = true;
    const spriteMat = new THREE.SpriteMaterial({
      map: texture,
      depthTest: false,
      depthWrite: false,
    });
    const sprite = new THREE.Sprite(spriteMat);
    sprite.scale.set(canvas.width / 80, canvas.height / 80, 1);
    return sprite;
  }

  /**
   * 相机聚焦到指定 AGV
   */
  focusOnAgv(agvId: string, smooth: boolean = true): boolean {
    const agvMesh = this._agvMeshes.get(agvId);
    if (!agvMesh) return false;

    const target = agvMesh.position.clone();
    if (smooth) {
      // 使用 GSAP 或手动 lerp 实现平滑过渡
      this.animateCameraTo(target);
    } else {
      this.controls.target.copy(target);
      this.camera.position.set(target.x + 10, target.y + 12, target.z + 10);
    }
    return true;
  }

  /**
   * 平滑相机动画
   */
  private animateCameraTo(target: THREE.Vector3): void {
    const startPos = this.controls.target.clone();
    const startCam = this.camera.position.clone();
    const endTarget = target.clone();
    const endCam = target.clone().add(new THREE.Vector3(10, 12, 10));

    const startTime = Date.now();
    const duration = 800;

    const animate = () => {
      const elapsed = Date.now() - startTime;
      const t = Math.min(elapsed / duration, 1);
      const easeT = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; // easeInOutQuad

      this.controls.target.lerpVectors(startPos, endTarget, easeT);
      this.camera.position.lerpVectors(startCam, endCam, easeT);

      if (t < 1) requestAnimationFrame(animate);
    };
    animate();
  }

  /**
   * 开始渲染循环
   */
  startAnimation(): void {
    if (this.isRunning) return;
    this.isRunning = true;
    this._lastFpsTime = performance.now();
    this.loop();
  }

  /**
   * 停止渲染循环
   */
  stopAnimation(): void {
    this.isRunning = false;
    if (this._animationFrameId) {
      cancelAnimationFrame(this._animationFrameId);
      this._animationFrameId = null;
    }
  }

  /**
   * 渲染循环
   */
  loop = (): void => {
    if (!this.isRunning) return;
    this._animationFrameId = requestAnimationFrame(this.loop);

    const delta = this._clock.getDelta();

    // FPS 计算
    this.frameCount++;
    const now = performance.now();
    if (now - this._lastFpsTime >= 1000) {
      this.currentFps = this.frameCount;
      this.fpsHistory.push(this.currentFps);
      if (this.fpsHistory.length > 60) this.fpsHistory.shift();
      this.frameCount = 0;
      this._lastFpsTime = now;
    }

    // 控制器更新
    this.controls.update();

    // AGV 微动效 (呼吸效果)
    const breathe = Math.sin(now * 0.002) * 0.01 + 1;
    this._agvMeshes.forEach(agv => {
      const body = agv.children[0] as THREE.Mesh;
      if (body) body.scale.y = breathe;
    });

    // 后处理渲染
    this.composer.render(delta);
  };

  /**
   * 清理方法 ====================
   */

  clearMap(): void {
    this.mapGroup.clear(true);
    this._nodeMeshes.clear();
    this._edgeMeshes.clear();
  }

  clearAgvs(): void {
    this.agvGroup.clear(true);
    this._agvMeshes.clear();
  }

  clearTrajectories(): void {
    this.trajectoryGroup.clear(true);
    this._trajectoryLines.clear();
  }

  clearHeatmap(): void {
    this.heatmapGroup.clear(true);
  }

  clearAll(): void {
    this.clearMap();
    this.clearAgvs();
    this.clearTrajectories();
    this.clearHeatmap();
  }

  removeAgv(agvId: string): void {
    const mesh = this._agvMeshes.get(agvId);
    if (mesh) {
      this.agvGroup.remove(mesh);
      this._agvMeshes.delete(agvId);
    }
  }

  /**
   * 调整窗口大小
   */
  resize(width: number, height: number): void {
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height);
    this.composer.setSize(width, height);
  }

  /**
   * 获取 DOM 元素
   */
  getDomElement(): HTMLCanvasElement {
    return this.renderer.domElement;
  }

  /**
   * 销毁引擎，释放资源
   */
  dispose(): void {
    this.stopAnimation();
    this.clearAll();
    this.controls.dispose();
    this.renderer.dispose();
    this.composer.dispose();

    // 断开所有引用
    (this as any).scene = null;
    (this as any).camera = null;
    (this as any).renderer = null;
    (this as any).controls = null;
  }

  // Private alias for internal use
  private _lastFpsTime: number = performance.now();
}
