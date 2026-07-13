# BubbleEvent 功能总览

> 热点事件驱动投资 Agent 系统 — 功能模块索引
> 最后更新: 2026-07-12 | 版本: Phase 1A-1K + 架构优化P1 + 多维边强度融合(v5) + 数理方法全系统应用 + Git仓库 + 管道速度优化P2 + SPA架构改造 + 手动更新功能 + 全面Bug修复(~50项) + 嵌入模型切换(bge-small-zh-v1.5)

---

## 系统架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                        BubbleEvent 系统全景                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐             │
│  │ ① 事件管道    │   │ ② 用户源管理  │   │ ③ AI研究助手 │             │
│  │ 采集→提取→    │   │ 自定义URL+    │   │ 浮动对话窗口 │             │
│  │ 多维聚类→     │   │ API/网页源    │   │ 语义搜索排序 │             │
│  │ 验证→统计评分 │   │ +开关控制     │   │ 事件聚焦     │             │
│  └──────┬───────┘   └──────┬───────┘   └──────┬───────┘             │
│         │                  │                  │                      │
│         └──────────────────┼──────────────────┘                      │
│                            │                                         │
│                            ▼                                         │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐             │
│  │ ④ 行情+预测  │   │ ⑤ 每日简报   │   │ ⑥ Bubble │             │
│  │ 热点→股票    │   │ AKShare实时  │   │ 事件网络+    │             │
│  │ 统计事件相关 │   │ 行情接入+    │   │ 5维边强度+   │             │
│  │ 数据自动落库 │   │ 6板块新闻稿  │   │ 因果发现+    │             │
│  │              │   │ +热力图+预警 │   │ 边验证       │             │
│  └──────────────┘   └──────────────┘   └──────────────┘             │
│                                                                      │
│  公共基础设施: Flask | SQLAlchemy | Celery | LLM工厂 | ECharts          │
│  数理引擎: edge_strength | stats_utils (语义+NPMI+时间衰减+Wilson)       │
│  数据层: SQLite(dev) | Redis | Playwright                                │
│  架构基础设施: 错误中间件 | 序列化层 | 请求验证 | 集中日志 | 自动注册源  │
│  UI: Terminal Noir + Gold 深色主题 | 首页极简AI输入 | 全局液态玻璃       │
│  前端架构: SPA客户端路由(router.js) | 8片段API | History API | SSR回退  │
│  手动更新: 全局管道触发按钮 | 批量采集全部源 | 仅处理API | Toast通知     │
└─────────────────────────────────────────────────────────────────────┘
```

## 项目统计

| 指标 | 数值 |
|------|------|
| 总文件 | 110+ |
| Python 文件 | 73+ |
| 模板 | 22+ (含 fragments + partials) |
| 路由总数 | 64 (9 Web + 8 片段API + 47 数据API) |
| Agent 数量 | 11 |
| 数理模块 | 2 (edge_strength + stats_utils) |
| 数据模型 | 14 (全部添加 to_dict() 序列化) |
| 信息源连接器 | 4 (CLS / AKShare / SmartCrawler / Generic + @register_connector) |
| Git 仓库 | `https://github.com/Ee5836/EventAlphaSystem` |
| CSS规模 | ~4900行 + 5个SVG滤镜 |
| Bug修复 | ~50 (2026-07-12 全面审计+修复+测试) |
| 架构基础设施 | 5个 (errors / serializers / validators / 集中日志 / fragment路由) |
| UI升级组件 | 22+ (Sidebar/Topbar/Card/Modal/Toast/SourceStat/SourceTable/ConfirmModal等全站玻璃化) |
| SPA路由 | 客户端router.js (History API + 片段加载 + 页面生命周期 + SSR回退) |

## 功能模块索引

| 编号 | 功能模块 | 文档 | 核心能力 | Agent | 页面 | 状态 |
|------|---------|------|---------|-------|------|------|
| ① | **事件管道** | [01_event_pipeline.md](./01_event_pipeline.md) | 全渠道采集→批量提取→聚类→验证→评分→卡片 + 手动触发 + 仅处理模式 | 6个 | `/events`, `/events/<id>` | ✅ |
| ② | **用户源管理** | [02_source_management.md](./02_source_management.md) | 自行添加API/webpage源+启用/停用+测试连接 | — | `/sources` | ✅ |
| ③ | **AI研究助手** | [03_ai_assistant.md](./03_ai_assistant.md) | 自由浮动窗口+首页内联 · 6数据源并行检索 · 全局分析中枢 · 拖拽/缩放/全屏 | 1个 | 浮动组件（全页面）+ `/assistant` | ✅ |
| ④ | **行情+预测** | [04_market_prediction.md](./04_market_prediction.md) | 热点→股票自动推荐 + 全图加载 + 预测叠加 + 数据自动落库 + sessionStorage缓存持久化 + ResizeObserver自适应 | 2个 | `/prediction` | ✅ |
| ⑤ | **每日简报** | [05_daily_briefing.md](./05_daily_briefing.md) | 全源刷新+AKShare实时行情+6板块新闻稿+行业热力图+风险预警 | 1个 | `/briefing` | ✅ |
| ⑥ | **Bubble** | [06_bubble.md](./06_bubble.md) | 事件网络+自动构建+5维边强度(语义+NPMI+时间衰减+等级+Wilson)+因果发现+边验证+5种节点类型+缩放浮现全屏细节+液态玻璃节点+SVG滤镜 | 1个 | `/timeline` | ✅ |
| 🏠 | **投资仪表盘** | — | 首页DeepSeek风格极简布局: 中央AI输入框+建议卡片+事件液态玻璃泡泡背景动画(弹性碰撞物理引擎) | — | `/` | ✅ |

## 技术栈

| 层次 | 技术 | 说明 |
|------|------|------|
| Web框架 | Flask 3.1 + Jinja2 | 左侧功能栏布局，深色金融终端主题 |
| 数据库 | SQLAlchemy + SQLite(dev) | ORM + 14 个模型 |
| 任务队列 | Celery + Redis | 定时采集 + 异步管道 |
| LLM | DeepSeek v4-flash | 工厂模式(通义千问/智谱备选) |
| 嵌入模型 | BAAI/bge-small-zh-v1.5 (本地) | 512维中文语义向量, HF离线模式 |
| 行情数据 | AKShare | DB缓存优先 + 3次重试 + 合成数据兜底 |
| 图表 | ECharts 5.5 (本地) | K线+预测叠加 / 力导向图 / 热力图 |
| 前端资源 | Bootstrap 5.3.3 + Icons (本地) | Terminal Noir + Gold 设计系统 |
| SPA前端 | router.js (无框架客户端路由) | 拦截侧边栏→fetch片段→替换#appContent, History API回退, SSR完整回退 |
| UI材质 | SVG滤镜 + CSS backdrop-filter | 全局液态玻璃 (shuding/liquid-glass 技法): feDisplacementMap边缘畸变 + saturate()湿润光泽 + 镜面高光伪元素 + 20+组件升级 |

## Agent 清单

| # | Agent | 文件 | 所属模块 | 状态 |
|---|-------|------|----------|------|
| ① | Information Scout | `agents/scout.py` | 事件管道 | ✅ |
| ② | Event Extraction | `agents/extraction.py` | 事件管道 | ✅ 批量LLM |
| ③ | Event Clustering | `agents/clustering.py` | 事件管道 | ✅ |
| ④ | Credibility Verification | `agents/verification.py` | 事件管道 | ✅ |
| ⑤ | Event Scoring | `agents/scoring.py` | 事件管道 | ✅ |
| ⑥ | Card Generation | `agents/card_generation.py` | 事件管道 | ✅ |
| ⑦ | Research Assistant | `agents/research_assistant.py` | AI助手 | ✅ 6数据源并行检索 |
| ⑧ | Market Data | `agents/market_data.py` | 行情+预测 | ✅ AkShare+指标 |
| ⑨ | Prediction | `agents/prediction.py` | 行情+预测 | ✅ 多维融合 |
| ⑩ | Daily Briefing | `agents/daily_briefing.py` | 每日简报 | ✅ 6板块 |
| ⑪ | Timeline Builder | `agents/timeline_builder.py` | Bubble | ✅ 自动构建+因果发现+5维边强度+预测延伸+快照 |

## 数理引擎

| # | 模块 | 文件 | 核心方法 | 状态 |
|---|------|------|---------|------|
| S1 | Edge Strength | `agents/edge_strength.py` | 5维融合公式 + NPMI + 时间衰减 + Wilson + TagStatistics | ✅ |
| S2 | Stats Utils | `agents/stats_utils.py` | event_timeliness + event_relevance (语义+标签+时间+等级) | ✅ |

## 页面清单

| 路由 | 页面 | 所属模块 | 状态 |
|------|------|----------|------|
| `/` | 投资仪表盘 (首页Dashboard) | — | ✅ |
| `/events` | 事件列表页 (卡片流+等级筛选) | 事件管道 | ✅ |
| `/events/<id>` | 事件详情页 | 事件管道 | ✅ |
| `/sources` | 信息源管理 (CRUD+开关+测试) | 用户源管理 | ✅ |
| `/prediction` | 走势预测 (热门推荐+全图预测叠加+数据落库) | 行情+预测 | ✅ |
| `/briefing` | 每日简报 (6板块+热力图) | 每日简报 | ✅ |
| `/briefing?date=YYYY-MM-DD` | 历史简报 | 每日简报 | ✅ |
| `/timeline` | Bubble (力导向图+缩放浮现全屏细节+边验证) | Bubble | ✅ |
| `/assistant` | 独立助手全屏页面 | AI助手 | ✅ |

## 管道性能 (v3, 2026-07-07)

### fast_mode (默认, 跳过Timeline LLM分析)

| 阶段 | 方式 | 耗时 |
|------|------|:---:|
| Scout (采集) | 4源并行 + poll_interval保护 | ~2s |
| Extraction (提取) | 批量LLM (5篇/批 × 8并发) | ~21s |
| Clustering (聚类) | 5维相似度 + LLM合并 | ~55s |
| Verification (验证) | 五维度规则 (无LLM) | <1s |
| Scoring (评分) | 逐条LLM (8并发) | ~12s |
| Card Gen (卡片) | 逐条LLM (8并发) | ~8s |
| Timeline | fast_mode跳过 | — |
| **总计** | | **~97s** |

### 并发参数

| 参数 | 值 | 说明 |
|------|:--:|------|
| LLM_MAX_CONCURRENCY | 8 | 所有LLM阶段共用 |
| Extraction BATCH_SIZE | 5 | 每批文章数 |
| Scout | 4源并行 | poll_interval 30-60min |
| 管道触发 | 异步 | 即时返回 + 状态轮询 |

### 调度

| 任务 | 频率 | 说明 |
|------|:----:|------|
| 采集+自动加工 | 每小时 | 有新文章则自动链式处理 |
| 安全网加工 | 每4小时 | 兜底处理遗漏文章 |
| 每日简报 | 08:30 CST | 采集+加工一体化 |

每次全量采集 ≈ **92篇**，覆盖新浪财经/36氪快讯/央视新闻/上期所快讯。

## 模块间联动关系

```
用户自定义源 ──→ 事件管道 ──→ 事件卡片 ──→ Bubble 节点 (自动)
                    │              │
                    ├──→ 行情数据 ──→ 走势预测 ──→ DB落库 ──→ AI可查
                    │        │              │              │
                    │        └──────┬───────┘              │
                    │               │                      │
                    ├──→ Bubble ←──┘ (多维边强度+LLM, 液态玻璃)
                    │        │                             │
                    │        ├──→ 点击放大+边验证+覆层      │
                    │        │                             │
                    ├──→ 每日简报 (综合以上所有输出)        │
                    │                                      │
                    └──→ AI助手 ←── 统计数理引擎 ──────────┘
                         (语义搜索)    (edge_strength+stats_utils)
```

## 启动命令

```bash
cd D:\PyCharm_work\EventAgent
python run.py                    # 启动 Flask (端口5000)

# 触发管道
curl -X POST http://localhost:5000/api/v1/pipeline/trigger

# 仅采集
curl -X POST http://localhost:5000/api/v1/pipeline/trigger/scout
```
