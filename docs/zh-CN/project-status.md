# SRPG95 项目状态

## 当前正式主线

当前项目已经形成一条完整可用的正式主线：

- 解包、回填、封包、运行时补丁已经连成一条稳定流程
- 长中文对白正式由 pack 层原生拆行 / 分页解决
- `stable-menu16` 是唯一正式推荐 runtime profile
- `stable-menu16` 是唯一正式推荐 runtime profile
- `strong-dialogue` 仅保留为实验性对白 profile

## 已完成能力

- 结构化解包
- 固定 DAT / `MAPC` / `SMAP` 回包
- `SMAP` 事件长度与 trailer 保持
- `project` 顶层工作流
- `txt_src / txt_zh / txt_map / machine / reports` 工作区结构
- 空白 `txt_zh` 种子主线
- `always` 导入模式主线
- UI 同文权威块写回
- 孤立 `opcode 201` 对白续行写回
- `opcode 45` 菜单 GDI bridge
- hover highlight 修复
- 12px 菜单 UI 字号
- `BGM / EFS / BMP` 资源别名兼容
- 当前已确认 DAT-backed UI 中文显示链

## 当前默认规则

- 默认 `--zh-seed empty`
- 默认 `--import-mode always`
- `txt_zh` 非空块即权威块
- UI / 固定文本与对白块采用不同写回策略
- DAT 固定槽位超长仍是硬错误

## 正式推荐命令

```powershell
python -m srpg95tool project init <game_dir> <workspace_dir>
python -m srpg95tool project doctor <workspace_dir>
python -m srpg95tool project build <game_dir> <workspace_dir> <out_dir>
```

## 当前推荐 runtime profile

`stable-menu16` 当前包含：

- `opcode 45` 菜单 GDI bridge
- hover highlight 修复
- 12px 菜单 UI 字号
- `BGM / EFS / BMP` 资源别名兼容
- 当前正式覆盖的 DAT-backed UI surfaces

## 当前已确认的 UI 覆盖面

至少包括：

- `unit_sheet_panel`
- `compact_unit_card`
- `sortie_unit_roster`
- `item_magic_help_panel`
- `battle_unit_hover_name`
- `camp_shop_item_list`
- `battle_skill_popup`

## 未来仍可继续研究

这些方向依然保留为后续研究主题：

- DAT 扩容
- 更多 UI 面覆盖
- 更深的 `HARMONY.DLL` 研究
- 更深的对白 / runtime 研究

## 报告现状

当前正式工作流会持续产出：

- `txt_consistency.json`
- `txt_import_result.json`
- `dialogue_layout_plan.json`
- `dialogue_runtime_audit.json`
- `standalone_dialogue_audit.json`
- `pack_risks.json`
- `pack_result.json`
- `project_build_result.json`
- `ui_dat_crosswalk.json`
- `dat_ui_priority.json`
- `ui_label_audit.json`
- `ui_patch_coverage.json`
- `untranslated_gdi_ui_blocks.json`
