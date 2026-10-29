# Sweet Media Kit

Retry 的 AIGC 媒体创作 skill：图片与视频 prompt、短片策划与分镜、战斗镜头以及成片剪辑。每个 skill 放在 `skills/<skill>/`，可单独安装。

## Skills

- `film-produce`：逐轮访谈对齐 AIGC 短片需求并确认制作简报，再按范围做世界观、剧本、资产建档与分镜装配。
- `image-prompt`：按主体、构图、色彩光影、材质肌理、风格美学五层编写单张图片的生成或编辑 prompt，输出分 section 的结构化提示词。
- `video-prompt`：按六层与时间轴编写单次生成、编辑或延长视频的 prompt，写清因果、连续性与无歧义表达。
- `fight-prompt`：编写冷热兵器、拳脚、机甲、超凡法术与巨物战的视频 prompt，覆盖单镜精拍、多镜长段与分段接力，锁死攻防力学、实体视效、节奏曲线、空间战损与运镜交接。
- `video-edit`：把一批无序视频素材剪成一条成片，或按意见修改已有成片。
- `style-anchor`：抽取参考图的稳定风格锚点，剥离具体内容，输出可迁移到新主体、可锁定系列视觉的规则 JSON，视频目标另含镜头语言。
- `image-replicate`：反推图片的完整生成 prompt，保留内容与位置，输出可复刻原图的 JSON。

## 相邻能力的边界

| 任务 | 入口 | 按需加载 |
| --- | --- | --- |
| 规划故事、资产与分镜 | `film-produce` | 当前制作阶段 reference |
| 剪辑视频素材成片 | `video-edit` | 按需读剪辑手法与看片 reference |
| 写单张图片／单次视频 prompt | `image-prompt`／`video-prompt` | 无 reference |
| 写战斗、交手或出招终爆的视频 prompt | `fight-prompt` | 分粒度封装结构与打击视效实体化 reference |
| 抽取去内容的风格锚点或系列视觉规则 | `style-anchor` | 无 reference |
| 反推 prompt 复刻原图 | `image-replicate` | 无 reference |

这些入口互不引用，也不默认串联；同一任务确实包含多类问题时再组合。

## 维护约定

- 每个 skill 使用独立目录，位于 `skills/<skill>/`，目录名与 `SKILL.md` 中的 `name` 一致；`name`、`display_name` 和一级标题统一使用英文。
- skill 专属的按需资料放在该 skill 的 `references/`。
- 保持 skill 职责独立，避免通过目录分层掩盖能力重叠。
- 全部 skill 只能显式调用，三家宿主各读一份配置，缺一个该宿主就会自动加载：Claude Code 与 pi 读 `SKILL.md` 的 `disable-model-invocation: true`，Codex 读 `agents/openai.yaml` 的 `policy.allow_implicit_invocation: false`。每个 skill 两份都要写；修改后检查显式可调用、隐式不触发。
- 用户反馈驱动的修改遵循 [AGENTS.md](AGENTS.md) 中的反馈闭环。
- 修改 `image-prompt` 或 `video-prompt` 前先读 [docs/prompt-skill-principles.md](docs/prompt-skill-principles.md)。

## 安装

三家宿主都用 `skills/<skill>/` 整个目录，`agents/` 与 `references/`、`scripts/` 一起带上。

### Claude Code

复制或链接到项目级 `.claude/skills/<skill>/` 或用户级 `~/.claude/skills/<skill>/`。带有 `disable-model-invocation: true` 的 skill 不会被 Claude 自动加载，只能由用户通过 `/skill-name` 调用。

### Codex

复制或链接到用户级 `~/.codex/skills/<skill>/` 或项目级 `.agents/skills/<skill>/`。`agents/openai.yaml` 里的 `policy.allow_implicit_invocation: false` 让 Codex 不把 skill 注入上下文，只能由用户用 `$skill-name` 显式调用；`/skills` 可列出已安装的 skill。

### pi

复制或链接到用户级 `~/.pi/agent/skills/<skill>/` 或项目级 `.pi/skills/<skill>/`，也支持 `~/.agents/skills/` 与 `.agents/skills/`。`disable-model-invocation: true` 让 pi 不把 skill 列入自动选择，只能由用户通过 `/skill:name` 调用；改完用 `/reload` 重载。

## 来源

这些 skill 原在 [retry-kit](https://github.com/SweetRetry/retry-kit) 的 `skills/media/` 下，此前的提交历史留在该仓库。
