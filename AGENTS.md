# Project Agent Notes

## GAIA 实验记录

- 后台实验沿用 `/data/xsy/AGENTS.md` 规则：用 `nohup` 加日志重定向启动，并维护 `/data/xsy/活的进程.csv`。
- 自 `2026-05-03 18:31 +0800` 起，GAIA / web_search 实验默认暂用 Serper：`NLRL_WEB_SEARCH_PROVIDER=serper`。启动前必须用计划使用的代理和 key 做一次 `num=1` smoke；若返回 `Not enough credits`、TLS/SSL 错误或非 200，先换 key 或代理再跑整组实验。
- 默认搜索 provider、Serper key 有序列表、fallback、代理端口的机器可读配置在本机 `/data/xsy/project_gaia_skillrl/configs/search_runtime.json`。以后轮换 key 或代理优先改这个文件，不要在实验脚本里新增硬编码；`serper_api_keys` 按优先级从前到后尝试，前面的 key 没额度或鉴权失败时自动尝试下一把。
- 当前 Serper key 明细和状态记录直接维护在 `/data/xsy/project_gaia_skillrl/configs/search_runtime.json` 与 `/data/xsy/skill-pool/API说明/26.4.29_2314_web_search_API管理.md`。Brave 只用于显式后端对照或历史复现。
- GAIA 实验涉及 Serper、Brave、DDG 或其它外部搜索/API 时，当前优先使用 `HTTP_PROXY=http://127.0.0.1:17890` 和 `HTTPS_PROXY=http://127.0.0.1:17890`，并设置对应小写变量；同时清空 `ALL_PROXY` / `all_proxy`，避免残留 SOCKS 配置影响 `requests`。
- `http://127.0.0.1:23457` 在 `2026-05-03 18:22 +0800` 对 Serper 和 `api.ipify.org` 均出现 SSL EOF，暂时不要用于 GAIA/search 实验，除非重新验证通过。
- `127.0.0.1:7897` 当前是转发到 `17890` 的 HTTP 转发口；若使用它，写成 `http://127.0.0.1:7897`。不要写成 `socks5://127.0.0.1:7897`。
- 新开 GAIA 实验队列时，若有明确需要后续验收的 run、日志、PID 或配置组合，同步更新 `/data/xsy/project_gaia_skillrl/实验设计与迭代/ZZ_当前待验收事项与版本索引.md`。
- prompt、flow、role、executor、runtime 版本轴发生变化时，同步更新 `/data/xsy/project_gaia_skillrl/实验设计与迭代/ZZ_版本技能图草案.md`；普通数据切片、并发、GPU 分配只写 run 记录。
- 明显失败、中途异常、样本量很小的实验先放在待验收或问题记录里，验收后再进入版本技能图的已跑组合索引。
