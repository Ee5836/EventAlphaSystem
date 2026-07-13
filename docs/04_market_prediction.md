# ④ 行情数据 + 走势预测 (Market Data & Prediction)

> 核心功能：A股K线+实时行情 + 技术指标 + 多维LLM预测 + 统计事件相关度 + 预测区间图表叠加 + 热点事件自动推荐股票
> 更新: 2026-07-03 | 状态: ✅ 已完成 (v3 统计事件相关度融合)

---

## 1. 概述

- **MarketData Agent**: AKShare 数据拉取 + 技术指标计算 + DB缓存 + 重试容错 + 合成数据兜底
- **Prediction Agent**: 多维LLM融合预测 + 统计事件相关度(55%LLM+45%公式) + 预测结果图表叠加
- **热门股票推荐**: 自动分析当前S/A级事件 → 行业加权排名 → 股票映射 → 全图表展示
- **图表渲染**: ECharts K线 + MA均线 + 预测目标线 + 预测区间色块 + 投影线 + ResizeObserver自适应
- **事件相关度** (v3): event_relevance(语义50%+标签25%+时效15%+等级10%) 替代 LLM-only 评分

## 2. 数据获取架构 (三层容错)

```
请求价格数据
    │
    ├─ 第1层: DB缓存 (PriceHistory表)
    │   └─ >=30条 → 直接返回 (毫秒级)
    │
    ├─ 第2层: AKShare实时拉取 (含3次重试 × 递增延迟)
    │   └─ 成功 → 缓存到DB → 返回
    │
    └─ 第3层: 合成数据生成
        └─ generate_synthetic_history() → 返回模拟K线
```

### 数据源
| 功能 | AKShare函数 | 说明 |
|------|-------------|------|
| 个股信息 | `stock_individual_info_em()` | 名称/行业/上市日期，含3次重试 |
| K线历史 | `stock_zh_a_hist(period)` | 日/周/月, 前复权, DB缓存优先 |
| 实时快照 | `stock_zh_a_spot_em()` | 最新价/涨跌幅/成交额 |
| 指数行情 | `stock_zh_index_daily()` | 上证/深证/创业板 |
| 行业板块 | `stock_board_industry_name_em()` | 涨跌幅/涨跌家数 |

### 容错机制
| 机制 | 配置 | 说明 |
|------|------|------|
| 连接重试 | 3次, 延迟1.5/3/4.5秒 | 自动识别 Connection/Remote/Timeout 错误 |
| DB回退 | `_fallback_db()` | AKShare全部失败后返回已有缓存（不限条数） |
| 合成数据 | `generate_synthetic_history()` | 基于真实价格区间生成随机游走K线 |

## 3. Prediction Agent (`agents/prediction.py`)

### 预测维度与权重
| 维度 | 权重 | 来源 | 说明 |
|------|------|------|------|
| **技术面** | 40% | MA/MACD/RSI | 趋势/金叉死叉/超买超卖 |
| **事件驱动** | 30% | LLM分析 | 关联事件影响评分 (-1~+1) |
| **资金面** | 15% | AKShare | 资金流向 |
| **情绪面** | 10% | LLM分析 | 新闻情绪极性 |
| **大盘环境** | 5% | 指数行情 | 市场风险偏好 |

### 预测流程
```
输入 symbol + time_horizon (+ event_context可选)
    ↓
① 同步个股信息 (StockInfo, 含重试)
    ↓
② 拉取K线数据 (DB缓存 → AKShare → 合成数据)
    ↓
③ 计算技术指标 (MA+MACD+RSI)
    ↓
④ LLM事件影响评分 (可选)
    ↓
⑤ LLM多维融合预测
    ↓
输出 PredictionResult (含ECharts图表数据)
```

### 输出结构
```python
@dataclass
class PredictionResult:
    symbol: str          # 股票代码
    direction: str       # bullish / bearish / neutral
    confidence: float    # 0.0-1.0
    time_horizon: str    # T+1 / T+3 / T+7
    target_low: float    # 预测低价
    target_high: float   # 预测高价
    key_factors: list    # 各维度贡献度
    risk_flags: list     # 风险提示
    technical_signals: list  # 技术信号详情
    event_impact_score: float
    reasoning_chain: list
    chart_data: dict     # ECharts K线 + MA + 信号数据
```

## 4. 热门股票自动推荐

### 推荐流程
```
页面加载
    ↓
GET /api/v1/prediction/hot
    ↓
① 查询最近7天 S/A级 EventCard (含 affected_industries)
    ↓
② 行业加权排名 (S级×3, A级×1)
    ↓
③ 行业→股票映射 (app/utils/industry_stocks.py, 80+行业, 200+股票)
    ↓
④ 返回 Top 8 股票 + 关联事件信息
    ↓
前端并行加载所有股票预测 (每次2只, 避免限流)
    ↓
单列全宽展示所有图表卡片
```

### 行业→股票映射 (`app/utils/industry_stocks.py`)
- 覆盖 82 个中文行业 + 60+ 别名 + 67 个关键词兜底 (4层匹配: 精确→别名→子串→关键词) → 200+ A股代表股票
- 支持别名解析 + TikTok关键词兜底 Tier 4 fallback（如 "智慧城市"→匹配"人工智能"板块）
- 每个行业 2-5 只高流动性代表股
- 去重：同一股票不重复出现在多个行业

## 5. 图表预测叠加

### K线图增强元素
| 元素 | ECharts实现 | 说明 |
|------|-------------|------|
| 🕯️ K线蜡烛 | `candlestick` series | 红涨绿跌 (中国习惯) |
| 📈 MA均线 | `line` series × 3 | MA5(黄)/MA20(紫)/MA60(青) |
| 🎯 目标价线 | `markLine` (yAxis) | 红色虚线(高)/绿色虚线(低) |
| 📐 预测区间 | `markArea` | 半透明色块，从末K线延至预测日 |
| 📉 投影线 | `line` series (虚线) | 黄色虚线从最新收盘价→目标中点 |
| 📅 未来日期 | xAxis category | 橙色加粗，自动计算交易日 |

### 空数据安全
- ECharts candlestick 使用 `'-'` 占位符（ECharts官方空数据标记）
- OHLC逐条四值校验 (open/close/low/high 全部 > 0)
- `setOption` 包裹 try/catch，渲染失败显示错误提示

### 图表自适应 (`ChartResizeManager`)
- **ResizeObserver** 替代 `window.resize`：精确监听容器尺寸变化，浏览器缩放(Ctrl+/-)时图表同步缩放
- **100ms 防抖**：避免拖拽/快速缩放时过度重绘
- **生命周期管理**：图表销毁时自动断开 Observer，防止内存泄漏
- **全局共享**：`app.js` 中的 `ChartResizeManager` 同时服务于 prediction / briefing / timeline 三个页面的 ECharts 实例

## 6. 预测页面 (`/prediction`)

### 页面行为
- **自动加载**: 打开页面 → 分析当前热点事件 → 匹配股票 → 自动显示全部图表
- **单列全宽**: 每只股票图表卡片占满宽度，K线图高度320px
- **行业筛选**: 点击行业标签筛选相关股票，标签高亮正确切换
- **周期切换**: T+1/T+3/T+7 切换，已缓存数据瞬间渲染，仅请求新周期数据
- **搜索备用**: 搜索栏保留，支持手动输入任意股票代码

### 前端缓存架构 (2026-06-28 新增)
- **内存缓存** (`_predictionDataCache`): key=`"symbol_horizon"`，避免同页面内重复 API 请求
- **sessionStorage 持久化** (10分钟TTL): 页面导航（如 走势预测→首页→走势预测）后自动恢复，**0次API请求**瞬间渲染全部图表
- **预渲染优化**: 缓存命中时同步渲染（Phase 1），无 spinner 闪烁；未缓存股票全并发请求（Phase 2，v2 改为全部同时发射 + 15s AbortController 超时保护），单只完成即时渲染并持久化缓存
- **图表生命周期**: `_chartInstances` 跟踪所有 ECharts 实例，网格清除前批量释放 Observer + dispose

### 图表卡片内容
- K线图 (含MA均线 + 预测目标线 + 预测区间)
- 方向标签 (看涨/看跌/震荡) + 置信度
- 目标价格区间
- 多维度贡献进度条
- 风险提示标签

## 7. API端点

```
GET  /api/v1/prediction/hot                       → 热门股票推荐 (基于当前事件)
GET  /api/v1/prediction/stocks/search?q=           → 股票搜索
GET  /api/v1/prediction/stocks/<symbol>/history    → K线历史
GET  /api/v1/prediction/stocks/<symbol>/snapshot   → 实时快照
GET  /api/v1/prediction/predict/<symbol>?horizon=  → 运行预测
GET  /api/v1/prediction/sectors                    → 行业板块表现
GET  /api/v1/prediction/index/<code>               → 指数数据
```

## 8. 关键文件

| 文件 | 说明 |
|------|------|
| `agents/market_data.py` | AKShare数据 + 技术指标 + 容错重试 + DB缓存 + 合成数据 |
| `agents/prediction.py` | 多维LLM融合预测 + 合成数据兜底 + 股票基础价映射 |
| `app/routes/web_prediction.py` | Web + API路由 (含 `/hot` 推荐端点) |
| `app/templates/prediction.html` | 单列全宽图表卡片 + 热门推荐 + 行业筛选 + 预测叠加 + sessionStorage缓存持久化 + ResizeObserver自适应 |
| `app/utils/industry_stocks.py` | 80+行业 → 200+股票映射库 + 别名解析 |
| `app/static/lib/` | Bootstrap 5 + ECharts 5.5 本地化 (消除CDN追踪警告) |
| `models/market.py` | StockInfo / PriceSnapshot / PriceHistory |
| `models/card.py` | EventCard (含 affected_industries, level) |

## 9. 更新日志

### 2026-07-03 — v2 并发加载 + 行业匹配增强

- **全并发加载**: Phase 2 从 2只/批串行 改为全部同时发射，总加载时间从 12-20s 降至 3-5s
- **实时渲染**: 每只股票请求完成即渲染图表 + 写缓存，不再等待批次
- **超时保护**: 单请求 15s AbortController 超时，避免永久等待
- **4层行业匹配**: 新增 Tier 4 关键词兜底（67个关键词），匹配率从 88.9% 提升至 ~100%
- **行业扩展**: 新增 7 个行业条目 (银行业/债券/资产管理/私募股权/定制家居/液压机制造/审计服务业) + 10+ 别名
- **空筛选提示**: 筛选无匹配行业时显示友好提示，不再空白
- **图表宽度约束**: CSS overflow:hidden + max-width:100% 确保图表不超出卡片容器
- **后置数据保障**: predict() 返回前检查 chart_data，<5条K线时强制生成合成数据
