# indbase MVP 分阶段产品与架构计划

版本：v0.1 收敛版  
状态：开发指导稿  
日期：2026-05-12  
范围：Foundation MVP、Dogfood MVP、Intelligent Workflow MVP

## 1. 分阶段原则

MVP 拆成三个层级，避免第一版同时调试 OCR、embedding、TUI、LLM、任务队列、翻译和候选卡片。

```text
v0.1 Foundation MVP
  目标：可靠 ingest、metadata、revision、chunk、FTS、search snippets、task/error/review、轻 TUI

v0.2 Dogfood MVP
  目标：PDF/OCR、embedding、hybrid search、自动分类、标签建议、翻译、review flow

v0.3 Intelligent Workflow MVP
  目标：ask、候选原子卡片、card review、智能工作流、完整 TUI 评估
```

v0.1 的核心判断：

- 先把数据链路做稳，再做智能能力。
- 只做 `search`，不做 `ask`。
- 只搜索 `sources/` 的 current revision。
- 不做 OCR、embedding、hybrid search、自动分类、翻译、candidate cards。
- CLI first，TUI lite。
- 任务系统使用 SQLite-backed local task runner，不引入 Taskiq。
- 普通 search citation 只渲染，不默认持久化。

## 2. 三阶段总览

### 2.1 v0.1 Foundation MVP

目标：建立一个可靠、可追踪、可搜索、可修复的本地知识库地基。

包含：

- vault init
- schema migration
- category template
- manual category/tag commands
- local file/folder ingest
- original archive
- Markdown writer
- metadata DB
- immutable revision
- chunker
- SQLite FTS
- basic search
- citation snippets
- task table
- error log
- review queue
- ingest_runs / ingest_items
- converter_runs
- duplicate detection v0
- archive/restore
- `indb doctor`
- Typer + Rich + InquirerPy 轻 TUI

不包含：

- OCR
- embedding
- hybrid search
- LLM answer
- auto classification
- tag suggestion
- translation
- candidate cards
- Textual full TUI
- Bot
- URL platform ingest

### 2.2 v0.2 Dogfood MVP

目标：让系统开始适合真实日常使用。

包含：

- PDF ingest v1
- OCR v0
- local OCR
- OCR quality flags
- embedding adapter
- vector index
- hybrid search
- index rebuild for vectors
- category suggestion
- tag suggestion
- confidence gate
- classification feedback
- selected chunk translation
- full document translation
- translation records
- stronger review queue
- stronger harness

不包含：

- candidate atomic notes
- card review
- knowledge graph
- Bot
- complex URL extractors
- Textual full TUI 的强承诺

### 2.3 v0.3 Intelligent Workflow MVP

目标：在可靠资料层和 dogfood 能力上加入智能执行流。

包含：

- `indb ask`
- answer with citations
- candidate card extraction
- claim-level citation schema
- card review
- accepted atomic notes
- richer workflows
- Textual TUI 评估或落地
- export markdown bundle
- URL ingest v0 评估

不包含：

- 企业级权限
- 多用户协作
- 云同步
- 高级知识图谱
- 平台级爬虫覆盖

## 3. v0.1 文件类型分级

v0.1 不把所有可导入格式写成同一等级。

### 3.1 Tier 1：强支持

这些格式简单、可控、可测试，v0.1 必须稳定：

```text
md
txt
html
csv
json
```

要求：

- 成功 ingest。
- 保留 original。
- 生成可读 Markdown。
- 生成 metadata、revision、chunks。
- 建立 FTS。
- 可被 `indb search` 搜到。

### 3.2 Tier 2：best-effort ingest

这些格式可以通过 MarkItDown 尝试处理，但 v0.1 不承诺复杂结构质量稳定：

```text
docx
xlsx
pptx
```

要求：

- 尝试转换。
- 转换成功则进入正常 ingest。
- 转换失败或质量低时创建 `review_items`。
- 不承诺复杂表格、公式、合并单元格、批注、脚注、图片、图表、视觉顺序完整还原。

M3 checkpoint 不以 Tier 2 成功作为阻塞验收项。`docx` / `xlsx` / `pptx` 可以有 optional/local tests，但自动化 M3 checkpoint 的必过 fixture 不包含 Tier 2。

### 3.3 Tier 3：unsupported_source

未列入 Tier 1 / Tier 2 的格式，v0.1 统一视为 `unsupported_source`。

要求：

- 不进入 source Markdown。
- 不进入 FTS。
- 不阻塞同一文件夹内其他文件 ingest。
- 记录 `ingest_item.status = unsupported`。
- 创建 `review_items.type = unsupported_source`。

v0.1 不为某个 unsupported 格式建立专用处理分支。格式扩展放到 v0.2+。

## 4. v0.1 技术选型

CLI / 轻 TUI：

```text
Typer
Rich
InquirerPy
```

暂不使用：

```text
Textual
Taskiq
Celery
RQ
```

任务执行：

```text
SQLite task table
in-process worker
indb task worker
TUI 启动时可拉起本地 worker
```

M0-M3 优先使用同步、确定、可测试的执行路径。`indb ingest <path>` 可以在当前进程内直接跑完，但必须完整写入 `tasks`、`task_events`、`ingest_runs`、`ingest_items`、`errors`、`review_items`。后台 worker 的长期运行体验不作为 M3 阻塞项。

Python 工程结构：

```text
pyproject.toml
src/
  indbase_core/
  indbase_cli/
tests/
```

工具链：

```text
uv
pytest
requires-python = ">=3.11"
indb = "indbase_cli.main:app"
```

M3 阶段暂不单独拆 `indbase-skills` 包。normalizers、converters、chunker、indexer 作为 `indbase_core` 内部服务实现。

搜索：

```text
SQLite metadata search
SQLite FTS
citation snippets rendered from chunks
CJK substring fallback over chunks/documents
```

v0.1 不做：

```text
embedding
hybrid search
rerank
query expansion
LLM answer
```

## 5. v0.1 模块边界

### 5.1 indbase-core

负责：

- config
- schema migration
- vault path
- ID generation
- SQLite connection
- task state machine
- task events
- error records
- ingest run records
- converter run records
- document metadata
- revision
- chunk
- FTS indexer
- category/template
- tag basics
- review queue
- duplicate detection v0
- archive/restore
- doctor checks

Core 不依赖 UI，不直接调用模型。

### 5.2 indbase-cli

v0.1 稳定命令：

```text
indb init
indb ingest <path> [--recursive]
indb search <query>
indb catalog list
indb catalog add <name>
indb doc classify <doc_id> --category <category_id>
indb doc tag list <doc_id>
indb doc tag add <doc_id> <tag>
indb doc tag remove <doc_id> <tag>
indb task list
indb task show <task_id>
indb task worker
indb review list
indb review show <review_id>
indb review resolve <review_id>
indb error list
indb doc show <doc_id>
indb doc open <doc_id>
indb doc open <doc_id> --original
indb doc open <doc_id> --folder
indb doc open <doc_id> --revision <revision_id>
indb doc archive <doc_id>
indb doc restore <doc_id>
indb doc revisions <doc_id>
indb index status
indb index rebuild --fts
indb doctor
```

v0.1 不提供：

```text
indb ask
indb translate
indb card
indb index rebuild --vectors
indb index rebuild --all
```

### 5.3 indbase-tui-lite

v0.1 不是完整应用式 TUI，而是 guided CLI。

包含：

- init wizard
- category template selection
- ingest wizard
- task queue
- search panel
- review list
- error viewer
- settings summary

技术：

```text
Typer commands
Rich tables / panels / progress
InquirerPy prompts
```

Textual 是否引入留到 v0.2 末或 v0.3 决策。

### 5.4 indbase-skills

v0.1 skills：

- source inspector
- source archiver
- MarkItDown converter for Tier 2
- direct normalizer for Tier 1
- markdown normalizer
- source metadata builder
- content metadata builder
- quality validator v0
- chunker
- FTS indexer
- duplicate detector v0

v0.1 不做 LLM skill。

Tier 1 的 direct normalize 也必须写入 `converter_runs`，例如 `converter_name = direct_normalizer`、`converter_version = indbase.v0.1`。不要因为没有调用 MarkItDown 就跳过转换记录。

### 5.5 indbase-harness

v0.1 只建立目录和接口，不让 harness 设计拖住 Foundation。

建议结构：

```text
indbase-harness/
  schemas.py
  model_types.py
  validators.py
  noop_provider.py
```

规则：

- v0.1 core 不调用 harness。
- v0.2 开始所有 LLM 调用必须经过 harness。
- 业务模块不得裸调 provider。

## 6. Vault 目录结构

```text
vault/
  inbox/
  sources/
    YYYY/
      MM/
        short-title__doc_YYYYMMDD_<shortid>__rev_0001.md
  notes/
    atomic/
  outputs/
    translations/
    summaries/
  assets/
  .indbase/
    originals/
      YYYY/
        MM/
          doc_YYYYMMDD_<shortid>/
            original.<ext>
    db.sqlite
    indexes/
    logs/
      app.log
      tasks/
    cache/
    config/
      config.toml
```

说明：

- v0.1 只写 `sources/`。
- `inbox/` v0.1 保留但不使用。
- `notes/atomic/` 和 `outputs/` 为 v0.2/v0.3 预留。
- SQLite FTS 数据在 `db.sqlite` 内，`.indbase/indexes/` v0.1 只作预留，不承担实际功能。

### 6.1 文件名规则

文件名应有简短语义，但系统身份只认 `doc_id`。

推荐格式：

```text
<short-title-slug>__doc_YYYYMMDD_<shortid>__rev_0001.md
```

示例：

```text
agent-harness-design__doc_20260512_a8f3c2__rev_0001.md
```

规则：

- `doc_id` 是唯一身份。
- `revision_id` 使用绑定 `doc_id` 的顺序格式，例如 `rev_doc_20260512_a8f3c2_0001`。
- 每个 content revision 写一个新的 immutable Markdown 文件，不能覆盖旧 revision 文件。
- title slug 只用于 Obsidian 可读性。
- 用户可在 ingest 时输入文件名 slug。
- 未输入时系统从标题或源文件名生成短 slug。
- 所有 DB 关联、search、chunk、revision 都使用 `doc_id`。
- `document_revisions.markdown_path` 指向该 revision 的 Markdown 文件。
- `documents.canonical_path` 指向当前 revision 的 Markdown 文件。
- 文件重命名必须通过受控命令更新当前 `canonical_path`，不得改写旧 revision 文件。

### 6.2 路径字段命名

统一使用以下语义：

- `source_uri`：用户传入的原始来源标识，例如导入时的本地绝对路径；未来也可表示 URL。它不是 vault 内部路径。
- `normalized_source_uri`：规范化后的来源标识，用于 re-ingest 匹配和重复检测，不用于替代审计用的 `source_uri`。
- `original_path`：`documents` 上表示当前/latest 原始文件归档路径；`source_files` 上表示该次 ingest/re-ingest 的 immutable 原始文件归档路径。
- `canonical_path`：`documents` 上表示当前 revision 的 source Markdown 路径，位于 `sources/`。
- `document_revisions.markdown_path`：该 revision 的 immutable Markdown 路径。

frontmatter 和数据库都应使用这些名字，避免再引入 `source_path`、`archived_path` 等同义字段。Core 和 doctor 以 DB 为权威状态；frontmatter 是可读镜像，不作为 durable state 的权威来源。

## 7. v0.1 配置文件

`vault/.indbase/config/config.toml`

```toml
schema_version = "indbase.config.v1"
vault_path = "..."
default_language = "zh"

[ingest]
recursive = true
preserve_original = true
max_file_size_mb = 200
detect_duplicates = true
tier1_extensions = ["md", "txt", "html", "csv", "json"]
tier2_extensions = ["docx", "xlsx", "pptx"]

[search]
default_scope = "sources_current"
top_k = 20
persist_search_results = false
log_queries = true
cjk_strategy = "substring_fallback"

[tui]
mode = "lite"

[features]
ocr = false
embedding = false
ask = false
translation = false
candidate_cards = false
auto_classification = false
```

feature flags 用于防止 v0.1 误触发未实现能力。

`search.log_queries` 控制是否写入 `search_queries`。关闭后 search 仍正常工作，但不记录查询文本。`search.persist_search_results` 默认关闭，普通搜索结果不落库。

## 8. Schema migration

每个 vault 必须记录 schema migration。

```text
schema_migrations
  version TEXT PRIMARY KEY
  applied_at TEXT NOT NULL
```

规则：

- 每次启动检查 DB schema version。
- 新程序不得直接打开更高版本 vault。
- migration 必须可重复运行。
- migration 前自动备份 `db.sqlite`。
- migration 失败不得破坏原 DB。

## 9. v0.1 数据表

字段规范：

- 实体表保留 `created_at`、`updated_at`、必要时 `deleted_at`。
- 事件和日志表不可变，不做软删除。
- 状态字段统一命名为 `status`。
- JSON 字段统一命名为 `xxx_json`。
- hash 统一存 `algo:value`，例如 `sha256:abcdef...`。

### 9.1 documents

```text
doc_id TEXT PRIMARY KEY
current_revision_id TEXT
title TEXT
original_title TEXT
filename_slug TEXT
status TEXT NOT NULL              -- active, archived
source_type TEXT
source_uri TEXT
normalized_source_uri TEXT
source_hash TEXT                  -- algo:value, latest/current source file hash
canonical_path TEXT
original_path TEXT                -- latest archived original path
language TEXT
category_id TEXT
quality_status TEXT               -- passed, warning, failed
quality_signals_json TEXT
needs_review INTEGER
ingest_status TEXT
fts_status TEXT
embedding_status TEXT
classification_status TEXT
archived_at TEXT
created_at TEXT NOT NULL
updated_at TEXT
deleted_at TEXT
```

规则：

- `documents.source_uri` 保留最新一次 ingest/re-ingest 的原始用户输入。
- `documents.normalized_source_uri` 用于 re-ingest 匹配。
- `documents.original_path` 指向 latest archived original；历史 original 从 `source_files` 查询。
- `documents.canonical_path` 指向 current revision 的 Markdown 文件。
- category、tag、title、status 等 metadata edit 不创建 document revision，也不得重写旧 revision frontmatter。
- 手动编辑 `sources/*.md` 不会自动进入 revision；`doctor` 应检测 DB hash 与 Markdown 文件内容不一致。
- `search` 以 DB chunks / FTS 为准，不直接重读 Markdown。
- v0.1 不做物理删除。`deleted_at` 只为未来迁移预留，不暴露 delete 命令。

### 9.2 document_revisions

Revision 是不可变快照。

```text
revision_id TEXT PRIMARY KEY
doc_id TEXT NOT NULL
sequence INTEGER NOT NULL
markdown_path TEXT NOT NULL
content_hash TEXT NOT NULL
converter_name TEXT
converter_version TEXT
chunk_strategy TEXT
text_length INTEGER
chunk_count INTEGER
created_at TEXT NOT NULL
updated_at TEXT
deleted_at TEXT
notes TEXT
```

规则：

- `revision_id` 使用 `rev_<doc_id>_<sequence>`，例如 `rev_doc_20260512_a8f3c2_0001`。
- sequence 在单个 `doc_id` 内从 `0001` 递增。
- `documents.current_revision_id` 只是当前指针。
- 旧 revision 不删除。
- 旧 revision 的 chunks、outputs、citations 不删除。
- v0.1 默认只搜索 current revision。

### 9.3 source_files

```text
source_file_id TEXT PRIMARY KEY
doc_id TEXT NOT NULL
source_uri TEXT
normalized_source_uri TEXT
original_filename TEXT
original_ext TEXT
mime_type TEXT
size_bytes INTEGER
source_hash TEXT
original_path TEXT
created_at TEXT NOT NULL
updated_at TEXT
deleted_at TEXT
```

`source_files` 记录每次归档过的原始文件，包括 re-ingest 时 content 未变化但 source bytes 变化的情况。

### 9.4 ingest_runs

事件型记录，不软删除。

```text
ingest_id TEXT PRIMARY KEY
task_id TEXT
source_kind TEXT NOT NULL         -- file, folder
source_input TEXT
status TEXT NOT NULL
total_items INTEGER
succeeded_items INTEGER
failed_items INTEGER
unsupported_items INTEGER
duplicate_items INTEGER
review_items_count INTEGER
created_at TEXT NOT NULL
updated_at TEXT
finished_at TEXT
```

`ingest_runs.status` 使用与父 task 一致的语义：全部成功为 `succeeded`；有 unsupported、duplicate、review warning 或 item failure 但整体流程完成为 `completed_with_issues`；任务无法启动或整体流程崩溃为 `failed`。

### 9.5 ingest_items

事件型记录，不软删除。

```text
ingest_item_id TEXT PRIMARY KEY
ingest_id TEXT NOT NULL
doc_id TEXT
source_uri TEXT
normalized_source_uri TEXT
status TEXT NOT NULL              -- pending, running, succeeded, failed, duplicate, unsupported
error_id TEXT
created_at TEXT NOT NULL
updated_at TEXT
finished_at TEXT
```

### 9.6 converter_runs

事件型记录，不软删除。

```text
converter_run_id TEXT PRIMARY KEY
doc_id TEXT
revision_id TEXT
converter_name TEXT
converter_version TEXT
input_hash TEXT
output_hash TEXT
warnings_json TEXT
quality_signals_json TEXT
status TEXT NOT NULL
started_at TEXT
finished_at TEXT
created_at TEXT NOT NULL
updated_at TEXT
```

当 re-ingest 同一 document 且 normalized Markdown body 没有变化时，可以记录 `converter_runs.status = succeeded_no_content_change` 或 `skipped_no_content_change`，但不得创建新的 `document_revision`。

### 9.7 chunks

```text
chunk_id TEXT PRIMARY KEY
doc_id TEXT NOT NULL
revision_id TEXT NOT NULL
sequence INTEGER NOT NULL
heading_path_json TEXT
text TEXT NOT NULL
start_offset INTEGER
end_offset INTEGER
source_page INTEGER
language TEXT
token_count INTEGER
content_hash TEXT
is_current INTEGER
created_at TEXT NOT NULL
updated_at TEXT
deleted_at TEXT
```

### 9.8 chunks_fts

v0.1 使用独立 FTS 表，由 indexer 显式同步，不使用 trigger。

```text
chunks_fts USING fts5(
  chunk_id UNINDEXED,
  doc_id UNINDEXED,
  revision_id UNINDEXED,
  title,
  heading_path,
  text,
  tags,
  category
)
```

规则：

- FTS 数据在 `db.sqlite` 内。
- `indb index rebuild --fts` 只重建 current sources 的 FTS。
- archived documents 的 FTS 记录可保留，但默认 search 通过 `documents.status = active` 过滤。
- search 展示不得直接信任 FTS 表内容。FTS 只返回候选 `chunk_id`，最终 title、path、status、snippet 必须回表读取 `chunks` 和 `documents`。

`indb index rebuild --fts` M3 语义：

- 清空并重建 `chunks_fts`。
- 只索引 `documents.status = active` 且 `documents.current_revision_id = chunks.revision_id` 的 current chunks。
- 不索引 archived documents。
- 不索引 old revisions。
- 重建成功后更新相关 `documents.fts_status = indexed`。
- 缺 chunks、缺 current revision、缺 Markdown 的 document 必须记录 error/review，不得静默跳过。

Exit code 语义：

- `0`：FTS rebuild 成功，index 内部一致。
- `1`：只有非阻塞 warning/review，index 内部仍一致。
- `2`：存在 index integrity error，例如 current searchable document 缺 revision、Markdown 或 chunks。

### 9.9 CJK search fallback

v0.1 不能假设 SQLite FTS tokenizer 对中文、日文等 CJK 查询足够可靠。

规则：

- 检测到 query 含 CJK 字符时，先执行正常 FTS。
- 同时对 active current sources 执行 substring fallback。
- fallback 使用 `chunks.text`、`documents.title` 和 `chunks.heading_path_json` 的规范化文本匹配。
- 结果仍必须回表读取 `chunks` / `documents`，并通过 `documents.status = active` 和 `documents.current_revision_id = chunks.revision_id` 过滤。
- v0.1 的 CJK fallback 目标是可靠找回，不追求复杂排序。更好的 tokenizer、ngram index 或外部搜索引擎放到后续版本。

### 9.10 citations

统一 citation 表用于“生成物引用”，不用于普通 search 缓存。

```text
citation_id TEXT PRIMARY KEY
source_type TEXT NOT NULL         -- answer, card, translation, summary, export, saved_search
source_id TEXT NOT NULL
doc_id TEXT NOT NULL
revision_id TEXT NOT NULL
chunk_id TEXT NOT NULL
quote TEXT
quote_hash TEXT
created_at TEXT NOT NULL
```

v0.1 默认不写 `citations`。普通 `indb search` 只渲染 citation snippets。

### 9.11 categories

```text
category_id TEXT PRIMARY KEY
name TEXT NOT NULL
description TEXT
parent_id TEXT
sort_order INTEGER
is_active INTEGER
is_system INTEGER
include_rules TEXT
exclude_rules TEXT
created_at TEXT NOT NULL
updated_at TEXT
deleted_at TEXT
```

v0.1 只做用户自定义大目录，不做自动分类。

### 9.12 tags

```text
tag_id TEXT PRIMARY KEY
name TEXT NOT NULL
normalized_name TEXT NOT NULL
description TEXT
language TEXT
created_at TEXT NOT NULL
updated_at TEXT
deleted_at TEXT
```

### 9.13 tag_aliases

```text
alias_id TEXT PRIMARY KEY
tag_id TEXT NOT NULL
alias TEXT NOT NULL
normalized_alias TEXT NOT NULL
language TEXT
created_at TEXT NOT NULL
updated_at TEXT
deleted_at TEXT
```

用于后续处理多语言同义标签，例如 `LLM`、`large language model`、`大语言模型`、`大模型`。

### 9.14 document_tags

```text
doc_id TEXT NOT NULL
tag_id TEXT NOT NULL
source TEXT
confidence REAL
created_at TEXT NOT NULL
updated_at TEXT
deleted_at TEXT
PRIMARY KEY (doc_id, tag_id)
```

v0.1 支持手动 tag，不做自动建议。

### 9.15 tasks

```text
task_id TEXT PRIMARY KEY
type TEXT NOT NULL
status TEXT NOT NULL
input_json TEXT
result_json TEXT
error_json TEXT
trace_id TEXT
created_at TEXT NOT NULL
updated_at TEXT
started_at TEXT
finished_at TEXT
progress_current INTEGER
progress_total INTEGER
created_by TEXT
```

状态：

```text
pending
running
waiting_user_input
succeeded
completed_with_issues
failed
cancelled
retrying
```

`completed_with_issues` 用于流程跑完但不是纯成功的情况，例如 folder ingest 内存在 unsupported、duplicate、review warning 或 item failure。不要用 `succeeded` 搭配 `result_json.has_issues = true` 来表示这类结果。

### 9.16 task_events

事件型记录，不软删除。

```text
event_id TEXT PRIMARY KEY
task_id TEXT NOT NULL
event_type TEXT
message TEXT
payload_json TEXT
created_at TEXT NOT NULL
```

### 9.17 errors

日志型记录，不软删除。

```text
error_id TEXT PRIMARY KEY
task_id TEXT
trace_id TEXT
component TEXT
error_type TEXT
severity TEXT                    -- info, warning, error, critical
retryable INTEGER
user_message TEXT
developer_message TEXT
message TEXT
stack TEXT
payload_json TEXT
created_at TEXT NOT NULL
```

TUI 默认显示 `user_message`，调试模式再显示 `developer_message` 和 stack。

### 9.18 review_items

统一 review 队列。必须同时记录 `target_type` 和 `target_id`。

```text
review_id TEXT PRIMARY KEY
type TEXT NOT NULL               -- unsupported_source, conversion_low_quality, duplicate_candidate, missing_metadata
target_type TEXT NOT NULL        -- ingest_item, converter_run, duplicate_candidate, document
target_id TEXT NOT NULL
priority INTEGER
reason TEXT
status TEXT NOT NULL             -- pending, accepted, rejected, resolved
created_at TEXT NOT NULL
updated_at TEXT
resolved_at TEXT
```

v0.1 不使用 `manual_classification` 作为默认 review type。未分类文档直接使用默认 category。

### 9.19 search_queries

日志型记录，不软删除。

仅当 `search.log_queries = true` 时写入。

```text
query_id TEXT PRIMARY KEY
query_text TEXT NOT NULL
mode TEXT NOT NULL               -- keyword, fts
filters_json TEXT
result_count INTEGER
created_at TEXT NOT NULL
```

### 9.20 search_results

可选表。默认不持久化，由 `search.persist_search_results = false` 控制。

```text
query_id TEXT NOT NULL
rank INTEGER NOT NULL
doc_id TEXT NOT NULL
revision_id TEXT NOT NULL
chunk_id TEXT NOT NULL
snippet TEXT
score REAL
created_at TEXT NOT NULL
PRIMARY KEY (query_id, rank)
```

如果未来启用，可只保留最近 N 天。

### 9.21 duplicate_candidates

```text
duplicate_id TEXT PRIMARY KEY
doc_id_a TEXT
doc_id_b TEXT
existing_doc_id TEXT
source_uri TEXT
reason TEXT
confidence REAL
status TEXT NOT NULL             -- pending, ignored, merged, confirmed
created_at TEXT NOT NULL
updated_at TEXT
```

v0.1 duplicate 规则：

- `source_hash` 完全相同：不创建新 document，不创建新 doc_id，记录 `ingest_item.status = duplicate`，返回 existing doc_id。
- `content_hash` 相同但 `source_hash` 不同：默认创建新 document，标记 possible duplicate，进入 review；如果是明确 re-ingest 到同一 `doc_id`，则只新增 `source_files`，不创建新 revision。
- 文件名相似但 hash 不同：不自动判断重复。
- exact duplicate 在 folder ingest 中算 issue，不算 failure；父 task / ingest_run 应为 `completed_with_issues`。
- `content_hash` 使用规范化 Markdown 正文 hash，不包含 frontmatter。

## 10. Source Markdown Frontmatter

```yaml
---
schema_version: "indbase.source.v1"
type: "source_document"
status: "active"
doc_id: "doc_20260512_a8f3c2"
revision_id: "rev_doc_20260512_a8f3c2_0001"
title: "Agent Harness Design"
original_title: "Agent Harness Design"
filename_slug: "agent-harness-design"
source_type: "docx"
source_uri: "E:/Downloads/agent-harness-design.docx"
normalized_source_uri: "E:/Downloads/agent-harness-design.docx"
original_path: ".indbase/originals/2026/05/doc_20260512_a8f3c2/original.docx"
canonical_path: "sources/2026/05/agent-harness-design__doc_20260512_a8f3c2__rev_0001.md"
source_hash: "sha256:..."
source_size_bytes: 123456
mime_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
content_hash: "sha256:..."
converter: "markitdown"
converter_version: "..."
ingest_task_id: "task_20260512_9d21ac"
trace_id: "trace_20260512_x7a9"
language: "en"
category_id: "cat_uncategorized"
category: "未分类"
tags: []
quality_status: "passed"
quality_signals:
  text_length: 12345
  empty: false
  binary_garbage_detected: false
  frontmatter_valid: true
  code_fence_balanced: true
  script_style_ratio_high: false
  link_density_high: false
chunk_count: 42
fts_indexed: true
embedding_indexed: false
needs_review: false
review_reasons: []
ingested_at: "2026-05-12T10:30:00+08:00"
---
```

v0.1 使用 `quality_status` 和 `quality_signals`，避免依赖假精确的 `quality_score`。

frontmatter 是 revision 生成时的可读快照。DB 是 durable state 的权威来源；category、tag、title、archive status 等 metadata edit 不重写旧 revision frontmatter。

## 11. v0.1 任务流

### 11.1 Vault Init

```text
indb init
  -> choose vault path
  -> create directory structure
  -> create SQLite DB
  -> run migrations
  -> choose category template
  -> user edits categories
  -> write config.toml
  -> run doctor check
```

### 11.2 Category Templates

初始化提供三套模板。

Minimal：

```text
未分类
工作
学习
资料
个人
工具
```

Academic：

```text
未分类
计算机科学
数学与统计
自然科学
社会科学
人文
工具与参考
跨学科
```

Full：

```text
未分类
计算机科学
数学与统计
物理
生命科学
哲学
社会科学
经济与商业
历史与文化
语言与文学
艺术与设计
个人管理
工具与参考
跨学科
```

### 11.3 Local File Ingest

```text
input path
  -> validate path
  -> inspect source
  -> if unsupported extension:
       record ingest_item = unsupported
       create review item type unsupported_source
       stop this item
  -> preliminary source metadata
  -> calculate source_hash
  -> normalize source_uri for matching
  -> duplicate / re-ingest check
  -> if exact source duplicate:
       record ingest_item = duplicate
       return existing doc_id
       stop this item
  -> if normalized_source_uri or explicit --doc-id matches existing document:
       re-ingest as that document
  -> otherwise create doc_id
  -> ask or generate short filename slug
  -> archive original
  -> create source_file record
  -> convert or direct normalize
  -> normalize markdown
  -> final content metadata
  -> quality validate
  -> if quality warning:
       create review item type conversion_low_quality
  -> write source markdown
  -> create immutable document_revision
  -> create chunks
  -> update SQLite FTS
  -> mark document searchable
  -> finish ingest item
```

M3 re-ingest 规则：

- 显式 `--doc-id <doc_id>` 优先，作为该 document 的 re-ingest。
- 否则用 `normalized_source_uri` 匹配既有 document。
- `source_hash` 完全相同是 exact duplicate，不创建 document，不创建 revision。
- `source_hash` 不同但 normalized Markdown body 的 `content_hash` 相同，不创建新 revision；归档新的 original，新增 `source_files`，记录 converter run 和 task event。
- `content_hash` 变化时创建新的 immutable revision，sequence 递增，例如 `__rev_0002`。
- 文件名相似、标题相似、basename 相同都不自动合并为同一 document。
- `content_hash` 是规范化 Markdown 正文 hash，不包含 frontmatter 或 ingest 时间等 volatile fields。

### 11.4 Folder Ingest

```text
input folder
  -> scan files
  -> create parent task
  -> create ingest_run
  -> create ingest_items
  -> process each supported/best-effort file
  -> unsupported items become review_items
  -> aggregate report
```

单个 unsupported item 不阻塞整个 folder ingest。

Folder ingest 状态和退出码：

- 全部成功：父 task / ingest_run 为 `succeeded`，CLI exit code `0`。
- 有 unsupported、duplicate、review warning 或 item failure，但 folder 流程跑完：父 task / ingest_run 为 `completed_with_issues`，CLI exit code `1`。
- folder 无法扫描、任务无法启动或核心流程崩溃：父 task / ingest_run 为 `failed`，CLI exit code `2`。
- exact duplicate 是 issue，但不是 failure；不创建 review item。

### 11.5 Ingest 事务边界

Stage 1：Source Archive Transaction

必须全部成功：

```text
validate path
calculate hash
duplicate check
archive original
create source_file record
```

失败则该 ingest item 失败。

Stage 2：Conversion Transaction

可以失败，但不得污染主索引：

```text
convert or normalize
quality validate
write markdown candidate
create converter_run
```

失败则记录 converter error，不创建 current revision。

Stage 3：Index Transaction

必须成功才使文档 searchable：

```text
write final markdown
create document_revision
create chunks
update FTS
set documents.current_revision_id
set fts_status = indexed
```

FTS 是 v0.1 ingest 必需条件。v0.2 开始 embedding 必须异步补，不阻塞 ingest。

如果 Markdown 写入、revision/chunks 写入和 FTS 更新不能作为一个一致的成功单元完成，则该 ingest item 失败，document 不进入 searchable 状态。实现上应优先写临时 Markdown，待 Index Transaction 成功后再提交最终路径；不得出现“revision 已标记 current，但 search 查不到”的静默成功。

如果 archive original 已成功，但 conversion 或 index 失败：

- 可以保留 `documents.ingest_status = failed` 的失败记录。
- `documents.current_revision_id` 必须为 `NULL` 或保持旧 current revision，不得指向失败 revision。
- `documents.fts_status = failed`。
- `ingest_item.status = failed`。
- `errors` 和 `task_events` 必须记录失败。
- 如需人工处理，创建 `review_items`。
- `search` 永远不返回 `current_revision_id IS NULL` 的 document。

### 11.6 Chunker v0

v0.1 chunk 策略：

- 优先按 Markdown heading 切分。
- heading 下内容过长时按段落合并切分。
- 单 chunk 目标 500-1000 tokens。
- 单 chunk 最大 1500 tokens。
- 不跨文档。
- 不跨 revision。
- 保留 `heading_path_json`。
- chunk text 可包含必要标题上下文，保证搜索片段有语义。
- offset 仍尽量对应正文区域。

### 11.7 Search

```text
indb search <query>
  -> create search_queries row if search.log_queries = true
  -> parse filters
  -> metadata search
  -> SQLite FTS
  -> if CJK query: run substring fallback
  -> collect candidate chunk_ids
  -> join chunks and documents
  -> filter documents.status = active
  -> filter documents.current_revision_id = chunks.revision_id
  -> rank
  -> build snippets from chunks.text
  -> render citation snippets
  -> do not persist citations by default
```

M3 search ranking 保持简单可解释：

- FTS 命中按 `bm25(chunks_fts)`。
- CJK fallback 命中按 title 命中、heading 命中、chunk text 命中的顺序给简单权重。
- 同一 chunk 同时由 FTS 和 fallback 命中时去重。
- 默认 `top_k = 20`。
- snippet 只从 `chunks.text` 构造；找到命中词时围绕命中词，否则取 chunk 开头。
- 不做 query expansion、rerank、semantic boost、embedding 或跨文档综合。

若 `search.log_queries = true`，写入 `search_queries`，记录 query text、filters、top_k 和 result_count。默认 `search.persist_search_results = false`，普通 search 不写 `search_results`，也不写 `citations`。

v0.1 search 只搜：

```text
sources current revisions
```

默认不搜：

```text
archived documents
old revisions
translations
atomic notes
```

### 11.8 Archive / Restore

Archive：

```text
documents.status = archived
documents.archived_at = now
default search excludes the document
FTS rows may remain
doc open still works
```

Restore：

```text
documents.status = active
documents.archived_at = null
default search includes the document again
```

v0.1 不做物理删除。

### 11.9 Review Queue

```text
indb review list
  -> list pending review_items

indb review show <review_id>
  -> show target_type, target_id, reason, status, and related error/task context when available

indb review resolve <review_id>
  -> update review_items.status / resolved_at
  -> do not run hidden business actions
```

v0.1 review item 类型：

- unsupported_source
- conversion_low_quality
- duplicate_candidate
- missing_metadata

`review resolve` 只处理 review item 状态。实际修复动作必须通过显式命令完成，例如重新 ingest、archive、restore、修改 metadata 或处理 duplicate。这样 review queue 不会隐藏副作用。

M3 必须实现 review 可见性：unsupported、low-quality conversion、duplicate candidate 等 review item 必须能通过 `review list/show` 查询。`review resolve` 可以是只更新状态和 `resolved_at` 的薄实现，不作为 M3 主链路阻塞项。

### 11.10 Doctor

`indb doctor` 检查：

- DB 是否存在。
- schema_migrations 是否完整。
- 目录结构是否完整。
- config 是否存在。
- original 是否丢失。
- source markdown 是否丢失。
- current_revision 是否有效。
- chunks 是否存在。
- FTS 是否同步。
- archived docs 是否正确从默认 search 过滤。
- orphan files。
- orphan DB records。
- review queue 是否有阻塞项。

M3 doctor 规则：

- 只诊断，不提供 `doctor --fix`。
- 支持 `--json`，便于自动化验收。
- exit code `0`：无问题。
- exit code `1`：只有 warning，没有 error/critical。
- exit code `2`：存在 error 或 critical。
- 以 DB 为权威状态，检测 Markdown frontmatter 的 `doc_id`、`revision_id`、`content_hash` 与 DB 不一致。
- 检测 Markdown 文件内容 hash 与 `document_revisions.content_hash` 不一致。
- 检测 current revision、chunks、FTS、canonical_path、original_path 是否一致。
- 报告 MarkItDown availability，但 MarkItDown 缺失本身不应阻断 Tier 1 md/txt/csv/json 的健康。

## 12. Converter fallback matrix

v0.1：

所有成功或失败的转换路径都必须写 `converter_runs`。Tier 1 的 direct normalize / structured parse 也算 converter run。

MarkItDown 在 M3 是可选能力：

- `.html` 优先使用 MarkItDown；MarkItDown 不可用或失败时走 basic HTML text extraction fallback，并在 `converter_runs.warnings_json` 记录。
- `.docx` / `.xlsx` / `.pptx` 只有 MarkItDown 可用时尝试；失败或不可用时创建 error/review，不阻塞同一 folder ingest 的其他文件。
- `indb doctor` 报告 MarkItDown 是否可用。

```text
.md
  tier: strong
  primary: direct copy + normalize
  fallback: none
  converter_run: direct_normalizer

.txt
  tier: strong
  primary: direct copy + normalize
  fallback: encoding detection
  converter_run: direct_normalizer

.html
  tier: strong
  primary: MarkItDown
  fallback: basic HTML text extraction
  warnings:
    script/style ratio too high
    body text too short
    link density too high

.csv
  tier: strong
  primary: direct structured parse + markdown table
  fallback: plain text block

.json
  tier: strong
  primary: pretty print + markdown code block
  fallback: raw text

.docx
  tier: best-effort
  primary: MarkItDown
  low quality: review item

.xlsx
  tier: best-effort
  primary: MarkItDown
  low quality: review item

.pptx
  tier: best-effort
  primary: MarkItDown
  low quality: review item

other extensions
  tier: unsupported_source
  behavior: review item, no Markdown, no FTS
```

## 13. v0.1 质量门禁

### 13.1 Ingest gate

必须满足：

- original archived
- source_file record exists
- converter_run exists
- markdown exists
- document exists
- document_revision exists
- current_revision_id valid
- chunks exist
- FTS updated
- errors recorded if failed

M3.1 hardening rule:

```text
No chunks = no searchable document.
No searchable document = no successful M3 ingest.
Empty or no-extractable-content sources must fail conversion as no_extractable_content,
create visible error/review state, and must not create current_revision_id, chunks, or FTS rows.
```

### 13.2 Markdown gate

检查：

- frontmatter schema_version exists
- doc_id exists
- revision_id exists
- content_hash exists
- text_length > minimum threshold
- markdown is not empty
- code fences balanced where detectable
- no obvious binary garbage
- HTML conversion warns on high script/style ratio
- HTML conversion warns on high link density

### 13.3 Search gate

每条搜索结果必须有：

- doc_id
- revision_id
- chunk_id
- snippet
- source path
- score

搜索结果不得引用不存在的 chunk。

搜索结果展示必须回表读取：

- `chunks.text` 生成 snippet。
- `documents.title` / `documents.canonical_path` 生成显示信息。
- `documents.status` 和 `documents.current_revision_id` 做过滤。

不得直接把 `chunks_fts` 中的冗余字段当作最终展示事实。

## 14. v0.2 Dogfood MVP

### 14.1 v0.2 目标

v0.2 目标是让系统开始真实 dogfood：

- 扩展复杂文档处理。
- 加入 OCR v0。
- 搜索从 FTS 升级到 embedding + hybrid。
- 分类从手动升级到建议 + 确认。
- 翻译进入核心流程。

### 14.2 v0.2 模块

新增：

- PDF pipeline
- local OCR adapter
- ocr_pages
- embedding adapter
- vector index
- hybrid search
- vector index rebuild
- category suggestion
- tag suggestion
- classification feedback
- translation
- stronger harness

### 14.3 v0.2 新增表

#### ocr_pages

```text
ocr_page_id TEXT PRIMARY KEY
doc_id TEXT NOT NULL
revision_id TEXT NOT NULL
page_number INTEGER NOT NULL
text TEXT
confidence REAL
quality_status TEXT
quality_signals_json TEXT
needs_review INTEGER
engine TEXT
engine_version TEXT
created_at TEXT NOT NULL
updated_at TEXT
deleted_at TEXT
```

#### embeddings

```text
embedding_id TEXT PRIMARY KEY
chunk_id TEXT NOT NULL
doc_id TEXT NOT NULL
revision_id TEXT NOT NULL
provider TEXT
model TEXT
dimension INTEGER
vector_ref TEXT
content_hash TEXT
status TEXT NOT NULL
created_at TEXT NOT NULL
updated_at TEXT
deleted_at TEXT
```

#### classification_suggestions

```text
suggestion_id TEXT PRIMARY KEY
doc_id TEXT NOT NULL
revision_id TEXT NOT NULL
suggested_category_id TEXT
confidence REAL
reason TEXT
alternative_category_ids_json TEXT
suggested_tags_json TEXT
needs_user_confirmation INTEGER
model TEXT
prompt_version TEXT
status TEXT NOT NULL
created_at TEXT NOT NULL
updated_at TEXT
deleted_at TEXT
```

#### classification_feedback

```text
feedback_id TEXT PRIMARY KEY
doc_id TEXT NOT NULL
old_category_id TEXT
new_category_id TEXT
old_tags_json TEXT
new_tags_json TEXT
reason TEXT
created_at TEXT NOT NULL
updated_at TEXT
deleted_at TEXT
```

#### executions

```text
execution_id TEXT PRIMARY KEY
type TEXT NOT NULL
source_doc_id TEXT
source_revision_id TEXT
input_json TEXT
output_path TEXT
output_json TEXT
model TEXT
prompt_version TEXT
status TEXT NOT NULL
created_at TEXT NOT NULL
updated_at TEXT
finished_at TEXT
deleted_at TEXT
```

#### translations

```text
translation_id TEXT PRIMARY KEY
execution_id TEXT
source_doc_id TEXT NOT NULL
source_revision_id TEXT NOT NULL
source_language TEXT
target_language TEXT
translation_mode TEXT       -- selected_chunks, full_document
source_chunk_ids_json TEXT
output_path TEXT
glossary_id TEXT
model TEXT
prompt_version TEXT
status TEXT NOT NULL
created_at TEXT NOT NULL
updated_at TEXT
deleted_at TEXT
```

### 14.4 v0.2 任务流

Embedding Flow：

```text
document indexed by FTS
  -> enqueue embedding task
  -> embed current revision chunks
  -> write vector index
  -> set embedding_status = indexed
```

Embedding 不阻塞 ingest。

Auto Classification Flow：

```text
document searchable
  -> enqueue classification task
  -> load category definitions
  -> suggest category and tags
  -> validate category exists
  -> confidence gate
  -> auto apply / review / uncategorized
```

Translation Flow：

```text
selected_chunks first
full_document later
write outputs/translations/YYYY/MM/<short-title>__doc_id.<target_lang>.md
do not overwrite source Markdown
```

## 15. v0.3 Intelligent Workflow MVP

### 15.1 v0.3 目标

v0.3 在稳定数据层和 dogfood 能力上增加智能工作流。

包含：

- `indb ask`
- answer with citations
- candidate card extraction
- claim-level citation
- card review
- accepted atomic notes
- richer TUI
- possible Textual adoption

### 15.2 candidate_cards

```text
card_id TEXT PRIMARY KEY
source_doc_id TEXT NOT NULL
source_revision_id TEXT NOT NULL
title TEXT
summary TEXT
claims_json TEXT
suggested_category_id TEXT
suggested_tags_json TEXT
confidence REAL
status TEXT NOT NULL       -- drafted, reviewing, accepted, rejected, merged, superseded
accepted_note_path TEXT
created_by_task_id TEXT
created_at TEXT NOT NULL
updated_at TEXT
deleted_at TEXT
```

### 15.3 candidate_card_sources

```text
card_id TEXT NOT NULL
chunk_id TEXT NOT NULL
doc_id TEXT NOT NULL
revision_id TEXT NOT NULL
quote TEXT
PRIMARY KEY (card_id, chunk_id)
```

### 15.4 Claim schema

```json
[
  {
    "claim_id": "claim_001",
    "text": "Agent harness 的核心作用是约束和验证模型输出。",
    "source_chunk_ids": ["chunk_a8f3c2_0012"],
    "quotes": ["..."],
    "confidence": 0.88
  }
]
```

规则：

- 每个 claim 必须有 source chunk。
- 没有 citation 的 claim 不得进入 accepted atomic note。
- candidate card 未经用户确认不得写入 `notes/atomic/`。

### 15.5 Ask Flow

```text
indb ask <query>
  -> search
  -> retrieve snippets
  -> LLM answer using only snippets
  -> validate citations
  -> output answer + sources
```

`search` 和 `ask` 必须是两个命令：

- `search` 只返回检索结果和原文片段。
- `ask` 才生成综合答案。

## 16. 更新后的里程碑

### M0：Core Skeleton

交付：

- config
- schema_migrations
- path resolver
- DB migration
- ID generator
- logging
- task schema
- error schema
- review_items schema

### M1：Vault Init + CLI First

交付：

- `indb init`
- category templates
- `indb catalog list/add`
- `indb task list/show`
- `indb doctor`

### M2：Local Ingest Minimal

交付：

- file ingest
- folder ingest
- original archive
- source markdown
- metadata
- revision
- quality validator
- errors
- unsupported_source handling
- duplicate handling

### M3：Chunk + FTS + Citation Snippet Search

交付：

- automated doctor checkpoint
- chunker
- SQLite FTS
- `indb search`
- citation snippets
- search query log
- archive / restore affects default search
- `index rebuild --fts`
- review list/show visibility
- M3 automated test suite

这是 v0.1 第一个可 dogfood 的 Foundation checkpoint。M3 不是人工 checklist，必须能用自动化测试在临时 vault 中重复验证。

M3 自动验收必须覆盖：

- `indb init` 创建 vault、schema、category template。
- Tier 1 fixtures：`md`、`txt`、`html`、`csv`、`json`。
- unsupported fixtures：`pdf`、`png` 进入 review，不生成 Markdown，不进入 FTS。
- multilingual fixtures：中文、英文、日文。
- empty / no-extractable-content fixtures fail conversion and do not create current revisions, chunks, or FTS rows.
- exact duplicate 不创建新 doc。
- same normalized content with different bytes 进入 possible duplicate 或 no-content-change re-ingest 分支。
- changed re-ingest 生成 `__rev_0002` 和 `rev_<doc_id>_0002`。
- search 返回 `doc_id` / `revision_id` / `chunk_id` / `snippet`。
- archive / restore 改变默认 search 结果。
- `index rebuild --fts` 可恢复 FTS。
- `doctor --json` 能发现缺文件、orphan records、FTS desync、frontmatter/hash mismatch，并返回正确 exit code。

M3 测试分层：

- core service tests 覆盖大多数状态机和边界。
- CLI black-box tests 覆盖关键 happy path、失败可见性、退出码和 JSON 输出。
- checkpoint 命令建议为 `pytest tests/m3_checkpoint`。

### M4：Practical TUI Lite

交付：

- dashboard
- task queue
- ingest wizard
- search panel
- review list
- error viewer
- settings summary
- minimal manual category/tag commands

v0.1 完成。

### M5：Manual Catalog + Better Review Queue

交付：

- category CRUD
- tag CRUD
- manual document category edit
- review item resolution
- archive/restore polish

### M6：Complex Documents + OCR v0

交付：

- PDF ingest
- local OCR
- ocr_pages
- quality flags

### M7：Embedding + Hybrid Search

交付：

- embedding adapter
- vector index
- hybrid search
- index status
- index rebuild for vectors

### M8：Auto Classification

交付：

- category suggestion
- tag suggestion
- confidence gate
- feedback log

### M9：Translation

交付：

- selected chunk translation
- full document translation
- translation records
- translation output writer

v0.2 完成。

### M10：Candidate Cards

交付：

- candidate extraction
- claim citations
- card review
- accepted atomic notes

### M11：Ask + Intelligent Workflow

交付：

- `indb ask`
- answer citation validation
- workflow orchestration
- Textual TUI decision

v0.3 完成。

## 17. 关键实现约束

1. v0.1 只支持 Tier 1 强支持和 Tier 2 best-effort。
2. v0.1 只搜索 `sources/` 的 current revision。
3. v0.1 只有 `search`，没有 `ask`。
4. v0.1 不做 OCR、embedding、hybrid search、翻译、自动分类、candidate cards。
5. 普通 search 不默认持久化 citations。
6. `citations` 表只用于生成物引用。
7. 所有稳定身份使用 doc_id，不使用标题或文件名。
8. 文件名必须有简短语义 slug，以改善 Obsidian 浏览体验。
9. revision 是不可变快照。
10. current_revision_id 只是指针。
11. 旧 revision 的 chunks、citations、outputs 不得删除。
12. FTS 是 v0.1 ingest 必需条件。
13. embedding 从 v0.2 开始，且必须异步，不阻塞 ingest。
14. classification 从 v0.2 开始，且必须异步，不阻塞 ingest。
15. candidate cards 从 v0.3 开始，必须 claim-level citation。
16. 不允许业务层裸调 LLM，必须经过 harness。
17. v0.1 core 不调用 harness。
18. 不允许转换失败后静默写入成功状态。
19. 不允许直接物理删除文档，先 archive。
20. `indb doctor` 是长期使用必需能力。

## 18. v0.1 硬验收 12 条

Foundation MVP 完成必须满足：

1. `indb init` 可创建 vault 和 db。
2. category template 可选择并写入 DB。
3. `indb ingest` 单文件成功。
4. `indb ingest` 文件夹成功，单个 unsupported item 不阻塞整体任务。
5. 成功文档保留 original。
6. 成功文档生成可读 Markdown，文件名包含 slug + doc_id + revision sequence。
7. `documents` / `document_revisions` / `source_files` / `chunks` 记录完整。
8. SQLite FTS 可搜索 current sources。
9. search 结果有 doc_id / revision_id / chunk_id / snippet。
10. task / error / review 可查询。
11. archive / restore 会影响默认 search。
12. `indb doctor` 能发现缺文件、孤儿记录、FTS 不同步。

v0.1 典型使用命令：

```text
indb init
indb ingest ./docs --recursive
indb search "agent harness"
indb doc show <doc_id>
indb doc open <doc_id>
indb task list
indb review list
indb error list
indb doctor
```
