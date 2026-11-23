# 每次运行的任务契约

导入保存解析契约；应用端分析取得稳定人物 ID 后，在选卡、生图预留和正式审核前明确本次任务要求。旧运行不会因为已有三张卡片就被推断为“用户只要三张”。任务配置是操作者对意图的记录，不是人工内容批准或权利认证。

```powershell
.venv\Scripts\python -m novel_quote_posters.cli contract-template --run runs/my-demo --output runs/my-demo/task-request-v1.json
```

填写 editor、reason、definition.character_ids 和 input_rights_note，核对其余值，再提交：

```powershell
.venv\Scripts\python -m novel_quote_posters.cli apply-contract runs/my-demo/task-request-v1.json --run runs/my-demo
.venv\Scripts\python -m novel_quote_posters.cli prepare-cards --run runs/my-demo
.venv\Scripts\python -m novel_quote_posters.cli status --run runs/my-demo
```

模板默认目标 10 张、雾感电影主风格、至少 1 套风格、原文、不自动改写、12～100 字、1080×1440 PNG sRGB、首张兼首图，无额外封面。默认人物列表留空，须按任务明确选择；章节空列表表示全部章节。常规精彩短句与是否接受长句由应用端/用户判断，不保证模型召回全部好句。

| 字段 | 作用 |
| --- | --- |
| target_count | 明确成品张数，不能用当前卡片数替代 |
| character_ids、chapter_ids、quote_types | 限定人物、章节和对白/独白范围 |
| style_ids、min_distinct_styles | 允许的风格与至少几套不同风格；不自动判断视觉差异 |
| allow_rewrite、min_quote_chars、max_quote_chars | 约束显示文字；--allow-rewrite 不能越过任务的禁止政策 |
| candidate_buffer_ratio | 默认 0.3，报告额外候选缺口，不为凑数补句，不作为硬性出图张数 |
| output | 当前正式交付固定 1080×1440、PNG、sRGB、首张兼首图；其他画幅明确拒绝此契约 |
| theme、forbidden_elements | 展示给实际审核人，由人判断情境与禁用元素；脚本不宣称理解画面含义 |
| image_call_limit、retry_limit_per_asset | 本次总调用上限及固定单资产最多 1 次重试；总额度不得超过数据库既有上限或低于已保守占用 |
| input_rights_note、allowed_destinations、allow_source_upload | 明确来源权限声明；V1 只允许本地及可选内置生图，不允许将正文外传或切到独立 API |
| execution_notes | 应用端执行约定与停止/对账说明；不提供无人值守执行器 |

任务文件、模板及其中的角色/章节名称属于私有运行资料，不能直接推送公共仓库。输入的权限声明不能替代实际权利；当前开发演示仅用项目自写虚构样本。预算分配与验收范围同时参考本次开发契约；本模块强制总上限和已有单资产重试规则，不将自由文本备注冒充自动化费用管理。

apply-contract 保存 task-contract.json，并在不可覆盖的事务中保留旧版本及原始请求；内容相同可幂等重放。输入快照过期或并发修改拒绝覆盖。缩小额度与新预留使用同一个 SQLite 协作写锁，调用失败/未知结果仍计保守占用。可设置 0 次，或只允许 local；此时不会批准新内置生图预留。设置契约不改变旧调用身份、次数和数据库上限。

prepare-cards 默认采用契约中的张数和首选风格；显式 --count/--style 不符则要求先明确修订契约。只挑范围内、来源可核对的应用端选中候选，preparation-report.json 保存成品缺口与 30% 候选缓冲缺口。没有足够候选就不足，不通过降低目标或编句伪造完成。已有卡片仍使用稳定 ID 编辑，不覆盖原图集。

正式单卡/整组批准绑定契约版本。任务变化使旧视觉与整组批准失效，实际像素未变时可以复用渲染，仍须重审新要求。当前来源和归属未变的原句审核可独立保留。数量、人物/章节/类型、风格种类、显示长度及改写政策不符合时拒绝正式批准/导出；草稿可用于诊断并保留问题说明。没有契约的旧运行仍可做本地待审检查、接收旧结果对账；新生图预留及正式批准需要先补任务配置，不伪造旧 ingest seal。

交付包 schema 4 加入按字段白名单输出的任务定义及版本、约束问题列表，整组目标也绑定其指纹。verify-export 的 task_conformance 仅检查包内声明的选择/显示约束，不能证明真实授权、原书准确、调用费用/预算实耗、人物一致性或人工身份。旧 schema 2/3 包保留原有完整性检查，返回 not_checked_legacy，不给旧包补出任务符合性结论。
