# ⑤ 每日简报 (Daily Briefing)

> 核心功能：综合管道输出生成6板块结构化日报 + 3个可视化图表 + 历史浏览
> 更新: 2026-07-07 | 状态: ✅ 已完成

---

## 1. 概述

整合事件管道 + AKShare 实时行情 + 事件风险标签的**日报生成Agent**，支持手动触发生成和历史浏览。

**本次更新 (2026-07-07)**：新增 3 个可视化图表（事件环形图、指标卡片、行业柱状图），热力图改为使用简报存储快照数据，风险预警覆盖 B 级事件，时区修正为北京时间，行业数据增加 THS 回退源。

---

## 2. DailyBriefing Agent (`agents/daily_briefing.py`)

### 6大板块

| # | 板块 | 数据来源 | 可视化 |
|---|------|----------|--------|
| 一 | **执行摘要** | LLM + 事件 + 行业摘要 | 🆕 事件等级环形图 (ECharts donut) + S/A/B/C 统计卡片 |
| 二 | **市场快照** | AKShare 三大指数 + 行业板块 | 🆕 4 列指标卡片网格 (事件总数 / S级 / A级 / 活跃源) |
| 三 | **今日重要事件** | 事件管道 EventCard | Top 8 事件卡片列表 |
| 四 | **走势预测速览** | LLM + 事件上下文 | 文本 |
| 五 | **行业热力图** | briefing.sector_heatmap_json (快照) | ECharts treemap + 🆕 涨跌 Top10 横向柱状图 |
| 六 | **风险预警** | EventCard.risk_flags (S/A/B级) + 指数 → LLM | 风险条目列表 |

### 生成流程

```
Phase 1: 数据采集
  ① 收集 EventCard (当日北京时间, 或最近20条兜底)
  ② 统计事件分布 (S/A/B/C)
  ③ 获取行业板块数据 → MarketDataAgent.fetch_sector_performance()
     EM 3次重试 → 失败则 THS 回退 (stock_board_industry_summary_ths)
  ④ 获取三大指数数据 → MarketDataAgent.fetch_index_data()
  ⑤ 聚合风险标签 → 从 S/A/B 级事件提取 risk_flags

Phase 2: LLM 生成
  ⑥ 各板块 LLM 生成 (传入真实数据上下文)
  ⑦ 组装完整 Markdown 报告 → full_report_md
  ⑧ 持久化到 DailyBriefing 表
```

### 关键方法

| 方法 | 说明 |
|------|------|
| `generate(target_date, force=False)` | 主入口。force=True 时删除旧缓存重新生成 |
| `_get_today_events(target_date)` | 获取当日事件 (**UTC+8 北京时间**, 含 risk_flags) |
| `_build_index_context(market_agent)` | 获取三大指数数据，返回文本摘要给 LLM |
| `_build_risk_context(events)` | 🆕 从 **S/A/B** 级事件中聚合 risk_flags (B级限2个/事件) |
| `_summarize_sectors(sector_data)` | 将30个行业压缩为 top-10 摘要 |
| `_generate_section(llm, section_name, **context)` | 调用 LLM 生成单个板块 |
| `_build_markdown(briefing)` | 组装完整 Markdown 报告 |

### 容错设计

- AKShare EM 调用失败 → 3次指数退避重试 → THS 回退
- 指数数据为空 → `"今日指数数据暂未更新（非交易时间或数据源延迟）"`
- 行业数据为空 → `"行业数据暂无"`
- 风险标签全部为空 → 提示 LLM 基于事件标题自行判断
- 🆕 风险输出无结构化行 → 回退到全文作为单条告警
- 单指数失败不影响其他指数 (独立 try/except)

---

## 3. 简报页面 (`/briefing`)

- **扁平化新闻稿设计** — 报刊报头 + 编号板块 + 细线分隔
- 日期选择器 → 浏览历史简报
- "生成简报"按钮强制重新生成 (force=True)
- 🆕 **3 个可视化图表** (全部使用存储快照，无外部 API 调用)：

| 图表 | 位置 | 类型 | 数据源 |
|------|------|------|--------|
| 事件等级环形图 | Section 1 | ECharts donut (中心显示总数) | `briefing.event_stats_json` |
| 关键指标卡片 | Section 2 | 4列 CSS Grid 卡片 | `briefing.key_numbers_json` |
| 行业涨跌柱状图 | Section 5 | ECharts 横向 bar (涨幅Top5+跌幅Top5) | `briefing.sector_heatmap_json.sectors` |

- 行业热力图 treemap 使用存储快照 (历史简报不再显示当前实时数据)
- `|markdown` Jinja2 过滤器转换 LLM 输出
- 页面卸载时 `ChartResizeManager.disposeAll()` 清理所有图表

### 响应式布局

| 断点 | 行为 |
|------|------|
| >768px | 环形图+卡片并排; 指标卡片 4 列 |
| ≤768px | 环形图+卡片纵向; 指标卡片 2 列 |
| ≤480px | 卡片 1 列 |

---

## 4. API端点

```
GET  /api/v1/briefing/latest          → 最新简报
GET  /api/v1/briefing/<YYYY-MM-DD>    → 指定日期简报
POST /api/v1/briefing/generate        → 强制重新生成 (force=True)
GET  /api/v1/briefing/dates           → 可用日期列表 (最近90天)
```

---

## 5. 数据模型

```python
class DailyBriefing(db.Model):
    date: Date                  # 简报日期 (unique)
    title: str                  # "BubbleEvent 每日投资简报 — 2026年06月27日"
    executive_summary: Text     # 核心摘要
    market_snapshot_json: JSON  # {"summary": "markdown", "timestamp": "iso"}
    top_events_json: JSON       # Top 10事件 (含risk_flags, event_type)
    event_stats_json: JSON      # {"total": N, "S": N, "A": N, "B": N, "C": N}
    prediction_summary_json: JSON  # {"summary": "markdown", "predictions": []}
    sector_heatmap_json: JSON   # {"sectors": [{name, change_pct, up_count, down_count}], "updated_at": "iso"}
    key_numbers_json: JSON      # {total_events, s_level, a_level, active_sources, sectors_updated, timestamp}
    risk_alert_json: JSON       # [{"alert": str, "level": "warning"|"info"}, ...] (max 5)
    full_report_md: Text        # 完整Markdown
    sources_count: int
    articles_processed: int
    generated_at: DateTime
```

---

## 6. 定时触发

```python
# config.py (已实现)
CELERY_BEAT_SCHEDULE["daily-briefing-morning"] = {
    "task": "tasks.processing.process_full_pipeline",
    "schedule": crontab(hour=0, minute=30),  # 08:30 CST = 00:30 UTC
}
```

注意：定时任务调用时使用 `force=False`（避免重复生成）；用户手动点击使用 `force=True`。

---

## 7. 依赖的数据接口

| 接口 | 提供方 | 说明 |
|------|--------|------|
| `MarketDataAgent.fetch_sector_performance()` | AKShare EM → THS fallback | 30个行业板块涨跌幅 (EM 3次重试, 失败回退 THS) |
| `MarketDataAgent.fetch_index_data(code)` | AKShare | 上证/深证/创业板日线 (自动sh/sz前缀) |
| `EventCard.risk_flags_json` | Card Generation Agent | 🆕 S/A/B级事件的风险标签 |
| `ChartResizeManager.disposeAll()` | app.js | 🆕 页面卸载前清理所有图表和 ResizeObserver |

---

## 8. 关键文件

| 文件 | 说明 |
|------|------|
| `agents/daily_briefing.py` | 简报生成Agent (~370行) |
| `agents/market_data.py` | 行情数据 (🆕 fetch_sector_performance 三重试+THS回退) |
| `app/routes/web_briefing.py` | Web+API路由 |
| `app/templates/briefing.html` | 🆕 新闻稿界面 + 3个可视化图表 + 图表清理 |
| `app/static/css/app.css` | 简报样式 (~250行, 🆕 .briefing-donut-row, .briefing-metrics-grid 等) |
| `app/static/js/app.js` | 🆕 ChartResizeManager.disposeAll() |
| `models/briefing.py` | DailyBriefing 数据模型 |
| `tasks/processing.py` | Celery 定时任务 (🆕 修复 collect_all_sources 导入) |

---

## 9. 变更记录

| 日期 | 变更 |
|------|------|
| 2026-07-07 | 🆕 新增 3 个可视化图表 (事件环形图、指标卡片、行业柱状图) |
| 2026-07-07 | 🆕 热力图改为使用简报存储快照 (不再调用实时 API) |
| 2026-07-07 | 🆕 行业数据接入 THS 回退源 (EM 3次重试失败后自动切换) |
| 2026-07-07 | 🆕 风险预警覆盖 B 级事件 + 输出解析容错增强 |
| 2026-07-07 | 🆕 时区修正为 UTC+8 北京时间 |
| 2026-07-07 | 🆕 ChartResizeManager.disposeAll() 页面卸载清理 |
| 2026-07-07 | 🔧 修复 process_full_pipeline 缺少 collect_all_sources 导入 |
| 2026-07-07 | 🔧 修复 key_numbers_json KV 行中文标签映射 |
| 2026-06-27 | 行业热力图接入 AKShare 实时数据 |
| 2026-06-27 | 风险预警接入 EventCard.risk_flags + 三大指数数据 |
| 2026-06-27 | 市场快照接入三大指数 (上证+深证+创业板) + 行业板块摘要 |
| 2026-06-27 | `fetch_index_data` 自动检测 sh/sz 前缀 |
| 2026-06-27 | `generate()` 新增 `force` 参数，手动触发强制重新生成 |
| 2026-06-27 | UI 改为扁平化新闻稿设计，新增 `|markdown` Jinja2 过滤器 |
| 2026-06-26 | 初始版本 (6板块骨架, 数据占位) |
