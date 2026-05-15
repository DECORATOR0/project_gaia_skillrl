# 当前待验收事项

更新时间：2026-05-15 01:10 +0800

用途：只保留当前需要验收的实验。历史分数和误跑记录回看对应日期实验文档。

## 当前状态

2026-05-15 01:10 EarthBench 9B 迁移执行：

- 当前有效 EarthBench 前缀：`20260515_0058_earthbench_9b_bootv7_a1_a2_gpu2_6_keep8b7`；master PID `2366626` 正在运行。
- 项目根目录：`/data/xsy/projects(25.12-26.2)/qs-work/project_skills-3.18dhc-19.40`。
- 目标矩阵：9B `baseline / BOOT-V7 / A1 / A2`，每组跑 `train52` 与 `test196`，共 `8` 条 counted eval。`A1` 沿用当前 A_full offline critic/actor；`A2` 使用正向保留、负向修复、tradeoff 分层的 actor-facing reward，并继续执行 locked graph add-only contract。
- 资源口径：EarthBench 9B 只调度 GPU2-6，ports `8130-8134`，executor 为 `Qwen3.5-9B-local`，模型路径 `/data/xsy/codes/checkpoints/Qwen3.5-9B`，`max_model_len=49152`，eval concurrency `2`。GPU7 保留一条 8B 路，port `8135` 上仍保留 `Qwen3-8B-local` 服务。
- 当前运行项：`baseline train` on GPU2，`baseline test` on GPU4，`boot train` on GPU3，`boot test` on GPU5；GPU6 的 9B vLLM 已就绪，等待后续补位。
- 01:10 manifest 快照：`running=4`，`pending=0`，`completed=0`，`eval_failures=0`，`offline_records=0`，`offline_failures=0`。落盘进度：baseline train `11/52`，baseline test `8/196`，boot train `3/52`，boot test `8/196`。
- BOOT-V7 bootstrap skill 已生成：`/data/xsy/projects(25.12-26.2)/qs-work/project_skills-3.18dhc-19.40/runs/2026/5/2026-5-15/20260515_0058_earthbench_9b_bootv7_a1_a2_gpu2_6_keep8b7_bootv7_train52_bootstrap/bootstrap/skill_after_bootstrap/earth-bench-batch-skill/SKILL.md`。
- A1/A2 触发条件：`boot train` 完成后，从该 train source 生成 A1 与 A2 skill；对应 train/test eval 随 GPU2-6 空 lane 补位。
- 8B EarthBench 旧队列状态：`20260514_2122_earthbench_ab_gaia_locked_all8_accel` 已在 `2026-05-15T01:01:27+08:00` 收为 `finished_with_failures`，`pending=0`，`running=0`，`completed=11`，`eval_failures=3`。旧 A/B 结果只作为迁移排障记录，当前主线转到 9B。
- 当前 EarthBench 9B master log：`/data/xsy/projects(25.12-26.2)/qs-work/project_skills-3.18dhc-19.40/runs/_earthbench_9b_queue_logs/20260515_0058_earthbench_9b_bootv7_a1_a2_gpu2_6_keep8b7_master.log`。
- 当前 EarthBench 9B manifest：`/data/xsy/projects(25.12-26.2)/qs-work/project_skills-3.18dhc-19.40/runs/_earthbench_9b_queue_logs/20260515_0058_earthbench_9b_bootv7_a1_a2_gpu2_6_keep8b7_manifest.json`。
- 当前 EarthBench 9B summary：`/data/xsy/projects(25.12-26.2)/qs-work/project_skills-3.18dhc-19.40/runs/_earthbench_9b_queue_logs/20260515_0058_earthbench_9b_bootv7_a1_a2_gpu2_6_keep8b7_summary.json`，收口后生成最终 score 表。
- 队列脚本：`/data/xsy/projects(25.12-26.2)/qs-work/project_skills-3.18dhc-19.40/scripts/run_earthbench_9b_bootv7_a1_a2_gpu2_7_20260515.py`。本轮通过 `EARTHBENCH_9B_GPU_IDS=2,3,4,5,6` 与 `EARTHBENCH_9B_RESERVED_8B_GPUS=7` 固定资源边界。
- 资源备注：GPU0/1 不在 EarthBench 9B 调度范围；若这两张卡出现非 EarthBench GAIA 任务，占用归属应按对应 GAIA master log 单独核查。

2026-05-14 12:30 9B V7 切换启动：

- 当前有效前缀切换为：`20260514_1221_9b_bootv7_boot_afull_b1_r3_remote132_gpu0_5`；master PID `2085021` 正在运行。
- 目标矩阵：9B `BOOT-V7` 三重复，每个 repeat fresh boot source，同源扇出 `boot-only / A_full / B1`，dev/test，共 `18` 条 counted eval。
- 资源：GPU0-5，ports `8128-8133`，`Qwen3.5-9B-local` thinking vLLM；vLLM PID 为 `2085100/2085102/2085104/2085106/2085108/2085110`。
- 强模型：SSSAI `gpt-5.2`，`reasoning_effort=high`，用于 bootstrap actor、offline critic、offline actor。
- 首批实际进度：`BOOTV7 r1/r2/r3 boot-only dev` 已启动，PID `2092360/2092373/2092385`；三条 bootstrap skill 已生成。12:34-12:35 `BOOTV7 r1/r3/r2 boot-only test` 补到 GPU3/4/5，PID `2094237/2094414/2094577`，当前 `running=6`、`completed=0`、`failures=0`。
- 启动确认：`/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl/实验设计与迭代/26.5.14_1221_GAIA_9B_BOOTV7三重复六路启动记录.md`。
- master log：`/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl/runs/_queue_logs/20260514_1221_9b_bootv7_boot_afull_b1_r3_remote132_gpu0_5_master.log`。
- manifest：`/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl/runs/_queue_logs/20260514_1221_9b_bootv7_boot_afull_b1_r3_remote132_gpu0_5_manifest.json`。
- summary：`/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl/runs/_queue_logs/20260514_1221_9b_bootv7_boot_afull_b1_r3_remote132_gpu0_5_summary.json`。
- 清理记录：GPU0-5 原 `20260514_0112_8b_bootv7v8_v3v6_matrix_remote132_gpu0_5` master、vLLM、eval 长尾已按 9B 切换需求停止；GPU6/7 上其它 8B 负载保留。
- Serper：`configs/search_runtime.json` 已切到 `e29b85...42a6` 主 key，活动 key 池 `16` 个，最近核查正余额总量 `33279` credits。

2026-05-14 13:32 9B V7 下游续跑补位：

- 触发原因：1221 前缀的 boot_dev 三个 repeat 停在 `79/83` 附近，旧 master 仍按 `partial_source_min_states=80` 等待；GPU2/3/4 出现显存保留但利用率接近 `0%`。
- 可用读数：`r3 boot_test` 已落盘 `81/82`，score `34/82`，return code `1`；`r1/r2 boot_test` 各约 `80/82`。尾部主因是 executor streaming 空响应和超长 prompt token guard。
- 代码修正：`scripts/run_gaia_8b_bootv7v8_v3v6_matrix_remote132_20260514.py` 已允许 longtail-ready source 直接进入下游，后续启动生效。
- 续跑前缀：`20260514_1329_9b_bootv7_downstream_resume_from_1221_gpu2_4`；master PID `2109474`，已在 13:36 左右停止，原因是 `A_full r1` actor 把 V7 伪完全图 `Next:` 边退回线性链，触发 graph lock violation。
- graphfix：`gaia_skillrl/actor.py` 已加 locked graph 自动恢复逻辑；phase order 未变时，自动按原 graph signature 恢复 `Next:` 行，保留 actor 文本改写。
- 当前续跑前缀：`20260514_1338_9b_bootv7_downstream_resume_graphfix_from_1221_gpu2_4`；master PID `2111244`。
- 续跑范围：复用 1221 前缀的 `bootv7_r1/r2/r3` partial source 与 boot skill，只跑 `A_full/B1` 的 dev/test，共 `12` 条 counted eval。
- 续跑资源：GPU2/3/4，ports `8130-8132`，SSSAI `gpt-5.2 high` 做 offline critic/actor。
- 当前续跑日志：`/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl/runs/_queue_logs/20260514_1338_9b_bootv7_downstream_resume_graphfix_from_1221_gpu2_4_master.log`。
- 当前续跑 manifest：`/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl/runs/_queue_logs/20260514_1338_9b_bootv7_downstream_resume_graphfix_from_1221_gpu2_4_manifest.json`。
- 13:40 快照：`A_full r1` offline skill 已生成，graph auto-restore 生效，`A_full r1 dev/test` 已排队等待 eval 补位。
- 13:44 快照：`A_full r1` 与 `A_full r2` offline skill 已生成；GPU2/3/4 已开始跑 `A_full r1 dev/test` 与 `A_full r2 dev`，当前 running `3`、pending `1`、failures `0`。
- 15:47 skill 结构核查：本轮 BOOTV7 三次 repeat 各有一份 boot skill；A_full 三次 repeat 各有一份 actor 后 skill；同 repeat 内 dev/test 共用同一份 skill。A_full graph policy 为 `locked`，最终拓扑与同 repeat boot skill 一致。报告：`/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl/实验设计与迭代/26.5.14_1547_GAIA_9B_BOOTV7_skill结构与AFull图锁核查.md`。
- 后续口径修正：同类复验应固定 skill 三重复。先挑一个优质 source 生成 boot skill，再用同一 boot skill 跑三次 repeat；A_full 同理，先生成一份 A_full skill，再用同一 A_full skill 跑三次 repeat。repeat 只统计 executor/eval 方差，避免把 skill 生成差异混入稳定性判断。

2026-05-14 16:45 9B V7 r1 固定 skill 补跑：

- 新前缀：`20260514_1635_9b_bootv7_r1fixed_bestsource_afull_gpu0_5`；master PID `2169243`。
- 目的：固定 1221 前缀的 `r1 boot skill`，补两次 boot-only dev/test；把原 r1 与两次补跑组成三次固定 skill boot 重复；选择总分最高的 dev source 作为 A_full creator 输入；生成一份 A_full skill 后再跑三次 dev/test。新增 counted eval 共 `10` 条。
- 资源：复用 GPU0-5 上已有 9B vLLM，ports `8128-8133`；启动时 GPU0-3 承载四条 `boot_fixed` eval，GPU4-5 预留给后续 A_full 固定 skill 重复补位。
- 已启动四条 boot_fixed：`r1skill_bootrep2_dev` PID `2169929` on GPU0；`r1skill_bootrep2_test` PID `2169954` on GPU1；`r1skill_bootrep3_dev` PID `2170239` on GPU2；`r1skill_bootrep3_test` PID `2170420` on GPU3。
- 启动命令：`nohup setsid .venv/bin/python -u scripts/run_gaia_9b_bootv7_r1fixed_bestsource_afull_20260514.py > runs/_queue_logs/20260514_1635_9b_bootv7_r1fixed_bestsource_afull_gpu0_5_outer.log 2>&1 < /dev/null &`
- 实际日志：`/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl/runs/_queue_logs/20260514_1635_9b_bootv7_r1fixed_bestsource_afull_gpu0_5_outer.log`；`master.log` 已软链到该文件。
- manifest：`/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl/runs/_queue_logs/20260514_1635_9b_bootv7_r1fixed_bestsource_afull_gpu0_5_manifest.json`。
- summary：`/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl/runs/_queue_logs/20260514_1635_9b_bootv7_r1fixed_bestsource_afull_gpu0_5_summary.json`，收口后生成。

2026-05-14 11:11 验收快照：

- 当前有效前缀：`20260514_0112_8b_bootv7v8_v3v6_matrix_remote132_gpu0_5`；master PID `1954070` 仍在运行，manifest 状态 `running`，`updated_at=2026-05-14T11:11:10+08:00`。
- 当前 summary 与执行记录文档尚未生成；manifest 已登记 `expected_counted_eval_count=72`，当前阶段已登记 counted specs `36`，`completed=30`，`running=6`，`pending=0`，`eval_failures=15`，`released_after_longtail=11`。
- 已完成组内均值：`BOOT-V7 boot-only dev=22.7/83`、`BOOT-V7 boot-only test=16.0/82`、`BOOT-V7 A_full dev=23.3/83`、`BOOT-V7 A_full test=16.0/82`、`BOOT-V7 B1 dev=19.7/83`、`BOOT-V7 B1 test` 当前已有 `12/82,17/82`；V7 的 A_full 只在 dev 上小幅抬升，test 无明显收益，B1 低于 boot-only。
- `BOOT-V8` 已完成 boot-only 三次：dev `14/83,18/83,21/83`，test `15/82,16/82,21/82`；整体低于 V7 boot-only 和历史较好 8B 结果，当前不宜作为主线候选。
- stage 2 的 `BOOT-V3/BOOT-V6` 正在补：已完成 `BOOT-V3 r1 dev/test=20/83,21/82`、`BOOT-V3 r2 test=14/82`，`BOOT-V6 r1 dev/test=13/83,7/82`、`BOOT-V6 r2 dev/test=19/83,13/82`。实时未收口项：`BOOT-V3 r2 dev=18/83 landed 82/83` 长尾释放，`BOOT-V3 r3 dev=21/83 landed 81/83`，`BOOT-V3 r3 test=17/82 landed 81/82`，`BOOT-V6 r3 dev=4/83 landed 31/83`，`BOOT-V6 r3 test=5/82 landed 28/82`。
- GPU 观察：GPU0-3 已分配给当前 GAIA eval；11:12 左右瞬时利用率显示 GPU2/3 满载，GPU0/1 可能在工具执行或等待阶段，GPU4/5 仅保留 vLLM 显存且利用率接近 `0%`。原因是当前脚本 `BOOT_WORKERS=2` 且 stage 2 仍在等待 `bootv3/bootv6` boot pipeline 收口，当前没有分配到 GPU4/5 的 eval；端口服务本身未见异常。GPU6/7 被 EarthBench 进程占用，不属于本 GAIA 前缀。
- 长尾风险：`bootv7_r2_B1_test` 和 `bootv3_r2_boot_dev` 已长尾释放但进程仍活；其中 `bootv7_r2_B1_test` 下有 `tool_snippet.py` CPU 长跑，不占新增 GPU lane。按长尾补位口径，当前先保留自然完成，除非后续确认影响 CPU 或输出收口。
- 72 行验收 log：`/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl/实验设计与迭代/26.5.14_20260514_0112_GAIA_8B_V7V8_V3V6_72行验收log.md`。
- 调度修正：`scripts/run_gaia_8b_bootv7v8_v3v6_matrix_remote132_20260514.py` 的默认 `BOOT_WORKERS` 已从 `2` 调到 `6`，`BOOTSTRAP_WORKERS` 仍保持 `2`，并将 partial source 默认 stall 从 `3600s` 调到 `900s` 且 `longtail_ready` 可直接进入下游；后续重启时 boot pipeline 可先排满 lane，同时继续限制强模型 bootstrap 并发。当前已启动 master 仍按启动时参数运行。

2026-05-14 12:09 9B 回顾落稿：

- 新增总结文档：`/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl/实验设计与迭代/26.5.14_1209_GAIA_9B实验回顾与强模型对齐调参建议.md`。
- 口径调整：早期四卡/四分片 9B direct 结果只保留为异常参考，当前 baseline 中心采用后续复跑 dev `35/83`、test `32-33/82`。
- 强模型锚点：SSSAI `gpt-5.2` no-reasoning direct 为 dev `38/83`、test `37/82`、total `75/165`；9B 后续若 test 稳定在 `36-38/82`，就已经接近该 paid baseline。
- 9B 后续优先线：`BOOT-V3/V7`，重点复验 `BOOT-V3 B1 38/82`，并把 V7 伪完全图路由迁到 9B；旧 `B_sharded 39/82` 作为结构来源和 best-run 参考，暂不单独当稳定收益。

2026-05-14 00:55 准备更新：

- 已准备 `BOOT-V7 / BOOT-V8 / BOOT-V3 / BOOT-V6` 8B 镜像矩阵脚本，尚未启动后台实验。
- `BOOT-V7 = BOOT-V3 + 伪完全图路由约束`；`BOOT-V8 = BOOT-V6 + 伪完全图路由约束`。
- V7/V8 的伪完全图规则：`INIT` 连通所有非 `INIT` phase，非 `INIT` phase 之间互通，非 `INIT` phase 不回 `INIT`，无自跳。
- boot、critic、actor 统一走 SSSAI `gpt-5.2` / `responses_sse` / `reasoning_effort=high`；executor 使用 remote132 GPU0-5 六路 `Qwen3-8B-local` thinking，ports `8128-8133`。
- 待启动矩阵：stage 1 先跑 V7/V8，stage 2 再跑 V3/V6；每个版本 `boot-only / A_full / B1`，dev/test，3 repeat，总 counted eval `72`。
- 准备说明：`/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl/实验设计与迭代/26.5.14_0055_GAIA_8B_BOOTV7V8_V3V6伪完全图矩阵准备.md`
- 队列脚本：`/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl/scripts/run_gaia_8b_bootv7v8_v3v6_matrix_remote132_20260514.py`

2026-05-13 上午验收更新：

- `20260513_0125_8b_fullmatrix_reuseboot_remote132` 已按 master/summary 口径收口：`2026-05-13 01:21:55 +0800` 启动，`09:25:02 +0800` 收口，summary 状态 `finished_with_eval_failures`。
- counted eval spec 已登记 `66/66`，`pending=0`，`completed=60`，`released_after_longtail=25`，`eval_failures=28`，`offline_records=24`，`offline_failures=0`。
- manifest 仍显示 `running`，原因是 watcher 还在续写，并保留 `6` 个 running 条目；`11:11` 复查实际仍活的是 `3` 个 eval 进程与 `1` 个 `tool_snippet.py` 子进程，8 张卡 GPU util 均为 `0%`，这些残留不影响本轮验收读数。
- 人工验收说明已落在：`/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl/实验设计与迭代/26.5.13_20260513_0125_8b_fullmatrix_reuseboot_remote132_GAIA_8B全矩阵夜跑执行与5月13日计划.md`
- 方向结论：`A_full` 在 8B 全矩阵里没有形成 test 收益；下一轮优先看 `B1/B2`，其中 `BOOT-V3 B1` test 均值最高，`BOOT-V4 B2` test 稳定性最好。

旧索引记录：`20260513_0024_8b_bootv3v4_afull_local132` 曾作为四卡补位 active queue，已在 `2026-05-13 01:18 +0800` 停止并由 `20260513_0125_8b_fullmatrix_reuseboot_remote132` 全矩阵夜跑取代；`20260512_2334_8b_bootv3v4_afull_remote132` 已按用户要求停止，manifest 状态为 `stopped_by_user`。`20260512_1811_gpt52_sssai_baseline_c2_seq` 属 SSSAI `gpt-5.2` no-skill direct baseline 补充项，本次 `11:07` 复查未在 queue logs、run 目录和进程表中找到对应 artifact 或活进程；若要验收该项，需要后续单独恢复线索。`20260512_1012_9b_bootv6_longrules_afull` 已收尾，状态为 `finished_with_eval_failures`；`20260512_0010_9b_bootv5_graphfree_step24_48_plus_step48` 已按用户要求停止，停止时间 `2026-05-12T09:19:02+08:00`，manifest 状态为 `stopped_by_user`。

8B remote132 BOOT-V3/V4 A_full 四卡补位旧记录：

- prefix：`20260513_0024_8b_bootv3v4_afull_local132`
- 资源：remote132 GPU0-3，ports `8128-8131`，`Qwen3-8B-local`，`gpu_memory_utilization=0.90`，`max_num_seqs=48`
- 调度：`BOOT-V4 boot_dev/test` 与 `BOOT-V3 boot_dev/test` 先占四路；各自 dev source 完成或达到 `>=80/83` 长尾门槛后生成 `A_full skill`，再按空 lane 补 `A_full_dev/test`
- Codex：bootstrap actor、offline critic、offline actor 均固定 `gpt-5.4 / codex_cli / model_reasoning_effort=xhigh`，远端代理 `http://127.0.0.1:17890`
- 启动确认：`/data/xsy/project_gaia_skillrl/实验设计与迭代/26.5.13_0024_GAIA_8B_BOOTV3V4_Afull_local132重发.md`
- master PID：`1540217`
- 初始 eval：`BOOT-V4 boot_dev` PID `1540292` on GPU0/8128；`BOOT-V3 boot_dev` PID `1540293` on GPU2/8130；两条 bootstrap Codex 调用均为 `gpt-5.4/xhigh`
- vLLM PID：GPU0/8128 `1523893`，GPU1/8129 `1523895`，GPU2/8130 `1523897`，GPU3/8131 `1523899`
- manifest：`/data/xsy/project_gaia_skillrl/runs/_queue_logs/20260513_0024_8b_bootv3v4_afull_local132_manifest.json`
- summary：`/data/xsy/project_gaia_skillrl/runs/_queue_logs/20260513_0024_8b_bootv3v4_afull_local132_summary.json`，master 结束后生成
- outer log：`/data/xsy/project_gaia_skillrl/runs/_queue_logs/20260513_0024_8b_bootv3v4_afull_local132_outer.log`

上一轮 8B remote132 重发前停止记录：

- prefix：`20260512_2334_8b_bootv3v4_afull_remote132`
- 停止时间：2026-05-13 00:23 +0800
- 处置：master `1528016`、eval `1528090`、`1528091`、`1537841` 与对应 Codex 子进程已停止；GPU0-3 vLLM 保留复用。
- manifest：`/data/xsy/project_gaia_skillrl/runs/_queue_logs/20260512_2334_8b_bootv3v4_afull_remote132_manifest.json`
- summary：`/data/xsy/project_gaia_skillrl/runs/_queue_logs/20260512_2334_8b_bootv3v4_afull_remote132_summary.json`

本批次结论：

- `BOOT-V5 graph-free` 标记为 `bad / rejected`，不进入下一轮主线。
- step48 方向暂停；当前证据显示 step48 放大长轨迹、forced answer 和工具失败，未稳定超过 baseline。
- 后续回到 `BOOT-V4` 与 `max_executor_steps=24`，继续沿 `BOOT-V4 B2 / A_full` 做 critic/actor 输入和小 patch。
- 已完成可比结果：`baseline step48 test=35/82`，`BOOT-V5 step24 boot dev=36/83`，`BOOT-V5 step24 boot test=32/82`，`BOOT-V5 step48 boot test=31/82`。

V6 队列最终状态：

- queue prefix：`20260512_1012_9b_bootv6_longrules_afull`
- background PID：`2997610`；Python master PID：`2997611`
- `r1_boot_test` 已完成：`35/82`
- `r1_boot_dev` 已从 `79/83` 释放到 `80/83`，当前计分 `39/83`，缺失 `3`；PID `2997690` 已在 `2026-05-12 15:42 +0800` 被 master 记录为 `code=-15`
- A_full offline actor 已完成，run dir 为 `/data/xsy/project_gaia_skillrl/runs/2026/5/2026-5-12/20260512_101248_9b_bootv6_longrules_afull_9b_bootv6_longrules_s1_A_full_offline_actor_iter1`；partial source 接受 `80/83`，缺失 `cffe0e32-c9a6-4c52-9877-78ceb4aaa9fb`、`9b54f9d9-35ee-4a14-b62f-d130ea00317f`、`9f41b083-683e-4dcf-9185-ccfeaa88fa45`
- 最终状态：`finished_with_eval_failures`，summary 更新时间 `2026-05-12T18:37:12+08:00`
- `r1_A_full_test` 在 `2026-05-12 18:31 +0800` 触发 partial stop，落盘 `80/82`，计分 `36/82`，缺失 `2`，returncode `-15`
- `r1_A_full_dev` 在 `2026-05-12 18:35 +0800` 触发 partial stop，落盘 `82/83`，计分 `40/83`，缺失 `1`，returncode `-15`
- 本轮四项最终分数：boot dev `39/83`（落盘 `80/83`，人为释放后 code `-15`）、boot test `35/82`、A_full dev `40/83`、A_full test `36/82`
- 运行中观察：`ec09fa32-d03f-4bf8-84b0-1f16922c3ae4` 的 `tool_snippet.py` 生成超大 Monte Carlo，连续卡住 dev；已写入长期规则和本轮实验文档，后续 GAIA 队列要显式设置/确认单题与工具 wall-time cap
- master log：`/data/xsy/project_gaia_skillrl/runs/_queue_logs/20260512_1012_9b_bootv6_longrules_afull_master.log`
- manifest：`/data/xsy/project_gaia_skillrl/runs/_queue_logs/20260512_1012_9b_bootv6_longrules_afull_manifest.json`
- 启动确认：`/data/xsy/project_gaia_skillrl/实验设计与迭代/26.5.12_0007_GAIA_5月12日BOOT输入与实验验收计划.md`

SSSAI `gpt-5.2` direct baseline c2 补充验收：

- prefix：`20260512_1811_gpt52_sssai_baseline_c2_seq`
- 调度：dev83 完成后再启动 validation_test82；总 SSSAI executor API 并发上限 `2`，避免 dev/test 同时各自占并发。
- master PID：`3111187`；当前 active split 为 dev，dev eval PID `3111194`。
- dev run：`20260512_1811_gpt52_sssai_baseline_c2_seq_gpt52_sssai_baseline_dev83_c2`；run dir `/data/xsy/project_gaia_skillrl/runs/2026/5/2026-5-12/20260512_181155_gpt52_sssai_baseline_c2_seq_gpt52_sssai_baseline_dev83_c2`
- 早期核对：`config_snapshot.json` 确认为 SSSAI `gpt-5.2` / `responses_sse` / `reasoning_effort=xhigh` / step24 / any_phase / atomic_v2 / task concurrency 2。
- 截至 `2026-05-12 20:32 +0800`，dev running，已落盘 `35/83`，计分 `18/83`；test 尚未启动。
- 连接/服务状态：18:51 后出现一次 SSSAI `Responses-SSE` 连续 8 次 `服务器错误，请稍后重试`，该 LLM call 失败；随后出现 `The read operation timed out` retry、若干 PDF 解析失败警告。进程未死，但外部 API 在低并发 `2` 下仍有明显尾延迟和服务端错误，不适合直接放大并发。
- summary：`/data/xsy/project_gaia_skillrl/runs/_queue_logs/20260512_1811_gpt52_sssai_baseline_c2_seq_master_summary.json`
- 启动确认：`/data/xsy/project_gaia_skillrl/实验设计与迭代/26.5.12_1811_GAIA_SSSAI_GPT52_baseline_c2顺序devtest.md`

## 当前批次总览

当前批次原计划查收 `14` 个逻辑实验；队列已停止，未完成项保留为历史索引：

- baseline step48：`2` 个，含当前卡上进行的 baseline step48 dev split，以及队列后续 baseline step48 test。
- BOOT-V4 step48：`4` 个，boot dev/test 与同源 A_full dev/test。
- BOOT-V5 graph-free boot-only：`4` 个，step24 dev/test 与 step48 dev/test。
- BOOT-V5 graph-free -> A_full：`4` 个，step24 A_full dev/test 与 step48 A_full dev/test。

已停止队列：

- queue prefix：`20260512_0010_9b_bootv5_graphfree_step24_48_plus_step48`
- master PID：`2943160`，已停止
- master log：`/data/xsy/project_gaia_skillrl/runs/_queue_logs/20260512_0010_9b_bootv5_graphfree_step24_48_plus_step48_master.log`
- manifest：`/data/xsy/project_gaia_skillrl/runs/_queue_logs/20260512_0010_9b_bootv5_graphfree_step24_48_plus_step48_manifest.json`
- summary：`/data/xsy/project_gaia_skillrl/runs/_queue_logs/20260512_0010_9b_bootv5_graphfree_step24_48_plus_step48_summary.json`，未生成
- 启动确认与 V4/V5 对比：`/data/xsy/project_gaia_skillrl/实验设计与迭代/26.5.11_2359_GAIA_BOOTV5_graph_free_step24_step48启动与V4对比.md`
- step48 追加确认：`/data/xsy/project_gaia_skillrl/实验设计与迭代/26.5.11_1410_GAIA_9B_BOOTV4_Afull三次复验结论与放大方向.md`

## 逻辑分类清单

### 1. Baseline Step48

验收目的：确认把 executor 步数从 24 放到 48 后，direct baseline 的 dev/test 上限和长尾行为。

1. `baseline step48 dev`
   - 状态：已在卡上运行，dev split 两半合并验收，算 1 个逻辑实验。
   - half1 run：`20260511_2332_9b_baseline_step48_devsplit_baseline_step48_dev_half1_c20_gpu0`
   - half2 run：`20260511_2332_9b_baseline_step48_devsplit_baseline_step48_dev_half2_c20_gpu1`
   - manifest：`/data/xsy/project_gaia_skillrl/runs/_queue_logs/20260511_2332_9b_baseline_step48_devsplit_master_summary.json`
   - 口径：合并 83 道 dev；分母 `83`；缺失按错；step `48`。

2. `baseline step48 test`
   - 状态：已排入当前 queue，等待 lane 补位。
   - run：`20260512_0010_9b_bootv5_graphfree_step24_48_plus_step48_step48_9b_baseline_test_c20`
   - 口径：validation_test 分母 `82`；step `48`；direct-eval-local；无 skill。

### 2. BOOT-V4 Step48

验收目的：用 BOOT-V4 现有 prompt/input 口径，单独检查 step48 下 boot-only 与 A_full 的 dev/test 表现。

3. `BOOT-V4 step48 boot dev`
   - run：`20260512_0010_9b_bootv5_graphfree_step24_48_plus_step48_step48_9b_bootv4_boot_dev_c20`
   - skill：该 run 的 `bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md`
   - 口径：dev 分母 `83`；step `48`；BOOT-V4 preprocess note。

4. `BOOT-V4 step48 boot test`
   - run：`20260512_0010_9b_bootv5_graphfree_step24_48_plus_step48_step48_9b_bootv4_boot_test_c20`
   - skill：同源 `BOOT-V4 step48 boot dev` 生成的 boot skill。
   - 口径：validation_test 分母 `82`；step `48`。

5. `BOOT-V4 step48 A_full dev`
   - run：`20260512_0010_9b_bootv5_graphfree_step24_48_plus_step48_step48_9b_bootv4_A_full_dev_eval_c20`
   - skill：由 `BOOT-V4 step48 boot dev` states 经 A_full offline critic/actor 生成。
   - 口径：dev 分母 `83`；step `48`。

6. `BOOT-V4 step48 A_full test`
   - run：`20260512_0010_9b_bootv5_graphfree_step24_48_plus_step48_step48_9b_bootv4_A_full_test_eval_c20`
   - skill：同源 `BOOT-V4 step48 A_full dev` 使用的 A_full skill。
   - 口径：validation_test 分母 `82`；step `48`。

### 3. BOOT-V5 Graph-Free Boot-Only

验收目的：题目端输入沿用 BOOT-V4 preprocess note，skill 图生成放开，只保留 no-CONCLUDE、any-phase answer、节点字段和 executor 固有约束，比较 step24/step48 boot-only 表现。

7. `BOOT-V5 step24 boot dev`
   - 状态：已启动。
   - run：`20260512_0010_9b_bootv5_graphfree_step24_48_plus_step48_v5_step24_9b_bootv5_boot_dev_c20`
   - run dir：`/data/xsy/project_gaia_skillrl/runs/2026/5/2026-5-12/20260512_001053_9b_bootv5_graphfree_step24_48_plus_step48_v5_step24_9b_bootv5_boot_dev_c20`
   - 口径：dev 分母 `83`；step `24`；prompt root `prompts/boot_v5_graph_free`。

8. `BOOT-V5 step24 boot test`
   - run：`20260512_0010_9b_bootv5_graphfree_step24_48_plus_step48_v5_step24_9b_bootv5_boot_test_c20`
   - skill：同源 `BOOT-V5 step24 boot dev` 生成的 boot skill。
   - 口径：validation_test 分母 `82`；step `24`。

9. `BOOT-V5 step48 boot dev`
   - run：`20260512_0010_9b_bootv5_graphfree_step24_48_plus_step48_v5_step48_9b_bootv5_boot_dev_c20`
   - 口径：dev 分母 `83`；step `48`；prompt root `prompts/boot_v5_graph_free`。

10. `BOOT-V5 step48 boot test`
    - run：`20260512_0010_9b_bootv5_graphfree_step24_48_plus_step48_v5_step48_9b_bootv5_boot_test_c20`
    - skill：同源 `BOOT-V5 step48 boot dev` 生成的 boot skill。
    - 口径：validation_test 分母 `82`；step `48`。

### 4. BOOT-V5 Graph-Free -> A_full

验收目的：在 V5 graph-free boot skill 后接同源 A_full，检查 A 对 V5 起点的放大效果；step24 后接 step24 dev/test，step48 后接 step48 dev/test。

11. `BOOT-V5 step24 A_full dev`
    - run：`20260512_0010_9b_bootv5_graphfree_step24_48_plus_step48_v5_Afull_step24_9b_bootv5_A_full_dev_eval_c20`
    - skill：由 `BOOT-V5 step24 boot dev` states 经 A_full offline critic/actor 生成。
    - 口径：dev 分母 `83`；step `24`。

12. `BOOT-V5 step24 A_full test`
    - run：`20260512_0010_9b_bootv5_graphfree_step24_48_plus_step48_v5_Afull_step24_9b_bootv5_A_full_test_eval_c20`
    - skill：同源 `BOOT-V5 step24 A_full dev` 使用的 A_full skill。
    - 口径：validation_test 分母 `82`；step `24`。

13. `BOOT-V5 step48 A_full dev`
    - run：`20260512_0010_9b_bootv5_graphfree_step24_48_plus_step48_v5_Afull_step48_9b_bootv5_A_full_dev_eval_c20`
    - skill：由 `BOOT-V5 step48 boot dev` states 经 A_full offline critic/actor 生成。
    - 口径：dev 分母 `83`；step `48`。

14. `BOOT-V5 step48 A_full test`
    - run：`20260512_0010_9b_bootv5_graphfree_step24_48_plus_step48_v5_Afull_step48_9b_bootv5_A_full_test_eval_c20`
    - skill：同源 `BOOT-V5 step48 A_full dev` 使用的 A_full skill。
    - 口径：validation_test 分母 `82`；step `48`。

## 统一验收口径

- dev 分母统一 `83`，validation_test 分母统一 `82`。
- counted score：`task_success` 求和；缺失 state 计错。
- 每个 run 记录落盘数、缺失数、空 final、hit max step 数、forced answer 数与正确率、token guard 题数、web/fetch fail ratio。
- step24 与 step48 分开比较；同 step 内比较 baseline、boot-only、A_full。
- BOOT-V5 额外检查 bootstrap skill：phase 数、是否含 `CONCLUDE`、是否含 answer-only/finalization-only 节点、`metadata.version` 是否为 `boot-v5`、prompt root 是否为 `/data/xsy/project_gaia_skillrl/prompts/boot_v5_graph_free`。
- A_full 额外检查 offline critic/actor 产物：critic request/response、actor request/response、`skill_after_actor` 路径、source boot states 数量。

## 旧项处理

旧的 no-meta-answer 复跑、误跑记录、BOOT-V4 stability2 已从当前待验收区清掉。需要追溯时看：

- `/data/xsy/project_gaia_skillrl/实验设计与迭代/26.5.11_2221_GAIA_BOOTV4_r1_no_meta_answer_skill_dev复跑.md`
- `/data/xsy/project_gaia_skillrl/实验设计与迭代/26.5.11_2255_GAIA_BOOTV4_r1_Afull_no_meta_answer_skill_dev复跑.md`
- `/data/xsy/project_gaia_skillrl/实验设计与迭代/26.5.11_1410_GAIA_9B_BOOTV4_Afull三次复验结论与放大方向.md`

## 2026-05-13 8B BOOT-V5/V6 全矩阵启动记录

- prefix：`20260513_1417_8b_bootv5v6_fullmatrix_remote132`
- master PID：`1756545`
- master log：`/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl/runs/_queue_logs/20260513_1417_8b_bootv5v6_fullmatrix_remote132_master.log`
- manifest：`/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl/runs/_queue_logs/20260513_1417_8b_bootv5v6_fullmatrix_remote132_manifest.json`
- summary：`/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl/runs/_queue_logs/20260513_1417_8b_bootv5v6_fullmatrix_remote132_summary.json`
- 调度脚本：`/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl/scripts/run_gaia_8b_bootv5v6_full_matrix_remote132_20260513.py`
- 目标 eval：`60` 条，即 `2` 个 boot 版本 x `3` repeat x `5` methods x dev/test。
- 优先级：先启动 BOOT-V5/BOOT-V6 三个 repeat 的 boot-only dev/test；boot-only 收口后按 `A_full -> B1 -> B2 -> B3` 分层推进。
- executor：8 个 Qwen3-8B thinking endpoint，端口 `8128-8135`，每条 eval `c20`。
- vLLM PID：`8128=1750868`，`8129=1750869`，`8130=1750870`，`8131=1750871`，`8132=1750872`，`8133=1750873`，`8134=1750874`，`8135=1750875`。
- 说明：GPU4 仍有异账号 `deploy.py --port 8090` 占用约 `2.6GB`，当前账号无权限停止；GPU4 的 8B vLLM 因此按 `gpu_memory_utilization=0.86` 启动，其余卡为 `0.90`。
- 首批已启动：`bootv5_r1/r2/r3` 与 `bootv6_r1/r2/r3` 的 dev bootstrap，各自使用对应 config：`system_bootv5_graph_free.json` 与 `system_bootv6_long_rules.json`。

### 2026-05-13 15:19 修复后重启

- 旧前缀处理：`20260513_1417_8b_bootv5v6_fullmatrix_remote132` 因 6 路 Codex bootstrap 同时发起，触发 `Selected model is at capacity`，已停止；`20260513_1456_8b_bootv5v6_fullmatrix_remote132_bootc2` 与 `20260513_1512_8b_bootv5v6_fullmatrix_remote132_fixretry` 为修复验证前缀，已停止。
- 代码修复：
  - `gaia_skillrl/llm.py`：Codex CLI 对 capacity、429/5xx、stream disconnected 等短时失败最多重试 `5` 次。
  - `gaia_skillrl/bootstrap.py`：bootstrap preprocess 少量缺失 task note 时写出 `bootstrap_task_preprocess_missing.json`，缺失任务回退原始 payload，整条 boot 继续。
  - `scripts/run_gaia_8b_bootv5v6_full_matrix_remote132_20260513.py`：新增 `BOOTSTRAP_WORKERS=2` semaphore，只限制同时生成 boot skill 的 Codex 数；boot pipeline worker 仍为 `6`，skill 生成后释放 bootstrap 名额，后续 eval 可继续补满 GPU。
- 当前有效前缀：`20260513_1519_8b_bootv5v6_fullmatrix_remote132_retry_sem`
- master PID：`1774730`
- master log：`/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl/runs/_queue_logs/20260513_1519_8b_bootv5v6_fullmatrix_remote132_retry_sem_master.log`
- manifest：`/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl/runs/_queue_logs/20260513_1519_8b_bootv5v6_fullmatrix_remote132_retry_sem_manifest.json`
- summary：`/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl/runs/_queue_logs/20260513_1519_8b_bootv5v6_fullmatrix_remote132_retry_sem_summary.json`
- 启动参数：`BOOT_WORKERS=6`，`BOOTSTRAP_WORKERS=2`，`OFFLINE_WORKERS=4`，`NLRL_CODEX_CLI_RETRIES=5`，eval 并发 `c20`。
- 当前状态：`bootv5_r1` 与 `bootv6_r1` 正在 bootstrap skill 阶段；该阶段主要等待 Codex，vLLM/GPU 利用率会偏低，skill 产出后 dev/test eval 会开始打 8128-8135。

### 2026-05-13 17:08 V5 downstream 取消与 V6 接力

- 决策：BOOT-V5 后续 `A_full/B1/B2/B3` 取消，只保留 `3 repeat x dev/test = 6` 条 boot-only；BOOT-V6 继续完整 `boot-only + A_full/B1/B2/B3`。
- 新计数口径：`36` 条 counted eval，即 V5 `6` 条 + V6 `30` 条；相比原 `60` 条，减少 V5 downstream `24` 条。
- 代码调整：`scripts/run_gaia_8b_bootv5v6_full_matrix_remote132_20260513.py` 新增 `GAIA_8B_BOOTV5V6_FULL_MATRIX_DOWNSTREAM_BOOT_KEYS`，默认只让 `bootv6` 进入 downstream。
- 旧 master 处理：`20260513_1519_8b_bootv5v6_fullmatrix_remote132_retry_sem` 的 master PID `1774730` 已在进入 downstream 前停止；当时 `offline_records=0`，没有登记 V5 downstream。
- 保留自然完成的 boot-only 子进程：`bootv5_r2/r3` dev、`bootv6_r3` dev、`bootv6_r3` test 等已启动任务继续自然收口。
- V6 接力前缀：`20260513_1713_8b_bootv6_downstream_from_1519`
- V6 接力 PID：`1800466`
- V6 接力 log：`/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl/runs/_queue_logs/20260513_1713_8b_bootv6_downstream_from_1519_master.log`
- V6 接力 manifest：`/data/xsy/projects(25.12-26.2)/qs-work/project_gaia_skillrl/runs/_queue_logs/20260513_1713_8b_bootv6_downstream_from_1519_manifest.json`
- 启动命令：`nohup setsid env GAIA_8B_BOOTV6_CONT_PREFIX=20260513_1713_8b_bootv6_downstream_from_1519 GAIA_8B_BOOTV6_CONT_SOURCE_PREFIX=20260513_1519_8b_bootv5v6_fullmatrix_remote132_retry_sem GAIA_8B_BOOTV6_CONT_OLD_MASTER_PID=1774730 .venv/bin/python -u scripts/run_gaia_8b_bootv6_downstream_from_bootv5v6_1519_20260513.py > runs/_queue_logs/20260513_1713_8b_bootv6_downstream_from_1519_master.log 2>&1 < /dev/null &`
- 接力行为：先等待旧前缀 V6 boot-only 子进程清完，仍被 V5 boot-only 占用的 endpoint 会从接力可用 lane 中临时剔除；随后复用 V6 r1/r2/r3 的 boot_dev source 与 skill 发 `A_full -> B1 -> B2 -> B3`，不再触发任何 V5 downstream。
- 17:42 观察到 `bootv6_r1 A_full` offline actor 单次耗时 `557.9s`，`bootv6_r2 A_full` actor 超过 14 分钟仍在 Codex 子进程中；为节省 Codex 预算，17:xx 停止 V6 接力 master 与 r2/r3 后续 offline actor，只保留已经发出的 `bootv6_r1 A_full dev/test` 两条 eval 自然完成。
- 停止后状态：manifest 标记 `stopped_to_save_codex_budget`；仍在跑的 eval 为 `20260513_1713_8b_bootv6_downstream_from_1519_8b_bootv6_r1_A_full_dev_eval_c20` 与 `..._test_eval_c20`。
