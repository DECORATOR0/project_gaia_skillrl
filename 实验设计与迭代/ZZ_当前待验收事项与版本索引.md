# 当前待验收事项

更新时间：2026-05-10 21:38 +0800

用途：只保留当前需要验收的实验。历史 ACC 快照、旧版本索引和已失效队列已清理；需要追溯时回看对应日期实验文档。

## 当前待验收

当前有 `1` 个正在运行的后台 master，另有 `1` 个已收口的验收事项：

- `20260510_1230_9b_bootv4_b2_b3_recheck_r3`：6 个 counted eval，已按 partial 分数收口，旧长尾已清理。
- `20260510_2132_9b_bootv4_A_full_from_r3`：2 个 counted eval，A_full skill 已生成，dev/test 正在 GPU0/GPU1 运行。

## 1. BOOT-V4 -> B2/B3 复跑

- 发起时间：2026-05-10 12:29 +0800
- run prefix：`20260510_1230_9b_bootv4_b2_b3_recheck_r3`
- 补位 master PID：`1294092`，已于 2026-05-10 21:40 +0800 按用户清理要求停止
- 补位 master log：`/data/xsy/project_gaia_skillrl/runs/_queue_logs/20260510_1230_9b_bootv4_b2_b3_recheck_r3_fill_master.log`
- 补位 manifest：`/data/xsy/project_gaia_skillrl/runs/_queue_logs/20260510_1230_9b_bootv4_b2_b3_recheck_r3_fill_manifest.json`
- 详细记录：`/data/xsy/project_gaia_skillrl/实验设计与迭代/26.5.10_GAIA_9B_BOOTV4_B2调优起点与日志.md`

实验目的：重新复跑 `9B BOOT-V4 -> B2`，看今天 `dev/test` 分数稳定性；同时从同一份新 `BOOT-V4` source 并列接 `B3`，比较当前 B2 与 B3 哪个分支更高。

| 序号 | 待验收实验 | 数据集 | 当前分数 | 当前状态 |
| ---: | --- | --- | --- | --- |
| 1 | `9b_bootv4_recheck_boot_dev` | validation_dev83 | `38/83` | partial，落盘 `82/83` |
| 2 | `9b_bootv4_recheck_boot_test` | validation_test82 | `33/82` | finished |
| 3 | `9b_bootv4_recheck_B2_dev` | validation_dev83 | `40/83` | finished，落盘 `83/83` |
| 4 | `9b_bootv4_recheck_B2_test` | validation_test82 | `31/82` | partial，落盘 `81/82`，exit code `1` |
| 5 | `9b_bootv4_recheck_B3_dev` | validation_dev83 | `39/83` | partial，落盘 `82/83`，PID `1310766` 已停止 |
| 6 | `9b_bootv4_recheck_B3_test` | validation_test82 | `34/82` | partial，落盘 `81/82`，exit code `1` |

## 2. BOOT-V4 -> A_full 补跑

- 发起时间：2026-05-10 21:36 +0800
- run prefix：`20260510_2132_9b_bootv4_A_full_from_r3`
- master PID：`1603240`
- master log：`/data/xsy/project_gaia_skillrl/runs/_queue_logs/20260510_2132_9b_bootv4_A_full_from_r3_master.log`
- manifest：`/data/xsy/project_gaia_skillrl/runs/_queue_logs/20260510_2132_9b_bootv4_A_full_from_r3_manifest.json`
- summary：`/data/xsy/project_gaia_skillrl/runs/_queue_logs/20260510_2132_9b_bootv4_A_full_from_r3_summary.json`
- source run：`/data/xsy/project_gaia_skillrl/runs/2026/5/2026-5-10/20260510_123058_9b_bootv4_b2_b3_recheck_r3_9b_bootv4_recheck_boot_dev_c20`
- source skill：`/data/xsy/project_gaia_skillrl/runs/2026/5/2026-5-10/20260510_123058_9b_bootv4_b2_b3_recheck_r3_9b_bootv4_recheck_boot_dev_c20/bootstrap/skill_after_bootstrap/gaia-general-skill/SKILL.md`
- 详细记录：`/data/xsy/project_gaia_skillrl/实验设计与迭代/26.5.10_GAIA_9B_BOOTV4_B2调优起点与日志.md`

实验目的：从当前 r3 `BOOT-V4` boot source 出发，补跑历史 A 口径 `critic_strategy=full`、`actor_graph_edit_policy=locked`，看 `BOOT-V4 -> A_full` 的 dev/test 分数，和 B2/B3 横向比较。

| 序号 | 待验收实验 | 数据集 | 当前分数 | 当前状态 |
| ---: | --- | --- | --- | --- |
| 1 | `9b_bootv4_A_full_dev` | validation_dev83 | running | PID `1609752`，GPU0 / `8610`，日志 `/data/xsy/project_gaia_skillrl/runs/_launch_logs/20260510_2132_9b_bootv4_A_full_from_r3_9b_bootv4_A_full_dev_eval_c20_20260510_214158.log` |
| 2 | `9b_bootv4_A_full_test` | validation_test82 | running | PID `1609753`，GPU1 / `8611`，日志 `/data/xsy/project_gaia_skillrl/runs/_launch_logs/20260510_2132_9b_bootv4_A_full_from_r3_9b_bootv4_A_full_test_eval_c20_20260510_214158.log` |

## 验收口径

- counted eval 统一按 `正确/总数` 汇报：dev 分母 `83`，validation_test 分母 `82`。
- 若 eval 因长尾或 streaming 空响应提前退出，按已落盘 `state.json` 计分，缺失题按错题记，并标注 `partial`。
- 先看 boot-only 是否复现，再比较 B2、B3、A_full 的 dev/test。优先保留 test 更高且 dev 不明显回落的分支。
