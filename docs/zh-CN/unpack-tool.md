# SRPG95 解封包工具说明

## 范围

`srpg95tool` 当前覆盖：

- 结构化解包
- 结构化回填
- `SMAP` 事件级重建
- 运行时补丁
- 风险与审计报告生成

正式生产入口现在优先推荐 `project` 子命令；底层命令仍保留，用于调试与研究。

## 主要 CLI

```powershell
python -m srpg95tool unpack <game_dir> <out_dir>
python -m srpg95tool inspect <out_dir>
python -m srpg95tool verify-roundtrip <game_dir> <out_dir>
python -m srpg95tool simulate-pack <unpack_dir>
python -m srpg95tool pack <game_dir> <unpack_dir> <out_dir>
python -m srpg95tool inspect-runtime <game_dir>
python -m srpg95tool patch-runtime <game_dir> <out_dir> --profile stable-menu16
python -m srpg95tool patch-runtime <game_dir> <out_dir> --profile strong-dialogue
python -m srpg95tool project init <game_dir> <workspace_dir>
python -m srpg95tool project import-txt <workspace_dir>
python -m srpg95tool project doctor <workspace_dir>
python -m srpg95tool project build <game_dir> <workspace_dir> <out_dir>
```

## 正式主线与底层命令的关系

正式主线：

- `project init`
- `project doctor`
- `project build`

底层命令保留用途：

- 单独检查解包结果
- 单独验证 `simulate-pack`
- 单独研究 runtime patch
- 调试某个阶段的中间产物

## 输出结构

低层解包目录仍然是：

```text
<out_dir>/
  manifest.json
  databases/
  maps/
  texts/
  raw/
  reports/
```

项目工作区则是：

```text
<workspace>/
  machine/
  txt_src/
  txt_zh/
  txt_map/
  reports/
```

## 翻译输入层

当前正式人工输入是：

- `txt_zh/`

兼容存在但不再是正式人工入口的层：

- `machine/texts/text_index.jsonl`
- `machine/texts/catalog/*.jsonl`
- `machine/texts/dialogue_catalog/*.jsonl`

## 长对白策略

长对白正式不依赖 `strong-dialogue`，而是依赖 pack 层：

- 自动识别 `opcode 1 + 连续 opcode 201`
- 自动拆行
- 自动分页
- 必要时新增新的 `opcode 1 + 201...` 页面
- 自动重算事件长度、声明长度与 chunk chain

## DAT 边界

固定 DAT 当前仍是硬限制模型：

- 超长直接报错
- 不静默截断
- 不自动扩容

DAT 扩容准备阶段的正式产物：

- `reports/ui_dat_crosswalk.json`
- `reports/dat_ui_priority.json`
- `docs/zh-CN/dat-growth-plan.md`

## runtime profile

### stable-menu16

当前唯一正式推荐 profile，包含：

- `opcode 45` 菜单 GDI bridge
- hover highlight 修复
- 12px 菜单 UI 字号
- `BGM / EFS / BMP` 资源别名兼容
- 当前正式覆盖的 DAT-backed UI 中文显示链

### strong-dialogue

仍然保留，但只作为实验性对白 profile，不是正式主线。

当前它仍然是 `opcode 1 / 201` 对白安全链的实验性 profile；
正式文档与日常构建仍统一推荐 `stable-menu16`。
