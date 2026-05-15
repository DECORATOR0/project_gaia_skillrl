# Project Agent Notes

## GAIA 实验总则

- 后台实验沿用 `/data/xsy/AGENTS.md`：用 `nohup` 加日志重定向启动，并维护 `/data/xsy/活的进程.csv`。
- 新开 GAIA 实验队列时，若有明确需要后续验收的 run、日志、PID 或配置组合，同步更新 `/data/xsy/project_gaia_skillrl/实验设计与迭代/ZZ_当前待验收事项与版本索引.md`。
- prompt、flow、role、executor、runtime 版本轴发生变化时，同步更新 `/data/xsy/project_gaia_skillrl/实验设计与迭代/ZZ_版本技能图草案.md`；普通数据切片、并发、GPU 分配只写 run 记录。
- 明显失败、中途异常、样本量很小的实验先放在待验收或问题记录里，验收后再进入版本技能图的已跑组合索引。

## remote132 主战场

- 自 2026-05-12 起，GAIA 主执行环境优先放在 remote132：`xsy@124.115.123.132 -p 22219`，项目路径 `/data/xsy/project_gaia_skillrl`，主机 `gpu-a800-19`。
- 迁移原因：从 254 侧频繁拉起 remote132 的细颗粒 Codex/实验操作、传递本地上下文到远端、实时查看和操控远端任务都会产生明显延迟；remote132 卡和本地资源更充足，GAIA 工作应尽量在 remote132 本地会话内闭环。
- 新的 GAIA vLLM、eval、bootstrap、offline critic/actor、A_full/B 系列 skill 生成，默认在 remote132 本地跑；254 侧主要做结果拉回、索引归档和必要的外部调度说明。
- 需要跨机同步时，以 remote132 的 run/log/manifest/summary/skill 产物为运行事实源；254 侧文档和索引可以作为镜像归档。同步前后都要说明方向：`254 -> 132` 表示推代码/配置，`132 -> 254` 表示拉结果/文档。
- remote132 上已有 Codex auth、项目数据、venv、Qwen3-8B/Qwen3.5-9B 模型和 Clash 代理。Codex/代理配置按远端现状使用；实验脚本里如需固定 skill generator，继续在确认块和 env 中显式写 `gpt-5.4 / codex_cli / model_reasoning_effort=xhigh`。
- remote132 上运行的后台实验也要登记到 `/data/xsy/活的进程.csv`；在 254 侧只登记远端 master PID、远端 cwd、run_dir、命令摘要、manifest 和日志路径，避免伪装成本地进程。
- 254 侧不再默认用 SSH tunnel 高频调用 remote132 vLLM。只有调试、临时 smoke 或历史脚本复现需要时，才使用 tunnel 模式，并在当轮记录里写清原因。

## 启动确认

- 用户要求挂起 GAIA 后台/无人值守实验，并给出 `/data/xsy/project_gaia_skillrl/实验设计与迭代/26.5.3_1856_GAIA系统问题地图与统一记录约定试验稿.md` 或相关启动检查文档时，先在当轮实验文档写“启动前确认”块并向用户确认，确认前不启动。
- 如果用户明确说“不要确认 / 跳过确认 / 直接启动 / 无需确认 / 不想确认”等同义表达，仍先写“启动前确认”块，然后直接启动并回报命令、PID、日志、run prefix 和确认块文档路径。
- 连续上下文里的 GAIA 实验请求，如果用户已经明确说“启动 / 跑起来 / 继续跑 / 发起 / 直接做”等执行动作，视为本轮启动确认；先写确认块，然后直接启动。用户明确要求先等确认、只讨论方案、暂不启动，或关键配置缺失且无法合理推断时，暂停询问。
- “启动前确认”至少覆盖三组信息：方法架构原理与六轴版本（BOOT/FLOW/ROLE/EXEC/TOOL/ARCH、是否 no-CONCLUDE、答案接收阶段、boot 到 A/B 的生成链路）；上下文与预算（输入限制、vLLM `max_model_len`、`max_tokens`、`thinking_token_budget`、no-trunc、并发、tail/partial 策略）；模型/provider（executor、bootstrap actor、offline critic、offline actor 的 `model`、`base_url`、`api_mode`、timeout、reasoning/thinking 设置）。
- GAIA Codex skill generator 的 reasoning effort 属于模型能力项。确认块必须写清有效值和来源：命令行 `-c model_reasoning_effort=...`、`/data/xsy/.codex/config.toml` 继承值，或模型默认值；`codex exec` 未带 `--ignore-user-config` 时会读取 `/data/xsy/.codex/config.toml`。
- GAIA Codex skill 生成意图必须在确认块、启动命令和后续 `config_snapshot.json` 中对齐到 `gpt-5.4`、`codex-cli`、`codex_cli`；SSSAI/Responses skill 生成意图必须显式写成 `gpt-5.2`、`responses_sse`、对应 base URL 的另一条实验假设。
- 启动后尽快回读首个 `config_snapshot.json`、bootstrap request 或启动日志 env，核对 provider、模型、answer policy、预算和端口；若实际配置与确认块不一致，默认报告用户并记录到当轮文档。只有用户在确认块里明确授权自动停止时，才停止队列并标记 `config_contaminated`。

## 运行约束

- GAIA 8B/9B 版本组合实验默认沿用 2026-05-04/05 稳定约束卡片：`NLRL_RUNTIME_MAX_CONTEXT_CHARS=0`，no-trunc 由 tokenizer token guard 控制；`NLRL_EXECUTOR_MAX_TOKENS=12288`；`NLRL_EXECUTOR_TOKEN_GUARD_SAFETY_MARGIN=512`；`NLRL_EXECUTOR_ENABLE_THINKING=1`；`thinking_token_budget` 不设置/为 `None`；`NLRL_RUNTIME_MAX_EXECUTOR_STEPS=24`；`NLRL_RUNTIME_TASK_CONCURRENCY=20` 与 `NLRL_LLM_MAX_CONCURRENT_REQUESTS=20`；`NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY=any_phase`；`NLRL_RUNTIME_TOOL_PROFILE=atomic_v2`；max-step 后按当前 runtime forced answer 口径收口。
- 这张约束卡片的模型窗口按 executor 模型分开填：Qwen3-8B 用 tokenizer `/data/xsy/codes/checkpoints/Qwen3-8B` 与 `NLRL_EXECUTOR_MAX_MODEL_LEN=40960`；Qwen3.5-9B 用 tokenizer `/data/xsy/codes/checkpoints/Qwen3.5-9B` 与 `NLRL_EXECUTOR_MAX_MODEL_LEN=49152`。不要把 9B 的 `49152` 直接套给 8B vLLM。
- GAIA 队列启动和确认块里显式考虑单题总 wall-time 与工具执行 wall-time，尤其是 `run_python` / `python_code` 生成的 `batch_state/runs/temp/*/tool_snippet.py`。若当前 runtime 没有 cap，也要写清风险和人工处置口径。
- dev/test 长时间停在少数 missing 时，优先检查 eval 子进程和 `tool_snippet.py`。若单个工具脚本持续数分钟 CPU 100%，且是超大 Monte Carlo、暴力穷举或明显无法在本轮预算内完成，默认只终止该工具子进程，保留 eval/queue 主进程，让该题按工具失败、max-step 或缺失收口，并记录 task_id、PID、脚本路径、处置时间、当前落盘数和分数。
- direct/eval 队列遇到 executor streaming 连续空响应、单题 LLM 调用 exhausted 或同类 transient endpoint 异常时，优先按单题失败/partial 记录处理，让同一路剩余样本继续跑。
- bootstrap/skill 实验启动后尽早检查产物：确认 `bootstrap_input.json`、bootstrap request/response、`skill_after_bootstrap/.../SKILL.md` 存在且可读，抽查 metadata version、phase 数量、关键字段、no-CONCLUDE 约束和早期 run log；skill 尚未生成时，报告仍在等待的 actor 或阶段。
- GAIA 版本实验优先拆成独立脚本、独立提示词文件、独立环境变量开关和独立记录文档。空间占用可以接受，清晰、可回退、可切换优先。

## 队列与资源

- 默认把后台实验队列理解为通用资源补位池。lane 是调度资源，某个配置/lane 不应永久占用某张卡。
- GAIA 队列的推进、补位和 master 收口不要以最终 summary/report 文件是否落盘为主指标。最终汇总文件只作为报告产物；调度判断优先看 per-task `state.json` 落盘进度、`selected_tasks.json` 总数、长尾阈值、停滞时间、进程状态和 endpoint/GPU 实际负载。
- run 达到长尾可接收口径后，后续队列可以补位或让 master 先收口，原长尾子进程默认保留自然完成。只有用户明确清理、进程 wedged、输出冲突或资源占用被确认影响后续任务时，才单独停止长尾子进程。
- 除非用户明确要求隔离，或实验之间存在严格前后依赖、输出冲突、端口/缓存/run 目录不可共享，否则不要因为某个 lane 的管理进程还活着就认定对应 GPU/端口不可用。
- 判断能否补位时综合看 GPU utilization、端口是否 open、vLLM 服务是否 ready、当前任务进程是否真实在使用该服务、manifest 中是否已有同一 queue item running。
- 如果长尾阶段 GPU 利用率接近 0 但仍被队列标记 busy，主动修正 watcher/调度规则，让后续任务共享该空闲服务，同时保留仍在正常计算的子任务。
- 全局队列已空、后续没有可补任务，或用户明确要求收尾清理时，才自动清掉空闲 GPU 服务。

## Provider、代理与搜索

- GAIA / web_search 实验默认暂用 Serper：`NLRL_WEB_SEARCH_PROVIDER=serper`。启动前用计划使用的代理和 key 做一次 `num=1` smoke；若返回 `Not enough credits`、TLS/SSL 错误或非 200，先换 key 或代理再跑整组实验。
- 默认搜索 provider、Serper key 有序列表、fallback、代理端口维护在 `/data/xsy/project_gaia_skillrl/configs/search_runtime.json`；轮换 key 或代理优先改这个文件，避免在实验脚本新增硬编码。
- Serper key 明细和状态记录维护在 `/data/xsy/project_gaia_skillrl/configs/search_runtime.json` 与 `/data/xsy/skill-pool/API说明/26.4.29_2314_web_search_API管理.md`。Serper key 是独享保真 key，单把额度总共 `2500` credits；Brave 只用于显式后端对照或历史复现。
- GAIA 涉及 Serper、Brave、DDG 或其它外部搜索/API 时，当前优先使用 `HTTP_PROXY=http://127.0.0.1:17890` 和 `HTTPS_PROXY=http://127.0.0.1:17890`，并设置对应小写变量；同时清空 `ALL_PROXY` / `all_proxy`。
- 2026-05-04 复验：`http://127.0.0.1:23457` 访问 Serper account 失败，错误为 `SSL_ERROR_SYSCALL` / HTTP 000；`http://127.0.0.1:17890`、`http://127.0.0.1:7897`、`http://127.0.0.1:7890` 均可访问 Serper account，`17890` 的真实 Serper search smoke 返回 HTTP 200。
- `http://127.0.0.1:23457` 仅保留为历史可用记录，再次启用前必须重新 smoke；`127.0.0.1:7897` 当前是转发到 `17890` 的 HTTP 转发口，写成 `http://127.0.0.1:7897`。
- 备用代理为 `http://127.0.0.1:7890` 和 `socks5h://127.0.0.1:7891`；legacy Clash HTTP/HTTPS 为 `http://127.0.0.1:17890`；legacy SOCKS5 为 `socks5://127.0.0.1:17891`；Clash external controller 为 `127.0.0.1:17900`。
- 不要依赖 `127.0.0.1:43214`。若现有模板或环境变量指向旧代理，启动前按当轮 smoke 和 `search_runtime.json` 改写。项目明确要求直连目标端点时，按项目规则执行。

## vLLM 与远端执行

- GAIA eval 进程如果 executor 是远端 vLLM 或通过本地 tunnel 访问远端 endpoint（例如 `127.0.0.1:181xx` -> remote132），本地 eval 子进程默认设置 `CUDA_VISIBLE_DEVICES=`，避免 `onnxruntime-gpu`、`faster_whisper/ctranslate2` 等工具依赖在 254 GPU 上初始化 CUDA。只有本地进程本身负责 254 上的 vLLM/GPU 推理时，才显式设置 `CUDA_VISIBLE_DEVICES=0/1/...`。
- 上述 CPU-only 本地 eval 设置不影响远端 executor 性能；潜在影响只在本地工具 fallback，音频转写等本地 GPU 工具会走 CPU 或 API fallback。GAIA remote executor 队列默认优先保护 254 显存。
- 区分外部中转 API 与自托管 remote132 vLLM：外部 API 先用低并发验收并观察 `服务器错误`、read timeout、capacity 等信号；自托管 remote132 要在队列/服务端侧做 per-lane 背压，限制每个 GPU endpoint 的并发、等待队列和超时。
- 若把实验主体迁到 remote132 执行，按“远端 runner + 本地只同步结果”处理，启动前确认代码、数据、venv、搜索代理、API key、结果 rsync 路径和监控日志齐全。
- remote132 上单卡自托管 Qwen3-8B vLLM baseline 默认优先用 `gpu_memory_utilization=0.90`，提升 KV cache 余量；只有启动日志显示 OOM、KV capacity 不足或显存被其它服务占用时，才降回 `0.72` 或更低。
