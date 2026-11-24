# 生成同句六风格样张

应用端先完成原句定位和具名人物提案，登记实际底图及人物基准。本地命令只做确定性排字，不判断精彩程度/同脸、不调用生图；生成出来仍是待审候选，不能替代真实工具记录或人工批准。

```powershell
.venv\Scripts\python -m novel_quote_posters.cli sample-template --run runs/my-demo --quote 实际quote_id --font C:/Windows/Fonts/msyh.ttc --output runs/my-demo/samples-plan-v1.json
```

模板绑定源/原句/人物/任务内容版本，保留source_hash和expected_content_fingerprint。填写实际editor、reason及六套各自asset_id、font、font_license_note、reuse_reason和layout_overrides。available只列可选登记资产，不替用户选取。默认draft=true；去除待审标签也不授予批准。

六种style_id必须完整、各一次。font可逐套选择本机实际文件，记录哈希和本地使用说明，字体文件不随样张分发；不能把字体存在当作已经取得任何用途许可。reuse_reason记录构图/时代/语气兼容性的应用端判断，不是程序证明。山路对白配旧站时，应说明属于主题化表达，不能宣称逐细节场景还原。

layout_overrides只接受已有九项色板/画框/文字面板/取景参数，空对象沿用模板。先长句适配，再覆盖明确字段；非法几何、非有限值、低实色对比或缺字明确拒绝。容量仍由排字阶段检查，不无限缩字；没有人脸检测，必须看实际结果。

```powershell
.venv\Scripts\python -m novel_quote_posters.cli render-samples runs/my-demo/samples-plan-v1.json --run runs/my-demo --output runs/my-demo/six-styles-v1
.venv\Scripts\python -m novel_quote_posters.cli render-samples runs/my-demo/samples-plan-v1.json --run runs/my-demo --output runs/my-demo/six-styles-v1 --resume
```

output必须是run内新的单层命名目录。先核对六套输入，再按每套内容签名缓存PNG、手机图和布局报告；修改一套明确参数通常只重排那套，其余复用。源/人物/任务/底图/字体/代码变化重新计算依赖，不强行沿用旧样张。默认1080×1440 sRGB，手机图360×480。

完整目录有21文件：六套PNG/手机图/布局报告共18项，加中文标签对照板、manifest.json和build-seal.json。manifest兼容[六风格审阅](style-review.md)，记录实际资产/字体/图像哈希、取景、复用理由和待审状态；不包含字体物理路径。build-seal列出另外20项的确切哈希，原运行的私有.state/sample-inputs另存完整依赖快照。

未完成状态留在run/.sample-staging，损坏缓存保留原文件并用新尝试编号。中断后--resume可复用完成且核对有效的内容；发布前再次检查输入，最后原子移动完整目录。已发布版本默认拒绝覆盖，即使损坏或计划变化也保留旧目录，改用新版本名；同内容--resume只读核对复用。程序不保证非协作外部写入的绝对隔离。

```powershell
.venv\Scripts\python -m novel_quote_posters.cli style-review --run runs/my-demo --samples six-styles-v1 --output runs/my-demo/six-style-review-v1.html
```

托管样张进入审阅时还会复核完整目录封条、原运行快照和当前字体/来源/代码；过期或损坏不能接着批准。此前一次性schema 1目录仍按已有像素/布局/来源规则检查，不自动升级为托管成果或批准。补充记录只针对样张，不修改主卡、原句归属或整组审核。便携待审包不含原始底图、字体与私有快照，不是完整重建环境；实际工具真实性仍须回到原任务对照。

本轮实际六套重新排字，沿用现有真实图；五套解码像素与v6相同，水墨扩大画框并保留完整头部，均待人审。重复运行六套全部复用，新增图像调用0、总台账10/10。浏览器file限制未绕过，独立语法/图像/ZIP检查不等于真实页面交互或客户端下载通过。
