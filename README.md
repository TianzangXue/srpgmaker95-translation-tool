# SRPG Maker 95 Translation Tool

English | [中文](#srpg-maker-95汉化工具)

## Overview

SRPG Maker 95 Translation Tool is a Python-based workflow for unpacking SRPG Maker 95 games, exporting editable TXT files, importing translated text, repacking game data, and applying the stable runtime patch used by the Chinese localization workflow.

This repository intentionally contains only the tool source code and documentation. It does not include game data, translation workspaces, build outputs, or sample projects.

## Core Capabilities

- Unpack SRPG Maker 95 game directories into structured machine-readable exports
- Export editable `txt_src/` and `txt_zh/` translation files
- Import translated TXT back into machine catalogs
- Validate TXT consistency, encoding, slot limits, and pack risks
- Repack translated game data and apply the formal `stable-menu16` runtime patch
- Generate reports for UI coverage, pack risks, runtime patching, and project builds

## Repository Layout

```text
.
├─ srpg95tool/   # Python tool source
├─ docs/         # Chinese and English documentation
├─ README.md
├─ LICENSE
├─ requirements.txt
├─ .gitignore
└─ .gitattributes
```

## Requirements

- Windows is the primary supported environment
- Python 3.10+
- `pefile` for PE inspection and runtime patching

## Installation

```powershell
pip install -r requirements.txt
```

## Quick Start

Show command help:

```powershell
python -m srpg95tool --help
```

Initialize a translation workspace:

```powershell
python -m srpg95tool project init <game_dir> <workspace_dir> --zh-seed empty
```

Validate a workspace:

```powershell
python -m srpg95tool project doctor <workspace_dir> --import-mode always
```

Build a playable output:

```powershell
python -m srpg95tool project build <game_dir> <workspace_dir> <out_dir> --import-mode always
```

## Formal Workflow

The formal production workflow is:

1. `project init`
2. Edit `txt_zh/`
3. `project doctor`
4. `project build`

Formal defaults:

- `--zh-seed empty`
- `--import-mode always`
- `stable-menu16` is the formal runtime profile
- `strong-dialogue` remains experimental / compatibility-only

## Important Limits

- `txt_src/` is the read-only source baseline
- `txt_zh/` is the formal human input surface
- Non-empty `txt_zh` blocks are authoritative and are written back even when identical to the source
- Fixed-slot DAT fields still use hard length limits
- Long dialogue is handled by pack-layer wrapping and pagination

## Documentation

- Chinese workflow: [docs/zh-CN/project-workflow.md](docs/zh-CN/project-workflow.md)
- Chinese operation guide: [docs/zh-CN/operation-guide.md](docs/zh-CN/operation-guide.md)
- Chinese project status: [docs/zh-CN/project-status.md](docs/zh-CN/project-status.md)
- Chinese data schema: [docs/zh-CN/data-schema.md](docs/zh-CN/data-schema.md)
- English workflow: [docs/en/project-workflow.md](docs/en/project-workflow.md)
- English operation guide: [docs/en/operation-guide.md](docs/en/operation-guide.md)
- English project status: [docs/en/project-status.md](docs/en/project-status.md)
- English data schema: [docs/en/data-schema.md](docs/en/data-schema.md)

## License

This repository is released under the MIT License. See [LICENSE](LICENSE).

---

## SRPG Maker 95汉化工具

## 概览

SRPG Maker 95 汉化工具是一套基于 Python 的工作流，用于解包 SRPG Maker 95 游戏、导出可编辑 TXT、导入译文、重新封包游戏数据，并应用当前正式使用的 `stable-menu16` 运行时补丁。

这个仓库只保留工具源码和文档，不包含游戏本体、翻译工作区、构建产物或示例项目。

## 核心能力

- 将 SRPG Maker 95 游戏目录解包为结构化 machine 导出
- 导出可编辑的 `txt_src/` 与 `txt_zh/`
- 将翻译后的 TXT 回写到 machine catalog
- 检查 TXT 一致性、编码、固定槽位长度和封包风险
- 重新封包译文游戏，并应用正式的 `stable-menu16` 运行时补丁
- 生成 UI 覆盖、封包风险、运行时补丁和项目构建报告

## 仓库结构

```text
.
├─ srpg95tool/   # Python 工具源码
├─ docs/         # 中英文文档
├─ README.md
├─ LICENSE
├─ requirements.txt
├─ .gitignore
└─ .gitattributes
```

## 环境要求

- 主要支持 Windows 环境
- Python 3.10+
- 需要 `pefile` 进行 PE 分析和运行时补丁处理

## 安装依赖

```powershell
pip install -r requirements.txt
```

## 快速开始

查看命令帮助：

```powershell
python -m srpg95tool --help
```

初始化翻译项目：

```powershell
python -m srpg95tool project init <game_dir> <workspace_dir> --zh-seed empty
```

检查工作区：

```powershell
python -m srpg95tool project doctor <workspace_dir> --import-mode always
```

构建可运行游戏：

```powershell
python -m srpg95tool project build <game_dir> <workspace_dir> <out_dir> --import-mode always
```

## 正式工作流

当前正式生产流程是：

1. `project init`
2. 编辑 `txt_zh/`
3. `project doctor`
4. `project build`

正式默认值：

- `--zh-seed empty`
- `--import-mode always`
- `stable-menu16` 是正式 runtime profile
- `strong-dialogue` 只保留为实验/兼容能力

## 重要限制

- `txt_src/` 是原文只读基线
- `txt_zh/` 是正式人工输入面
- 非空 `txt_zh` 块都是权威块，即使与原文相同也会写回
- 固定槽位 DAT 字段仍然有硬长度限制
- 长对白依赖 pack 层的自动拆行与分页

## 文档入口

- 中文工作流：[docs/zh-CN/project-workflow.md](docs/zh-CN/project-workflow.md)
- 中文日常指南：[docs/zh-CN/operation-guide.md](docs/zh-CN/operation-guide.md)
- 中文项目状态：[docs/zh-CN/project-status.md](docs/zh-CN/project-status.md)
- 中文数据结构：[docs/zh-CN/data-schema.md](docs/zh-CN/data-schema.md)
- English workflow: [docs/en/project-workflow.md](docs/en/project-workflow.md)
- English operation guide: [docs/en/operation-guide.md](docs/en/operation-guide.md)
- English project status: [docs/en/project-status.md](docs/en/project-status.md)
- English data schema: [docs/en/data-schema.md](docs/en/data-schema.md)

## License

仓库使用 MIT License，详见 [LICENSE](LICENSE)。
