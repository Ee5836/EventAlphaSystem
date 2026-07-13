# ③ AI 研究助手 (AI Research Assistant)

> 核心功能：全局系统分析中枢 · 自由浮动窗口 · 首页玻璃风格 · 跨页面会话保持 · 多维语义搜索 · 历史管理+删除
> 更新: 2026-07-12 | 状态: ✅ 已完成 (对话历史管理+批量清空+分页)

---

## 1. 架构

```
任意页面右下角 ◆ FAB 按钮
    │
    ▼
自由浮动窗口 (可拖拽 + 缩放 + 全屏)
    │
    ▼
ResearchAssistantAgent — 意图分类 + 并行检索
    │
    ├── ① 事件检索 (_lookup_events) ⚡v2多维语义排序
    │      └── 旧: 关键词计数 → 新: event_relevance(语义50%+标签25%+时效15%+等级10%)
    │      └── text2vec 嵌入余弦相似度 + NPMI标签关联 + 指数时间衰减 + 等级加权
    │
    ├── ② 行情检索 (_lookup_market)
    │      └── 4层匹配: DB精确 → 行业映射 → 名称直搜 → 事件关联
    │      └── PriceSnapshot + PriceHistory (MA5/MA10/MA20 + MACD + RSI-14)
    │
    ├── ③ Bubble 统计 (_lookup_timeline_stats)
    │      └── 液态玻璃节点按类型/等级/状态分组计数 + 边按验证状态分组
    │
    ├── ④ 简报查询 (_lookup_briefing)
    │      └── 最新/指定日期简报 (执行摘要+市场快照+风险预警)
    │
    ├── ⑤ 走势预测 (_lookup_prediction) 🆕
    │      └── 实时价 + 20日K线 → MA/MACD/RSI 技术指标解读
    │
    ├── ⑥ 系统状态 (_lookup_system_stats) 🆕
    │      └── 聚合: 事件数/时间线规模/活跃源/简报日期
    │
    └── ⑦ 外部搜索 (仅当所有系统数据均为空时)
           └── DuckDuckGo → 补充背景信息
```

### 首页集成

```
首页中央输入框
    │  用户提问
    │  ↓ 后台会话处理
    │  ↓ 内联回答区显示 (玻璃卡片, fadeInUp)
    │  FAB 隐藏, 浮动窗口不出现
    │
    ├─→ 用户切换到其他页面
    │
    ▼
浮动窗口飞入动画 (ai-fly-in 0.55s)
  └── 恢复首页全部对话历史
```

---

## 2. 核心模块

### 2.1 ResearchAssistantAgent (`agents/research_assistant.py`)

**意图分类** — `_classify_intent()` 识别6种意图：
- `event` — 事件分析 (默认)
- `briefing` — 简报查询 ("今天有什么大事" / "帮我更新简报")
- `timeline` — Bubble 统计 ("有多少B级节点" / "Bubble网络状态")
- `prediction` — 走势预测 ("分析600519" / "茅台走势")
- `system` — 系统状态 ("系统运行怎么样")
- `general` — 通用知识

**并行检索** — 根据意图激活对应数据源，全部结果注入 LLM context。

**系统提示词**升级：
- 移除400字限制 → 按需扩展
- 新增6项能力描述
- 数据源标注，区分事实与推测

### 2.2 自由浮动窗口 (`app/static/js/assistant_widget.js`)

完全重写的零依赖 vanilla JS 组件：

| 功能 | 实现 |
|------|------|
| **拖拽** | 标题栏 mousedown/mousemove/mouseup，边界保留80px |
| **缩放** | 右下角把手，380×420 ~ 1200×900 |
| **全屏** | 切换铺满/恢复，保存原位置 |
| **持久化** | localStorage: 位置/尺寸/打开状态/会话ID |
| **跨页面** | DOM预渲染为正确状态 → 零闪烁切换 |
| **首页模式** | FAB隐藏，内联回答，离站飞入继承 |

**对外 API** (完全向后兼容):
```javascript
window._aiWidget.open()
window._aiWidget.close()
window._aiWidget.toggle()
window._aiWidget.quickAsk(question)
window._aiWidget.toggleFullscreen()
window._aiWidget.newSession()
window._aiWidget.loadSession(sid)
window._aiWidget.deleteSession(sid, event)
```

### 2.3 首页风格设计

完全对齐首页 "Terminal Noir + Gold" 设计语言：

| 属性 | 值 |
|------|-----|
| 窗口背景 | `rgba(18, 23, 31, 0.92)` + `backdrop-filter: blur(20px)` |
| 圆角 | `16px` (首页输入框同款) |
| 阴影 | `0 24px 64px rgba(0,0,0,0.7)` + 金色微边框 |
| 标题栏 | 金色渐变 `rgba(212,168,83,0.06)` |
| 发送按钮 | 圆形金色 `#d4a853`, 38×38px |
| 输入框 | 玻璃 pill `border-radius: 20px`, focus 金色光晕 |
| 用户气泡 | `rgba(212,168,83,0.08)` 金色底 |
| 快捷提问 | 首页 suggestion chips 同款玻璃药丸 |

### 2.4 ChatManager (`assistant/chat_manager.py`)
- 会话生命周期：创建 → 对话 → 删除
- 上下文窗口：保留最近20轮，超出自动摘要压缩
- 消息持久化到 ChatMessage 表
- 自动标题生成（首条消息截取50字）
- 跨页面会话保持（localStorage session_id）
- **会话列表分页** (2026-07-12): `list_sessions(offset, limit)` 返回 `(sessions, total)`
- **单条消息删除** (2026-07-12): `delete_message()` 删除用户消息时自动配对删除AI回复
- **批量清空** (2026-07-12): `delete_all_sessions()` 删除全部会话/消息/笔记
- **ResearchNote 级联** (2026-07-12): `delete_session()` 同步清除关联笔记，防止孤儿记录

---

## 3. 交互设计

| 操作 | 效果 |
|------|------|
| 点击右下角 ◆ 按钮 | 弹出玻璃窗口 (弹性动画) |
| 拖拽标题栏 | 自由移动 (保存位置) |
| 拖拽右下角把手 | 缩放窗口 (保存尺寸) |
| 点击全屏按钮 | 铺满全屏 / 恢复 |
| 点击 × / Esc | 关闭窗口 |
| 输入问题后 Enter | 意图识别 → 并行检索 → LLM 回答 |
| 首页发送问题 | 内联回答区显示，窗口不弹 |
| 首页 → 其他页面 | 窗口飞入动画继承对话 |
| 首页 🕐 按钮 | 展开历史面板 (会话列表+清空全部+加载更多) |
| 首页 ➕ 按钮 | 新建会话，清空当前对话 |
| 首页 清空全部 | 删除所有会话，确认后批量清除 |
| 切换会话 | 点击历史面板中的会话行 |
| 删除会话 | 历史面板每行hover显示× (confirm确认) |
| 删除消息 | 消息气泡hover显示圆形× (自动配对删除AI回复) |

---

## 4. API端点

```
GET    /api/v1/assistant/sessions              → 会话列表 (?offset=&limit=, 分页)
POST   /api/v1/assistant/sessions              → 创建会话
DELETE /api/v1/assistant/sessions              → 清空全部会话 (2026-07-12)
GET    /api/v1/assistant/sessions/<id>         → 会话详情+完整历史
DELETE /api/v1/assistant/sessions/<id>         → 删除会话 (级联清除消息+笔记)
POST   /api/v1/assistant/sessions/<id>/rename  → 重命名
POST   /api/v1/assistant/sessions/<id>/messages → 发送消息 (支持stream SSE)
DELETE /api/v1/assistant/sessions/<id>/messages/<mid> → 删除单条消息+配对AI回复
POST   /api/v1/assistant/quick-search          → 快速搜索
POST   /api/v1/assistant/crawl                 → 爬取URL
GET    /api/v1/assistant/industries            → 行业分类列表
```

---

## 5. 数据模型

```python
class ChatSession(db.Model):
    title: str          # 自动从首条消息生成

class ChatMessage(db.Model):
    session_id → ChatSession
    role: str           # "user" / "assistant"
    content: Text
    reasoning_chain_json: JSON
    tool_calls_json: JSON
    sources_json: JSON
```

---

## 6. 关键文件

| 文件 | 说明 |
|------|------|
| `agents/research_assistant.py` | 主Agent — 6数据源并行检索 + 意图分类 |
| `assistant/chat_manager.py` | 会话管理 + 上下文压缩 |
| `assistant/reasoning.py` | 推理链数据结构 |
| `assistant/tools/web_search.py` | DuckDuckGo搜索 |
| `assistant/tools/web_crawler.py` | 静态+动态爬虫 |
| `assistant/tools/api_caller.py` | 通用 HTTP API 调用 |
| `app/routes/web_assistant.py` | Web+API路由 |
| `app/static/js/assistant_widget.js` | 自由浮动窗口 (拖拽+缩放+全屏+持久化) |
| `app/static/css/app.css` | 首页玻璃风格 (.ai-window-*) |
| `app/templates/assistant.html` | 独立助手页面 (居中大窗口) |

---

## 7. 更新日志

### 2026-06-28 — 三合一重大升级

**能力升级**:
- 新增 4 个系统级检索方法: 时间线统计、简报查询、走势预测、系统状态
- 意图分类 (_classify_intent): 6种意图自动激活对应数据源
- 并行检索 + 综合回答，token 按复杂度动态调整 (1536-2048)

**窗口自由化**:
- 完全重写 assistant_widget.js: 拖拽 + 缩放 + 全屏 + localStorage 持久化
- 跨页面零闪烁保持 (DOM预渲染 + 会话恢复)
- 首页隐藏FAB，内联回答区，离站飞入继承对话
- `/assistant` 改为独立全屏页面

**风格统一**:
- 全面替换为首页 "Terminal Noir + Gold" 玻璃风格
- 16px圆角 · 20px blur · 金色光晕 · 深色层叠阴影
- 用户气泡金色底 · 输入框首页pill + focus金光

**其他**:
- 首页泡泡背景修复 (7天事件回退 + JS占位兜底)
- 窗口0×0容器避免遮挡泡泡渲染

### 2026-07-12 — 对话历史管理+删除功能

**首页历史管理面板**:
- header 右侧新增 🕐 历史对话 + ➕ 新对话 按钮
- 点击历史按钮展开下拉面板: 会话列表(最近排序)、每行 hover 显示 × 删除、清空全部按钮
- 分页加载(每次30条) + "加载更多"按钮
- 对话状态 `.chatting` 时工具栏自动隐藏
- AI 禁用时工具栏 CSS 隐藏

**删除功能完善**:
- 新增 `DELETE /api/v1/assistant/sessions` 批量清空所有会话
- `list_sessions` 支持分页参数 `?offset=&limit=`，返回 `total` 计数
- `delete_session()` 级联清除 `ResearchNote`，防止孤儿记录
- 单条消息删除自动配对删除紧随的 AI 回复
- 防双击 loading 态: `deleteSession()` 点击后按钮 disabled + 低透明度
- 删除当前会话后自动创建新空会话 + 同步清除首页/widget 展示
- 浮窗 widget `deleteSession()` 同步更新首页 DOM

**SPA 兼容**:
- 回到首页时 `setTimeout(80ms)` 重新调用 `_initHomepageHistory()` 绑定事件
- `newSession()` 同步清除首页 chat body + 退出 chatting 模式

**JS 新增函数**: `_initHomepageHistory()`, `refreshHomeSessions(append)`, `loadHomeSession(sid)`, `_exitChatMode()`
**CSS 新增**: `~100行` (.home-header-actions, .home-session-panel, .home-session-row, .home-session-row-del, .home-session-clear-all, .home-session-load-more + light theme)
