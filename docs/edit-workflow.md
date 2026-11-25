# 受控修改

V1 由应用端理解修改要求，再提交明确操作；本地命令不重新判断说话人，也不发起生图。编辑模板包含私有引文和证据，只留在当前任务，不要公开。

```powershell
.venv\Scripts\python -m novel_quote_posters.cli edit-template --run runs/my-demo --output runs/my-demo/edits-v1.json
```

填写实际 editor、reason，以及非空 operations。保留 source_hash、expected_fingerprint 和 expected_document_head；available 仅供选择现有 ID，可保留或移除。例：

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

五种操作：

| type | 必需字段 | 修改结果 |
| --- | --- | --- |
| set-display-text | quote_id、display_text、text_mode | 保留 quote_exact/source，清除相关说话人/单卡审核；保留底图，相关卡片待重排 |
| bind-card | card_id、quote_id | 换用已有原句候选，从已核对的归属资料取得人物；同人物底图留待视觉重审，换人物移除旧配图和成品指针 |
| set-style | card_id、style_id | 清除旧布局覆盖参数和成品指针，保留底图待兼容性重审 |
| set-layout | card_id、overrides | 整体替换单卡布局覆盖；空对象恢复默认。保留底图，清除成品和视觉审核，等待重排 |
| reorder-cards | card_ids | 每张恰好一次，保留有效单卡；旧导出因顺序改变失效 |

set-display-text 使用 original 时必须与 quote_exact 完全相同。润色、拼接、省略等只能使用 rewrite，并在调用 apply-edits 时显式加 --allow-rewrite；排版和清单会标改写，正式任务的默认禁止改写政策仍由 export 再查。该开关是操作意图，不替代实际用户授权。

换另一句连续原文：先通过应用端分析提案建立带确切坐标和归属证据的新候选，再 bind-card；不要覆盖旧 quote_exact。歧义和旁白不能绑定为具名人物卡。换人物后需重新登记并绑定合适配图，不会自动复用旧人的图。保留旧文件便于反查，不能把旧图存在当作新卡片就绪。

编辑请求绑定当前 quotes/characters/cards 完整快照及不可变文档事务头 expected_document_head。内容恢复为相同值也会有新的事务身份，因此不会重新接受之前的旧计划。编辑政策4要求重新生成缺少事务头的旧模板。整批校验后，在与文档提交相同的协作锁内再次比较依赖，冲突时不覆盖后来的修改；请重新生成模板。单卡输出、审核或其他资料已改变时，旧请求也可能失效。直接手改文件不受协作锁保护，不应与命令并发写同一任务。

相同已完成请求仅在相同内容和该请求提交的确切事务头下重放才复用结果；提交完成但调用返回前中断，恢复后能按请求身份找到原事务。无实际文档变化的批次也不会制造重复事务。原请求留在私有 .state/edits/，before/after 在 .state/transactions/；后续状态不同则拒绝旧请求，避免用“重试”覆盖新内容。操作者名称是声明，不是身份认证。

恢复原文字和原风格也用新模板，明确提交恢复值；可复用哈希相同的旧排版，但审核保持待定。所有上述操作图像工具调用数为 0，新的视觉审核仍须本人实际执行。顺序或卡片版本变化后需在新审核页重做整组判断，当前单卡批准不代表完整图集放行。

## 单卡取景与颜色

在新模板的operations中使用以下片段（card003换为实际ID）：

```json
{"type":"set-layout","card_id":"card003","overrides":{"image":[0.18,0.39,0.94,0.87],"image_fit":"cover","image_anchor":[0.5,0.05]}}
```

每次整体替换overrides，未列字段回到当前模板默认；不是与旧覆盖合并。用`"overrides":{}`恢复默认。只接受bg/fg/accent、image/panel/text、image_fit/max_crop/image_anchor、show_chapter十项，颜色规范为不透明RGB，矩形/锚点为归一化坐标，非法/未知/非有限值整批拒绝。切换风格会清除旧覆盖。

本次采纳的正文安全区为横向80/1080至1-80/1080、纵向0.15～0.875，文字框必须在不透明面板内，实色正文字/面板对比至少4.5:1。这是工程选择，不是平台规定，也不检测人脸。合法参数仍须render检查字形/容量，再由用户看实际整图/手机图。长句先获得模板自适应面积，再覆盖明确字段，单改锚点不会取消长句适配。

事务版本在规划、历史重放、写入锁和提交锁内检查；后续任意文档事务可能使模板过期。恢复布局请用新模板，不删除历史或修改旧请求指纹。原图和原句不会因此重生或改写。无水印候选仍可处于pending，正式导出继续要求真实人工批准。

六风格补充样张使用独立[审阅记录](style-review.md)。单卡修改与补充样张分别计算实际依赖，不能把六风格通过转成主卡或整组批准；后续人工判断有快照，旧记录不能覆盖新否决。

主审核请求现为schema 4，逐原句/单卡/整组决策版本独立保存在review-decisions.json。显示编辑、取景或重排变为pending也不清除历史版本；恢复完全相同的内容后，旧已被后来判断取代的批准不能覆盖。真正需要改判断时生成新审核页，勿修改旧快照字段。

无章节图文示例：`{"type":"set-layout","card_id":"card001","overrides":{"show_chapter":false}}`。该布尔项只控制画面标签，原句来源章节、审核上下文及导出出处仍保留；字符串或数字拒绝。可与image/panel/text等紧凑布局参数一并保存，渲染缓存、修改回退和ZIP均携带该选择。设置true或用空覆盖恢复模板可重新显示章节，且需要重新视觉审阅。
