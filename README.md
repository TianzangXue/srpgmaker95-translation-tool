# SRPG Maker 95 汉化工具

English version is available below.

## 项目简介

SRPG Maker 95 汉化工具是一套基于 Python 的工作流，用于：

- 解包 SRPG Maker 95 游戏目录
- 导出可编辑的翻译 TXT
- 将译文导回 machine 层
- 检查编码、长度和封包风险
- 重新封包游戏并应用正式运行时补丁

这个仓库只保留工具源码和发布所需说明，不包含游戏本体、翻译工作区、构建产物或示例游戏资源。

## 环境要求

- Windows
- Python 3.10+
- `pefile`

## 安装

```powershell
pip install -r requirements.txt
```

## 命令入口

查看总帮助：

```powershell
python -m srpg95tool --help
```

查看 `project` 工作流帮助：

```powershell
python -m srpg95tool project --help
```

## 标准汉化流程

### 1. 初始化项目

```powershell
python -m srpg95tool project init <game_dir> <workspace_dir>
```

初始化后会生成这些目录：

- `machine/`
- `txt_src/`
- `txt_zh/`
- `txt_map/`
- `reports/`

其中：

- `txt_src/` 是原文只读基线
- `txt_zh/` 是正式人工输入

### 2. 编辑译文

只编辑：

- `txt_zh/dat/*.txt`
- `txt_zh/smap/*.txt`

不要手改：

- `txt_src/`
- `txt_map/`
- `machine/`

## TXT 规则

- 块分隔符固定为单独一行 `====`
- 块顺序不能改
- 块数量不能改
- 可以直接写多行文本
- 空白 `txt_zh` 块表示未翻译，不写回
- `\0` 表示显式空字符串，会写回
- 其它所有非空块都会被当作权威块写回
- 即使和原文完全相同，非空块也会写回

对白规则：

- 第一行是说话人
- 第二行起是正文
- 单独一行 `\f` 表示强制分页

### 3. 检查项目

```powershell
python -m srpg95tool project doctor <workspace_dir>
```

这一步会检查：

- TXT 和 sidecar 是否一致
- 编码是否可写回 `cp936`
- 固定槽位是否超长
- 对白是否会在封包时溢出

### 4. 正式封包

```powershell
python -m srpg95tool project build <game_dir> <workspace_dir> <out_dir>
```

构建会自动执行：

1. TXT 导入
2. `simulate-pack`
3. `pack`
4. `patch-runtime --profile stable-menu16`

成品游戏目录位于：

```text
<out_dir>\playable_game
```

## 默认策略

- 初始化默认使用空白 zh seed
- 导入默认使用 always 模式
- `stable-menu16` 是正式推荐的运行时 profile
- `strong-dialogue` 仅保留为实验 / 兼容能力

## 重要限制

- 固定槽位 DAT 字段仍有硬长度限制
- 超长文本会直接报错，不会静默截断
- 长对白依赖 pack 层自动拆行 / 分页
- 运行时正式主线以稳定性优先，不建议随意切换实验 profile

## 最小可复制流程

```powershell
python -m srpg95tool project init <game_dir> <workspace_dir>
python -m srpg95tool project doctor <workspace_dir>
python -m srpg95tool project build <game_dir> <workspace_dir> <out_dir>
```

## License

MIT License. See [LICENSE](LICENSE).

---

# SRPG Maker 95 Translation Tool

## Overview

SRPG Maker 95 Translation Tool is a Python-based workflow for:

- unpacking SRPG Maker 95 game directories
- exporting editable translation TXT files
- importing translated TXT back into the machine layer
- validating encoding, slot limits, and pack risks
- rebuilding a playable game directory and applying the formal runtime patch

This repository contains the tool source code and release-facing usage instructions only. It does not include game data, translation workspaces, build outputs, or sample game assets.

## Requirements

- Windows
- Python 3.10+
- `pefile`

## Installation

```powershell
pip install -r requirements.txt
```

## Entry Commands

Show top-level help:

```powershell
python -m srpg95tool --help
```

Show `project` workflow help:

```powershell
python -m srpg95tool project --help
```

## Standard Workflow

### 1. Initialize a workspace

```powershell
python -m srpg95tool project init <game_dir> <workspace_dir>
```

The workspace contains:

- `machine/`
- `txt_src/`
- `txt_zh/`
- `txt_map/`
- `reports/`

Meaning:

- `txt_src/` is the read-only source baseline
- `txt_zh/` is the formal human translation input

### 2. Edit translations

Only edit:

- `txt_zh/dat/*.txt`
- `txt_zh/smap/*.txt`

Do not manually edit:

- `txt_src/`
- `txt_map/`
- `machine/`

## TXT Rules

- Blocks are separated by a standalone `====` line
- Do not change block order
- Do not change block count
- Multi-line text is allowed
- Empty `txt_zh` blocks are treated as untranslated and are not written back
- `\0` means explicit empty string and is written back
- Any other non-empty block is treated as authoritative and written back
- A non-empty block is still written back even if it is identical to the source

Dialogue rules:

- first line = speaker
- remaining lines = body
- a standalone `\f` line forces a page break

### 3. Validate the workspace

```powershell
python -m srpg95tool project doctor <workspace_dir>
```

This checks:

- TXT / sidecar consistency
- `cp936` write-back safety
- fixed-slot overflows
- dialogue layout and pack-time risks

### 4. Build the game

```powershell
python -m srpg95tool project build <game_dir> <workspace_dir> <out_dir>
```

The build flow automatically runs:

1. TXT import
2. `simulate-pack`
3. `pack`
4. `patch-runtime --profile stable-menu16`

The playable output is generated at:

```text
<out_dir>\playable_game
```

## Defaults

- initialization uses the blank zh seed by default
- import uses the `always` mode by default
- `stable-menu16` is the formal runtime profile
- `strong-dialogue` is experimental / compatibility-only

## Important Limits

- Fixed-slot DAT fields still use hard length limits
- Overflow is treated as an error; nothing is silently truncated
- Long dialogue relies on pack-layer wrapping and pagination
- The formal runtime path prioritizes stability over experimental dialogue behavior

## Minimal Copyable Flow

```powershell
python -m srpg95tool project init <game_dir> <workspace_dir>
python -m srpg95tool project doctor <workspace_dir>
python -m srpg95tool project build <game_dir> <workspace_dir> <out_dir>
```

## License

MIT License. See [LICENSE](LICENSE).
