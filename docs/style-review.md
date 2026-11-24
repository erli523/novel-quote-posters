# 六风格补充审阅

此流程检查同一句、同人物的六套实际样张及15对差异。它与主卡、原句归属、整组审核独立，不能替代这些门禁或自动发布。默认未检查、未签名；AI不能替用户填写本人确认。

应用端先准备任务内的样张目录和schema 1 manifest.json，包含六种固定style_id、相同quote_id/character_id、源哈希、基准哈希、每套asset_id、PNG/背景/字体哈希、相对path及layout_overrides。每张须有同名.layout.json，1080×1440 sRGB PNG，引用已登记的真实底图和可核对布局；人物基准也须登记。程序核对实际像素/布局/原句和来源链，不把manifest的声明本身当成真实调用证据。样张生成仍由应用端协调，不由审阅命令发起生图。

```powershell
.venv\Scripts\python -m novel_quote_posters.cli style-review --run runs/my-demo --samples six-styles-v1 --output runs/my-demo/six-style-review-v1.html
```

samples相对于run，不能越界。已有输出拒绝覆盖，请用新版本名。页面包含完整图、360×480、方形裁切模拟、人物基准和原句/归属证据的完整相邻段落。上下文有间隔会说明，不能当作整本书已经读完。所有小说文本仅作数据，不生成页面脚本。当前不支持自动判断同脸、精彩程度、画面语义或风格差异。

每套逐项检查文字、人物、手机阅读、裁切和风格情境。请区分场景还原和主题化延伸：例如山路对白配旧站画面可以表达继续前行，但不能被说成原场景复刻。风格样张可以含待审标签；认可其效果仍不批准主卡或正式图集。方形图只是裁切模拟，本次只交付完整3:4。

每对风格须由本人勾选构图、排字、留白、背景、配色中至少三维实质差异才能选通过。未检查和否决可保存，未完成不会被自动通过。填写审阅人并确认本人检查后下载JSON；该文件含私有引文、上下文及来源信息，请留本机。

```powershell
.venv\Scripts\python -m novel_quote_posters.cli apply-style-review runs/my-demo/six-style-human-review.json --run runs/my-demo
.venv\Scripts\python -m novel_quote_posters.cli status --run runs/my-demo
```

导入要求schema 2、具名本人声明、带时区时间、完整唯一六套/15对集合。旧一次性表单schema 1不能自动迁移为批准，应生成新页面重新判断。只有真实用户记录可用于实际任务，测试中的模拟签名不算人工证据；声明本身不提供身份认证。

记录绑定源/原句上下文、人物/基准/底图、实际PNG和视图、布局报告/代码、任务与检查政策。内容变化时状态显示invalidated；生成新页会登记新目标版本，恢复旧内容不自动接受已被新目标取代的表单。后续人工判断也有快照，旧批准不能覆盖后来的否决；继续修改请生成新页。

整批验证后使用文档事务提交，并在提交锁内复查文件和上一个审核版本。相同记录仍是当前结果时重放复用；提交后返回丢失可恢复，不重复记录。私有.state/style-review-records保留接收的尝试，是否实际生效以文档事务及six-style-review.json为准，不能把单独历史文件当作已提交。status的six_style_review显示not_started/pending/rejected/approved/invalidated；approved仅指这份补充样张记录，不表示完整V1验收。

本轮浏览器file协议仍受安全策略拒绝，未绕过。独立JS语法、内嵌图像哈希/解码和本地ZIP复制解压检查不等于浏览器交互或实际客户端下载通过。

同句六套可先通过[可恢复样张命令](style-samples.md)生成，替代一次性脚本。带managed声明的目录进入审阅时，额外核对build-seal的20项载荷、原运行私有快照和当前字体/来源/代码；文件或依赖变化后不能继续批准。原一次性目录不自动升级为托管成果。
