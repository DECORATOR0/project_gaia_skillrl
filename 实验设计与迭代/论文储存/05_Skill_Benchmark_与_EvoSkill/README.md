# Skill Benchmark 与 EvoSkill 论文 PDF

整理时间：2026-05-06 +0800

本目录存放近期 benchmark 迁移、EvoSkill 对齐和 skill 论文讨论中直接用到的 PDF。

## 文件与来源

- `2026_EvoSkill_Automated_Skill_Discovery_for_Multi_Agent_Systems.pdf`
  - 来源：`https://arxiv.org/pdf/2603.02766`
  - 用途：EvoSkill 方法、OfficeQA/SealQA 实验切分、multi-tolerance reward 口径。

- `2026_OfficeQA_End_to_End_Grounded_Reasoning.pdf`
  - 来源：`https://arxiv.org/pdf/2603.08655`
  - 用途：OfficeQA benchmark 数据、scorer、PDF/TXT corpus 和 frontier model 评测口径。

- `2025_SealQA_Search_Augmented_Language_Model_Benchmark.pdf`
  - 来源：`https://arxiv.org/pdf/2506.01062`
  - 用途：SealQA / LongSeal 数据形态和 search-augmented QA 评测口径。

- `2025_BrowseComp_A_Simple_Yet_Challenging_Benchmark_for_Browsing_Agents.pdf`
  - 来源：`https://arxiv.org/pdf/2504.12516`
  - 用途：BrowseComp 任务定义、加密数据发布方式、browsing agent 评测口径。

- `2025_SkillsBench_Benchmarking_Agent_Skills.pdf`
  - 来源：`https://www.skillsbench.ai/skillsbench.pdf`
  - 用途：SkillsBench 任务、BenchFlow/Docker verifier 和 skill benchmark 评测设计。

## 本地约定

- 讨论中反复引用的论文优先落 PDF，不只保留 arXiv 链接或 txt 抽取文件。
- 文件名使用 `年份_短标题.pdf`，标题用英文下划线，避免空格路径。
- 论文摘要、实验口径和本地 benchmark 适配结论写到 `实验设计与迭代/*.md`；PDF 目录只保留原文和来源索引。
