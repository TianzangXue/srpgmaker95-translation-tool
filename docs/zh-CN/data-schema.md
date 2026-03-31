# SRPG95 数据结构说明

## 目标

当前数据结构服务于：

- 结构化解包
- 结构化翻译
- `SMAP` 事件重建与回包
- 长对白自动拆行 / 分页
- 项目级导入、审计和风险报告

## 固定 DAT

固定 DAT 导出仍然保留：

- `record_id`
- `offset`
- `size`
- `fields`
- `raw_record_hex`
- `raw_record_sha256`

文本字段仍然保留：

- `text_id`
- `text`
- `source_bytes_hex`
- `actual_text_bytes_hex`
- `max_bytes`
- `null_terminated`
- `source_offset_in_file`

这些字段仍受固定槽位限制约束。

## MAPC

`MAPC_*.json` 仍以线性 `tiles` 为主载荷。

## SMAP 顶层

每张地图仍然导出为：

- `maps/SMAP_<id>/smap.json`
- `maps/SMAP_<id>/events/*.json`

`smap.json` 仍然保留：

- `ev_chunks`
- `event_declarations`
- 静态段
- 原始哈希

## SMAP 事件对象

事件对象仍然保留：

- `declared_length`
- `padded_length`
- `command_bytes_length`
- `chunk_chain`
- `raw_event_bytes_hex`
- `raw_event_sha256`
- `commands`

这些字段仍是回包长度契约的权威来源。

## machine 层与 TXT 工作区

正式项目工作区包含：

- `machine/`
- `txt_src/`
- `txt_zh/`
- `txt_map/`
- `reports/`

其中：

- `txt_src` = 原文只读基线
- `txt_zh` = 正式人工输入
- `txt_map` = 机器维护的映射层

## authoritative block 语义

当前工作流新增并正式使用这些概念：

- `translation_present`
- `same_as_source`
- `authoritative_blocks`
- `same_as_source_authoritative_blocks`

语义：

- `txt_zh` 空块 = 未翻译
- `txt_zh` 非空块 = 权威块，会写回
- 即使与原文完全相同，也可能是合法权威块

## 主索引与目录

兼容层仍然存在：

- `texts/text_index.jsonl`
- `texts/catalog/*.jsonl`
- `texts/dialogue_catalog/*.jsonl`

但正式人工入口已经转移到 `txt_zh/`。

## 对白布局契约

pack 阶段对长对白执行单独布局：

- `speaker` 必须单行
- `speaker` 超过 `79` 字节直接报错
- `body` 支持 `\n`
- `body` 支持 `\f`
- 默认按 `cp936` 字节长度自动换行
- 每页最多 `4` 行正文
- 超过自动分页
- 新页默认重复说话人

## 孤立对白续行

`txt_map` 里的 `dialogue_block` 不是所有对白显示文本的唯一形态。

某些继续显示在上一句对白框内的续行，会以：

- `entry_kind = fixed_text`
- `opcode_id = 201`
- `display_role = dialogue`

的形式单独存在。

这些“孤立 `201` 续行”现在也会在 pack 阶段被正式写回成品事件 payload，不依赖 runtime profile 变化。

## 报告字段

当前正式主线至少会产出：

- `txt_consistency.json`
- `txt_import_result.json`
- `dialogue_layout_plan.json`
- `standalone_dialogue_audit.json`
- `pack_risks.json`
- `pack_result.json`
- `project_build_result.json`
- `ui_dat_crosswalk.json`
- `dat_ui_priority.json`
- `ui_label_audit.json`
- `ui_patch_coverage.json`
- `untranslated_gdi_ui_blocks.json`

## runtime 口径

当前正式 workflow 中：

- `stable-menu16` 是唯一正式推荐 runtime profile
- `stable-menu16` 保持稳定 UI 基线
- `strong-dialogue` 仅保留为实验性对白 profile
- 长对白正式由 pack 层 schema 与布局规则解决
