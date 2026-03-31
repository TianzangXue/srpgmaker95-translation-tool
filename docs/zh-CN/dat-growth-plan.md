# SRPG95 DAT 扩容准备说明

## 目标

这一阶段的目标不是立即实现完整 DAT 扩容器，而是先完成两件事：

- 证明哪些 UI 文本确实来自 DAT
- 形成一份足够指导后续实现的 DAT 扩容决策规格

当前正式主线仍然是：

- 长 `SMAP opcode 1 / 201` 对白由 pack 层原生拆行 / 分页解决
- `opcode 45` 与相关 UI 继续由 `stable-menu16` 处理

## 当前结论

已经确认，很多高价值 UI 文本并不是 EXE 常量，而是来自固定槽位 DAT。

已确认的 DAT-backed UI 消费点包括：

- `sub_412448`
  - `UNIT.DAT` 名称
  - `CLASS.DAT` 名称
  - `WORD.DAT` 短标签
- `sub_420564 -> sub_414344`
  - `WORD.DAT` 菜单标签
  - `ITEM.DAT / UNIT.DAT` 驱动的命令状态文本
- `sub_414B14`
  - `ITEM.DAT` 名称
  - `WORD.DAT` 奖励 / 提示标签
- `sub_414D40`
  - `UNIT.DAT` 名称
  - `MAGIC.DAT` 名称
  - `WORD.DAT` 总结标签
- `sub_41C960`
  - `UNIT.DAT` 名称
  - `WORD.DAT` 状态卡标签
- `sub_4177E8 / sub_417D9C / sub_40F4C4 / sub_40F650 / sub_40F930`
  - `ITEM.DAT / MAGIC.DAT` 名称与说明
  - `WORD.DAT` 装备 / 帮助标签

## 新报告

这一阶段正式产出：

- `reports/ui_dat_crosswalk.json`
- `reports/dat_ui_priority.json`

### ui_dat_crosswalk

它回答：

- UI 面是什么
- 属于哪个 runtime function
- 走哪条 draw path
- 文本来自 DAT、SMAP、EXE 常量还是未知来源
- 如果来自 DAT，对应哪个文件、哪个字段模式、当前槽位上限是多少

### dat_ui_priority

它回答：

- 下一轮最值得进入实现的 DAT 文件有哪些
- 各字段的 UI 价值
- 各字段的槽位压力
- 预估扩容难度
- 当前建议策略是什么

## 当前优先级

第一梯队：

- `WORD.DAT`
- `MAPNAME.DAT`
- `ITEM.DAT`
- `MAGIC.DAT`
- `UNIT.DAT`

第二梯队：

- `CLASS.DAT`
- `SWNAME.DAT`
- `VARNAME.DAT`
- `GEOLOGY.DAT`
- `ANIME.DAT`

## 当前建议策略

### WORD.DAT

- 角色：高频短标签枢纽
- 当前建议：`compress_translation`
- 原因：覆盖面很大，但多数槽位仍然很短，先压缩翻译比立即扩容更稳

### MAPNAME.DAT

- 角色：地图标题缓存
- 当前建议：`keep_fixed`
- 原因：加载链已确认，但最终 draw-site 仍值得保留一次确认余地

### ITEM.DAT

- 角色：物品 / 装备名称与说明
- 当前建议：
  - `name` -> `compress_translation`
  - `desc` -> `needs_pointer_rework`

### MAGIC.DAT

- 角色：技能 / 魔法名称与说明
- 当前建议：
  - `name` -> `compress_translation`
  - `desc` -> `needs_pointer_rework`

### UNIT.DAT

- 角色：单位名称与战斗 / 死亡文本
- 当前建议：
  - `name` -> `compress_translation`
  - `death_message` -> `needs_runtime_patch`

## 首批扩容候选

当前最适合进入第一批 DAT 扩容原型的两个字段是：

1. `ITEM.DAT.desc`
2. `MAGIC.DAT.desc`

原因：

- 中文价值高
- 固定 70 字节很快变成翻译瓶颈
- 两者结构相似，适合复用同一套扩容方案

## 这一阶段不做什么

这一阶段不实现：

- 完整 DAT 扩容器
- 对当前正式对白安全链之外的更深 runtime 对白研究
- 更深的 `HARMONY.DLL` 改造

## 下一步建议

1. 以 `ITEM.DAT.desc` 为中心做第一版 DAT 扩容原型
2. 将同一方案复用到 `MAGIC.DAT.desc`
3. 补齐 `MAPNAME.DAT` 最后一个 draw-site 的确认
4. 再决定是否有必要对 `WORD.DAT` 的部分短标签做结构性扩容
