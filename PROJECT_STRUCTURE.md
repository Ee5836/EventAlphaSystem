# BubbleEvent — 项目构成与功能实现详解

> 热点事件驱动投资 Agent 系统 | 生成日期：2026-07-13

---

## 一、项目总览

BubbleEvent 是一个**中文金融事件智能分析平台**，自动从多信息源采集财经新闻，通过 LLM 提取/分类/验证/评分事件，生成结构化事件卡片、因果时间线网络、每日投资简报，支持 AI 研究对话与股票走势预测。

| 维度 | 数据 |
|------|------|
| **语言** | Python 3.x |
| **Web 框架** | Flask + Jinja2 + Bootstrap 5 |
| **数据库** | SQLAlchemy + SQLite (dev) |
| **LLM** | DeepSeek v4-flash (主) / 通义千问 / 智谱 / Ollama |
| **嵌入模型** | BAAI/bge-small-zh-v1.5 (本地离线, normalize_embeddings=True) |
| **行情** | AKShare |
| **图表** | ECharts + vis.js |
| **任务队列** | Celery + Redis |
| **规模** | 73+ Python 文件 / 22+ 模板 / 14 数据模型 / 11 Agent / ~64 路由 |

---

## 二、系统架构

```
信息源 (Sina/CCTV/期货/用户 RSS/API)
    │
 ScoutAgent         ← 并行采集 + URL 去重 + poll_interval 智能跳过
    │
 ExtractionAgent    ← LLM 批量事件提取 (5篇/批, 8并发)
    │
 ClusteringAgent    ← 4维语义聚类去重 (嵌入50%+NPMI25%+时间15%+Jaccard10%)
    │
 VerificationAgent  ← 5维可信度验证 (规则化)
    │
 ScoringAgent       ← 5维重要性评分 (LLM + 统计修正, Wilson调整)
    │
 CardGeneration     ← LLM 生成展示卡片 (S/A/B/C/D 分级)
    │
    ├── TimelineBuilderAgent    ← 因果网络构建 + 2策略边发现 + 预测扩展
    ├── DailyBriefingAgent      ← 2阶段每日6段投资简报
    └── ResearchAssistantAgent ← 意图驱动7路并行检索 AI 对话
    │
 Flask Web UI (SPA 片段路由 + SSR 完整回退)
    │
 Celery Beat (每小时采集 / 4h 兜底 / 每日 08:30 CST 简报)
```

---

## 三、启动命令

```bash
cd D:\PyCharm_work\EventAgent
python run.py                        # Flask 端口5000
python -m flask run --port 5050      # 指定端口
curl -X POST localhost:5000/api/v1/pipeline/trigger  # 触发管道
```

### 功能开关 (.env)

```env
ENABLE_AI_ASSISTANT=true       # AI 助手 (false = 完全关闭)
ENABLE_ASSISTANT_WIDGET=true   # 浮动小助手 (false = 仅关闭FAB, 保留首页对话)
```

---

## 四、顶层文件

| 文件 | 说明 |
|------|------|
| `run.py` | 应用入口。强制 `HF_HUB_OFFLINE=1` `TRANSFORMERS_OFFLINE=1`，调用 `create_app()` 启动 Flask :5000 |
| `config.py` | 集中配置中心。定义 `Config` 基类 (所有设置从环境变量读取)，`DevelopmentConfig`/`ProductionConfig`/`TestingConfig` 子类。包括: LLM/Celery/来源/分页/功能开关/集中日志 dictConfig |
| `requirements.txt` | Python 依赖清单 (Flask 3.1, SQLAlchemy, Celery, openai, sentence-transformers, akshare, playwright, pyecharts...) |
| `.env` | 运行时环境变量 |
| `.env.example` | 环境变量模板，含完整注释 |
| `.flaskenv` | Flask 环境配置 |
| `.gitignore` | Git 忽略规则 |
| `PROJECT_STRUCTURE.md` | 本文档 |

---

## 五、数据模型 (models/ — 14个SQLAlchemy模型)

### 事件管线相关

| 模型 | 表名 | 核心字段 | 说明 |
|------|------|----------|------|
| `NewsSource` | `news_sources` | name, display_name, base_url, source_type, credibility, is_active, is_system, poll_interval, tags_json, config_json, last_fetch_* | 信息源配置。is_system=True 受保护不可删除 |
| `RawArticle` | `raw_articles` | source_id, url (UNIQUE), title, content, summary, content_hash, processed | 原始采集文章。URL去重，processed标记提取状态 |
| `Event` | `events` | cluster_id, title, event_type (14种类型), event_category, entities_json, location, effective_date, affected_industries_json, confidence, status (状态机: raw→clustered→verified→scored→published→archived) | 结构化事件 |
| `EventCluster` | `event_clusters` | canonical_title, description, merged_event_ids, similarity_score | 事件聚合簇 |
| `VerificationResult` | `verification_results` | event_id (UNIQUE), credibility_score, verification_status, source_grade_score, cross_source_score, official_confirm_score, time_consistency_score, historical_accuracy_score, evidence_chain_json, flags_json | 5维可信度验证 |
| `EventScore` | `event_scores` | event_id (UNIQUE), total_score, market_relevance_score(30%), impact_scope_score(25%), impact_depth_score(25%), interpretability_score(10%), timeliness_score(10%), level (S/A/B/C/D), rationale_json | 5维重要性评分 |
| `EventCard` | `event_cards` | event_id (UNIQUE), title, summary, level, credibility, credibility_label, affected_industries, event_type, key_entities, source_summary, risk_flags_json | 最终展示卡片 |

### AI 对话相关

| 模型 | 表名 | 核心字段 | 说明 |
|------|------|----------|------|
| `ChatSession` | `chat_sessions` | title, summary | 对话会话 |
| `ChatMessage` | `chat_messages` | session_id, role, content, reasoning_chain_json, tool_calls_json, sources_json | 对话消息 |
| `ResearchNote` | `research_notes` | session_id, title, content, tags_json | 研究笔记 |

### 行情相关

| 模型 | 表名 | 核心字段 | 说明 |
|------|------|----------|------|
| `StockInfo` | `stock_info` | symbol (PK), name, market (SH/SZ/HK/US), industry, list_date | 股票基本信息 |
| `PriceSnapshot` | `price_snapshots` | symbol (PK), latest_price, change_pct, volume, turnover, high, low, open, pre_close | 实时价格快照 (5分钟缓存) |
| `PriceHistory` | `price_history` | symbol, date, open, high, low, close, volume, period | K线历史 OHLCV (30行缓存阈值) |

### 简报与时间线

| 模型 | 表名 | 核心字段 | 说明 |
|------|------|----------|------|
| `DailyBriefing` | `daily_briefings` | date (UNIQUE), title, executive_summary, market_snapshot_json, top_events_json, event_stats_json, prediction_summary_json, sector_heatmap_json, key_numbers_json, risk_alert_json, full_report_md | 6段结构化每日投资报告 |
| `TimelineNode` | `timeline_nodes` | node_type (root_event/derived_event/prediction/market_reaction/verification), event_id, title, description, timestamp, status, confidence, tags_json, metadata_json, expires_at | 因果时间线节点 |
| `CausalEdge` | `causal_edges` | source_node_id, target_node_id, relation_type (causes/influences/correlates/contradicts), strength, logic_chain, verified (bool/None), verified_at, created_by | 有向因果关系边 |
| `TimelineSnapshot` | `timeline_snapshots` | date, event_count, edge_count, graph_json | 图谱定期快照 |

所有模型均包含 `to_dict()` 序列化方法。

---

## 六、核心管道 — 6阶段事件处理 (pipeline/)

### 架构

`PipelineOrchestrator` 协调6个Agent按顺序流转，状态机驱动，增量幂等处理。返回 `PipelineResult` dataclass (success, stage_results, errors, metadata)。

### 两个入口方法

**`run_full_pipeline(force_scout, fast_mode)`** — 完整7阶段管道:

| 阶段 | Agent | 输入状态 | 输出状态 | 并发 |
|------|-------|----------|----------|------|
| 1 | ScoutAgent | — | raw_article | 并行采集 |
| 2 | ExtractionAgent | processed=False | raw | 5篇/批×8并发 |
| 3 | ClusteringAgent | raw | clustered | 嵌入批处理 |
| 4 | VerificationAgent | clustered | verified | 顺序 |
| 5 | ScoringAgent | verified | scored | 每个事件并发 |
| 6 | CardGenerationAgent | scored | published | 每个事件并发 |
| 7 | TimelineBuilderAgent | published | — | 仅 fast_mode=False |

**`run_processing_only()`** — 跳过采集阶段，仅处理已存在的未处理文章。

### 错误处理

- 每阶段错误**不阻塞**后续阶段
- 第7阶段 (时间线) 有单独 try/except，失败标记为 "non-blocking"
- 最终 `success = not has_errors and total_events >= 0`

### 性能计时

每阶段独立计时 (time.time() 差值，精确到0.1秒)。所有阶段耗时存入 `metadata.stage_times_s`，总耗时存入 `metadata.total_time_s`。

### 第7阶段详解 (非fast_mode)

1. `auto_build_from_events()` — 从 EventCards 自动建图
2. 对 top-3 root/derived 节点调用 `extend_predictions()` (最多5个预测节点)
3. `cleanup_expired_nodes(older_than_days=90)` — 清理超期节点

---

## 七、Agent 实现详解 (agents/ — 11个Agent)

### 基础设施

**`agents/base.py`**: `BaseAgent` 提供 `AgentResult` dataclass (success, output, errors, metadata) 和 `_get_llm()` 工厂方法。Config 自动从 Flask current_app 或环境变量解析。

**`utils/concurrent.py`**: `run_concurrently(items, worker_fn, max_workers)` — 核心并发工具。使用 ThreadPoolExecutor (非 asyncio/ProcessPool，因为瓶颈是 I/O 而非 CPU)，自动在每个工作线程中推送 Flask app context。每项错误隔离 — 一个失败不中止其他。

---

### 7.1 ScoutAgent — 信息采集 (`agents/scout.py`)

**采集流程**:
1. `_get_active_sources()` 查询 `NewsSource.is_active=True`，回退到 `NEWS_SOURCES` 配置或注册表
2. 连接器解析: 先查注册表 (`get_connector`)，回退到 `GenericConnector` (用于用户自定义源)
3. 并行采集: `run_concurrently` 线程池，max_workers = min(源数量, LLM_MAX_CONCURRENCY)

**poll_interval 机制**:
```python
elapsed = (now - last_fetch).total_seconds()
interval = source_rec.poll_interval or 3600  # 默认1小时
if elapsed < interval and not force:
    跳过此源, 记录剩余秒数
```
- force=True 强制跳过检查
- 被跳过的源日志格式: `"src_name(1234s left)"`

**去重**: 对每篇文章检查 `RawArticle.query.filter_by(url=article["url"]).first()`。URL已存在则跳过。content_hash 由 `sha256(url.encode()).hexdigest()` 生成。

**源状态更新**: 每次采集后更新 `last_fetch_at`, `last_fetch_status`, `last_fetch_count`。

---

### 7.2 ExtractionAgent — 事件提取 (`agents/extraction.py`)

**输入**: `RawArticle` 中 `processed=False` 的文章 (最多100篇)

**批量策略**:
- `BATCH_SIZE = 5` 篇/LLM调用
- 每篇文章截断: title 前200字符 + content/summary 前400字符
- 批量并发处理: `run_concurrently` + `max_workers=LLM_MAX_CONCURRENCY`

**LLM 提示词**: `EXTRACTION_SYSTEM_PROMPT` 要求返回 JSON 数组，每个对象包含:
- `title` (≤100字符), `event_type` (14种枚举), `event_category` (中文标签)
- `entities` (实体列表), `location`, `effective_date` (YYYY-MM-DD或null)
- `affected_industries` (行业列表), `confidence` (0.0-1.0)
- `is_investment_relevant` (bool, 非投资相关则跳过)

**后处理**:
1. 过滤 `is_investment_relevant=False` 的文章
2. 验证 `effective_date` 格式
3. 创建 `Event` 记录 (status="raw")
4. 成功处理的文章标记 `processed=True`，失败的保留供重试
5. 最终一次性 `db.session.commit()`

**错误处理**: 每批LLM失败则整批标记失败；批内单项失败互不影响。

---

### 7.3 ClusteringAgent — 聚类去重 (`agents/clustering.py`)

**4维相似度矩阵** (融合公式):
```python
similarity = 0.50*D1 + 0.25*D2 + 0.15*D3 + 0.10*D4
```

| 维度 | 权重 | 方法 | 详细 |
|------|------|------|------|
| D1 语义 | 50% | bge-small-zh-v1.5 嵌入余弦 | `np.dot(embeddings, embeddings.T)`，L2归一化后等价于余弦 |
| D2 标签 | 25% | Jaccard + NPMI 混合 | `0.4*jaccard + 0.6*npmi_boost`。NPMI映射[-1,1]→[0,1] |
| D3 时间 | 15% | 指数衰减 | `temporal_decay(delta_days, half_life=7d)` |
| D4 实体 | 10% | Jaccard | 对 entities_json 集合的Jaccard系数 |

**聚类算法**: 贪心单次扫描，阈值0.75:
```
对每个未访问的事件i:
  创建新簇 = [i]
  对所有j>i且sim[i][j]>=0.75且未访问的: 加入簇
```

**LLM合并**: 仅对2+事件的簇进行LLM合并 (单事件簇直接标记clustered)。并发调用LLM (`CLUSTER_MERGE_PROMPT`)，请求 canonical_title, description, timeline, confidence。失败时回退到第一个事件的标题。

**落库**: 创建 `EventCluster` 记录 (merged_event_ids, similarity_score, canonical_title)。所有事件得到 cluster_id 和 status=CLUSTERED。

---

### 7.4 VerificationAgent — 可信度验证 (`agents/verification.py`)

**5维规则化评分** (每维 [0,1], 加权求和):

| 维度 | 权重 | 方法 |
|------|------|------|
| 来源权威度 | 25% | 对 raw_sources_json 中每个来源查 NewsSource.credibility，取平均值 |
| 交叉验证 | 20% | 独立来源计数: 3+ = 0.9, 2 = 0.6, 1 = 0.3 |
| 官方确认 | 25% | 关键词匹配 ("官方/公告/证监会/央行/SEC/Fed/White House" 等20+词)。3+匹配=0.9, 1+=0.6, 0=0.2 |
| 时间一致性 | 15% | 1个来源=0.5, 2+来源=0.8 (MVP占位值) |
| 历史准确率 | 15% | 固定0.6 (Phase 2将动态化) |

**状态分类**:
```python
total = 加权求和
if total >= 0.85: status = "confirmed"
elif total >= 0.50: status = "pending"
else: status = "disputed"
```

**标记**: cross_source<0.5 → "single_source"; official_confirm<0.3 → "unconfirmed_claim"

---

### 7.5 ScoringAgent — 重要性评分 (`agents/scoring.py`)

**LLM评分**: `SCORING_SYSTEM_PROMPT` 要求LLM对每个事件评5维 [0.0-1.0]:

| 维度 | 权重 | 说明 |
|------|------|------|
| market_relevance | 30% | 对资本市场的直接影响 |
| impact_scope | 25% | 涉及行业/资产类别广度 |
| impact_depth | 25% | 严重程度 |
| interpretability | 10% | 因果链清晰度 |
| timeliness | 10% | 内容新鲜度 |

每个事件独立并发调用LLM。

**时效性统计融合**:
```python
age_decay = event_timeliness(event.created_at)  # 30天半衰期指数衰减
adjusted_timeliness = 0.4 * llm_timeliness + 0.6 * age_decay
# 重新计算总分, 替换timeliness分量
```

**Wilson置信度调整** (基于范围因子):
```python
scope_factor = min(1.0, (industry_count + entity_count) / 6.0)
evidence_factor = 0.9 + 0.1 * scope_factor  # [0.9, 1.0]
total *= evidence_factor
```
- S级 + scope_factor<0.5 → 降级为A (防止薄证据高评)

**输出**: `EventScore` 记录，事件 status=SCORED

---

### 7.6 CardGenerationAgent — 卡片生成 (`agents/card_generation.py`)

**输入筛选**: status=SCORED, level>=min_level (默认 "B"), 无已有 EventCard。等级排序映射: `{"S":0, "A":1, "B":2, "C":3}`

**LLM提示词**: `CARD_SYSTEM_PROMPT` 要求生成中文JSON卡片:
- title, summary (1-2段投资者视角摘要)
- credibility_label ("高可信"/"需确认"/"待验证")
- key_entities, source_summary (如 "Reuters + 财联社 共5篇报道")
- risk_flags, affected_industries

使用 `temperature=0.3` (稍高于提取/评分的0.1，允许自然语言变化)

**处理**: 并发生成 (`run_concurrently`)，每次创建一个 `EventCard` 并更新事件 status=PUBLISHED。失败时 `db.session.rollback()`。

---

### 7.7 PredictionAgent — 走势预测 (`agents/prediction.py`)

**双模式**: `full` (LLM融合) 和 `quick` (纯规则, <50ms)

**预测流程 (`predict()`)**:
1. 同步股票信息 → 获取 K线历史 (DB优先, 缓存≥30行即跳过API)
2. 技术分析 (MA排列/MACD金叉死叉/RSI超买超卖)
3. 事件影响评分 (双通道: 统计相关性 + LLM分析)
4. 融合 (quick=规则投票, full=LLM)
5. 后置保证: chart_data 永不为空 (<5根则生成合成数据)

**技术分析** (`_analyze_technical()`):
- MA排列: MA5>MA20>MA60 = 多头, 反之为空头, 否则震荡
- MACD: 金叉/死叉 + 柱状图放大方向
- RSI(14): <30 超卖(看多), >70 超买(看空)

**事件影响评分** (`_score_event_impact()`): 双通道融合
- **D1 统计**: 每个事件调用 `event_relevance()` × BGE嵌入 (目标=股票名+代码+行业)
- **D2 LLM**: 事件JSON (截断3000字符) → LLM返回 impact_score(-1到+1), reasoning, confidence
- **D3 融合**: `0.55 * llm_score + 0.45 * mean(stat_relevance)`

**Quick模式** (`_quick_fuse()`):
- 计数多空信号数量 → 多数决定方向
- confidence = `0.55 + 0.05 * min(|bullish-bearish|, 4)`
- 目标区间 = last_close ± 3%

**LLM融合** (`_fuse_dimensions()`): 结构化提示词 (技术信号+事件影响+事件上下文)，temperature=0.2，请求 direction/confidence/target_low/target_high/key_factors/risk_flags/reasoning_chain

**合成数据回退** (`_estimate_base_price()`): 对~60只知名A股预设价格映射 (金融/白酒/科技/新能源/汽车/医药/军工/电力/房地产等)。未知代码按前缀估算 (6→15, 00→12, 30→25, else→20)

**输出**: `PredictionResult` dataclass (symbol, name, direction, confidence, time_horizon, target_low, target_high, key_factors, risk_flags, technical_signals, event_impact_score, reasoning_chain, chart_data)

---

### 7.8 MarketDataAgent — 行情数据 (`agents/market_data.py`)

**设计原则**: DB优先、API回退、合成兜底。所有API调用3次重试，检测 `_is_connection_error()`

**股票信息** (`sync_stock_info`): 调用 `ak.stock_individual_info_em()` → 提取名称/市场(SH/SZ)/行业 → Upsert到DB

**价格历史** (`fetch_price_history`):
- 缓存: DB中≥30行直接返回 (不调API)
- API: `ak.stock_zh_a_hist(symbol, qfq)` (前复权)
- 回退: API失败则返回DB中现有数据 (即使<30行)

**合成数据** (`generate_synthetic_history`):
- 确定性种子: `hash(symbol) % 2^31`
- 随机游走 + 均值回归: `price *= (1 + gauss(0.0003, vol) + (base_price-price)*0.001)`
- 跳过周末; 成交量 ~ Gaussian(5M, 2M)

**实时快照** (`fetch_snapshot`): `ak.stock_zh_a_spot_em()` 全市场快照 → 过滤目标 → Upsert。5分钟新鲜度。

**技术指标** (静态方法): `calc_ma()`, `calc_macd()` (EMA12/EMA26/DIF/DEA/MACD柱), `calc_rsi()` (Wilder平滑RSI-14), `_calc_ema()` (递归EMA+SMA种子)

**板块热力图** (`fetch_sector_performance`): 东方财富 → 同花顺回退 (含列名自动检测)

---

### 7.9 DailyBriefingAgent — 每日简报 (`agents/daily_briefing.py`)

**2阶段生成**:

**Phase 1 — 数据收集**:
- 事件: 今日 EventCards (北京时间 UTC+8)，回退到最近20张
- 行情: 3大指数 (上证/深证/创业板) + 板块表现
- 风险: S/A级事件 risk_flags 聚合
- 统计: 按等级计数/来源/时间戳

**Phase 2 — LLM生成5段**:
1. **executive_summary**: 2-3句核心摘要 (≤200字符)
2. **market_snapshot**: 指数走势+领涨领跌板块+情绪 (禁止回复"数据未提供")
3. **top_events**: 选5-8个S/A级事件+1-2句摘要
4. **prediction_summary**: 趋势预测 (≤200字符)
5. **risk_alert**: 3-5个下周具体风险 (解析"风险:"前缀提取告警项)

每段独立LLM调用: `temperature=0.3, max_tokens=500`

**缓存**: 日期匹配+force=False时直接返回缓存。force=True时删除旧简报重新生成。

---

### 7.10 TimelineBuilderAgent — 因果网络 (`agents/timeline_builder.py`)

**5大工作流**: 节点创建 → 因果发现 → 预测扩展 → 验证 → 增长/累积

**过期周期** (`IMPACT_PERIODS`):
- 按等级: S=90d, A=30d, B=14d, C=7d
- 按预测: T+3=3d, T+7=7d, T+30=30d
- 按类型: market_reaction=7d, verification=30d, root_event=30d, derived_event=14d

**节点创建** (`add_event_node`): Event+EventCard → TimelineNode (去重/影响周期/字段映射)

**因果发现** (`discover_causal_links`): 2步
1. LLM识别因果关系 (最多30候选节点)
2. **融合**: `0.6 * llm_strength + 0.4 * formula_strength` (5维边强度公式, 见下文)

**自动建图** (`auto_build_from_events`): S级优先→A→B (B限制 max_events//2)。每个EventCard→1个TimelineNode，节点类型多元化 (市场关键词→market_reaction, S级→root_event, A级+政策→root_event, else→derived_event)

**自动边发现** (`_auto_discover_links`): **2种互补策略**:

*策略1 — 规则化 (公式驱动)*:
- 预计算所有50个节点的嵌入+TagStatistics
- 每对有共同标签的节点对计算 `compute_edge_strength()` (relation_type="correlates")
- 最小阈值: 0.15
- 自动验证: strength≥0.55 + 2+共享标签 + 两节点confidence≥0.7 → verified=True
- 标记: `created_by="rule_based"`

*策略2 — LLM (并发批次)*:
- 每10个节点一批, 最多4个并发worker
- 每批发送给LLM做因果分析
- 去重后融合: `0.6 * llm_strength + 0.4 * formula_strength`
- 高强度边(≥0.65)标记 verified=True
- 标记: `created_by="llm_auto"`

**预测扩展** (`extend_predictions`):
1. LLM预测 3-5 个下游事件 (title/description/confidence/time_horizon/tags)
2. 创建预测节点 (node_type="prediction", status="predicted")
3. 计算边强度 + 融合: `0.5 * pred_confidence + 0.5 * formula_strength`
4. 使用 savepoint 嵌套事务保证回滚安全

**图数据** (`get_graph_data`): 返回 vis.js 兼容格式
- 自动初始化: 无节点→auto_build, 有节点无边→auto_discover
- 排除孤立节点 (无边的节点过滤)
- 颜色编码: root_event=蓝, derived_event=紫, prediction=琥珀, market_reaction=绿, verification=红
- 边样式: verified=True→绿色实线, verified=False→红色虚线, verified=None→灰色实线

**重建** (`rebuild`): 删边→删节点→重建→因果发现→top5节点预测扩展

**清理** (`cleanup_expired_nodes`): 物理删除超期≥30天的节点 (级联删边)

---

### 7.11 ResearchAssistantAgent — AI对话 (`agents/research_assistant.py`)

**意图分类** (`_classify_intent`): 关键词优先级分类
1. **action**: "更新/添加/删除/生成/触发/采集/重建/清理/执行"
2. **briefing**: "简报/今日/日报/ briefing"
3. **timeline**: "时间线/因果/节点/边/timeline/网络"
4. **prediction**: 6位股票代码正则 或 "走势/预测/k线/均线/macd/rsi/技术分析"
5. **system**: "系统状态/统计/多少/几个/数量/dashboard/概况"
6. **event** (默认回退)

**7路并行数据检索**:

| # | 查询 | 条件 | 方法 |
|---|------|------|------|
| 1 | 事件 | 始终 | `event_relevance()` (语义50%+标签25%+时效15%+等级10%) + 关键词加分(+0.05/词) + 关注行业加权(+0.15) |
| 2 | 行情 | 始终 | 4级级联匹配: DB直接查找→关注行业映射→事件行业映射→别名匹配；然后取 PriceSnapshot + PriceHistory 技术指标 |
| 3 | 时间线 | 意图触发 | 按类型/状态/等级统计节点+边 |
| 4 | 简报 | 意图触发 | 最新DailyBriefing → 摘要/关键数据/风险 |
| 5 | 预测 | 意图触发 | 提取6位代码 → 价格快照+30天K线+MA5/10/20+DIF+RSI+20日振幅 |
| 6 | 系统 | 意图触发 | 今日EventCard数/S/A级数/活跃源数/时间线统计/最新简报日期 |
| 7 | 动作 | 意图触发 | LLM解析动作+参数 → `execute_action()` (破坏性操作需二次确认) |

**补充搜索**: 仅当所有系统源返回"暂无/查询异常"时才触发 DuckDuckGo 作为最后手段。

**响应生成** (`_generate_response`):
- 上下文模板: 所有采集数据分节组装
- 系统提示词增强: 加入用户关注行业
- 多轮对话: 最近6条消息
- 动态 max_tokens: ≥3数据源→2048, else→1536
- temperature=0.3

---

## 八、边强度公式 — 核心数学引擎 (`agents/edge_strength.py`)

### 5维融合公式

| 维度 | 权重 | 方法 | 公式 |
|------|------|------|------|
| D1 语义 | 35% | bge嵌入余弦相似度 | `(cos_sim+1)/2` 映射到[0,1] |
| D2 标签NPMI | 25% | 归一化逐点互信息 | `max(NPMI(t_a,t_b))`，映射到[0,1] |
| D3 Jaccard | 15% | 标签集重叠 | `|A∩B| / |A∪B|` |
| D4 时间 | 15% | 指数衰减+自适应半衰期 | `exp(-ln(2)/T_half * Δt)` |
| D5 等级 | 10% | 重要性加权平均 | `(W_a + W_b)/2` 其中 S=1.0, A=0.75, B=0.50, C=0.30 |

```python
strength = 0.35*D1 + 0.25*D2 + 0.15*D3 + 0.15*D4 + 0.10*D5
```

### TagStatistics — 全局NPMI计算

`TagStatistics` 类从所有 TimelineNode 对象一次性构建:
- `tag_doc_count`: Counter {tag → 文档频率}
- `tag_pair_count`: dict {(tag_a, tag_b) → 共现文档数}

NPMI公式:
```
PMI(x;y) = log2( P(x,y) / (P(x)*P(y)) )
NPMI(x;y) = PMI(x;y) / -log2(P(x,y))
```

`tag_set_npmi()` 对两个标签集的所有交叉对计算NPMI并返回最大值 (代表最强统计关联)。

### Wilson置信下界

```python
p_hat = successes / trials
center = (p_hat + z²/(2n)) / (1 + z²/n)
margin = z * sqrt(p_hat*(1-p_hat)/n + z²/(4n²)) / (1 + z²/n)
lower_bound = center - margin
```
z=1.96 (95%置信度)。小样本时边界更接近0，惩罚低证据关联。

在 `compute_edge_strength()` 中用作**可靠性调整**: 找到最强标签对的Wilson可靠性，混合 `0.9*raw_strength + 0.1*reliability_factor`

### 自适应半衰期

`adaptive_half_life()` 融合:
- 60% 事件等级 (S=60d, A=30d, B=14d, C=7d)
- 40% 关系类型 (causes=45d, influences=25d, correlates=15d, contradicts=10d)

### 批量优化

`build_edge_context()` 预计算所有节点的嵌入向量和 TagStatistics，返回可复用的上下文 dict。

---

## 九、辅助统计函数 (`agents/stats_utils.py`)

### event_timeliness()

```python
timeliness = temporal_decay(delta_days, half_life_days=30)
# 新事件=1.0, 30天=0.5, 90天=0.125
```

自动处理时区感知/朴素 datetime。

### event_relevance() — 4维加权融合

| 维度 | 权重 | 方法 |
|------|------|------|
| 语义 | 50% | 嵌入余弦相似度 |
| 标签 | 25% | Jaccard + NPMI (50/50混合) |
| 时效 | 15% | event_timeliness() |
| 等级 | 10% | LEVEL_WEIGHT |

用于研究助手中的搜索排序、事件-股票匹配、简报选择。

---

## 十、LLM 集成层 (llm/)

### 抽象接口 (`llm/base.py`)

| 方法 | 用途 |
|------|------|
| `complete(system_prompt, user_message, **kwargs) -> str` | 单轮文本补全 |
| `complete_json(system_prompt, user_message, **kwargs) -> dict` | 补全后JSON解析 |
| `chat(messages, **kwargs) -> str` | 多轮对话 |
| `embed(texts) -> list[list[float]]` | 嵌入向量 |

构造函数从 config 读取: `LLM_MODEL`, `LLM_TEMPERATURE`, `LLM_MAX_TOKENS`

**`_local_embed()`** 具体回退: 使用 `BAAI/bge-small-zh-v1.5` 本地模型 (local_files_only=True, normalize_embeddings=True)，失败时返回768维零向量。

### 工厂 (`llm/factory.py`)

`get_llm(config=None)`: 无config时自动从 Flask current_app 或环境变量解析。路由映射: `deepseek/qwen/zhipu/ollama` → 对应Provider。

### 各Provider对比

| 特性 | DeepSeek | Ollama | 智谱 | 通义千问 |
|------|----------|--------|------|----------|
| API客户端 | OpenAI SDK | `requests` 原生 | OpenAI SDK | OpenAI SDK |
| API地址 | api.deepseek.com/v1 | localhost:11434 | open.bigmodel.cn | dashscope.aliyuncs.com |
| 嵌入 | DeepSeek API → 本地回退 | 仅本地 | 仅本地 | 仅本地 |
| JSON解析 | `{.*}` 正则 | `{.*}` + `[.*]` 正则 | `{.*}` 正则 | `{.*}` 正则 |
| 重试策略 | 指数退避 (2^attempt秒) | Timeout固定5s等待 | 指数退避 | 指数退避 |
| 特殊处理 | — | Qwen3.5思考模式: 检查 thinking 字段，逐行CJK比例>50%截断 | — | — |

**Ollama Qwen3.5 思考模式处理**: Qwen3.5将所有内容输出到 `thinking` 字段。Provider检查 `content` 为空后回退到 `thinking`，用 `_extract_final_answer()` 逐行分析CJK字符比例 (>50%即为最终回答)，剥离英文思维链。

**JSON解析流程**: 剥离 markdown 代码围栏 → `json.loads()` → 正则回退 `\{.*\}` → 抛出异常

---

## 十一、信息源连接器 (sources/)

### 注册表 (`sources/registry.py`)

- `SOURCE_MAP: dict[str, type]` 全局连接器注册表
- `@register_connector("name")` 类装饰器自动注册
- `get_connector(name, config, source_record)` 工厂函数，自动注入 source_record

### 内置连接器

| 连接器 | 注册名 | 来源 | 详细 |
|--------|--------|------|------|
| `CLSConnector` | `cls` | 新浪财经公开滚动API | feed.mix.sina.com.cn, 30篇/次, 10位Unix时间戳 |
| `AkshareConnector` | `ak_cctv` `ak_futures` | CCTV新闻 + 上海期货交易所 | DataFrame列映射规范化, 合成 akshare:// URL |
| `SmartCrawlerConnector` | `smart_crawler` | 任意网页自动检测 | 3层容器检测: 用户CSS→自动评分DOM→已知选择器回退; httpx+Playwright双引擎; 分页支持 |
| `GenericConnector` | 不注册 | 用户自定义源 | API/网页/RSS自适应; JSON路径自动发现; Atom+RSS2.0解析 |

### SmartCrawler — 智能爬虫详解

**容器自动检测** (`_auto_detect_containers`):
- 找到叶子容器 (含链接+文本>20字符，排除 nav/footer/header/sidebar/menu/comment/ad/banner)
- 按父元素分组 → 评分 (子元素数量+结构一致性)
- 得分公式: 链接+3, 文本>50+3, 标题+2, 图片+0.5, 时间+1; 惩罚项: 链接比例>80%(-3), 文本>2000字符(-2)

**标题验证黑名单**: "关于我们/联系我们/首页/登录/注册/更多/详情/上一篇/下一篇" 等

### GenericConnector — 通用连接器详解

- **API模式**: JSON端点 → 2级路径搜索 (data/items/results/articles/list/news/posts/entries/records/content) → 键名规范化 (TITLE_KEYS/URL_KEYS/CONTENT_KEYS 等已知键集)
- **网页模式**: 委托 SmartCrawler → 回退到 assistant/web_crawler.crawl_url()
- **RSS模式**: Atom+RSS2.0 → 命名空间感知 ElementTree 解析
- **时间解析**: Unix秒/毫秒, ISO格式, 通用模式; 回退到 `datetime.now(UTC)`

---

## 十二、AI 助手组件 (assistant/)

### ChatManager (`assistant/chat_manager.py`)

| 常量 | 值 | 说明 |
|------|-----|------|
| MAX_CONTEXT_MESSAGES | 20 | 上下文窗口大小 |
| SUMMARY_THRESHOLD | 16 | 触发摘要的阈值 |

**会话管理**:
- `create_session` → 新建 ChatSession
- `delete_session` → 级联删除 ResearchNote → ChatMessage → 会话
- `delete_all_sessions` → 批量删除所有会话/消息/笔记
- `rename_session` → 更新标题

**消息管理**:
- `add_message` → 创建 ChatMessage + 自动标题 (首条用户消息前50字符) + 更新 session.updated_at
- `get_context_messages` → ≤20条全部返回; >20条时保留最后20条，摘要旧消息 (最近10条截断+角色前缀) 合并为 system 消息
- `delete_message` → 单条删除。如果是用户消息，同时删除紧随其后的助手回复 (消息对)

### ReasoningChain (`assistant/reasoning.py`)

- `ReasoningStep` dataclass: step_id, type (tool_call/llm_inference/knowledge_lookup), description, input, output, confidence (0-1), sources
- `ReasoningChain`: 最多20步; 超限时自动删除最旧的 non-tool_call 步骤; `to_markdown()` 用 emoji 图标渲染

### 工具集 (`assistant/tools/`)

| 工具 | 核心能力 |
|------|----------|
| `web_search.py` | DuckDuckGo Instant Answer API → HTML抓取回退; URL去重 |
| `web_crawler.py` | 生产级爬虫: httpx连接池 (20连接/10保持) + Playwright单例; LRU缓存 (300条); 3相并行: LRU检查→并行静态→串行动态; TreeWalker文本提取 |
| `api_caller.py` | 通用 HTTP API 调用 (GET/POST, JSON→文本回退, 5000字符截断) |
| `action_handler.py` | **10个注册动作**: 管道触发/采集, 源CRUD, 简报生成, 时间线重建/因果发现/清理。破坏性操作 (delete_source, rebuild_timeline, cleanup_expired) 需二次确认。LLM可读的动作清单由 `get_available_actions_markdown()` 生成 |

---

## 十三、Flask 应用架构 (app/)

### 应用工厂 (`app/__init__.py`)

`create_app()` 执行顺序:
1. 读 FLASK_ENV → 加载 config 对象
2. 初始化集中日志 (dictConfig)
3. 注册 Jinja2 `markdown` 过滤器 (Markdown→安全HTML)
4. 初始化扩展 (db/migrate/celery)
5. 注册错误处理器 (APIError + HTTP状态码)
6. 注册15个Blueprint
7. 建表 + 种子化4个系统源

### Blueprint 一览

**Web页面 (SSR)**:

| Blueprint | 路由 | 说明 |
|-----------|------|------|
| web_health | `/` `/health` | 仪表盘首页 + 健康检查 |
| web_events | `/events` `/events/<id>` | 事件列表+详情 |
| web_sources | `/sources` | 源管理 |
| web_assistant | `/assistant` | AI助手全屏页 |
| web_prediction | `/prediction` | 走势预测 |
| web_briefing | `/briefing` | 每日简报 |
| web_timeline | `/timeline` | 因果时间线 |

**SPA片段** (`/api/v1/fragment/*`):

| 路由 | 说明 |
|------|------|
| `/home` | 首页 (含Bubble动画事件) |
| `/events` | 事件列表 (分级+分页) |
| `/events/<id>` | 事件详情 |
| `/sources` | 源管理 |
| `/prediction` | 预测页 (StockInfo+PriceSnapshot) |
| `/briefing` | 简报页 (日期+历史选择) |
| `/timeline` | 时间线 (含预取图数据) |
| `/assistant` | 助手片段 |

**REST API**:

| Blueprint | 前缀 | 主要端点 |
|-----------|------|----------|
| api_events | `/api/v1` | events CRUD + pipeline/trigger (异步，后台线程+run_id轮询) + pipeline/status/<id> + pipeline/trigger/process + pipeline/trigger/scout |
| api_sources | `/api/v1` | sources CRUD + test-connection + toggle + collect + batch-toggle |
| api_assistant | `/api/v1/assistant` | sessions CRUD + messages (SSE流式选项) + quick-search + crawl + industries |
| api_prediction | `/api/v1/prediction` | stocks/search + stocks/<symbol>/history + stocks/<symbol>/snapshot + predict/<symbol> + hot + sectors + index/<code> |
| api_briefing | `/api/v1/briefing` | latest + <date> + generate + dates |
| api_timeline | `/api/v1/timeline` | graph + nodes + edges + discover + extend + rebuild + cleanup + snapshots + isolated |

### 错误处理 (`app/errors.py`)

- `APIError(Exception)`: 自定义异常类 (message, status_code, code)。Web路由返回HTML错误页; API路由返回 `{"success":false, "error":"...", "code":"..."}` JSON
- 处理: 400/404/405/500 + APIError

---

## 十四、前端架构

### 模板布局 (`app/templates/base.html`)

```
.app-layout
├── aside#appSidebar        ← 侧栏: Logo + 6个导航项 + 免责声明
├── #sidebarOverlay          ← 移动端遮罩
└── .app-main
    ├── header.app-topbar    ← 顶栏: 汉堡按钮 + 面包屑 + 管道触发 + 页面操作 + 主题切换
    ├── main#appContent      ← 内容区 (SSR渲染或SPA替换)
    └── footer.app-footer    ← "仅供参考，不构成投资建议"
```

**SVG滤镜**: 6个内联 liquid-glass 滤镜 (feTurbulence + feDisplacementMap + feGaussianBlur + feColorMatrix)

**脚本加载顺序**: Bootstrap → ECharts → app.js → router.js → 内联配置注入 → assistant_widget.js → 页面脚本

### CSS 主题 (`app/static/css/app.css`)
~5000行 "Terminal Noir + Gold" 暗黑金融终端主题 + 完整 light 主题变体。大量 backdrop-filter 玻璃模糊效果 (明暗双主题)。

### SPA 路由器 (`app/static/js/router.js`)

**路由映射**:
- 精确路由: `/` `/home` → `/api/v1/fragment/home`
- 动态路由: `/events/<id>` → `/api/v1/fragment/events/$1`
- 前缀路由: `/events` `/sources` `/prediction` `/briefing` `/timeline` `/assistant` → `/api/v1/fragment` + 原路径

**核心机制**:
- 5秒缓存 (fragmentUrl → {html, title, ts})
- 进度条: 2px绝对定位顶栏，导航时动画到70%→100%
- 脚本执行: 所有 `<script>` 标签包装在 IIFE 中 (防止 const/let 重复声明)
- 页面清理: `_pageDestroy()` → `_pageCleanups[]` → `ChartResizeManager.disposeAll()` → 清除模态框
- 链接拦截: 同域+非下载+侧栏或 data-spa 属性; 修饰键保留新标签行为
- History API: `pushState` + `popstate`; 初始状态替换保证后退按钮可用
- 导出: `window._router = {navigate, reload, currentPath, onBeforeNavigate, onAfterNavigate}`

### 全局工具 (`app/static/js/app.js`)

| 功能 | 实现 |
|------|------|
| 侧栏 | `initSidebar()` / `toggleSidebar()` / 移动端自动关闭 |
| 主题 | localStorage持久化 + `themechange` CustomEvent + ECharts联动 |
| 管道触发 | POST触发→2秒轮询run_id→通知+页面刷新 (最大5分钟超时) |
| 图表管理 | `ChartResizeManager` (ResizeObserver 100ms防抖) + `createECharts()` (主题联动) |
| SSE | `createSSEConnection()` (指数退避自动重连: 1s→30s上限) |
| 加载态 | `LoadingState` (容器spinner / 按钮spinner+restore) |
| Toast | `showNotification(message, type)` |
| 格式化 | `escHtml()`, `timeAgo()` (中文相对时间), `formatNumber()` (亿/万), `formatPercent()` (红涨绿跌) |
| 全局搜索 | debounce 300ms + 键盘导航 + 点击外部关闭 |
| 计数器动画 | ease-out cubic, 800ms |

### AI 助手组件 (`app/static/js/assistant_widget.js`)

**双模式**: 首页内嵌对话 + 非首页浮动可拖拽窗口。共享同一 session。

**状态管理**:
- `currentSessionId` / `isProcessing` / `isOpen` / `isFullscreen`
- `_homeGeneration` 计数器: 每次重进首页+1，API响应时比对，防竞态

**消息发送** (`_sendMessage`):
1. 拍快照 (_homeGeneration)
2. 追加用户消息+加载指示器
3. POST `/api/v1/assistant/sessions/<id>/messages` (含 focus_industries)
4. 移除加载，追加助手回复 (_simpleMD 渲染)
5. 检查代际: 若已变化则委托 `renderHomepageResponse()` (竞态安全)

**首页对话**:
- `_enterChatMode()` → 加 `chatting` class → 模糊玻璃 + 隐藏建议条
- `renderHomepageResponse()` → 差分更新: 对比渲染消息ID vs 会话消息ID → 删旧添新

**Widget DOM** (`buildWidget`):
- FAB按钮 (#aiFab) — 机器人图标+脉冲动画
- 悬浮窗 (#aiWindow) — 保存坐标/尺寸到localStorage
- 标题栏 — 可拖拽 (mousedown/mousemove/mouseup)
- 会话列表 + 行业设置面板 + 消息体 + 输入行
- 缩放把手 — 右下角拖拽 (min 380×420, max 90%视口)
- 空状态: ◆ + "BubbleEvent 研究助手" + 4个快捷提问按钮

**首页历史面板** (`_initHomepageHistory`):
- 历史按钮 (#homeBtnHistory) 切换面板
- 新对话 (#homeBtnNewChat) / 清空全部 (#homeClearAllBtn)
- 分页加载 (#homeLoadMoreBtn, offset+30)
- 会话行点击加载 + × 删除按钮

**路由器钩子** (`_setupRouterHooks`):
- 离开首页: 标记 `ai_from_homepage`
- 进入非首页 (widget开启): 自动弹出+恢复会话
- 进入首页: 关闭浮动窗 → 恢复内嵌对话 → 初始化历史

**初始化**:
- `_AI_ASSISTANT_ENABLED=false` → 完全跳过
- `_AI_ASSISTANT_WIDGET_ENABLED=false` → 跳过 FAB+悬浮窗 (仅首页对话可用)
- 首页: 隐藏FAB → 初始化历史 → 自动创建/恢复会话
- 非首页无widget: 仅创建会话
- 非首页有widget: 创建/恢复会话 → 来自首页则自动打开

**导出**: `window._aiWidget = {open, close, toggle, quickAsk, toggleFullscreen, toggleSessionList, newSession, loadSession, deleteSession, deleteMessage, refreshSessions, refreshHomeSessions, send, renderHomepageResponse, initHomepageHistory, isOpen, hasSession}`

---

## 十五、Celery 定时任务 (tasks/)

| 任务 | 文件 | 频率 | 功能 |
|------|------|------|------|
| `collect_all_sources` | `collection.py` | 每小时 | ScoutAgent采集 → 如有新文章自动 chain `process_new_articles` (fast_mode) |
| `process_new_articles` | `processing.py` | 每4小时 | 全管道 (非fast_mode) — 兜底安全网 |
| `process_full_pipeline` | `processing.py` | 每日 08:30 CST | 链式: 采集→全管道→简报 — 每日简报保障 |

---

## 十六、系统源种子化 (`utils/seed.py`)

启动时在 app context 内自动插入4个默认系统源 (标记 `is_system=True`):

| 名称 | 展示名 | 可信度 | 采集间隔 | 标签 |
|------|--------|--------|----------|------|
| `cls` | 新浪财经 | 0.70 | 1800s | 财经/A股/快讯/中国 |
| `36kr` | 36氪快讯 | 0.65 | 1800s | 科技/创投/快讯/新经济 |
| `ak_cctv` | 央视新闻 | 0.88 | 3600s | 央视/官方/宏观/政策 |
| `ak_futures` | 上期所快讯 | 0.75 | 1800s | 期货/大宗商品/快讯/国际 |

系统源受保护: 不可删除, 仅可修改 is_active/credibility/poll_interval。

---

## 十七、完整 API 路由速查

### Web 页面
| 路由 | 说明 |
|------|------|
| `GET /` | 仪表盘首页 |
| `GET /events` | 事件列表 |
| `GET /events/<id>` | 事件详情 |
| `GET /sources` | 源管理 |
| `GET /assistant` | AI助手 |
| `GET /prediction` | 走势预测 |
| `GET /briefing` | 每日简报 |
| `GET /timeline` | 因果时间线 |

### SPA 片段
`GET /api/v1/fragment/{home,events,events/<id>,sources,prediction,briefing,timeline,assistant}`

### Events API
`GET/POST /api/v1/events` · `GET /api/v1/events/<id>` · `POST /api/v1/pipeline/{trigger,trigger/process,trigger/scout}` · `GET /api/v1/pipeline/status/<run_id>`

### Sources API
`GET/POST /api/v1/sources` · `GET/PUT/DELETE /api/v1/sources/<id>` · `POST /api/v1/sources/{test-connection,<id>/toggle,<id>/collect,batch-toggle}`

### Assistant API
`GET/POST/DELETE /api/v1/assistant/sessions` · `GET/DELETE /api/v1/assistant/sessions/<id>` · `POST /api/v1/assistant/sessions/<id>/{rename,messages}` · `DELETE /api/v1/assistant/sessions/<id>/messages/<mid>` · `POST /api/v1/assistant/{quick-search,crawl}` · `GET /api/v1/assistant/industries`

### Prediction API
`GET /api/v1/prediction/stocks/search` · `GET /api/v1/prediction/stocks/<symbol>/{history,snapshot}` · `GET /api/v1/prediction/predict/<symbol>` · `GET /api/v1/prediction/{hot,sectors}` · `GET /api/v1/prediction/index/<code>`

### Briefing API
`GET /api/v1/briefing/{latest,dates}` · `GET /api/v1/briefing/<date>` · `POST /api/v1/briefing/generate`

### Timeline API
`GET /api/v1/timeline/{graph,nodes,snapshots}` · `GET /api/v1/timeline/nodes/<id>` · `GET /api/v1/timeline/edges/<id>` · `POST /api/v1/timeline/{discover,rebuild,cleanup,snapshot}` · `POST /api/v1/timeline/edges/<id>/verify` · `POST /api/v1/timeline/extend/<id>` · `GET /api/v1/timeline/isolated` · `DELETE /api/v1/timeline/nodes/<id>`

---

## 十八、设计决策记录

1. **LLM 优先 DeepSeek v4-flash** — 工厂模式可切换 通义千问/智谱/Ollama
2. **不引入 LangChain** — 自研管道式 Agent 编排，更轻量、更可控
3. **SPA + SSR 混合** — History API 客户端路由 + 完整服务端渲染回退
4. **嵌入模型本地离线** — `BAAI/bge-small-zh-v1.5`，零网络依赖
5. **数学 + LLM 混合评分** — 统计公式为基底，LLM 为增量；Wilson 置信区间修正低证据场景
6. **DB优先 + API回退 + 合成兜底** — 行情数据三层防护，保证系统永不因数据源故障而崩溃
7. **所有投资输出标注** — "仅供参考，不构成投资建议"
8. **并发通过 ThreadPoolExecutor** — I/O瓶颈非CPU瓶颈; 每线程独立 Flask app context
9. **管道状态机驱动** — 事件状态 raw→clustered→verified→scored→published，增量幂等
10. **功能开关细粒度** — ENABLE_AI_ASSISTANT (全部) / ENABLE_ASSISTANT_WIDGET (仅FAB)
