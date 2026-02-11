# 新闻模块改进规划

> 基于 3 层流水线架构 (Layer 1 → 1.5 → 2) 的现状分析，梳理质量、可观测性、功能扩展三个维度的改进方向。

---

## 当前架构概览

```
Layer 1  (news_monitor, 每15min)     → 新闻发现 + 初筛
Layer 1.5 (batch_fetch_content)       → HTTP抓取 (Semaphore(3) + 1s延迟)
Layer 2  (process_news_article)       → LangGraph: read_file → filter → embed → update_db
Cleanup  (cleanup_expired_news)       → 定期清理 (每天 4:00 AM)
```

**关键文件**:
- `worker/tasks/news_monitor.py` — Layer 1
- `worker/tasks/full_content_tasks.py` — Layer 1.5 + Layer 2 入口
- `backend/app/agents/langgraph/workflows/news_pipeline.py` — Layer 2 LangGraph
- `backend/app/agents/langgraph/state.py` — NewsProcessingState
- `backend/app/services/two_phase_filter_service.py` — 初筛 + 精筛 LLM 调用
- `backend/app/services/filter_stats_service.py` — Redis 统计
- `backend/app/services/news_storage_service.py` — JSON 文件存储
- `frontend/src/components/admin/FilterStats.tsx` — 管理后台统计面板

---

## 一、质量提升

### Q1. 实体验证层 (Entity Validation)

**现状**: `validate_entities()` 只做格式校验（type合法、score在[0,1]），不验证股票代码是否真实存在。LLM可能输出 `"APPLEINC"` 而非 `"AAPL"`。

**方案**:
- 在 `validate_entities()` 中加入 `StockListService` 查询
- 未找到的代码尝试模糊匹配（name → symbol 反查）
- 无法匹配的降低 score 或标记为 `unverified`
- 影响范围: `two_phase_filter_service.py`

**优先级**: P1
**复杂度**: 低
**收益**: 防止脏数据污染 RAG 检索和实体索引

---

### Q2. 语义去重 (Semantic Dedup)

**现状**: 同一事件 (如 "Apple Q4 Earnings") 可能来自多个源，每篇独立走完 3 层流水线。当前只有 URL 去重。

**方案**:
- Layer 1 commit 后、Layer 1.5 分发前：
  - 对同批次新文章计算 title embedding（轻量，仅 title）
  - 余弦相似度 > 0.92 的标记为 cluster
  - 每个 cluster 只选一篇代表文章进入 Layer 1.5/2
  - 其余标记 `content_status=DUPLICATE`，关联代表文章 ID
- 新增 News 字段: `duplicate_of` (UUID FK, nullable)

**优先级**: P2
**复杂度**: 中（需要 embedding 计算 + 聚类逻辑）
**收益**: 减少 LLM 调用成本，提升 feed 质量（去重后展示更多不同事件）

---

### Q3. 过滤反馈闭环 (Filter Feedback Loop)

**现状**: `FilterStatsService` 只统计 keep/delete 计数，无法知道决策是否正确。

**方案**:
- 定期抽样: 每天从 "skip" 和 "delete" 中各随机抽 N 篇
- 二次评估: 用不同 prompt（或不同模型）重新评估
- 计算一致率: 两次评估一致 → 高置信度
- 存储结果: 新表 `filter_audit` (sample_date, news_id, original_decision, audit_decision, consistent)
- 管理后台展示: 一致率趋势图、低一致率告警

**优先级**: P2
**复杂度**: 中
**收益**: 长期质量保障，发现 prompt 退化或模型变化

---

### Q4. 全文截断优化

**现状**: `deep_filter_article()` 粗暴截断 `full_text[:8000]`。长文章（财报、研报）关键数据可能在后半部分。

**方案**:
- 分段策略: 取前 4000 + 后 2000 + 中间关键段落 2000
- 关键段落检测: 包含数字、百分比、实体名的段落优先
- 或: 先用小模型生成摘要，再用精筛模型评估

**优先级**: P2
**复杂度**: 低-中
**收益**: 提升长文章的过滤和实体提取质量

---

### Q5. 初筛批量干扰问题

**现状**: `batch_initial_filter()` 将多篇文章打包成一个 LLM 调用（batch_size=20），单篇异常文章可能干扰整批判断。

**方案**:
- 降低 batch_size 至 10
- 或: 异常检测 — 如果某批次 skip_rate 异常高/低，自动拆分重评
- 或: 对高价值 source (reuters, bloomberg) 的文章单独评估，不参与批量

**优先级**: P3
**复杂度**: 低
**收益**: 提升初筛准确率

---

## 二、可观测性

### O1. 全链路追踪 (Pipeline Tracing)

**现状**: 无法追踪一篇文章从发现到嵌入的完整旅程。问题排查需手工搜索三个 Celery 任务日志。

**方案**:
- 新增 `pipeline_events` 表或 Redis Stream:
  ```
  pipeline_event = {
    news_id: UUID,
    layer: "1" | "1.5" | "2",
    node: "initial_filter" | "fetch" | "read_file" | "deep_filter" | "embed" | ...,
    status: "start" | "success" | "error" | "skip",
    duration_ms: int,
    metadata: {word_count, decision, error_msg, chunks_stored, ...},
    timestamp: datetime,
  }
  ```
- 管理后台: 单篇文章时间线可视化
- 按 layer/node 聚合延迟分布

**优先级**: P0
**复杂度**: 中
**收益**: Debug 效率 10x 提升，是所有后续改进的观测基础

---

### O2. Source 维度统计

**现状**: `FilterStatsService` 只有全局计数，不知道哪个 source 质量最高/最低。

**方案**:
- 扩展 Redis key 结构:
  ```
  news:filter:20260210:fine_keep → 42           (全局)
  news:filter:20260210:fine_keep:reuters → 15    (按 source)
  news:filter:20260210:fine_keep:eastmoney → 20  (按 source)
  ```
- 管理后台: Source 质量排行榜
  - reuters: keep_rate=92%, avg_entities=3.2
  - eastmoney: keep_rate=67%, avg_entities=1.8
- 可用于自动降级低质量 source

**优先级**: P0
**复杂度**: 低-中
**收益**: 数据驱动优化新闻源配置

---

### O3. Layer 1.5 抓取统计

**现状**: `batch_fetch_content` 只在日志中记录成功/失败数，没有持久化统计。

**方案**:
- 新增 stat types: `fetch_success`, `fetch_failed`, `fetch_blocked`, `fetch_partial`
- 延迟直方图: `fetch_duration_ms`
- 内容质量: `fetch_word_count` 平均值
- 管理后台新卡片: 抓取成功率、平均字数、屏蔽域名命中率、平均抓取延迟

**优先级**: P1
**复杂度**: 低
**收益**: 补全可观测性盲区，快速发现抓取问题

---

### O4. 延迟追踪

**现状**: 完全没有延迟指标。不知道文章从发布到可检索经历了多久。

**方案**:
- 关键延迟指标:
  1. `discovery_lag`: published_at → monitor 发现 (取决于 15min 周期)
  2. `fetch_lag`: monitor 发现 → content_fetched_at
  3. `process_lag`: content_fetched_at → EMBEDDED
  4. `e2e_lag`: published_at → EMBEDDED (端到端)
- 在 `update_db_node` 中计算并记录到 Redis

**优先级**: P1
**复杂度**: 低
**收益**: 量化新闻处理时效性，发现瓶颈环节

---

### O5. 死信追踪 (Dead Letter Tracking)

**现状**: 多次重试后仍失败的文章静默消失（Celery 丢弃）。

**方案**:
- Celery `task_failure_handler`: 记录到 Redis sorted set `news:dead_letter`
- 管理后台: "失败文章" 列表，支持手动重试
- 定期清理: 7 天后自动移除

**优先级**: P2
**复杂度**: 低
**收益**: 避免文章静默丢失

---

## 三、功能扩展

### F1. 新闻情感趋势 (Sentiment Timeline)

**现状**: `sentiment_tag` (bullish/bearish/neutral) 是逐篇标签，没有聚合成时间序列。

**方案**:
- 对每个 symbol，按天聚合 sentiment_tag 分布
- 前端: 在 StockDetailPage 展示 7/30 天情感趋势图
- 计算: `sentiment_score = (bullish - bearish) / total`
- 叠加到 K 线图上作为辅助指标
- 数据来源已有: News 表的 sentiment_tag + published_at + symbol

**优先级**: P1
**复杂度**: 低（纯 SQL 聚合 + 前端图表）
**收益**: 直接提升用户体验，数据已就绪

---

### F2. 事件聚类 (Event Clustering)

**现状**: 同一事件的多篇报道分散在列表中，无聚合视图。

**方案**:
- Layer 2 embed 后，对同 symbol 24h 内文章做聚类
- 相似度 > 0.85 归为同一事件
- 前端: 事件卡片 → 展开看多源报道
- 每个事件: 合并 entities、取 sentiment 众数、选最佳 investment_summary
- 新增表: `news_event_cluster` (cluster_id, representative_news_id, symbol, event_date)

**优先级**: P2
**复杂度**: 中-高
**收益**: 用户体验大提升

---

### F3. 个性化新闻 Feed

**现状**: `/news/feed` 只按 symbol 过滤，无个性化排序。

**方案**:
- 基于用户 portfolio + watchlist 计算兴趣权重
- 排序: `relevance = entity_score × holding_weight × recency_decay`
- 新增: `user_news_interaction` 表 (user_id, news_id, action: read/save/dismiss)
- 已读状态避免重复推送

**优先级**: P3
**复杂度**: 中
**收益**: 用户粘性提升

---

### F4. 每日/每周新闻摘要 (News Digest)

**方案**:
- 每天/每周为用户 portfolio 生成新闻摘要
- 挑选最高 importance 文章 + 情感变化
- LLM 生成 3-5 句总结
- 集成到现有 `ReportSchedule` 系统

**优先级**: P3
**复杂度**: 中
**收益**: 用户价值高

---

### F5. 新闻源质量管理

**方案**:
- 自动计算每个 source 的 keep_rate、avg_entity_count、avg_word_count
- 低于阈值的 source 自动降级（初筛直接 skip）
- 管理后台: Source 管理页面，支持手动黑/白名单

**优先级**: P2 (依赖 O2)
**复杂度**: 中
**收益**: 减少 LLM 调用浪费

---

### F6. 多语言新闻翻译

**方案**:
- 东财新闻(中文) → 英文摘要; Reuters(英文) → 中文摘要
- 在 deep_filter prompt 中要求双语 investment_summary 输出
- 新增字段: `investment_summary_en`, `investment_summary_zh`

**优先级**: P3
**复杂度**: 低（改 prompt + 加字段）
**收益**: 跨语言用户体验

---

### F7. 实时新闻推送

**方案**:
- 高重要性文章 (importance >= 2.5) → WebSocket / WebPush 推送
- 复用已有 PushSubscription + VAPID 体系
- 基于 portfolio/watchlist 过滤推送目标

**优先级**: P3
**复杂度**: 中
**收益**: 实时性提升

---

### F8. 动态过滤阈值 / Prompt 管理

**现状**: 初筛/精筛 prompt 硬编码在 `TwoPhaseFilterService` 中。

**方案**:
- 将 prompt 移到 `prompts/templates/` 下（已有类似模式）
- 管理后台: 可编辑初筛/精筛 prompt
- A/B 测试: 50%用 prompt A, 50%用 prompt B, 比较 keep_rate

**优先级**: P2
**复杂度**: 中
**收益**: 运营灵活性

---

### F9. 渐进式内容处理

**现状**: 3 层线性执行，必须等抓取完才能过滤和嵌入。

**方案**:
- 对已有 summary 的文章，Layer 1 commit 后立即生成 summary embedding（轻量）
- Layer 2 embed 后用 full_text embedding 替换
- 用户在抓取完成前就能通过 RAG 检索到新文章

**优先级**: P3
**复杂度**: 中
**收益**: 新闻可检索时效性从 ~5min 降到 <1min

---

## 优先级汇总

| 级别 | 项目 | 维度 | 复杂度 |
|------|------|------|--------|
| **P0** | O1. 全链路追踪 | 可观测性 | 中 |
| **P0** | O2. Source 维度统计 | 可观测性 | 低-中 |
| **P1** | Q1. 实体验证层 | 质量 | 低 |
| **P1** | O3. Layer 1.5 抓取统计 | 可观测性 | 低 |
| **P1** | O4. 延迟追踪 | 可观测性 | 低 |
| **P1** | F1. 新闻情感趋势 | 功能 | 低 |
| **P2** | Q2. 语义去重 | 质量 | 中 |
| **P2** | Q3. 过滤反馈闭环 | 质量 | 中 |
| **P2** | Q4. 全文截断优化 | 质量 | 低-中 |
| **P2** | O5. 死信追踪 | 可观测性 | 低 |
| **P2** | F2. 事件聚类 | 功能 | 中-高 |
| **P2** | F5. 新闻源质量管理 | 功能 | 中 |
| **P2** | F8. 动态 Prompt 管理 | 功能 | 中 |
| **P3** | Q5. 初筛批量干扰 | 质量 | 低 |
| **P3** | F3. 个性化 Feed | 功能 | 中 |
| **P3** | F4. 每日摘要 | 功能 | 中 |
| **P3** | F6. 多语言翻译 | 功能 | 低 |
| **P3** | F7. 实时推送 | 功能 | 中 |
| **P3** | F9. 渐进式处理 | 功能 | 中 |
