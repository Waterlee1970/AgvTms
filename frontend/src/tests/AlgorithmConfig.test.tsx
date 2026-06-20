/**
 * AlgorithmConfig 页面组件基础测试
 *
 * 测试目标:
 * - 算法配置页面基本渲染
 * - 各算法参数配置区域显示
 * - 保存/重置按钮
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import AlgorithmConfig from '../pages/AlgorithmConfig';

// 使用 vi.hoisted 解决变量提升问题
const mockGetAlgorithmConfig = vi.fn().mockResolvedValue({
  aco: { num_ants: 10, alpha: 1, beta: 2, evaporation_rate: 0.1, iterations: 100, q0: 0.9 },
  sa: { initial_temp: 1000, cooling_rate: 0.95, iterations: 500, min_temp: 0.001 },
  nlp: { solver: 'ipopt', tolerance: 1e-6, max_iter: 1000, verbose: false },
  hybrid: { aco_weight: 0.4, sa_weight: 0.3, nlp_weight: 0.3, strategy: 'adaptive' },
});

vi.mock('../services/api', () => ({
  getAlgorithmConfig: () => mockGetAlgorithmConfig(),
  updateAlgorithmConfig: vi.fn().mockResolvedValue({}),
}));

vi.mock('antd', async () => {
  const actual = await vi.importActual('antd');
  return {
    ...actual,
    message: {
      success: vi.fn(),
      error: vi.fn(),
    },
  };
});

describe('AlgorithmConfig Page', () => {

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders page title', () => {
    render(<AlgorithmConfig />);
    expect(screen.getByText(/算法配置/i)).toBeInTheDocument();
  });

  it('renders algorithm section headers (ACO, SA, NLP, Hybrid)', async () => {
    render(<AlgorithmConfig />);

    // 等待数据加载完成 - 使用 findAllByText 处理多个匹配
    await screen.findAllByText(/蚁群算法|ACO/i, {}, { timeout: 3000 });

    // 验证各算法配置区域存在（使用 getAllByText 因为可能有多个匹配）
    const acoElements = screen.getAllByText(/蚁群算法|ACO/i);
    expect(acoElements.length).toBeGreaterThan(0);
    const saElements = screen.getAllByText(/模拟退火|SA/i);
    expect(saElements.length).toBeGreaterThan(0);
    const nlpElements = screen.getAllByText(/非线性规划|NLP/i);
    expect(nlpElements.length).toBeGreaterThan(0);
    const hybridElements = screen.getAllByText(/混合策略|Hybrid/i);
    expect(hybridElements.length).toBeGreaterThan(0);
  });

  it('renders save button', () => {
    render(<AlgorithmConfig />);
    // 按钮文本可能因实现而异，检查是否存在包含"保存"的元素
    const saveButtons = screen.getAllByText(/保存/);
    expect(saveButtons.length).toBeGreaterThan(0);
  });
});
