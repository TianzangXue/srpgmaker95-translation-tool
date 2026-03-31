# SRPG95 项目工作流

## 概览

当前正式主线已经统一为 `project` 工作流：

1. `project init`
2. 编辑 `txt_zh/`
3. `project doctor`
4. `project build`

正式对外口径：

- 默认 `--zh-seed empty`
- 默认 `--import-mode always`
- `stable-menu16` 是唯一正式推荐的运行时 profile
- `strong-dialogue` 仍保留，但只作为实验 profile

## 工作区结构

```text
<workspace>/
  machine/
  txt_src/
  txt_zh/
  txt_map/
  reports/
```

- `machine/`：内部结构化层，给导入、校验、封包和报告使用
- `txt_src/`：原文只读基线
- `txt_zh/`：正式人工输入
- `txt_map/`：机器维护的映射层，不手改
- `reports/`：项目级检查和构建报告

## 正式命令

初始化项目：

```powershell
python -m srpg95tool project init <game_dir> <workspace_dir>
```

显式指定空白种子：

```powershell
python -m srpg95tool project init <game_dir> <workspace_dir> --zh-seed empty
```

兼容旧种子模式：

```powershell
python -m srpg95tool project init <game_dir> <workspace_dir> --zh-seed copy-source
```

检查 TXT 与构建风险：

```powershell
python -m srpg95tool project doctor <workspace_dir>
```

执行完整导入、封包与运行时补丁：

```powershell
python -m srpg95tool project build <game_dir> <workspace_dir> <out_dir>
```

只导入 TXT 回 machine 层：

```powershell
python -m srpg95tool project import-txt <workspace_dir>
```

如需兼容旧逻辑，可显式切换导入模式：

```powershell
python -m srpg95tool project doctor <workspace_dir> --import-mode diff-only
python -m srpg95tool project build <game_dir> <workspace_dir> <out_dir> --import-mode diff-only
python -m srpg95tool project import-txt <workspace_dir> --import-mode diff-only
```

## TXT 规则

- 每个源文件导出为一个 TXT
- `txt_src/` 与 `txt_zh/` 的文件名必须一致
- 块分隔符固定为单独一行 `====`
- 块顺序不能改
- 块数量不能改
- 可以直接写多行文本
- 空白 `txt_zh` 块表示未翻译，不写回
- `\0` 表示显式空字符串，会写回
- 任何其它非空块都是 `authoritative block`
- 即使与 `txt_src/` 完全相同，非空块也会写回

对白块约定：

- 第一行是说话人
- 第二行起是正文
- 单独一行 `\f` 表示强制分页

转义规则：

- 如果正文里真的要写 `====`，会导出为 `\====`

## 导入模式

- `always`
  - 默认模式
  - 只要 `txt_zh` 块非空，就视为权威文本并写回
- `diff-only`
  - 兼容旧逻辑
  - 只有 `txt_zh` 与 `txt_src` 不同才写回

例子：

- 原文：`真千代`
- 译文：`真千代`

在 `always` 下：

- 该块会写回
- 会计入 `authoritative_blocks`
- 会计入 `same_as_source_authoritative_blocks`

在 `diff-only` 下：

- 该块会被跳过
- 会计入 `unchanged_blocks`

## 正式运行时策略

- `SMAP opcode 1 / 201`
  - 长中文对白正式由 pack 层原生拆行 / 分页处理
  - `stable-menu16` 保持稳定 UI 基线，不启用实验性的 `opcode 1 / 201` side-buffer 对白链
- `opcode 45`
  - 菜单文本正式通过 `stable-menu16` 的 GDI 路线显示
- DAT-backed UI
  - 当前正式覆盖面包括单位面板、紧凑单位卡、出击角色一览、物品/魔法帮助栏、战斗悬浮单位名、据点商店、战斗技能近身弹出框等
- `DAT`
  - 仍然执行固定槽位限长校验
  - 超长是硬错误，不会静默截断

## 主要报告

- `reports/txt_consistency.json`
- `reports/txt_import_result.json`
- `reports/dialogue_layout_plan.json`
- `reports/dialogue_runtime_audit.json`
- `reports/pack_risks.json`
- `reports/pack_result.json`
- `reports/project_build_result.json`
- `reports/ui_dat_crosswalk.json`
- `reports/dat_ui_priority.json`
- `reports/ui_label_audit.json`
- `reports/ui_patch_coverage.json`
- `reports/untranslated_gdi_ui_blocks.json`

## 正式结论

- 日常入口以 `project` 子命令为准
- `unpack / simulate-pack / pack / patch-runtime` 仍保留，主要用于调试和研究
- `txt_zh` 是正式人工输入，不再把预填原文当主流程
- UI / 固定文本与对白块采用不同写回策略
- `stable-menu16` 是正式 UI 中文链基线
- `strong-dialogue` 仅保留为实验性对白 runtime 分支
