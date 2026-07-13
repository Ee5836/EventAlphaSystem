# ② 用户自定义信息源管理 (Source Management)

> 核心功能：用户自行添加网站URL作为分析源，自主决定每个源是否参与管道
> 更新: 2026-06-28 | 状态: ✅ 已完成 + 智能爬虫 + AKShare扩展

---

## 1. 概述

支持 **API 端点 / 网页爬取 / RSS / AKShare** 四种类型，每个源可独立控制启用/停用。系统源开关**直接生效**于采集管道，用户源通过 `GenericConnector` 或 `SmartCrawlerConnector` 自动适配。

## 2. 功能详情

### 2.1 源管理页面 (`/sources`)

- 玻璃质感统计卡片：总源数 / 已启用(金accent) / 已停用 / 用户自定义
- 玻璃面板包裹的源表格：名称+标签、类型徽章、可信度进度条、采集间隔、开关、最近采集状态、操作按钮组
- **立即采集**：每行首个按钮(金hover)，手动触发单源采集（实时反馈采集数量，更新行内状态）
- 添加/编辑模态框：名称、显示名、URL、类型(带高级配置区)、可信度滑块、采集间隔、标签、编码
- **网页类型高级配置**：CSS选择器（容器/标题/链接/正文/时间/下一页+最大页数，可选，留空自动检测）
- **测试连接**：添加前验证URL可达性
- 批量操作栏：玻璃pill + 已选计数badge + 全部启用/停用
- **删除确认**：玻璃Modal确认框（代替浏览器confirm）
- **无刷新交互**：toggle/delete/collect/save 全部内联DOM更新，不再整页reload
- 系统源保护：不可删除，仅可切换开关+调整可信度

### 2.2 系统预置源

| 名称 | 显示名 | 类型 | 可信度 | 采集间隔 | 连接方式 |
|------|--------|------|--------|----------|----------|
| cls | 新浪财经 | api | 0.70 | 30分钟 | 专用连接器 (CLSConnector) |
| 36kr | 36氪快讯 | rss | 0.65 | 30分钟 | RSS Feed → GenericConnector |
| ak_cctv | 央视新闻 | api | 0.88 | 60分钟 | AKShare news_cctv() |
| ak_futures | 上期所快讯 | api | 0.75 | 30分钟 | AKShare futures_news_shmet() |

> 合计：4个系统源，每次全量采集 ≈ **92篇**文章

### 2.3 用户自定义源 + SmartCrawler

用户可添加三种类型，自动路由到对应连接器：

| 类型 | 连接器 | 采集策略 | 说明 |
|------|--------|----------|------|
| **API 端点** | GenericConnector | HTTP GET → 智能JSON解析 → 标准化文章 | 自动匹配常见字段 (title/url/content等)，支持嵌套结构 |
| **网页爬取** | **SmartCrawlerConnector** | 静态httpx(快速) → Playwright(JS页面回退) → DOM文章列表自动检测 | 用户可选填CSS选择器精确指定 |
| **RSS/Atom** | GenericConnector | XML解析 (RSS 2.0 + Atom) | 自动识别feed类型 |

### 2.4 SmartCrawler — 智能网页文章检测

核心算法：
1. **DOM 扫描**：查找所有含链接+文本的元素，排除导航/页脚/侧栏
2. **聚类评分**：按父容器分组，评分维度 = 链接存在(+3) + 文本长度(+3) + 标题(+2) + 时间元素(+1) + 结构一致性(+5)
3. **文章提取**：每篇提取标题/URL/正文/摘要/发布时间
4. **Playwright 回退**：静态内容不足时自动启用 JS 渲染
5. **分页支持**：用户可选下一页选择器 + 最大页数

实测效果：
| 网站 | 检测容器 | 提取文章 |
|------|---------|---------|
| 新浪财经首页 | 112 (评分332) | **45篇** |
| 东方财富首页 | 225 (评分642) | **45篇** |
| 财联社电报 | JS渲染 | 4篇(静态)→更多(Playwright) |

### 2.5 AKShare 数据源

通过 `sources/akshare.py` 的 `AkshareConnector` 封装 AKShare 新闻 API：
- `ak_cctv` → `ak.news_cctv()` — 央视新闻联播文字稿 (12篇/次)
- `ak_futures` → `ak.futures_news_shmet()` — 上期所实时快讯 (20篇/次)

新增 AKShare 源只需在 `FUNCTION_MAP` 中注册函数映射即可。

## 3. API端点

```
GET    /api/v1/sources                  → 列表 (支持 ?active=true/false 过滤)
POST   /api/v1/sources                  → 创建 (name/display_name/base_url/source_type/config/...)
GET    /api/v1/sources/<id>             → 详情
PUT    /api/v1/sources/<id>             → 更新 (系统源仅允许 is_active/credibility/poll_interval)
DELETE /api/v1/sources/<id>             → 删除 (系统源禁止)
POST   /api/v1/sources/<id>/toggle      → 切换启用/停用
POST   /api/v1/sources/<id>/collect     → ★ 手动采集单个源
POST   /api/v1/sources/test-connection  → 测试URL (不持久化)
POST   /api/v1/sources/batch-toggle     → 批量切换 (ids + is_active)
```

config_json 支持的字段（网页类型）：
```json
{
    "container_selector": ".news-list > li",
    "title_selector": "h3.title",
    "url_selector": "a",
    "content_selector": "p.summary",
    "time_selector": "time",
    "next_page_selector": "a.next",
    "max_pages": 3,
    "encoding": "gbk"
}
```

## 4. 数据模型

```python
class NewsSource(db.Model):
    name: str              # 唯一标识 (英文)
    display_name: str      # 显示名称
    base_url: str          # API/RSS/网页 URL 或 akshare://协议
    source_type: str       # "api" | "webpage" | "rss"
    is_system: bool        # 系统预置(不可删) vs 用户添加
    is_active: bool        # ★ 开关 (系统源也有效)
    credibility: float     # 0.0-1.0 基准可信度
    poll_interval: int     # 专属采集间隔(秒)
    tags_json: JSON        # 自定义标签
    config_json: JSON      # ★ CSS选择器/分页等高级配置
    last_fetch_at: DateTime
    last_fetch_status: str # success / timeout / error
    last_fetch_count: int
```

## 5. 关键文件

| 文件 | 说明 |
|------|------|
| `app/routes/api_sources.py` | 9个API端点 (含 collect) |
| `app/routes/web_sources.py` | `/sources` 页面路由 |
| `app/templates/sources.html` | ★ 玻璃质感管理界面 (含CSS选择器高级配置+无刷新交互) |
| `models/source.py` | NewsSource + RawArticle |
| `sources/base.py` | AbstractSourceConnector 抽象基类 |
| `sources/smart_crawler.py` | **SmartCrawlerConnector** — DOM自动检测+文章提取 (341行) |
| `sources/generic.py` | **GenericConnector** — API/RSS/网页用户源通用适配器 |
| `sources/akshare.py` | **AkshareConnector** — AKShare新闻函数封装 |
| `sources/registry.py` | 连接器注册表 (cls/ak_cctv/ak_futures/smart_crawler) |
| `sources/cls.py` | 新浪财经专用连接器 |
| `utils/seed.py` | 4个系统源种子数据 |
| `app/static/css/app.css` | ~170行 source-specific 玻璃UI样式 |
