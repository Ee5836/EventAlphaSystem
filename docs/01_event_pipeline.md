# ① 事件管道 (Event Pipeline)

> 核心功能：全渠道采集 → 批量LLM提取 → 聚类去重 → 验证 → 评分 → 卡片
> 更新: 2026-07-12 | 状态: ✅ 已完成 | 实测: 16篇→7事件→6卡片，~97s (fast_mode) | 新增: 手动触发 + 仅处理模式 + 批量源采集

---

## 1. 管道流程

```
┌──────────────────────────────────────────────────────────────┐
│ 触发: Celery Beat (每小时, 自动链式加工)                      │
│       或 POST /api/v1/pipeline/trigger (异步, 即时返回)      │
├──────────────────────────────────────────────────────────────┤
│ Stage 1: Scout Agent     → 4源并行 (poll_interval保护) ~2s   │
│ Stage 2: Extraction      → 批量LLM(5篇/批, 8并发)    ~21s   │
│ Stage 3: Clustering      → 5维相似度+LLM合并          ~55s   │
│ Stage 4: Verification    → 五维度规则评分 (无LLM)     ~0.1s  │
│ Stage 5: Scoring         → LLM重要性评估 (8并发)      ~12s   │
│ Stage 6: Card Generation → LLM中文卡片 (8并发)        ~8s    │
│ Stage 7: Timeline Build  → fast_mode跳过 (按需开启)   —      │
├──────────────────────────────────────────────────────────────┤
│ 总计: ~97s (fast_mode) → 6张事件卡片                         │
│ 完整模式(含Timeline LLM因果分析): ~420s                       │
└──────────────────────────────────────────────────────────────┘
```

## 2. 关键文件

| 文件 | 说明 |
|------|------|
| `pipeline/orchestrator.py` | 7 Agent 链式编排 + 阶段计时 + fast_mode + **run_processing_only()** (跳过scout) |
| `agents/scout.py` | 采集 — **4源并行** + poll_interval保护 + URL去重 + 入库 |
| `agents/extraction.py` | **批量提取** — 5篇/批 × 8并发LLM调用 |
| `agents/clustering.py` | 聚类 — **5维相似度**(语义50%+标签Jaccard/NPMI 25%+时间衰减15%+实体重叠10%) + LLM合并 |
| `agents/verification.py` | 验证 — 五维度规则评分 (无LLM, <0.1s) |
| `agents/scoring.py` | 评分 — LLM五维重要性评估 + **统计时效衰减**(60%指数衰减) + **证据Wilson惩罚** |
| `agents/card_generation.py` | 卡片 — LLM中文结构化摘要 |
| `agents/edge_strength.py` | **数理引擎** — 5维公式 + NPMI + 时间衰减 + Wilson + TagStatistics(支持多tag_attr) |
| `agents/stats_utils.py` | **共享工具** — event_timeliness + event_relevance |
| `agents/timeline_builder.py` | **时间线** — 自动构建 + 规则边发现 + **并发LLM因果分析** (仅full模式) |
| `tasks/collection.py` | Celery定时任务 + **自动链式加工** (采集→有新文章→自动触发处理) |
| `app/routes/api_events.py` | **异步管道触发** + 状态轮询 |
| `utils/concurrent.py` | **通用并发工具** — ThreadPoolExecutor + Flask context |

## 3. 数据模型

### RawArticle
```
source_id | url (unique) | title | content | summary
published_at | content_hash | processed | raw_metadata
```

### Event
```
title | event_type (18种) | event_category (12种)
entities_json | location | effective_date
affected_industries_json | confidence | status (raw/clustered/verified/scored/published)
raw_sources_json | timeline_json
```

### EventCard
```
event_id → Event | title | summary | level (S/A/B/C)
credibility | credibility_label (高可信/需确认/待验证)
event_type | affected_industries | key_entities
source_summary | risk_flags_json
```

## 4. 信息源配置

| 源名称 | 数据源 | 连接器 | poll_interval | 采集量/次 |
|--------|--------|--------|:-----------:|-----------|
| cls | 新浪财经 API | `CLSConnector` | 30min | ~30篇 |
| 36kr | 36氪 RSS | `GenericConnector` (RSS) | 30min | ~20篇 |
| ak_cctv | 央视新闻 | `AkshareConnector` | 60min | ~12篇 |
| ak_futures | 上期所快讯 | `AkshareConnector` | 30min | ~20篇 |

> poll_interval 保护：各源按自身间隔独立跳过，`force=true` 可强制拉取。

## 5. 性能优化 (v3, 2026-07-07)

### 并发参数

| 参数 | 值 | 说明 |
|------|:--:|------|
| LLM_MAX_CONCURRENCY | 8 | LLM调用并发上限 |
| Extraction BATCH_SIZE | 5 | 每批文章数 |
| Scout 采集方式 | 并行 | ThreadPoolExecutor, 4源同时拉取 |

### 阶段耗时分布 (实测 16篇→7事件→6卡片, fast_mode)

| 阶段 | 耗时 | 并发方式 |
|------|:---:|----------|
| Scout | 1.6s | 4源并行HTTP |
| Extraction | 20.6s | 5篇/批 × 8并发LLM |
| Clustering | 55.4s | embedding + LLM合并 |
| Verification | 0.1s | 纯规则, 无LLM |
| Scoring | 11.5s | 逐事件 × 8并发LLM |
| Card Gen | 7.7s | 逐事件 × 8并发LLM |
| Timeline | 0s | fast_mode跳过 |
| **总计** | **96.8s** | |

### fast_mode vs 完整模式

| 模式 | 耗时 | Timeline | 预测延伸 | 适用场景 |
|------|:---:|:--------:|:--------:|----------|
| **fast_mode** (默认) | ~97s | 跳过 | 跳过 | 日常采集+事件更新 |
| 完整模式 | ~420s | 规则+LLM因果发现 | LLM预测 | 深度分析/Bubble构建 |

## 6. 调度配置

```python
# config.py — CELERY_BEAT_SCHEDULE
{
    "collect-and-process-hourly": {        # 每小时采集
        "task": "tasks.collection.collect_all_sources",
        "schedule": 3600.0,
    },
    "process-safety-net": {                # 4小时兜底加工
        "task": "tasks.processing.process_new_articles",
        "schedule": crontab(minute=45, hour="*/4"),
    },
    "daily-briefing-morning": {            # 每日简报 08:30 CST
        "task": "tasks.processing.process_full_pipeline",
        "schedule": crontab(hour=0, minute=30),
    },
}
```

### 采集→加工 自动链式

```
collect_all_sources()
  ├─ 有新文章? → process_new_articles() → 事件卡片自动刷新
  └─ 无新文章? → 跳过 (节省LLM成本)
```

## 7. 触发方式

### API 端点

```bash
# 异步全管道 (即时返回run_id, 前端轮询状态)
curl -X POST http://localhost:5000/api/v1/pipeline/trigger
curl -X POST http://localhost:5000/api/v1/pipeline/trigger \
  -H "Content-Type: application/json" -d '{"force": true}'

# 仅处理 (跳过采集，直接处理已采集文章)  ← NEW
curl -X POST http://localhost:5000/api/v1/pipeline/trigger/process

# 轮询状态
curl http://localhost:5000/api/v1/pipeline/status/<run_id>

# 仅采集 (不处理)
curl -X POST http://localhost:5000/api/v1/pipeline/trigger/scout
curl -X POST http://localhost:5000/api/v1/pipeline/trigger/scout \
  -H "Content-Type: application/json" -d '{"force": true, "sources": ["cls"]}'

# 采集单个源 (通过源管理API)
curl -X POST http://localhost:5000/api/v1/sources/<source_id>/collect
```

### UI 触发

| 按钮 | 位置 | 功能 |
|------|------|------|
| 🔄 采集 | 全局顶部栏 (所有页面) | 异步全管道 → 轮询状态 → Toast通知 → 自动刷新 |
| 🔄 更新事件 | 事件列表页 filter bar 旁 | 同上（有事件卡片时也可见） |
| 🔄 开始采集 | 事件列表页空状态 | 同上（无数据时引导用户） |
| ⚡ 采集全部源 | 信息源管理页 action bar | 逐个拉取所有已启用源 → 汇总通知 |
| ☁️ (云下载图标) | 信息源表每行 | 单源采集 → 更新该行采集状态 |

### 信息源管理页手动采集

- **单源采集**: 每行「☁️」按钮 → `POST /api/v1/sources/<id>/collect`
- **全部采集**: 「⚡ 采集全部源」按钮 → 遍历已启用源逐个调用单源API
- **后续处理**: 采集完成后使用顶部 🔄 按钮触发处理管道
