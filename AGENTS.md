# Project Agent Notes

## GAIA 实验记录

- 后台实验沿用 `/data/xsy/AGENTS.md` 规则：用 `nohup` 加日志重定向启动，并维护 `/data/xsy/活的进程.csv`。
- 新开 GAIA 实验队列时，若有明确需要后续验收的 run、日志、PID 或配置组合，同步更新 `/data/xsy/project_gaia_skillrl/实验设计与迭代/ZZ_当前待验收事项与版本索引.md`。
- prompt、flow、role、executor、runtime 版本轴发生变化时，同步更新 `/data/xsy/project_gaia_skillrl/实验设计与迭代/ZZ_版本技能图草案.md`；普通数据切片、并发、GPU 分配只写 run 记录。
- 明显失败、中途异常、样本量很小的实验先放在待验收或问题记录里，验收后再进入版本技能图的已跑组合索引。

