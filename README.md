# GAIA SkillRL

独立于 `Earth-Bench` 的 `GAIA` skill 训练与执行项目。

当前目标：

- 保留单一 `executor` 下游。
- 先打通 `GAIA validation` 下载、附件读取、网页访问和最小答案闭环。
- 在此基础上复用 `actor / critic / skill` 的训练框架。
- 先做低成本冒烟，再扩展 benchmark 适配能力。

目录约定沿用旧项目习惯：

- `configs/` 配置
- `prompts/` 提示词
- `runs/` 运行产物
- `runtime_state/` 经验缓冲
- `实验设计与迭代/` 文档记录

当前 run 目录规则：

- 路径形态：`runs/年/月/日期/run_name`
- `run_name` 会自动规范成秒级时间前缀：`YYYYMMDD_HHMMSS_描述`

环境说明：

- 当前机器没有 `conda/mamba`，本项目默认使用 `uv` 管理 `.venv`。
- 如果后续机器补齐了 `conda`，再决定是否切换。

配置说明：

- 本地实际运行配置使用 `configs/system.json`，该文件默认不纳入版本控制。
- 仓库内提供 `configs/system.example.json` 作为模板，填入本机路径、base URL 和 API key 后再本地使用。
