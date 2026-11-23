# 本地运行与应用端交接

## 安装

Python 3.11+；建议独立环境，避免全局插件与 Pillow 原生组件冲突。Windows：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e '.[dev]'
.venv\Scripts\python scripts/check.py
```

Linux/macOS 使用 `.venv/bin/python`。测试需要中文字体：Windows 使用本机微软雅黑；Ubuntu 安装 fonts-noto-cjk；其他系统设置 POSTER_TEST_FONT。字体不随仓库分发。当前锁定版本见 requirements-dev.lock，跨平台安装以支持该平台的官方 wheel 为准。

## 1. 导入

```powershell
.venv\Scripts\python -m novel_quote_posters.cli ingest fixtures/mist_letters.txt --run runs/my-demo --title 雾山来信 --chunk-size 300 --overlap 80
```

TXT 支持严格解码 UTF-8/BOM、UTF-16/BOM、GB18030，可用 --encoding 指定。EPUB 按 spine 顺序读取 XHTML，不执行其中脚本。DOCX/PDF/OCR 当前明确不支持。book.json 含私有全文；不要推送 runs/。重复 run 路径默认拒绝。

导入先在同级私有 .ingest-staging/ 保存原文件及解析/窗口/候选检查点，完成后才原子发布任务目录。中断后用相同参数增加 --resume，复用已完成阶段；损坏检查点会隔离保存并重做。完整任务的 --resume 校验固定输入及配置，保留后续审核和分析，不重置预算。旧任务若没有 ingest-contract/seal，不伪装成新格式的可恢复导入，应保留旧任务并另建新 run。完整任务的不可变导入文件损坏会明确拒绝，不能悄悄覆盖已有人工数据。

## 2. 应用端语义分析

把 book.json 和 chunks.json 的必要窗口交给当前应用端处理，每块回填 hash 与摘要。按 fixtures/mist_analysis.json 的结构提交人物、证据、说话人及候选理由：

```powershell
.venv\Scripts\python -m novel_quote_posters.cli apply-analysis fixtures/mist_analysis.json --run runs/my-demo
```

该样本分析只适配原样的自写样本。程序检查源哈希、窗口哈希、原句与证据位置，去重并保留原始提案字节快照；不会把 AI 归属标为人工批准。可分批提交：保留其他窗口的已处理状态、其他候选和人物；同 ID 人物在新提案中应提供更新后的完整档案。相同原句的实质分析未变时保留既有审核。加工覆盖与精彩句召回是不同指标。数量不足要报告原因，不编句补足。

## 3. 真实生图与卡片

先按 [每次运行的任务契约](task-contract.md) 生成并填写 contract-template，再 apply-contract；明确张数、人物/风格、原文政策、来源权利和预算。prepare-cards 不传数量/风格时采用该契约，默认不扩大权限。没有明确契约不能预留新生图或批准正式成品，旧运行可先保留待审资料。

由应用端实际工具生成基准肖像及无字底图，保存到 run 内，保留提示词、实际参考图和调用记录。[图像交接与预算](image-workflow.md) 给出 image-plan/image-import/budget/set-reference/prepare-cards/attach-image 的完整步骤。V1 没有从 Python 请求应用内置生图的隐藏接口。若工具不可用，可继续验证程序，不能把测试底图记为真实生图。

cards.json 是数组，每项示例：

```json
{
  "card_id": "card001",
  "quote_id": "使用 quotes.json 的实际 ID",
  "character_id": "lin_che",
  "style_id": "mist_cinema",
  "background": "images/card001-v1.png",
  "poster": "posters/card001-v1.png",
  "image_kind": "generated"
}
```

上例用于说明旧草稿数据格式。正式流程用 image-import/attach-image 自动填入 image_asset_id、来源种类及路径，不能只手填 image_kind=generated。路径须在 run 内。poster 可省略；render 会为 card_id 与输入内容生成稳定版本文件名，并回填 cards.json，不覆盖旧图。来源登记仍需对照真实工具轨迹，不能独立证明图像内容真实。`styles` 命令打印当前布局参数；单卡可提供 layout_overrides 覆盖色板和归一化坐标，须重新排版审阅。

## 4. 排版与导出

```powershell
.venv\Scripts\python -m novel_quote_posters.cli render --run runs/my-demo --font C:/Windows/Fonts/msyh.ttc
.venv\Scripts\python -m novel_quote_posters.cli export --run runs/my-demo --output dist/my-demo-v1 --draft
```

默认绘制“待审核”，导出 draft-review.zip。再次 render 时，输入和产物均未变的卡片直接复用；修改文字、角色、模板、画幅或字体后重新排版，旧版本仍保留。来源文件、人物参考图、字体、底图、软件版本和渲染代码均进入输入签名；正式导出重新验证签名。字体不再依赖用户手动删除旧图才能更新。超容量/缺字明确报错。--card 单张制作；--width/--height 独立布局，正式图集导出当前只支持契约默认 1080×1440。

待检查没有水印的成品候选时使用 render --clean-candidate；会产生独立版本，只去掉草稿标签，不授予批准。最终 export 不加 --draft，要求 quote.review 与 card.review 真实人工批准并分别绑定内容 fingerprint。软件中的人工字段是操作记录，不是身份认证系统；不得由 AI 手填 human 欺骗门禁。

## 5. 离线审核页

```powershell
.venv\Scripts\python -m novel_quote_posters.cli review --run runs/my-demo --output runs/my-demo/review-v1.html --quotes-only
```

在本地浏览器打开 HTML，查看原句、上下文和归属依据。人物图就绪后去掉 --quotes-only 生成图文审核页；图片嵌入 HTML，无需启动服务，不向网络传输。页面含私有原文/图片，不要发布到公开仓库。所有决定默认待审核，填写实际审核人和本人核对后下载 review-record.json；部分审核也可保存。带待审核标签的图片可退回，正式批准须核对无标记候选图。

每张卡片显示人物档案分类、基准图（若有）、完整海报、360×480 手机图，以及居中正方形裁切模拟；裁切只是检查工具，不代表抖音实际规则。点击图像打开可滚动原尺寸弹窗，关闭按钮或 Esc 返回。人物、逐字文字、手机阅读、裁切及画面情境五项默认未检查，真实审核人逐项选择；只有全部通过才可批准卡片。工具生成的卡片还必须有已登记来源的非测试基准图，仅填写参考图路径或使用测试基准不能批准；用户提供的替换图来源单列，不据此满足真实生图验收。

```powershell
.venv\Scripts\python -m novel_quote_posters.cli apply-review 下载目录/review-record.json --run runs/my-demo
```

程序先验证整个记录、来源、时间格式和全部内容版本，再原子更新决定。过期记录、AI 署名、缺少本人声明或试图批准带草稿标签的图片均拒绝；重复导入相同记录不会重复修改。未实际审核的项保持原状态。

当前审核记录 schema 3，保存逐项视觉判断、视图哈希、裁切范围和整组决定。schema 1/2 旧表单不能自动升级成当前批准，请保留原记录并重新生成审核页。既有内容仍匹配的单卡批准可保留，整组批准须新作真实判断。手机图损坏或参考图/人物/文字更新时，需重新排版或生成审核材料；不要修改旧记录的哈希来绕过检查。修正说话人后，原卡片人物不匹配会阻止重排，须先在应用端核对并更新卡片人物及兼容配图。

“图集顺序与节奏”按实际 cards 顺序显示缩略图，点击可看原尺寸。当前首张卡片兼首图，不额外增加封面；人工检查数量/顺序、首图、首中末节奏和整组一致性。所有整组项默认未检查，逐项通过后才可批准无草稿标签的整组。可以只提交单卡决定或只提交整组决定，pending 不撤回已有决定；需要撤回时选择退回。整组记录写入 album-review.json，排序、书名、卡片或图片版本变化后失效，正式 export 同时要求有效单卡与整组批准。

同名人物会显示不同的别名，例如“林澈（阿澈）”和“林澈（林先生）”；没有可区分别名时显示人物 ID。名字或别名更新后，旧说话人审核失效，需要重新查看上下文。

改文字、换已有原句、风格或顺序使用 [受控修改](edit-workflow.md) 的 edit-template/apply-edits；保留原句和旧资产，过期请求拒绝覆盖后来的修改，不调用生图。

## 6. 导出恢复与独立校验

```powershell
.venv\Scripts\python -m novel_quote_posters.cli export --run runs/my-demo --output dist/my-demo-v1 --draft --resume
.venv\Scripts\python -m novel_quote_posters.cli verify-export dist/my-demo-v1/draft-review.zip
.venv\Scripts\python -m novel_quote_posters.cli status --run runs/my-demo
```

导出在同级私有 .export-staging/ 制作，核对完整包及当前输入后原子发布目标目录。失败和中断的尝试保留；相同输入加 --resume 可复用已验证完整包。已发布目录默认拒绝覆盖，输入变化或包损坏时保留旧目录并另选版本。status 显示最近一次导出是 current、invalidated、corrupt 或 missing，不把历史包当当前成品。

ZIP 包含序号 PNG、手机缩略图、CSV/JSON、layouts 布局报告、image-provenance 来源摘要、逐文件 SHA-256/大小清单和顺序说明。只带所选人物，移除运行用物理字体路径与无关运行字段。整本书、字体文件、无字底图、原始参考图和提示词保留原任务，不装进成品包；因此包可独立核对与浏览，但不是完整重建环境。包内引文、证据、姓名和审核人仍可能属于私有信息，不能自动公开。

verify-export 无需原任务，检查清单、路径、哈希、图像尺寸/配置文件和交叉引用；它不认证作者、审核人的真实身份或真实生图。原句是否来自书中、人物归属和原始调用链仍须对照原任务。相同清单的 ZIP 使用固定容器元数据，可确定性复现字节；生成式图片本身不承诺逐像素复现。

包清单从 schema 3 起包含 album-review.json 的整组目标和审核摘要，验证时与序号图片交叉核对。待审包中的整组记录保持 pending，不带旧版本批准。旧 schema 2 包仍可检查原有完整性；没有整组记录不能被理解为已做整组审核。

当前新增包清单 schema 4 同时带 task-contract.json，整组与单卡审核绑定任务版本；任务选择约束与实际图片/引语交叉核对。旧 schema 2/3 仍可检查原完整性；task_conformance_scope 明确只是包内声明的选择约束，不能替代真实图片、权限或人工验收。任务改动后需要新的审核页，像素未变不会自动请求生图。

## 恢复与当前限制

workflow.Ledger 已接入 render 的资产与依赖记录。CLI 的分析、审核、卡片指针更新使用多文件事务：中断后在下一次操作前完成同一事务；也可手动执行 recover --run runs/my-demo。已渲染完但尚未回填 cards.json 的产物能被发现并复用；缺失或损坏的图/报告会生成新的尝试版本，保留损坏证据。排序变化不触发重新生图或重排；正文/档案/模板变化会使受影响结果及审核失效。

当前恢复验证覆盖初次导入的阶段边界、文档更新、确定性排版、图像注册/结果对账，以及导出完整包已落盘而发布未完成的中断；**未覆盖实际成功生图的端到端恢复**。预算预留和未知结果对账已经有命令，但真正调用仍由应用端协调，不能宣传脚本可以自动请求内置生图。本版本不提供无人值守生图，也未通过完整能力验收；参看开发记录。
