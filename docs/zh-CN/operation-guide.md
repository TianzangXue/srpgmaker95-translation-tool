# SRPG95 日常操作指南

## 推荐入口

日常生产流程只推荐使用：

1. `project init`
2. 编辑 `txt_zh/`
3. `project doctor`
4. `project build`

底层命令 `unpack / simulate-pack / pack / patch-runtime` 仍然保留，但它们更适合调试、研究和专项验证。

## 初始化

```powershell
python -m srpg95tool project init <game_dir> <workspace_dir>
```

默认行为：

- `txt_src/` 导出完整原文
- `txt_zh/` 使用空白种子
- `txt_map/` 生成完整映射
- `machine/` 生成内部结构化层

## 翻译

日常只编辑：

- `txt_zh/dat/*.txt`
- `txt_zh/smap/*.txt`

注意事项：

- 不要手改 `txt_src/`
- 不要手改 `txt_map/`
- 不要更改块顺序或块数量
- 同文也可以写回，只要 `txt_zh` 块非空即可

## 检查

```powershell
python -m srpg95tool project doctor <workspace_dir>
```

这一步会检查：

- TXT 与 sidecar 是否一致
- 导入统计是否合理
- 固定槽位是否超长
- 对白布局是否会溢出
- 已迁移 UI 面中是否还有未翻译块

## 构建

```powershell
python -m srpg95tool project build <game_dir> <workspace_dir> <out_dir>
```

正式构建链会自动执行：

1. TXT 导入
2. `simulate-pack`
3. `pack`
4. `patch-runtime --profile stable-menu16`

## 默认规则

- 默认 `--zh-seed empty`
- 默认 `--import-mode always`
- 默认 runtime profile 是 `stable-menu16`

只有在明确需要兼容旧流程时，才建议使用：

```powershell
--zh-seed copy-source
--import-mode diff-only
```

## 运行时策略

`stable-menu16` 当前是唯一正式推荐 profile，包含：

- `opcode 45` 菜单 GDI bridge
- hover highlight 修复
- 12px 菜单 UI 字号
- `BGM / EFS / BMP` 资源别名兼容
- 当前已确认的 DAT-backed UI 中文显示链

`strong-dialogue` 仍保留，但只作为实验性对白 profile，不属于正式主线。

## 长对白

长对白正式依赖 pack 层原生拆行 / 分页：

- `speaker` 单行
- `body` 自动按 `cp936` 字节数换行
- 每页最多 4 行正文
- 超过时自动分页
- 新页自动重复说话人

## DAT 限制

固定槽位 DAT 仍然使用硬限制：

- 超长直接报错
- 不静默截断
- 不自动扩容

如果构建失败，请先检查：

- `reports/pack_risks.json`
- `reports/project_build_result.json`
- `reports/dat_ui_priority.json`

## 日常最小流程

```powershell
python -m srpg95tool project init "01闇鍋企画前編" "project_01"
python -m srpg95tool project doctor "project_01"
python -m srpg95tool project build "01闇鍋企画前編" "project_01" "build_01"
```
