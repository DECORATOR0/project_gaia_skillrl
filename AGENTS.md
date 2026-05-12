# Project Agent Notes

## GAIA 实验记录

- 后台实验沿用 `/data/xsy/AGENTS.md` 规则：用 `nohup` 加日志重定向启动，并维护 `/data/xsy/活的进程.csv`。
- 自 `2026-05-03 18:31 +0800` 起，GAIA / web_search 实验默认暂用 Serper：`NLRL_WEB_SEARCH_PROVIDER=serper`。启动前必须用计划使用的代理和 key 做一次 `num=1` smoke；若返回 `Not enough credits`、TLS/SSL 错误或非 200，先换 key 或代理再跑整组实验。
- 默认搜索 provider、Serper key 有序列表、fallback、代理端口的机器可读配置在本机 `/data/xsy/project_gaia_skillrl/configs/search_runtime.json`。以后轮换 key 或代理优先改这个文件，不要在实验脚本里新增硬编码；`serper_api_keys` 按优先级从前到后尝试，前面的 key 没额度或鉴权失败时自动尝试下一把。
- 当前 Serper key 明细和状态记录直接维护在 `/data/xsy/project_gaia_skillrl/configs/search_runtime.json` 与 `/data/xsy/skill-pool/API说明/26.4.29_2314_web_search_API管理.md`。Brave 只用于显式后端对照或历史复现。
- Serper API key 是独享保真 key；单把额度是总共 `2500` credits，非每月 `2500`。估算实验搜索预算时按一次性剩余额度计算。
- GAIA 实验涉及 Serper、Brave、DDG 或其它外部搜索/API 时，当前优先使用 `HTTP_PROXY=http://127.0.0.1:17890` 和 `HTTPS_PROXY=http://127.0.0.1:17890`，并设置对应小写变量；同时清空 `ALL_PROXY` / `all_proxy`，避免残留 SOCKS 配置影响 `requests`。
- `http://127.0.0.1:23457` 在 `2026-05-03 18:22 +0800` 对 Serper 和 `api.ipify.org` 均出现 SSL EOF，暂时不要用于 GAIA/search 实验，除非重新验证通过。
- `127.0.0.1:7897` 当前是转发到 `17890` 的 HTTP 转发口；若使用它，写成 `http://127.0.0.1:7897`。不要写成 `socks5://127.0.0.1:7897`。
- 用户要求挂起 GAIA 后台/无人值守实验，并给出 `/data/xsy/project_gaia_skillrl/实验设计与迭代/26.5.3_1856_GAIA系统问题地图与统一记录约定试验稿.md` 或相关启动检查文档时，先在当轮实验文档写“启动前确认”块并向用户确认，确认前不启动。
- 如果用户在同一轮 GAIA 启动请求中明确说“不要确认 / 跳过确认 / 直接启动 / 无需确认 / 不想确认”等同义表达，仍然先把“启动前确认”块写入当轮实验文档，然后直接启动并回报命令、PID、日志、run prefix 和确认块文档路径。
- 连续上下文里的 GAIA 实验请求，如果用户已经明确说“启动 / 跑起来 / 继续跑 / 发起 / 直接做”等执行动作，就把它视为本轮启动确认；先写“启动前确认”块，然后直接启动，不要再额外追问确认。只有用户明确说先等确认、只讨论方案、暂不启动，或关键配置缺失且无法合理推断时，才暂停询问。
- “启动前确认”至少覆盖：当前方法架构原理与六轴版本（BOOT/FLOW/ROLE/EXEC/TOOL/ARCH、是否 no-CONCLUDE、答案接收阶段、boot 到 A/B 的生成链路）；上下文与预算（输入限制、vLLM `max_model_len`、`max_tokens`、`thinking_token_budget`、no-trunc、并发、tail/partial 策略）；模型/provider（executor、bootstrap actor、offline critic、offline actor 的 `model`、`base_url`、`api_mode`、timeout、reasoning/thinking 设置）。
- GAIA 8B/9B 版本组合实验默认沿用 2026-05-04/05 稳定约束卡片：`NLRL_RUNTIME_MAX_CONTEXT_CHARS=0`，no-trunc 由 tokenizer token guard 控制；`NLRL_EXECUTOR_MAX_TOKENS=12288`；`NLRL_EXECUTOR_TOKEN_GUARD_SAFETY_MARGIN=512`；`NLRL_EXECUTOR_ENABLE_THINKING=1`；`thinking_token_budget` 不设置/为 `None`；`NLRL_RUNTIME_MAX_EXECUTOR_STEPS=24`；`NLRL_RUNTIME_TASK_CONCURRENCY=20` 与 `NLRL_LLM_MAX_CONCURRENT_REQUESTS=20`；`NLRL_RUNTIME_ANSWER_ACCEPTANCE_POLICY=any_phase`；`NLRL_RUNTIME_TOOL_PROFILE=atomic_v2`；max-step 后按当前 runtime forced answer 口径收口。
- 这张约束卡片的模型窗口按 executor 模型分开填：Qwen3-8B 用 tokenizer `/data/xsy/codes/checkpoints/Qwen3-8B` 与 `NLRL_EXECUTOR_MAX_MODEL_LEN=40960`；Qwen3.5-9B 用 tokenizer `/data/xsy/codes/checkpoints/Qwen3.5-9B` 与 `NLRL_EXECUTOR_MAX_MODEL_LEN=49152`。不要把 9B 的 `49152` 直接套给 8B vLLM。
- GAIA 后台实验启动后尽快回读首个 `config_snapshot.json`、bootstrap request 或启动日志 env，核对 provider、模型、answer policy、预算和端口；若实际配置与确认块不一致，默认只向用户报告并记录到当轮文档。只有用户在确认块里明确授权自动停止时，才停止队列并标记 `config_contaminated`。
- GAIA 版本实验优先拆成独立脚本、独立提示词文件、独立环境变量开关和独立记录文档。空间占用可以接受，清晰、可回退、可切换优先；避免把候选版本叠成复杂的追加/覆盖流程。
- GAIA bootstrap/skill 实验启动后尽早检查产物是否正常生成：确认 `bootstrap_input.json`、bootstrap request/response、`skill_after_bootstrap/.../SKILL.md` 是否存在且可读，抽查 metadata version、phase 数量、关键字段、no-CONCLUDE 约束和早期 run log；skill 尚未生成时，要明确报告仍在等待的 actor 或阶段。
- GAIA eval 进程如果 executor 是远端 vLLM 或通过本地 tunnel 访问远端 endpoint（例如 `127.0.0.1:181xx` -> remote132），本地 eval 子进程默认设置 `CUDA_VISIBLE_DEVICES=`，避免 `onnxruntime-gpu`、`faster_whisper/ctranslate2` 等工具依赖在 254 GPU0 初始化 CUDA 并占用几百 MiB 显存。只有本地进程本身负责 254 上的 vLLM/GPU 推理时，才显式设置 `CUDA_VISIBLE_DEVICES=0/1/...`。
- 上述 CPU-only 本地 eval 设置不影响远端 executor 性能；潜在影响只在本地工具 fallback：音频转写等本地 GPU 工具会走 CPU 或 API fallback。GAIA remote executor 队列默认优先保护 254 显存。
- 区分外部中转 API 与自托管 remote132 vLLM：外部 API 不假设无限并发，SSSAI/Responses-SSE 先用低并发验收并观察 `服务器错误`、read timeout、capacity 等信号；自托管 remote132 可以让客户端提交多实验，但必须在队列/服务端侧做 per-lane 背压，限制每个 GPU endpoint 的并发、等待队列和超时，避免只是把卡顿从本地线程挪到远端请求队列。
- 若把实验主体迁到 remote132 执行，按“远端 runner + 本地只同步结果”处理，不再使用 254 本地 evaluator 通过 SSH tunnel 循环调用远端模型的旧模式；启动前确认代码/数据/venv/搜索代理/API key/结果 rsync 路径/监控日志是否齐全。
- GAIA 队列默认显式确认单题 wall-time 与工具子进程 wall-time cap；遇到 `tool_snippet.py` 或类似 runaway 子进程时，只释放该题并让 executor 继续收尾，不阻塞整批 dev/test 队列。
- 新开 GAIA 实验队列时，若有明确需要后续验收的 run、日志、PID 或配置组合，同步更新 `/data/xsy/project_gaia_skillrl/实验设计与迭代/ZZ_当前待验收事项与版本索引.md`。
- prompt、flow、role、executor、runtime 版本轴发生变化时，同步更新 `/data/xsy/project_gaia_skillrl/实验设计与迭代/ZZ_版本技能图草案.md`；普通数据切片、并发、GPU 分配只写 run 记录。
- 明显失败、中途异常、样本量很小的实验先放在待验收或问题记录里，验收后再进入版本技能图的已跑组合索引。
