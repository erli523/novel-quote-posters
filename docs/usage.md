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

TXT 支持严格解码 UTF-8/BOM、UTF-16/BOM、GB18030，可用 --encoding 指定。EPUB 按 spine 顺序读取 XHTML，不执行其中脚本。DOCX/PDF/OCR 当前明确不支持。book.json 含私有全文；不要推送 runs/。重复 run 路径会拒绝，避免覆盖证据。

## 2. 应用端语义分析

把 book.json 和 chunks.json 的必要窗口交给当前应用端处理，每块回填 hash 与摘要。按 fixtures/mist_analysis.json 的结构提交人物、证据、说话人及候选理由：

```powershell
.venv\Scripts\python -m novel_quote_posters.cli apply-analysis fixtures/mist_analysis.json --run runs/my-demo
```

该样本分析只适配原样的自写样本。程序检查源哈希、窗口哈希、原句与证据位置，去重并保留原始提案字节快照；不会把 AI 归属标为人工批准。可分批提交：保留其他窗口的已处理状态、其他候选和人物；同 ID 人物在新提案中应提供更新后的完整档案。相同原句的实质分析未变时保留既有审核。加工覆盖与精彩句召回是不同指标。数量不足要报告原因，不编句补足。

## 3. 真实生图与卡片

由应用端实际工具生成基准肖像及无字底图，保存到 run 内，保留提示词、实际参考图和调用记录。V1 没有从 Python 请求应用内置生图的隐藏接口。若工具不可用，可继续验证程序，不能把测试底图记为真实生图。

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

路径须在 run 内。poster 可省略；render 会为 card_id 与输入内容生成稳定版本文件名，并回填 cards.json，不覆盖旧图。image_kind 为来源声明，不能单独证明真实工具调用；验收仍须检查调用记录与资产。`styles` 命令打印当前布局参数；单卡可提供 layout_overrides 覆盖色板和归一化坐标，须重新排版审阅。

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

```powershell
.venv\Scripts\python -m novel_quote_posters.cli apply-review 下载目录/review-record.json --run runs/my-demo
```

程序先验证整个记录、来源、时间格式和全部内容版本，再原子更新决定。过期记录、AI 署名、缺少本人声明或试图批准带草稿标签的图片均拒绝；重复导入相同记录不会重复修改。未实际审核的项保持原状态。

ZIP 包含序号 PNG、手机缩略图、CSV/JSON、来源及顺序说明；不包含整本书或原始参考图。包内角色和卡片数据仍可能包含私有信息，只用于当前任务，不能自动作为公开产物。

## 恢复与当前限制

workflow.Ledger 已接入 render 的资产与依赖记录。CLI 的分析、审核、卡片指针更新使用多文件事务：中断后在下一次操作前完成同一事务；也可手动执行 recover --run runs/my-demo。已渲染完但尚未回填 cards.json 的产物能被发现并复用；缺失或损坏的图/报告会生成新的尝试版本，保留损坏证据。排序变化不触发重新生图或重排；正文/档案/模板变化会使受影响结果及审核失效。

当前恢复验证覆盖文档更新及确定性排版，**未覆盖真实图像工具调用的端到端恢复**。预算预留和未知结果对账仍由应用端协调，不能宣传脚本可以自动请求内置生图。本版本不提供无人值守生图，也未通过完整能力验收；参看开发记录。
