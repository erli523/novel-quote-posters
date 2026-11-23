# 应用端图像交接与预算

本文件说明 V1 本地工具如何与当前应用端协调。这里的命令**不发起图片 API 请求**；调用实际生图工具仍由应用端执行。生成、参考图编辑、取得文件必须分别实测，不因登记字段存在就声称能力通过。

## 1. 预算、历史与状态

新建任务时 `ingest --image-limit 10` 固定该任务的调用上限；后续操作不会用默认值覆盖已有上限。所有预留（含失败、未知、未执行）保守占用名额，不推断免费，也不虚构金额。`budget` 不打印提示词或原始响应。正式预留前须在 [任务契约](task-contract.md) 明确分项额度，余额不能自动跨项借用；budget 分别显示总余额、各项占用和可执行类别余额。

```powershell
.venv\Scripts\python -m novel_quote_posters.cli budget --run runs/my-demo
.venv\Scripts\python -m novel_quote_posters.cli status --run runs/my-demo
```

历史失败若只存在旧 JSON 中，可用 `import-call-log 旧日志.json --run ...` 迁入；同 ID 幂等、不清零，成功记录必须另有真实文件对账，不能只凭旧日志导入成成功。已有 SQLite 调用不需要重复迁移。数据库升级是增量增加请求/结果表，保留已有预算、调用与事件；未来未知版本拒绝降级。

status 区分待图、待排版、草稿可看、待审核和已失效，另列索引与应用端处理窗口数。检查点只能证明有可恢复材料，不能据此判断某进程仍活着。进度统计不等于 28 项验收通过。

## 2. 预留一项实际调用

应用端先建立人物档案，写一份私有请求 JSON：

```json
{
  "call_id": "lin-che-reference-v1",
  "asset_id": "lin-che-reference",
  "kind": "reference",
  "character_id": "lin_che",
  "tool": "image_gen.imagegen",
  "prompt": "Original fictional character reference portrait. Single 27-year-old Chinese man, short black hair, subtle scar at the outer end of his left eyebrow, dark navy modern trench coat. No lettering, no watermark.",
  "references": []
}
```

`call_id` 是应用端的本地调用 ID，不能冒充提供方返回的 ID；`asset_id` 是逻辑制作目标，后续失败重试仍指向同一目标。`kind` 可为 reference、background、edit，编辑必须有实际参考图。人物、源哈希、实际参考图哈希和请求版本一起存入私有快照。

```powershell
.venv\Scripts\python -m novel_quote_posters.cli image-plan request.json --run runs/my-demo
```

只有结果为 `action=invoke` 才表示本次预留可执行一次实际调用；`reconcile` 表示先对账，`reuse` 表示已有有效结果可复用。`tool_was_called=false` 明确表示命令本身没调用工具。应用端调用工具时只传所需的视觉提示词和实际参考图，不传整本书或整个内部请求快照。

同一 ID 输入变化会拒绝。仅契约修订而操作输入未变时，原操作仍对账，不重新预留。未知结果不能绕过为同一目标开启新调用。只有确认的临时故障/确认未执行，才能通过 `retry_of` 创建一次有预算占用的重试；重试不能再重试，输入和参考图必须一致，并占 retry 项。审美上重生是新候选，须记录 `new_candidate_reason`，占 alternative 项；新目标的备选也可显式设 purpose=alternative。其他初次请求分别占 reference/background/edit 项，编辑仍须实际参考图。

## 3. 失败、未知结果与停机

```powershell
.venv\Scripts\python -m novel_quote_posters.cli image-fail --run runs/my-demo --call lin-che-reference-v1 --status unknown_outcome --failure-kind unknown --note '已调用但暂时无法确认是否生成；先查找结果，不重发'
```

失败类型为 transient、not_invoked、quota、policy、unavailable、unknown、input。限额/权限/政策/工具缺失不盲重试。确定的最终结果不能改写成另一种结果；原失败保留，新调用另有 ID。未知结果在找到实际图像后可对账成功；不能凭空填写 success。

## 4. 导入实际工具产物

实际取得可读取的图片后，计算其真实 SHA-256（可使用本地 `Get-FileHash -Algorithm SHA256`，值转小写），填入私有收据：

```json
{
  "source_kind": "generated",
  "call_id": "lin-che-reference-v1",
  "input_hash": "image-plan 返回的完整 input_hash",
  "image_sha256": "实际图片文件的小写 SHA-256",
  "character_id": "lin_che",
  "tool_output_reference": "实际工具返回的产物标识；未暴露时明确填 unavailable"
}
```

```powershell
.venv\Scripts\python -m novel_quote_posters.cli image-import 实际图片.png --receipt receipt.json --run runs/my-demo
```

命令校验图片解码、收据哈希、已预留请求、人物与结果状态，按内容保存图片，并返回登记后的 `asset_id`。这与请求中的逻辑 asset_id 不同，后续绑定使用**返回的登记 ID**。注册中断可以重复同一导入，完成对账而不再次生图。图片、收据与记录不一致会拒绝；已损坏的同名内容文件隔离保留，再从哈希吻合的实际文件恢复。

工具来源记录属于应用端声明，不是提供方的密码学证明。验收仍须对照真实调用轨迹、原始产物和实际像素。自动测试的模拟收据明确标测试，不能计入真实生图。模型版本、seed、费用未暴露就记 unavailable。

## 5. 基准图、卡片与替换配图

```powershell
.venv\Scripts\python -m novel_quote_posters.cli set-reference 返回的登记ID --character lin_che --run runs/my-demo
.venv\Scripts\python -m novel_quote_posters.cli prepare-cards --run runs/my-demo --count 3 --style mist_cinema
.venv\Scripts\python -m novel_quote_posters.cli attach-image 返回的登记ID --card card001 --run runs/my-demo
```

prepare-cards 仅使用应用端已选择、原句核验通过且有明确/上下文归属的候选；数量不足返回 shortfall，不编台词。参考图与正式底图分别登记，基准肖像不算三张独立正式底图。换图或基准图更新后重新排版和审核。

用户提供的替换配图可用 `source_kind=user_provided`、真实图片哈希、人物 ID、`source_note` 说明来源后导入，无须伪造一次生图调用；正式导出仍要真实人工批准。它不会计入“本项目实际生成”的数量。程序测试素材用 test_fixture，正式导出明确拒绝。

source_kind 字段本身不够：正式导出还会检查登记 ID、实际文件、收据、输入哈希、成功调用（适用于 generated）和绑定人物。已有本地脚本直接合成的旧草稿可以预览，正式成品必须通过新登记链路。

## 6. 当前真实能力记录

本项目自写样本在项目配置修改前有三次实际失败，配置修改后的首次真实验证也在 127 毫秒内返回 HTTP 404，0 张真实图片；原失败未改写，预算 10 中保守占用 4、剩余 6。当前分项基准 3、底图 5、编辑 1、旧未分类 1，总额未扩大，基准项已用尽。已发现活动 provider 的本地图片路由缺失证据，项目级官方通道配置已本地准备，运行时恢复仍待重载后实测，详见 [生图排查](image-generation-diagnosis.md)。没有切到 API，没有把测试底图计为成品。CLI/API 备用路线须用户明确另行选择。
