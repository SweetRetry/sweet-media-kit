---
name: media-gen
description: 经兔子 API 实际生成图片或视频：用 token 查可用模型、端点与参数，按用户指定的模型和参数提交，取回结果验收；含 Midjourney V8.2 写法。只写 prompt、不提交时不触发。
disable-model-invocation: true
---

# Media Gen

经兔子 API 调用生成模型。Midjourney 走 `scripts/mj.py`，写法与按钮流程见 [references/midjourney.md](references/midjourney.md)。其余模型走 `scripts/gen.py`，端点、参数与计费都从站点目录读取。用法看各脚本 `-h`。

## Token

脚本先读环境变量 `TUZI_API_KEY`，再读 `~/.config/tuzi/api_key`。两处都没有时，让用户自己执行：

```sh
mkdir -p ~/.config/tuzi && pbpaste > ~/.config/tuzi/api_key && chmod 600 ~/.config/tuzi/api_key
```

token 只经脚本读取。不打印、不回显、不写进 prompt、日志或仓库。

## 模型与参数由用户定

每次生成前，用户必须明确模型和参数。缺任何一项时不替用户选，先查再问：

- **模型。** `gen.py models [--kind 生图|视频|音频] [--grep X]` 列出当前 token 能调用的模型（来自 `/v1/models`）。站点新上的模型以这里为准，不靠记忆。把与需求相关的候选列给用户选。
- **参数。** `gen.py info <model>` 打印站点说明、端点、参数表（类型、取值、范围、默认值）、请求示例、可用分组和计费方式（来自公开的 `/api/pricing`）。把参数表里的必填项和与需求相关的可选项列给用户。参数表没写的字段，按端点文档链接理解，标为未经站点确认。
- **分组差异。** 同一模型在不同分组能力不同，如 gpt-image 系列的 SVG 和 `output_format: "gif"` 只在官方或官方优惠分组生效。token 属于哪个分组查不到，要用到分组专属能力时问用户。
- **端点。** `info` 标了「gen.py 不支持」的端点（chat、gemini 形态等）不提交；模型只有这类端点时告诉用户并给出文档链接。

用户说“随便”或“你定”时，给出一个具体组合及理由，等用户确认后再提交。

## 流程

1. **写 prompt。** 用户给了完整 prompt 时，只修正不合规处并说明改了什么。Midjourney 按 reference 的 V8.2 写法。其他模型写成完整描述句。图片按主体、动作、环境、构图、光线、色彩、风格排列，画面文字放进引号。视频再写清镜头运动和动作的先后。
2. **校验。** `gen.py run <model> "<prompt>" --param k=v ... --dry-run` 打印请求，确认端点、字段与用户指定的一致。prompt 自动放进 `prompt`，端点要 `content` 时放进其中的 text 项。值按 JSON 解析，如 `--param duration=5`、`--param 'svg={"model":"gpt-5.5"}'`。
3. **报费用再提交。** 按 `info` 的计费方式说明。固定单价的写出单价、计价单位（看说明，如视频按秒）和分组倍率。按 token 计费的说明提交前算不出单次价格。单轮超过 4 次提交先等用户确认。
   - 参考图或文件用 `--file key=PATH`。有 `--file` 时自动选 multipart 端点（如图片 edits），JSON 端点转成 data URI。视频首尾帧等结构化输入按 `info` 的参数说明写进 `--param content=...`。
   - 异步端点（`/v1/videos`）提交后脚本自动轮询。超时用 `gen.py fetch <model> <task_id>` 继续取，不重新提交。
4. **验收。** 逐个打开下载的结果，对照意图检查主体、数量、构图、文字、风格；视频再看动作与时长。报告返回的 `usage`。
5. **交付与下一步。** 给出文件路径，指出最接近意图的一个和原因。下载失败时交付接口返回的 URL 并说明未能验收。偏差来自 prompt 时改 prompt 重新提交；换模型或改参数前再问用户。

接口报错时把错误原文告诉用户，不自行换模型重试。
