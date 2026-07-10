<div align="center">

# Giftia

![:访问量](https://count.getloli.com/@astrbot_plugin_giftia?name=astrbot_plugin_giftia&theme=rule34&padding=5&offset=0&scale=1&pixelated=1&darkmode=auto)

[![License](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org)
[![AstrBot](https://img.shields.io/badge/AstrBot-4.10.0%2B-75B9D8.svg)](https://github.com/AstrBotDevs/AstrBot)
[![Giftia](https://img.shields.io/badge/Giftia-v0.0.1-FFD700.svg)](https://github.com/sukafon/astrbot_plugin_giftia)

</div>

**Giftia** 是一款面向 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 的高性能、专注于聊天场景的人格与记忆沉淀插件。

> “赋予 AI 人格与记忆，以时间沉淀情感，用记忆塑造行为。”

通过本插件，你可以轻松让你的 Bot 拥有更像人类的情感记忆与聊天逻辑，实现智能的主动插嘴/接话、关系画像维护、好感度增减、长短期记忆 RAG 检索，以及极低 token 消耗的媒体转述系统。此外，它还配备了全功能的可视化 Web 仪表盘（Dashboard），让数据与缓存管理尽在掌控。

> [!IMPORTANT]
> **核心使用建议**
> - **关闭原生 AI 对话**：启用本插件时，推荐在 AstrBot 管理面板中**完全关闭“AI 对话总开关”**。由本插件完全接管 AI 的对话与接话决策逻辑，避免回复冲突或重复回复。
> - 如需使用tavily网页搜索，可以先在配置中填写好apikey，再关闭AI 对话总开关。

---

## 快速开始

### 1. 安装插件
- 在 AstrBot 插件市场中搜索 `Giftia` 并点击安装。
- 或者在 AstrBot 插件页面中，点击 **+**，选择“从链接安装”，填写本项目地址进行安装。

### 2. 必要配置指南
为了快速上手并使插件正常工作，请在 AstrBot 的插件配置中，完成以下 4 个必要步骤：

1. **添加并启用机器人**
   - 在 **机器人模板列表** 中添加新的机器人实例。
   - 勾选 **启用机器人**。
   - 机器人名称 (name) 填写为任意名称，但不可重复。
   - 机器人昵称 (nickname) 填写机器人在对应平台的名称，用于上下文判断。
   - 适配器ID列表 (adapter_ids)，打开Astrbot侧边栏中“机器人”页签，显示的名称就是适配器id。
   - 填入对应机器人的小模型判断提示词、大模型回复提示词，并选择对应的模型提供商。
   - 配置 **主动接话触发概率** 以及 **兴趣关键词列表**，以控制 Bot 主动搭腔或在提及特定话题时触发接话。
   
2. **配置媒体转述供应商**
   - 在 **媒体转述配置** 中，为图片和音频转述配置相应的多模态模型供应商（如 Gemini 等）。
   
3. **配置“启用的内置交互功能”**
   - 滚动到 **函数调用工具** 下方的 **启用的内置交互功能** 配置组。
   - 勾选允许当前 Bot 调用的内置 XML 互动功能（如戳一戳、复读、点赞名片、表情包发送等），未勾选的功能将不被允许调用。

4. **配置记忆、重排及总结模型**
   - 在 **记忆检索配置** 中，配置 **嵌入模型** 与 **重排模型** 的供应商及模型以开启长期 RAG 记忆。
   - 打开 **启用被动状态维护** 以开启后台自动聊天记录总结，并在下方的 **被动总结模型提供商** 中配置相应的模型提供商以提炼记忆和画像。

---

## 更多详情与高级使用

关于完整的常用命令列表、所有配置项的详细说明以及提示词模版详解，请参阅：
[**Giftia 详细使用文档 (DETAIL.md)**](DETAIL.md)

## 致谢
感谢 Codex 和 Google One PRO 提供代码补全与参考支持！