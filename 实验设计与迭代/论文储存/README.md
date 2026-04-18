# 论文储存索引

当前目录用于存放本轮 `skill + state graph + actor/critic` 方向调研涉及的论文 PDF。

## 目录结构

### 01_状态机_有限状态控制器_行为树

这一类主要回答两个问题：

- 如何用显式状态机、部分策略、行为树去约束策略空间
- 如何在结构约束下继续做强化学习或策略改进

文件列表：

- `1998_Reinforcement_Learning_with_Hierarchies_of_Machines.pdf`
- `2017_Efficient_RL_with_Hierarchies_of_Machines_by_Leveraging_Internal_Transitions.pdf`
- `2021_Improving_the_Performance_of_Backward_Chained_Behavior_Trees_that_use_RL.pdf`
- `2023_A_Framework_for_Learning_Behavior_Trees_in_Collaborative_Robotic_Applications.pdf`
- `2026_Progress_Constraints_for_RL_in_Behavior_Trees.pdf`
- `2026_Finite_State_Controllers_for_Hidden_Model_POMDPs_using_Deep_RL.pdf`

### 02_层级强化学习_选项_技能

这一类主要回答三个问题：

- 如何把长程任务拆成 temporally extended skills / options
- 高层如何调度技能，低层如何执行
- 技能的终止条件、子目标、分层 credit assignment 如何处理

文件列表：

- `1999_Between_MDPs_and_Semi_MDPs_A_Framework_for_Temporal_Abstraction_in_RL.pdf`
- `2000_Hierarchical_RL_with_the_MAXQ_Value_Function_Decomposition.pdf`
- `2016_The_Option_Critic_Architecture.pdf`
- `2017_FeUdal_Networks_for_Hierarchical_RL.pdf`
- `2018_Hierarchical_Actor_Critic.pdf`
- `2018_Data_Efficient_Hierarchical_RL.pdf`
- `2018_Diversity_is_All_You_Need_Learning_Skills_without_a_Reward_Function.pdf`

### 03_LLM_Agent_技能库_自改进

这一类主要回答四个问题：

- LLM agent 如何和工具、技能库结合
- actor / critic / self-reflection 怎么形成闭环
- 技能如何从轨迹中沉淀、复用、写回
- RL 如何和 agent runtime 解耦

文件列表：

- `2022_Do_As_I_Can_Not_As_I_Say_Grounding_Language_in_Robotic_Affordances.pdf`
- `2023_Toolformer_Language_Models_Can_Teach_Themselves_to_Use_Tools.pdf`
- `2023_Reflexion_Language_Agents_with_Verbal_RL.pdf`
- `2023_ReAct_Synergizing_Reasoning_and_Acting_in_Language_Models.pdf`
- `2023_CRITIC_LLMs_Can_Self_Correct_with_Tool_Interactive_Critiquing.pdf`
- `2023_Voyager_An_Open_Ended_Embodied_Agent_with_LLMs.pdf`
- `2023_ExpeL_LLM_Agents_Are_Experiential_Learners.pdf`
- `2025_SkillWeaver_Web_Agents_can_Self_Improve_by_Discovering_and_Honing_Skills.pdf`
- `2025_Agent_Lightning_Train_ANY_AI_Agents_with_RL.pdf`
- `2025_Reinforcement_Learning_for_Self_Improving_Agent_with_Skill_Library.pdf`
- `2026_Tool_R0_Self_Evolving_LLM_Agents_for_Tool_Learning_from_Zero_Data.pdf`
- `2026_Demystifying_RL_for_Long_Horizon_Tool_Using_Agents.pdf`

### 04_Benchmark_与_GAIA

这一类主要回答两个问题：

- `GAIA` 的任务分布与评测形态
- 未来如果扩到更动态环境，状态图设计要怎么继续扩展

文件列表：

- `2023_GAIA_a_benchmark_for_General_AI_Assistants.pdf`
- `2026_Gaia2_Benchmarking_LLM_Agents_on_Dynamic_and_Asynchronous_Environments.pdf`

## 当前用途

本目录服务于下面这份调研文档：

- `../26.4.17_1016_GAIA上skill+状态图+actor_critic闭环方案调研.md`

建议的阅读顺序：

1. 先读 `04_Benchmark_与_GAIA`
2. 再读 `02_层级强化学习_选项_技能`
3. 然后读 `01_状态机_有限状态控制器_行为树`
4. 最后读 `03_LLM_Agent_技能库_自改进`
