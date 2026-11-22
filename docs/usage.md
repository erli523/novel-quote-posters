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

该样本分析只适配原样的自写样本。程序检查源哈希、窗口哈希、原句与证据位置，去重并保留提案快照；不会把 AI 归属标为人工批准。加工覆盖与精彩句召回是不同指标。数量不足要报告原因，不编句补足。

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

路径须在 run 内，版本文件不覆盖旧图。image_kind 为来源声明，不能单独证明真实工具调用；验收仍须检查调用记录与资产。`styles` 命令打印当前布局参数；单卡可提供 layout_overrides 覆盖色板和归一化坐标，须重新排版审阅。

## 4. 排版与导出

```powershell
.venv\Scripts\python -m novel_quote_posters.cli render --run runs/my-demo --font C:/Windows/Fonts/msyh.ttc
.venv\Scripts\python -m novel_quote_posters.cli export --run runs/my-demo --output dist/my-demo-v1 --draft
```

默认绘制“待审核”，导出 draft-review.zip。修改图/文字后必须重排并使用新路径；导出会拒绝文本、角色、原始文件、底图或图片哈希不符的旧结果。字体哈希保存在排版报告中，尚未接入 CLI 的变更失效管理。超容量/缺字明确报错。--card 单张制作；--width/--height 独立布局，正式图集导出当前只支持契约默认 1080×1440。

待检查没有水印的成品候选时，先换一个 poster 路径，再 render --clean-candidate；这只去掉草稿标签，不授予批准。最终 export 不加 --draft，要求 quote.review 与 card.review 真实人工批准并分别绑定内容 fingerprint。模板/审核导入交互仍在开发；不得由 AI 手填 human 欺骗门禁。软件中的人工字段是操作记录，不是身份认证系统。

ZIP 包含序号 PNG、手机缩略图、CSV/JSON、来源及顺序说明；不包含整本书或原始参考图。包内角色和卡片数据仍可能包含私有信息，只用于当前任务，不能自动作为公开产物。

## 恢复与当前限制

workflow.Ledger 提供 SQLite 原子状态、资产哈希、依赖失效、预算预留、未知结果对账及事件历史；已通过模块测试，CLI 的完整恢复集成仍待实现。当前 render 拒绝覆盖已存在图像，不能因此宣称“断点恢复已完成”。本版本不提供无人值守生图、最终人工审核界面或完整能力验收；参看开发记录。
