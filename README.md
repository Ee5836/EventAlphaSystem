# 🔥 EventAlphaSystem — 热点事件驱动投资 Agent 系统

> **"市场对信息的反映不是终点，而是新一轮认知的起点。"**
>
> 一个会复盘的、持续进化的中文金融事件智能分析平台。自动采集、验证、分析热点事件，生成结构化事件卡片与投资简报，支持 AI 研究对话与股票走势预测。

---

## 📸 系统概览

```
┌───────────────────────────────────────────────────────────┐
│                  EventAlphaSystem                          │
├───────────────────────────────────────────────────────────┤
│  ① 事件管道     ② 源管理      ③ AI 研究助手               │
│  采集→提取→      自定义URL+     浮动对话窗口                │
│  聚类→验证→      API/网页源     语义搜索排序                │
│  统计评分        开关控制        事件聚焦                   │
│                                                           │
│  ④ 行情+预测    ⑤ 每日简报     ⑥ Bubble 网络              │
│  热点→股票       AKShare行情     事件因果网络               │
│  多行业匹配      6板块新闻稿      5维边强度                  │
│  走势预测        热力图+预警      因果发现                   │
│                                                           │
│  基础设施: Flask | SQLAlchemy | Celery | ECharts           │
│  LLM: DeepSeek v4-flash / 通义千问 / 智谱 / Ollama       │
│  前端: SPA 客户端路由 | 液态玻璃 UI | Terminal Noir 主题   │
└───────────────────────────────────────────────────────────┘
```

## 🚀 核心功能

| 模块 | 功能 | 状态 |
|------|------|:----:|
| **事件管道** | 全自动采集→提取→聚类→验证→评分→卡片生成 | ✅ |
| **信息源管理** | 多源接入（财联社/AKShare/通用爬虫），用户可自定义添加/开关 | ✅ |
| **AI 研究助手** | 首页全对话界面 + 右下角浮窗小助手，同一智能体两种形态 | ✅ |
| **行情预测** | 热点事件→关联股票→走势预测 + ECharts 可视化 | ✅ |
| **每日简报** | AKShare 6板块新闻 + 行情热力图 + 预警信号 | ✅ |
| **Bubble 网络** | 事件因果网络 + 5维边强度（语义/NPMI/时间衰减/Wilson/验证） | 🚧 |
| **学习复盘** | 预测账本 + T+N 自动复盘 + 权重自更新 | 📋 |
| **API 服务** | RESTful API + 结构化事件数据 JSON 接口 | ✅ |

> ✅ 已完成 &nbsp; 🚧 建设中 &nbsp; 📋 规划中

## 🏗️ 多 Agent 协作体系

系统由 **11 个专职 Agent** 组成管道式处理链：

```
Scout(采集) → Extraction(提取) → Clustering(聚类) → Verification(验证)
    → Scoring(评分) → Causal Reasoning(因果推理) → Market Mapping(资产映射)
    → Market Data(行情验证) → Prediction(走势预测) → Card Generation(卡片生成)
```

每个 Agent 只负责一个明确子任务，上游输出作为下游输入，各阶段可独立验证与调试。

## 🛠️ 技术栈

| 层次 | 技术 |
|------|------|
| **Web 框架** | Flask + Jinja2 + Bootstrap 5 |
| **数据库** | SQLAlchemy + SQLite (dev) / PostgreSQL (prod) |
| **LLM** | DeepSeek v4-flash (主) / 通义千问 / 智谱 / Ollama |
| **嵌入模型** | BAAI/bge-small-zh-v1.5 (本地离线) |
| **行情数据** | AKShare |
| **可视化** | ECharts + Bootstrap Icons |
| **任务队列** | Celery + Redis |
| **前端架构** | SPA 客户端路由 (History API) + 8 片段 API + SSR 回退 |
| **UI 主题** | Terminal Noir + Gold 深色主题 + 液态玻璃模糊效果 |

## 📦 快速开始

### 环境要求

- Python 3.10+
- Redis (Celery 消息队列)
- Git

### 安装

```bash
# 克隆仓库
git clone https://github.com/Ee5836/EventAlphaSystem.git
cd EventAlphaSystem

# 创建虚拟环境
python -m venv venv
# Windows
venv\Scripts\activate
# Linux/Mac
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env，填入你的 LLM API Key
```

### 启动

```bash
# 启动 Flask 开发服务器
python run.py
# 访问 http://localhost:5000
```

### 触发事件管道

```bash
curl -X POST http://localhost:5000/api/v1/pipeline/trigger
```

## 📁 项目结构

```
EventAlphaSystem/
├── agents/              # 11 个专职 Agent（采集/提取/聚类/验证/评分/推理/预测/卡片）
├── app/                 # Flask Web 应用
│   ├── routes/          # 路由（Web + API + 片段）
│   ├── templates/       # Jinja2 模板 + 片段 + 组件
│   ├── static/          # CSS/JS/第三方库
│   └── utils/           # 序列化器/验证器/行业映射
├── assistant/           # AI 研究助手（对话管理 + 推理 + 工具调用）
├── docs/                # 功能文档
├── llm/                 # LLM 工厂（DeepSeek/千问/智谱/Ollama）
├── models/              # 14 个数据模型（SQLAlchemy）
├── pipeline/            # 管道编排器
├── sources/             # 信息源连接器（CLS/AKShare/通用/智能爬虫）
├── tasks/               # Celery 定时任务
├── tests/               # 测试
├── utils/               # 并发工具/随机种子
├── config.py            # 配置文件
├── run.py               # 启动入口
└── requirements.txt     # Python 依赖
```

## 📖 文档

| 文档 | 内容 |
|------|------|
| [功能总览](docs/00_overview.md) | 系统全景 + 联动关系 |
| [事件管道](docs/01_event_pipeline.md) | 采集→提取→验证→评分全链路 |
| [信息源管理](docs/02_source_management.md) | 源注册/采集/开关机制 |
| [AI 助手](docs/03_ai_assistant.md) | 对话管理 + 工具调用 + 推理链 |
| [行情预测](docs/04_market_prediction.md) | 事件→股票映射 + 走势预测 |
| [每日简报](docs/05_daily_briefing.md) | 板块新闻 + 行情 + 预警 |
| [Bubble 网络](docs/06_bubble.md) | 事件因果网络 + 边强度 |
| [项目报告书](BubbleEvent_项目报告书.md) | 完整架构设计文档 |
| [项目结构](PROJECT_STRUCTURE.md) | 详细文件级结构说明 |

## ⚠️ 免责声明

> **本系统输出的所有内容仅为事件研究与市场分析参考，不构成任何形式的投资建议。**
>
> 市场价格的变动受多种因素影响，可能提前反映或过度反映相关信息。投资决策应结合个人风险承受能力，并在专业顾问指导下做出。

## 📊 项目统计

| 指标 | 数值 |
|------|:----:|
| 总文件数 | 117 |
| Python 文件 | 73+ |
| 数据模型 | 14 |
| Agent 数量 | 11 |
| 路由总数 | 64 |
| 信息源连接器 | 4 |
| Bug 修复 | ~50 |

## 📄 License

MIT License

---

<p align="center">
  <b>EventAlphaSystem</b> — 不只告诉你发生了什么，还会追踪后来怎么样了、判断对不对、从中能学到什么。
</p>
