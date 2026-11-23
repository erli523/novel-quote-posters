# 受控修改

V1 由应用端理解修改要求，再提交明确操作；本地命令不重新判断说话人，也不发起生图。编辑模板包含私有引文和证据，只留在当前任务，不要公开。

```powershell
.venv\Scripts\python -m novel_quote_posters.cli edit-template --run runs/my-demo --output runs/my-demo/edits-v1.json
```

填写实际 editor、reason，以及非空 operations。保留 source_hash 和 expected_fingerprint；available 仅供选择现有 ID，可保留或移除。例：

```json
{
  "editor": "实际操作者或应用端记录名称",
  "reason": "把第一张改为旧纸档案风格，并调整顺序",
  "operations": [
    {"type": "set-style", "card_id": "card001", "style_id": "archive_paper"},
    {"type": "reorder-cards", "card_ids": ["card002", "card001", "card003"]}
  ]
}
```

这是要填入模板的字段片段，card_ids 必须按实际任务列出全部卡片，不能漏掉或重复。

```powershell
.venv\Scripts\python -m novel_quote_posters.cli apply-edits runs/my-demo/edits-v1.json --run runs/my-demo
.venv\Scripts\python -m novel_quote_posters.cli render --run runs/my-demo --font C:/Windows/Fonts/msyh.ttc
```

四种操作：

| type | 必需字段 | 修改结果 |
| --- | --- | --- |
| set-display-text | quote_id、display_text、text_mode | 保留 quote_exact/source，清除相关说话人/单卡审核；保留底图，相关卡片待重排 |
| bind-card | card_id、quote_id | 换用已有原句候选，从已核对的归属资料取得人物；同人物底图留待视觉重审，换人物移除旧配图和成品指针 |
| set-style | card_id、style_id | 清除旧布局覆盖参数和成品指针，保留底图待兼容性重审 |
| reorder-cards | card_ids | 每张恰好一次，保留有效单卡；旧导出因顺序改变失效 |

set-display-text 使用 original 时必须与 quote_exact 完全相同。润色、拼接、省略等只能使用 rewrite，并在调用 apply-edits 时显式加 --allow-rewrite；排版和清单会标改写，正式任务的默认禁止改写政策仍由 export 再查。该开关是操作意图，不替代实际用户授权。

换另一句连续原文：先通过应用端分析提案建立带确切坐标和归属证据的新候选，再 bind-card；不要覆盖旧 quote_exact。歧义和旁白不能绑定为具名人物卡。换人物后需重新登记并绑定合适配图，不会自动复用旧人的图。保留旧文件便于反查，不能把旧图存在当作新卡片就绪。

编辑请求绑定当前 quotes/characters/cards 完整快照。整批校验后，在与文档提交相同的协作锁内再次比较依赖，冲突时不覆盖后来的修改；请重新生成模板。单卡输出、审核或其他资料已改变时，旧请求也可能失效。直接手改文件不受协作锁保护，不应与命令并发写同一任务。

相同已完成请求在相同后续状态下重放会复用结果；提交完成但调用返回前中断，恢复后能按请求身份找到原事务。无实际文档变化的批次也不会制造重复事务。原请求留在私有 .state/edits/，before/after 在 .state/transactions/；后续状态不同则拒绝旧请求，避免用“重试”覆盖新内容。操作者名称是声明，不是身份认证。

恢复原文字和原风格也用新模板，明确提交恢复值；可复用哈希相同的旧排版，但审核保持待定。所有上述操作图像工具调用数为 0，新的视觉审核仍须本人实际执行。整组顺序/首中末页审核尚在后续开发范围内，当前单卡批准不代表完整图集放行。
