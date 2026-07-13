# ⑥ Bubble（事件节点网络）

> 核心功能：液态玻璃节点网络可视化 + 自动构建 + 多维统计因果发现 + 边验证 + 点击放大动画 + 孤立事件侧边栏
> 更新: 2026-07-03 | 状态: ✅ 已完成 (v5 多维边强度融合: 语义+NPMI+时间衰减+等级加权+Wilson置信)

---

## 1. 概述

将孤立事件串联成**因果网络**，追踪"事件→影响→结果"的知识图谱。
**自动构建**：首次访问时自动从 EventCard 创建节点 + 规则匹配 + LLM因果发现。

### 设计语言

Bubble 节点采用 **Apple Liquid Glass（液态玻璃）** 材质，参考 Mikhail Bespalov 的 [CodePen](https://codepen.io/Mikhail-Bespalov/pen/MYwrMNy) 实现：

| 技术层 | 实现 |
|--------|------|
| 节点主体 | ECharts 径向渐变，高透明度 α 0.25-0.28 → 通透玻璃球体 |
| 镜面高光 | 顶点偏移 (30%/24%)，α 0.65-0.72 → 湿润玻璃反射点 |
| 容器底板 | `radial-gradient` 暗色渐变，无 blur 保持图区锐利 |
| SVG 滤镜 | `#liquid-glass-bespalov` — `feTurbulence(0.008)` + `feDisplacementMap(4)` + `feColorMatrix(saturate 1.8)` |
| 细节覆层 | `backdrop-filter: blur(20px) saturate(1.5)` — 弹层玻璃层次感 |
| UI 零件 | 图例/卡片/胶囊/按钮各层 `blur(4-12px) saturate(1.15-1.4)` |
| 节点发光 | `shadowBlur: 10` (正常) / `22` (hover) + 彩色光晕 |

> ⚠️ **设计原则**：图区主体保持锐利（无 backdrop-filter），玻璃效果仅用于弹层 UI 零件，避免整面模糊。

## 2. Bubble Agent (`agents/timeline_builder.py`)

### 工作流程

```
事件管道完成 → Stage 7: Bubble Build
    │
    ▼
① 自动构建 (auto_build_from_events):
    │  EventCard (S/A级) → TimelineNode
    │  节点类型: root_event / derived_event / market_reaction
    │  置信度: 来自 EventCard.credibility (默认0.6)
    │
    ▼
② 因果发现 (两层 + 多维强度融合):
    │  规则层: 多维统计公式 (语义+NPMI+Jaccard+时间衰减+等级) → 高区分度边 (~100条)
    │  LLM层: 语义因果分析 + 40%公式融合 → 高质量边 (2-5条)
    │  自动验证: strength≥0.55 + 标签重叠≥2 + 双方置信度≥0.7 → verified=True
    │
    ▼
③ 预测延伸 (自动):
    │  LLM生成下游预测节点 + 50%公式融合强度
    │
    ▼
④ 边验证 (手动/自动):
       confirmed → 绿色实线
       inferred → 灰色实线 (默认)
       refuted → 红色虚线
```

### 边强度多维融合公式 (v5)

旧公式：`strength = 0.35 + 0.05 × len(common_tags)` → **全部 0.40，无区分度**

新公式基于 2024-2025 年学术文献的5维度加权融合：

| 维度 | 权重 | 方法 | 文献依据 |
|------|------|------|---------|
| D1 语义相似度 | 35% | cosine(text2vec(title+desc)) | Naboka-Krell (2024) SBERT |
| D2 标签 NPMI | 25% | 归一化点互信息 | Williams (2022) Neural Computation |
| D3 Jaccard | 15% | \|A∩B\| / \|A∪B\| | 标签集合重叠系数 |
| D4 时间衰减 | 15% | exp(-ln2·Δt/T½) | Li (2025) IJICT 因果衰减 |
| D5 事件等级 | 10% | S=1.0, A=0.75, B=0.50, C=0.30 | Wu & Xu (2025) CKG-EIE |

```
strength = 0.35×semantic + 0.25×npmi + 0.15×jaccard + 0.15×time_decay + 0.10×level
         × reliability_factor (Wilson lower bound, 小样本惩罚)
```

**自适应半衰期**：S级事件60天 / A级30天 / B级14天 / C级7天，因果边比相关边衰减更慢。

**实际效果**：同域高相关 0.59-0.67 / 跨域低相关 0.23-0.49 / 旧值统一 0.40。

### 强度融合策略（按建边路径）

| 路径 | 方法 | 融合比例 |
|------|------|---------|
| 规则层 | `_auto_discover_links` Strategy 1 | 100% 公式 |
| LLM因果发现 | `_auto_discover_links` Strategy 2 | 60% LLM + 40% 公式 |
| 手动因果发现 | `discover_causal_links` | 60% LLM + 40% 公式 |
| 预测延伸 | `extend_predictions` | 50% LLM confidence + 50% 公式 |

### 节点类型与配色

| 类型 | 颜色 | 主体 α | 高光 α | 示例 |
|------|------|--------|--------|------|
| root_event | `#4f7cff` 深海蓝 | 0.28 | 0.72 | S级政策/宏观事件 |
| derived_event | `#7c5cfc` 宝石紫 | 0.26 | 0.68 | A级衍生事件 |
| prediction | `#d4913e` 古铜金 | 0.26 | 0.68 | LLM生成预测 |
| market_reaction | `#0ea57a` 深翡翠 | 0.25 | 0.65 | 市场反应 |
| verification | `#d9485e` 暗玫红 | 0.26 | 0.65 | 验证节点 |

### 边状态

| verified | 颜色 | 线型 | 含义 |
|----------|------|------|------|
| `None` | `#5c677d` 灰 | solid | 推断关联 (未验证) |
| `True` | `#22c55e` 绿 | solid | 已验证因果 |
| `False` | `#ef4444` 红 | dashed | 已证伪 |

### 节点过期机制

为避免面板节点过度拥挤，每个节点在创建时计算 `expires_at`。

#### 影响周期

| 事件等级/类型 | 影响周期 | 说明 |
|-------------|---------|------|
| S 级 | **90 天** | 系统性/宏观事件长期结构影响 |
| A 级 | **30 天** | 重大事件影响市场情绪数周 |
| B 级 | **14 天** | 中等事件，短期相关 |
| C 级 | **7 天** | 轻微事件，快速噪音 |
| 预测 T+3 | **3 天** | 短期预测快速过期 |
| 预测 T+7 | **7 天** | 中期预测 |
| 预测 T+30 | **30 天** | 长期预测 |
| market_reaction | **7 天** | 市场反应时效极短 |
| verification | **30 天** | 验证节点 |
| root_event (无等级) | **30 天** | 保守默认值 |

#### 策略

- **软过滤（默认）**: 过期节点保留在数据库，但从图查询中排除
- **用户切换**: 前端提供"显示已过期节点"复选框，过期节点以 35% 透明度显示
- **向后兼容**: `expires_at` 为 NULL 的现有节点视为永不过期
- **硬删除**: `POST /api/v1/timeline/cleanup` 物理删除过期超过 N 天的节点（含边级联删除）
- **管道自动清理**: Stage 7 完成后自动清理过期超过 90 天的节点

## 3. 数据模型

```python
class TimelineNode(db.Model):
    node_type: str       # root_event/derived_event/prediction/market_reaction/verification
    event_id → Event     # 关联事件
    title: str
    description: Text
    timestamp: DateTime
    status: str          # confirmed/predicted/refuted/pending
    confidence: float
    tags_json: JSON
    metadata_json: JSON
    expires_at: DateTime|null  # 过期时间，NULL 表示永不过期（向后兼容）

class CausalEdge(db.Model):
    source_node_id → TimelineNode
    target_node_id → TimelineNode
    relation_type: str   # causes/influences/correlates/contradicts
    strength: float      # 0.0-1.0
    logic_chain: Text
    verified: bool|null  # True=确认 / None=推断 / False=证伪
    created_by: str      # "rule_based" / "llm_auto" / "llm"

class TimelineSnapshot(db.Model):
    date: Date
    event_count: int
    edge_count: int
    graph_json: JSON
```

## 4. 可视化 (`/timeline`)

### 布局
- **全宽力导向图** — ECharts graph + force layout
- 摩擦阻尼 0.2（~15秒自然收敛）
- 浏览模式: 拖拽 ✓ 缩放 ✓ 滚轮 ✓
- 图底部提示 "点击节点查看事件详情"
- 图区容器: `radial-gradient` 暗色背景，**无 blur 保持锐利**

### 图例（玻璃条）
- 节点: 🔵根事件 🟣衍生事件 🟠预测 🟢市场反应 🔴证伪
- 边: ━ 灰 (推断关联) · ━ 绿 (已验证因果) · ┅┅ 红 (已证伪)
- 样式: `rgba(255,255,255,0.03)` + `border: 1px solid rgba(255,255,255,0.06)` — 干净半透明

### 点击交互（缩放→浮现 过渡动画）

```
浏览模式（拖拽+缩放自由）
  │
  │ 点击节点
  ▼
🎬 图以节点为中心 zoom-in (scale 1→2.8, 850ms)
  transform-origin = 被点击节点的坐标百分比
  力布局冻结
  │
  ├─ 200ms: 细节覆盖层开始浮现
  │    overlay opacity 0→1 (350ms)
  │    detail-content scale 0.92→1.0 (450ms)
  │    ← 内容从被放大的节点中"生长"出来的错觉
  │
  ▼
📋 全屏细节视图（覆盖整个图容器）
  ┌──────────────────────────────────────┐
  │  [×] 关闭 (sticky) · 按 Esc 同效     │
  │                                      │
  │  ● 节点颜色图标 (56px, box-shadow)    │
  │  事件标题 (1.35rem / 800 weight)      │
  │  类型 · 状态 · 置信度                 │
  │  [已确认] [置信度 85%] [3 个标签]     │
  │                                      │
  │  📋 事件描述（即时，来自 graph data）  │
  │  🏷 标签（即时，来自 graph data）     │
  │  ← 上游原因（API 异步加载）           │
  │  → 下游影响（API 异步加载）           │
  │  🔗 因果链（API 异步加载，含验证按钮） │
  │  ═══════════════════════════════════  │
  │  🔮 基于此事件延伸预测 · 仅供参考     │
  └──────────────────────────────────────┘
  │
  │ 点击 [×] / Esc / 刷新
  ▼
🎬 逆向动画
  封面内容 scale(1→0.95) + 淡出 (200ms)
  图 scale(2.8→1) (850ms)
  力布局恢复 · 拖拽重新启用
  │
  ▼
浏览模式
```

### 动画技术细节

| 阶段 | 技术 | 时长 | 缓动 |
|------|------|------|------|
| 进场-图缩放 | CSS transition on `transform` (wrapper div) | 850ms | `cubic-bezier(0.16, 1, 0.3, 1)` |
| 进场-封面浮现 | CSS transition on `opacity` + `transform` (content) | 350/450ms | ease / `0.16,1,0.3,1` |
| 退场-封面消失 | CSS transition on `opacity` + `transform` | 200/250ms | ease |
| 退场-图缩回 | CSS transition on `transform` (wrapper div) | 850ms | `cubic-bezier(0.16, 1, 0.3, 1)` |
| 节点定位 | `getItemLayout()` + `convertToPixel()` → 百分比 `transform-origin` | — | — |
| NaN 守卫 | `isFinite()` 检查所有坐标 + scale 值，无效时 fallback 中心 | — | — |

### 液态玻璃覆盖层

| 元素 | CSS |
|------|-----|
| 全屏覆层 | `background: rgba(8,11,16,0.92)` + `backdrop-filter: blur(20px) saturate(1.5)` |
| 关闭按钮 | `blur(12px) saturate(1.4)` + 内阴影高光 |
| 描述卡片 | `blur(8px) saturate(1.2)` + `box-shadow: inset 0 1px 0 rgba(255,255,255,0.04)` |
| 边卡片 | `blur(6px) saturate(1.2)` + hover 提升 |
| 关系行 | `blur(4px) saturate(1.15)` |
| 元数据胶囊 | `blur(4px) saturate(1.2)` |
| 延伸按钮 | `blur(4px) saturate(1.2)` + hover 金色发光 `0 0 24px rgba(212,168,83,0.1)` |

### ECharts 节点渲染

```javascript
// 径向渐变模拟 3D 液态玻璃球体
function glassGradient(highlight, body, edge, rim) {
    return {
        type: 'radial',
        x: 0.30, y: 0.24, r: 0.68,           // 高光偏移左上
        colorStops: [
            {offset: 0,    color: highlight}, // 镜面高光 α 0.65-0.72
            {offset: 0.12, color: body},      // 主体 α 0.25-0.28 (高透明)
            {offset: 0.50, color: edge},      // 曲面阴影 α 0.28-0.30
            {offset: 1,    color: rim}        // 边缘光晕 α 0.08-0.10
        ]
    };
}

// 节点配置
itemStyle: {
    borderWidth: 0.8,                         // 细边框 → 玻璃边缘
    borderColor: 'rgba(…,0.30-0.35)',         // 半透明边框
    shadowBlur: 10,                           // 柔和光晕
    shadowColor: 'rgba(…,0.15-0.18)',         // 彩色外发光
    opacity: n.is_expired ? 0.35 : 1.0,      // 过期节点更透明
}

// Hover 强化
emphasis: {
    shadowBlur: 22,                           // 更大光晕
    shadowColor: 'rgba(212,168,83,0.30)',     // 金色强调
    borderColor: 'rgba(255,255,255,0.35)',    // 亮边
}
```

### 新增 SVG 滤镜 (base.html)

```xml
<!-- Bespalov 风格 — 低频涟漪 + 高饱和湿润光泽 -->
<filter id="liquid-glass-bespalov" x="-20%" y="-20%" width="140%" height="140%">
    <feTurbulence type="fractalNoise" baseFrequency="0.008" numOctaves="3" seed="7" result="noise" />
    <feDisplacementMap in="SourceGraphic" in2="noise" scale="4" xChannelSelector="R" yChannelSelector="G" result="displaced" />
    <feGaussianBlur in="displaced" stdDeviation="1.0" result="blurred" />
    <feColorMatrix in="blurred" type="saturate" values="1.8" result="saturated" />
    <feComponentTransfer in="saturated">
        <feFuncA type="linear" slope="0.85" />
    </feComponentTransfer>
</filter>
```

## 5. API端点

```
GET  /api/v1/timeline/graph?days=90&exclude_isolated=1 → 图数据 (自动构建，默认排除孤立节点)
GET  /api/v1/timeline/isolated?type=&search=&level=  → 孤立事件列表 (无因果边的节点，支持筛选)
GET  /api/v1/timeline/nodes?type=&status=            → 节点列表
GET  /api/v1/timeline/nodes/<id>                     → 节点详情+上下游
DELETE /api/v1/timeline/nodes/<id>                   → 删除节点 (级联删除关联边)
GET  /api/v1/timeline/edges/<node_id>                → 关联边
POST /api/v1/timeline/edges/<id>/verify              → 验证/证伪/重置边
POST /api/v1/timeline/discover                       → 手动因果发现
POST /api/v1/timeline/extend/<node_id>               → 延伸预测
POST /api/v1/timeline/rebuild                        → 全量重建 (清空节点+边，S级优先重构建)
POST /api/v1/timeline/cleanup                        → 清理过期节点 (body: {"older_than_days": 30})
GET  /api/v1/timeline/snapshots                      → 快照列表
POST /api/v1/timeline/snapshot                       → 保存快照
```

## 6. 关键文件

| 文件 | 说明 |
|------|------|
| `agents/edge_strength.py` | **边强度核心** — 5维融合公式 + NPMI + 时间衰减 + Wilson + TagStatistics |
| `agents/timeline_builder.py` | Agent — 自动构建+Bubble节点+因果发现+预测延伸+全量重建 (集成多维强度) |
| `app/routes/web_timeline.py` | Web+API路由 (含边验证端点、分页、distinct去重、孤立事件API、重建API) |
| `app/templates/timeline.html` | ECharts力导向图 + 液态玻璃节点 + Bespalov覆层 + 缩放浮现 + 逆向退场 + 孤立事件侧边栏 |
| `app/templates/base.html` | SVG滤镜: `#liquid-glass-bespalov` (Bespalov风格低频涟漪+高饱和) |
| `models/timeline.py` | TimelineNode/CausalEdge/TimelineSnapshot (含 FK CASCADE) |
| `pipeline/orchestrator.py` | Stage 7: 管道完成后自动触发 Bubble 构建 |
| `docs/06_bubble.md` | 本文档 |

## 7. 孤立事件侧边栏 (v4)

主图默认只显示有因果边的节点（`exclude_isolated=1`），无边的孤立事件自动归入右侧侧边栏。

### 侧边栏功能

- **筛选**: 按节点类型 (root_event/derived_event/prediction/market_reaction/verification) + 标题搜索 (300ms防抖)
- **展示**: 类型色点 + 标题 + 置信度 + 事件等级(S/A/B)
- **交互**: 点击预览事件详情，◀ 折叠/▶ 展开 (折叠后图区右上角浮现展开按钮)
- **数据**: 自动统计各类型数量，显示在筛选下拉选项中

### 全量重建

新增 `POST /api/v1/timeline/rebuild` 端点和 🔧 重建按钮：
- 清空所有节点+边
- S 级 EventCard 优先构建（确保 root_event 节点生成）
- 自动运行因果发现 + 预测延伸
- 解决首次 auto_build 因 limit(30) 时间窗口遗漏 S 级事件的问题

## 8. 更新日志

### 2026-07-03 — 多维边强度融合 (v5)

- **5维统计公式**: 语义相似度(35%) + NPMI(25%) + Jaccard(15%) + 时间衰减(15%) + 等级加权(10%)，替代 `0.35+0.05×len(common)`
- **NPMI标签统计**: TagStatistics类全局统计标签共现显著性，自动惩罚高频标签"虚假相关"
- **自适应半衰期**: S级60天 / A级30天 / B级14天 / C级7天，因果/influences/correlates 不同衰减速率
- **Wilson置信下限**: 小样本标签共现自动降权（低证据→保守估计）
- **LLM边融合**: LLM因果发现+预测延伸均与公式融合（60/40或50/50），保留语义判断+统计校准
- **新增模块**: `agents/edge_strength.py` (290行) — 5维公式 + 6篇学术论文支撑
- **全系统推广**: 同一套数理方法同步应用到 Scoring(时效衰减)/Clustering(多维相似)/Prediction(事件相关性)/ResearchAssistant(语义搜索)

### 2026-07-03 — 孤立事件侧边栏 + 全量重建 + 交互优化

- **孤立事件侧边栏**: 主图只显示有边节点（41个），285个孤立事件归入右侧可折叠侧边栏
- **全量重建**: `POST /api/v1/timeline/rebuild` + 🔧 按钮，S级优先排序，root_event 从 0 → 65
- **交互优化**: loadGraph 防抖+AbortController，animation token 替代 setTimeout 链，Esc 监听器泄漏修复，Action 按钮 loading 状态，API 超时处理，ECharts 生命周期管理
- **前端优化**: SSE 自动重连，搜索 XSS 修复，Homepage rAF 后台暂停，CSS will-change/contain GPU 加速

### 2026-07-03 — 节点类型全覆盖 + 删除功能

- **节点删除功能**: 新增 `DELETE /api/v1/timeline/nodes/<id>` 端点，详情面板添加红色删除按钮，手动删除前先清关联边（兼容 SQLite FK）
- **market_reaction 死代码激活**: 查询扩展到 B 级 EventCard，调整 if/elif 顺序优先匹配市场事件（新增 `commodity/price/currency/trading` 英文关键词）
- **prediction 管道自动生成**: Stage 7 在 auto_build 后自动对 top 节点调用 `extend_predictions()`（root_event → derived_event fallback）
- **verification 节点实现**: LLM 因果发现后对 auto-verified 边创建 verification 节点（详见面板）
- **时区比较修复**: `get_graph_data()` 中 naive/aware datetime 比较兼容处理

### 2026-07-03 — Bug 修复 (tc 遮蔽)

### 2026-06-28 — 液态玻璃全面升级

- **品牌重命名**: Bubble（前称"因果时间线"）+ BubbleEvent（前称 EventAlpha）
- **节点材质**: 高透明度径向渐变 (主体 α 0.25-0.28)，模拟 3D 玻璃球体
- **Bespalov 技术**: 研究并应用 Mikhail Bespalov CodePen 的 `backdrop-filter: blur() saturate()` 湿润玻璃技法
  - 新增 SVG 滤镜 `#liquid-glass-bespalov`: `feTurbulence(0.008)` 低频涟漪 + `feColorMatrix(saturate 1.8)` 高饱和
  - 覆层: `blur(20px) saturate(1.5)` — 弹层玻璃层次
  - UI零件: 图例/卡片/胶囊/按钮逐层 `blur(4-12px) saturate(1.15-1.4)`
- **图区清晰**: 容器使用 `radial-gradient` 纯色背景，不应用 blur（避免整面模糊）
- **图例玻璃条**: 半透明底 + 细边框，无模糊保持可读性
- **节点发光**: `shadowBlur` 6→10 (正常), 14→22 (hover)
- **边框细化**: `1.0px` + 实色 → `0.8px` + `rgba(α 0.30-0.35)` 半透明
- **过期节点**: 透明度 0.40→0.35

### 2026-06-27 — 节点过期机制
- 新增 `expires_at` 字段到 TimelineNode 模型 (NULL = 永不过期)
- 根据事件等级 (S/A/B/C) 和预测期限 (T+3/+7/+30) 自动计算过期时间
- 默认软过滤过期节点 (`include_expired=0`)，前端提供切换复选框
- 过期节点以 35% 透明度显示
- 节点详情覆盖层显示过期倒计时/已过期天数
- 新增 `POST /api/v1/timeline/cleanup` 硬删除端点
- 管道 Stage 7 后自动清理过期超过 90 天的节点

### 2026-06-26 — 动画重构
- 全屏细节覆盖层替换侧边滑入面板
- 图缩放→浮现动画 (wrapper CSS transform + content scale/fade)
- 进场/退场完全镜像 (zoom-in → emerge → collapse → zoom-out)
- NaN 坐标守卫 (isFinite 检查 + fallback 中心)
- 状态标签中文化 (statusLabelMap 翻译 upstream/downstream)
- distinct() 去重 upstream/downstream 查询
- LLM 因果发现 seen_edge_keys set 同批次/跨批次去重
- savepoint 替代 session rollback 防止误回滚
- FK 添加 ondelete CASCADE
- list_nodes API 新增 offset 分页
- `...n` spread 顺序修正 (ECharts label config 不被覆盖)
- 边 tooltip 标题 _edgeTitle 修复
