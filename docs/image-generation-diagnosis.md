# 原生生图 404 排查

日期：2026-11-23。状态：已定位路由证据并准备项目级修复，**成功生图尚未验证**。

用户要求解决故障后，新增一次最小原生请求，依然在不到一秒内返回 `image generation failed: http 404 Not Found: Some("")`，无图片、无模型推理/政策拒绝元数据。三次失败均保留，保守占用 3/10，剩余 7，没有清零或切到独立付费图片 API。

本会话使用已配置的本地自定义 provider，登录模式为 ChatGPT。无副作用的路由检查中，文字 responses 路径返回 405 并声明允许 POST，图片 generations 路径返回 404。当前 [OpenAI 官方图像后端源码](https://github.com/openai/codex/blob/main/codex-rs/ext/image-generation/src/backend.rs) 通过活动 model provider 创建请求；[图像客户端](https://github.com/openai/codex/blob/main/codex-rs/codex-api/src/endpoint/images.rs) 使用 images/generations 和 images/edits。该机制与当前即时 404 相符，主要证据指向本地转发没有相应图片路由，而不是仅靠改提示词能解决。源码版本与已安装二进制可能不同，OPTIONS 本身也不等同成功 POST；仍须修复后真实验证。

本地已新增仅本项目生效的 `.codex/config.toml`，设置 model_provider 为 openai，复用现有 ChatGPT 登录。本轮未修改全局转发配置，未提取/重放任何存储凭据，未启用付费 API 或新连接器；该本地文件不进入公开仓库。项目配置的适用与优先级以 [官方配置说明](https://learn.chatgpt.com/docs/config-file/config-basic) 为依据。

配置文件落盘后，本聊天的原生入口复查仍是 404，不能宣布恢复。需重新加载项目/当前运行时，并确认没有更高优先级的旧 provider 覆盖，然后在剩余总预算内明确调整分项、预留一次真实调用，验证图片文件取得、参考编辑和排版交接。若重载后仍失败，继续保留实际响应并定位生效配置，不反复盲调；API 或第三方备用路线仍须用户明确选择。

回退只需移除本地新增的项目配置，重新继承原全局设置。删除本地新文件不会删除诊断、既有调用或小说；实际回退并未执行。当前主任务仍三张待图，真实图片/人工批准均为 0。
