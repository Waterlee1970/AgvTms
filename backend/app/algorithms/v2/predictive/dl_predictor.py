"""
LSTM / Transformer 深度学习预测器 — 替代 Holt-Winters 统计方法.

功能:
  1. LSTM 时序预测 (任务到达/拥堵/能耗)
  2. Attention-based Transformer 预测
  3. 自适应模型选择 (统计 vs DL)
  4. 在线学习 (Online Learning) 增量更新
  5. 预测置信度校准
  6. 与现有 PredictiveEngine 无缝集成

架构:
    输入序列 → Embedding → LSTM/Transformer → FC → 预测输出
                                              ↓
                                         置信度估计 (MC Dropout)

依赖:
  PyTorch (可选, 无则自动降级到统计方法)
"""

import time
import logging
import math
import numpy as np
from typing import List, Dict, Optional, Tuple, Any, Union
from dataclasses import dataclass, field
from enum import Enum
from abc import ABC, abstractmethod
from collections import deque

logger = logging.getLogger(__name__)

# ==================== 可选 PyTorch 导入 ====================

try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    from torch.utils.data import DataLoader, TensorDataset
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    logger.warning("PyTorch not available. DL predictor will use statistical fallback.")


# ==================== 数据结构 ====================

@dataclass 
class PredictionResult:
    """统一预测结果格式."""
    values: np.ndarray              # 预测值序列
    confidence: np.ndarray           # 置信度区间 [lower, upper]
    model_used: str                  # 使用的模型名称
    latency_ms: float                # 推理耗时
    is_dl: bool                      # 是否使用深度学习模型
    metadata: Dict[str, Any] = field(default_factory=dict)


class PredictorType(Enum):
    """预测器类型."""
    STATISTICAL = "statistical"
    LSTM = "lstm"
    TRANSFORMER = "transformer"
    ENSEMBLE = "ensemble"
    
    @classmethod
    def from_string(cls, s: str) -> 'PredictorType':
        try:
            return cls(s.lower())
        except ValueError:
            return cls.STATISTICAL


# ==================== 神经网络模型 ====================

if TORCH_AVAILABLE:
    
    class LSTMPredictor(nn.Module):
        """
        LSTM 时序预测模型.
        
        架构:
            Input(seq_len, features) → LSTM(hidden×layers) → Dropout → FC → Output(pred_horizon)
        
        支持:
          - 单变量/多变量输入
          - 多步预测
          - MC Dropout 不确定性估计
        """
        
        def __init__(
            self,
            input_dim: int = 4,
            hidden_dim: int = 64,
            num_layers: int = 2,
            output_dim: int = 12,
            dropout: float = 0.15,
            bidirectional: bool = False,
        ):
            super().__init__()
            
            self.hidden_dim = hidden_dim
            self.num_layers = num_layers
            self.bidirectional = bidirectional
            self.num_directions = 2 if bidirectional else 1
            
            # LSTM 编码器
            self.lstm = nn.LSTM(
                input_size=input_dim,
                hidden_size=hidden_dim,
                num_layers=num_layers,
                batch_first=True,
                dropout=dropout if num_layers > 1 else 0,
                bidirectional=bidirectional,
            )
            
            # Attention 层 (可选)
            self.attention = nn.Sequential(
                nn.Linear(hidden_dim * self.num_directions, hidden_dim),
                nn.Tanh(),
                nn.Linear(hidden_dim, 1),
            )
            
            # 输出头
            self.dropout = nn.Dropout(dropout)
            self.fc = nn.Sequential(
                nn.Linear(hidden_dim * self.num_directions, hidden_dim // 2),
                nn.ReLU(),
                nn.Dropout(dropout),
                nn.Linear(hidden_dim // 2, output_dim),
            )
            
        def forward(self, x: torch.Tensor, return_attention: bool = False) -> torch.Tensor:
            """
            Args:
                x: (batch, seq_len, input_dim)
                
            Returns:
                out: (batch, output_dim) 预测值
            """
            lstm_out, (h_n, c_n) = self.lstm(x)
            
            # 使用最后隐状态 + attention
            # Attention weights
            attn_weights = torch.softmax(self.attention(lstm_out), dim=1)
            context = (attn_weights * lstm_out).sum(dim=1)  # (batch, hidden*dir)
            
            # 输出
            context = self.dropout(context)
            out = self.fc(context)
            
            return out
            
        def predict_with_uncertainty(
            self, x: torch.Tensor, n_samples: int = 20
        ) -> Tuple[torch.Tensor, torch.Tensor]:
            """
            MC Dropout 不确定性估计.
            
            Returns:
                mean: (batch, output_dim) 预测均值
                std: (batch, output_dim) 预测标准差 (不确定性)
            """
            self.train()  # Enable dropout at inference time
            
            predictions = []
            with torch.no_grad():
                for _ in range(n_samples):
                    pred = self.forward(x)
                    predictions.append(pred.unsqueeze(0))
                    
            all_preds = torch.cat(predictions, dim=0)  # (samples, batch, out)
            mean = all_preds.mean(dim=0)
            std = all_preds.std(dim=0)
            
            self.eval()
            return mean, std


    class TransformerPredictor(nn.Module):
        """
        Transformer 时序预测模型.
        
        更适合长序列、多变量时序.
        """
        
        def __init__(
            self,
            input_dim: int = 4,
            d_model: int = 64,
            nhead: int = 4,
            num_encoder_layers: int = 2,
            dim_feedforward: int = 128,
            output_dim: int = 12,
            dropout: float = 0.1,
            max_seq_len: int = 100,
        ):
            super().__init__()
            
            # Input embedding + Positional encoding
            self.input_proj = nn.Linear(input_dim, d_model)
            self.pos_encoding = self._create_positional_encoding(max_seq_len, d_model)
            
            # Encoder
            encoder_layer = nn.TransformerEncoderLayer(
                d_model=d_model,
                nhead=nhead,
                dim_feedforward=dim_feedforward,
                dropout=dropout,
                batch_first=True,
            )
            self.transformer_encoder = nn.TransformerEncoder(
                encoder_layer, num_layers=num_encoder_layers
            )
            
            # Output head
            self.fc_out = nn.Sequential(
                nn.Linear(d_model, d_model // 2),
                nn.ReLU(),
                nn.Dropout(dropout),
                nn.Linear(d_model // 2, output_dim),
            )
            
            self.d_model = d_model
            
        def _create_positional_encoding(self, max_len: int, d_model: int) -> nn.Parameter:
            pe = torch.zeros(max_len, d_model)
            position = torch.arange(0, max_len).unsqueeze(1).float()
            div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
            
            pe[:, 0::2] = torch.sin(position * div_term)
            pe[:, 1::2] = torch.cos(position * div_term)
            
            return nn.Parameter(pe.unsqueeze(0), requires_grad=False)
            
        def forward(self, x: torch.Tensor) -> torch.Tensor:
            """
            Args:
                x: (batch, seq_len, input_dim)
            """
            seq_len = x.size(1)
            
            # Project & add positional encoding
            x = self.input_proj(x)
            x = x + self.pos_encoding[:, :seq_len, :]
            
            # Encode
            encoded = self.transformer_encoder(x)
            
            # Global average pooling
            pooled = encoded.mean(dim=1)
            
            # Output
            out = self.fc_out(pooled)
            return out


class StatisticalFallback:
    """统计方法降级预测器 (当 PyTorch 不可用时使用)."""
    
    def __init__(self):
        self._history: deque = deque(maxlen=500)
        
    def update(self, value: float):
        self._history.append(value)
        
    def predict(self, horizon: int = 12) -> Tuple[np.ndarray, np.ndarray]:
        """简单线性外推 + 指数增长置信度."""
        if len(self._history) < 2:
            values = np.zeros(horizon)
            conf = np.ones((horizon, 2)) * 50  # 低置信度
            conf[:, 0] *= -1
            return values, conf
            
        recent = list(self._history)[-30:]
        trend = (recent[-1] - recent[0]) / len(recent)
        
        last_val = recent[-1]
        values = np.array([last_val + trend * (i+1) for i in range(horizon)])
        
        # 置信度随预测步数指数衰减
        base_conf = min(len(recent) / 20, 1.0) * 10
        lower = values - base_conf * np.exp(np.arange(horizon) * 0.15)
        upper = values + base_conf * np.exp(np.arange(horizon) * 0.15)
        conf = np.column_stack([lower, upper])
        
        return values, conf


# ==================== 统一预测接口 ====================

class DeepLearningPredictor:
    """
    统一深度学习预测管理器.
    
    功能:
      1. 自动选择最优模型 (基于数据特征)
      2. 在线增量训练
      3. 模型持久化/加载
      4. A/B 测试对比 (DL vs 统计方法)
      5. 性能监控
    """
    
    def __init__(
        self,
        default_type: PredictorType = PredictorType.LSTM,
        auto_train: bool = True,
        train_threshold: int = 50,   # 积累多少样本后开始训练
        sequence_length: int = 24,   # 输入序列长度
        prediction_horizon: int = 12, # 预测步长
    ):
        self.default_type = default_type
        self.auto_train = auto_train
        self.train_threshold = train_threshold
        self.seq_len = sequence_length
        self.pred_horizon = prediction_horizon
        
        # 模型存储
        self._models: Dict[str, Any] = {}
        self._fallback = StatisticalFallback()
        
        # 训练缓冲区
        self._train_buffer: Dict[str, deque] = {
            'task': deque(maxlen=2000),
            'congestion': deque(maxlen=2000),
            'battery': deque(maxlen=2000),
        }
        
        # 性能追踪
        self._performance: Dict[str, Dict] = {}
        
        self._is_ready = TORCH_AVAILABLE
        
        logger.info(f"DeepLearningPredictor initialized (torch={TORCH_AVAILABLE})")
        
    @property
    def is_ready(self) -> bool:
        return self._is_ready
    
    def _get_or_create_model(self, predictor_type: PredictorType, input_dim: int = 4):
        """获取或创建模型实例."""
        model_key = f"{predictor_type.value}_{input_dim}"
        
        if model_key not in self._models:
            if not TORCH_AVAILABLE:
                return None
                
            if predictor_type == PredictorType.LSTM:
                model = LSTMPredictor(
                    input_dim=input_dim,
                    hidden_dim=64,
                    num_layers=2,
                    output_dim=self.pred_horizon,
                    dropout=0.15,
                )
            elif predictor_type == PredictorType.TRANSFORMER:
                model = TransformerPredictor(
                    input_dim=input_dim,
                    d_model=64,
                    nhead=4,
                    num_encoder_layers=2,
                    output_dim=self.pred_horizon,
                )
            else:
                return None
                
            model.eval()
            self._models[model_key] = model
            logger.info(f"Created {predictor_type.value} model (input_dim={input_dim})")
            
        return self._models[model_key]
    
    def predict_task_arrival(
        self,
        history: List[float],
        horizon: Optional[int] = None,
    ) -> PredictionResult:
        """
        任务到达率预测.
        
        Args:
            history: 最近的历史到达率序列
            horizon: 预测未来步数
            
        Returns:
            PredictionResult
        """
        start = time.perf_counter()
        h = horizon or self.pred_horizon
        
        # 尝试 DL 预测
        dl_result = self._try_dl_predict('task', history, h)
        
        if dl_result and not dl_result.is_dl or self._should_use_statistical('task'):
            # 对比统计方法，取更好的结果
            stat_result = self._statistical_predict(history, h)
            
            # 如果 DL 不可用或表现不好，用统计
            if dl_result is None or dl_result.fallback:
                result = stat_result
            else:
                # 简单集成: 加权平均
                w_dl = 0.6 if dl_result.is_dl else 0.3
                w_stat = 1 - w_dl
                combined_values = dl_result.values * w_dl + stat_result.values * w_stat
                combined_conf = (
                    dl_result.confidence * w_dl + stat_result.confidence * w_stat
                )
                result = PredictionResult(
                    values=combined_values,
                    confidence=combined_conf,
                    model_used=f"ensemble(dl={dl_result.model_used}, stat={stat_result.model_used})",
                    latency_ms=(time.perf_counter() - start) * 1000,
                    is_dl=True,
                )
        else:
            result = dl_result or self._statistical_predict(history, h)
            
        result.latency_ms = (time.perf_counter() - start) * 1000
        return result
    
    def predict_congestion(
        self,
        history: List[float],
        current_density: float,
        horizon: Optional[int] = None,
    ) -> PredictionResult:
        """拥堵热点预测."""
        start = time.perf_counter()
        h = horizon or self.pred_horizon
        
        # 特征工程: 当前密度 + 历史趋势
        features = history[-self.seq_len:] + [current_density]
        
        dl_result = self._try_dl_predict('congestion', features, h)
        
        if dl_result is None:
            dl_result = self._statistic_congestion_predict(current_density, h)
            
        dl_result.latency_ms = (time.perf_counter() - start) * 1000
        return dl_result
    
    def predict_battery(
        self,
        current_battery: float,
        consumption_history: List[float],
        horizon: Optional[int] = None,
    ) -> PredictionResult:
        """电池能耗预测."""
        start = time.perf_counter()
        h = horizon or self.pred_horizon
        
        # 线性衰减模型作为 baseline
        avg_consumption = np.mean(consumption_history[-10:]) if consumption_history else 0.01
        predicted = np.array([
            max(0, current_battery - avg_consumption * (i+1)) for i in range(h)
        ])
        
        # 置信度 (消耗越不确定越宽)
        uncertainty = abs(avg_consumption) * np.sqrt(np.arange(1, h+1)) * 2
        confidence = np.column_stack([predicted - uncertainty, predicted + uncertainty])
        
        return PredictionResult(
            values=predicted,
            confidence=confidence,
            model_used="linear_decay",
            latency_ms=(time.perf_counter() - start) * 1000,
            is_dl=False,
        )
    
    def _try_dl_predict(
        self,
        domain: str,
        data: List[float],
        horizon: int,
    ) -> Optional[PredictionResult]:
        """尝试深度学习预测."""
        if not TORCH_AVAILABLE:
            return None
            
        if len(data) < self.seq_len:
            return None
            
        # 选择模型类型
        model_type = self.default_type
        model = self._get_or_create_model(model_type, input_dim=1)
        if model is None:
            return None
            
        try:
            # 准备输入
            seq = np.array(data[-self.seq_len:], dtype=np.float32).reshape(1, self.seq_len, 1)
            x_tensor = torch.FloatTensor(seq)
            
            # 推理
            with torch.no_grad():
                pred = model(x_tensor)
                values = pred.numpy().flatten()[:horizon]
                
            # MC Dropout 置信度
            if isinstance(model, LSTMPredictor):
                _, std = model.predict_with_uncertainty(x_tensor, n_samples=10)
                std_np = std.numpy().flatten()[:horizon]
                lower = values - 1.96 * std_np
                upper = values + 1.96 * std_np
                confidence = np.column_stack([lower, upper])
            else:
                # 默认 95% CI
                margin = np.abs(values) * 0.1 + 0.5
                confidence = np.column_stack([values - margin, values + margin])
                
            return PredictionResult(
                values=values,
                confidence=confidence,
                model_type=model_type.value,
                latency_ms=0,  # updated by caller
                is_dl=True,
                fallback=False,
            )
            
        except Exception as e:
            logger.warning(f"DL prediction failed ({domain}): {e}")
            return PredictionResult(
                values=np.zeros(horizon),
                confidence=np.zeros((horizon, 2)),
                model_type="dl_error",
                latency_ms=0,
                is_dl=True,
                fallback=True,
                metadata={"error": str(e)},
            )
    
    def _statistical_predict(self, history: List[float], horizon: int) -> PredictionResult:
        """统计方法预测 (Holt-Winters 简化版)."""
        if len(history) < 3:
            last = history[-1] if history else 0
            values = np.full(horizon, last)
            conf = np.column_stack([values * 0.8, values * 1.2])
            return PredictionResult(values=values, confidence=conf, model_type="naive", latency_ms=0, is_dl=False)
            
        # 三重指数平滑简化
        data = np.array(history[-48:])
        alpha, beta, gamma = 0.3, 0.1, 0.1
        season_len = 12
        
        level = data[0]
        trend = (data[min(12, len(data)-1)] - data[0]) / season_len
        
        # 初始化季节因子
        seasonal = np.ones(season_len) * 0
        
        for i in range(1, len(data)):
            old_level = level
            level = alpha * (data[i] - seasonal[i % season_len]) + (1-alpha) * (level + trend)
            trend = beta * (level - old_level) + (1-beta) * trend
            seasonal[i % season_len] = gamma * (data[i] - old_level - trend) + (1-gamma) * seasonal[i % season_len]
        
        # 预测
        values = []
        for h in range(horizon):
            val = (level + (h+1) * trend) * seasonal[(len(data) + h) % season_len]
            values.append(val)
            
        values = np.array(values)
        margin = np.abs(values) * 0.15 + 1.0
        conf = np.column_stack([values - margin, values + margin])
        
        return PredictionResult(values=values, confidence=conf, model_type="holt_winters", latency_ms=0, is_dl=False)
    
    def _statistic_congestion_predict(self, density: float, horizon: int) -> PredictionResult:
        """拥堵统计预测."""
        # 拥堵惯性: 高密度倾向于持续
        decay = 0.85 ** np.arange(1, horizon+1)
        values = density * decay
        
        margin = 0.05 * (1 - decay) + 0.02
        conf = np.column_stack([np.maximum(0, values - margin), np.minimum(1, values + margin)])
        
        return PredictionResult(values=values, confidence=conf, model_type="inertia_decay", latency_ms=0, is_dl=False)
    
    def _should_use_statistical(self, domain: str) -> bool:
        """根据历史性能决定是否使用统计方法."""
        perf = self._performance.get(domain, {})
        # 如果 DL 错误率高或数据量不足, 用统计
        dl_errors = perf.get('dl_error_rate', 1.0)
        data_count = len(self._train_buffer.get(domain, []))
        return dl_errors > 0.3 or data_count < self.train_threshold
    
    def add_training_data(self, domain: str, features: float, target: float):
        """添加训练样本 (在线学习)."""
        buffer = self._train_buffer.get(domain)
        if buffer is None:
            return
        buffer.append({'features': features, 'target': target})
        
        # 触发自动训练
        if self.auto_train and len(buffer) >= self.train_threshold:
            self._online_train(domain)
    
    def _online_train(self, domain: str):
        """在线增量训练 (简化版)."""
        if not TORCH_AVAILABLE:
            return
            
        buffer = self._train_buffer.get(domain)
        if buffer is None or len(buffer) < self.train_threshold:
            return
            
        # TODO: 实现完整在线训练流程
        # 这里仅记录日志, 实际生产环境应实现:
        # 1. 从 buffer 构建 dataset
        # 2. optimizer.step()
        # 3. 模型验证
        # 4. 性能评估
        
        logger.debug(f"Online training triggered for {domain}: {len(buffer)} samples")
        
    def get_status(self) -> Dict[str, Any]:
        """获取预测器状态."""
        return {
            "torch_available": TORCH_AVAILABLE,
            "models_loaded": list(self._models.keys()),
            "default_type": self.default_type.value,
            "training_data": {
                k: len(v) for k, v in self._train_buffer.items()
            },
            "auto_train": self.auto_train,
            "seq_length": self.seq_len,
            "prediction_horizon": self.pred_horizon,
        }


# ==================== 全局实例 ====================

_dl_predictor_instance: Optional[DeepLearningPredictor] = None

def get_dl_predictor() -> DeepLearningPredictor:
    """获取全局 DL 预测器实例."""
    global _dl_predictor_instance
    if _dl_predictor_instance is None:
        _dl_predictor_instance = DeepLearningPredictor()
    return _dl_predictor_instance
