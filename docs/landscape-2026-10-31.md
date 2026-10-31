# 生态调研：LLM 推理运维工具（2026-10-31）

目的：在动手写 `llmtriage` 之前，先确认"这件事有没有人做过、饱和在哪、空白在哪"。
数据来源：GitHub REST API（`/search/repositories` 与 `/repos/{owner}/{repo}`），
星数为 **2026-10-31 当天读数**，会随时间变化。

## 1. 三类已饱和的生态

### 服务引擎
| 仓库 | ★ | 语言 | 说明 |
|---|---|---|---|
| vllm-project/vllm | 95,971 | Python | 高吞吐、省显存的推理与服务引擎 |
| sgl-project/sglang | 38,321 | Python | 高性能服务框架（LLM + 多模态） |
| ai-dynamo/dynamo | 8,413 | Rust | 数据中心级分布式推理框架 |
| llm-d/llm-d | 5,109 | Python | Kubernetes 上的推理性能方案 |

### 监控 / 指标（"这张卡在干什么"）
| 仓库 | ★ | 说明 |
|---|---|---|
| Syllo/nvtop | 11,113 | 跨厂商 GPU/加速器进程监控（C，TUI） |
| XuehaiPan/nvitop | 7,235 | 交互式 GPU 进程查看器，另带 `nvisel`（CUDA_VISIBLE_DEVICES 选择器）与 exporter 与低层 API |
| wookayin/gpustat | 4,390 | 一行命令看 GPU 状态 |
| NVIDIA/dcgm-exporter | 1,920 | DCGM → Prometheus |
| utkuozdemir/nvidia_gpu_exporter | 1,578 | nvidia-smi/NVML → Prometheus |

### 基准测试（"它有多快"）
| 仓库 | ★ | 说明 |
|---|---|---|
| ray-project/llmperf | 1,127 | 负载测试 + 正确性测试；测 inter-token latency 与生成吞吐 |
| kubernetes-sigs/inference-perf | 266 | k8s-sigs 标准化工具：TTFT / TPOT / ITL / normalized TPOT / goodput，自动饱和度扫描，vLLM/SGLang/TGI 已支持，有 JOSS 论文（10.21105/joss.10314） |

**结论：这三层都不要重造。** 引擎层有 96k 星的主宰，监控层有 4 个万星级项目加两条 exporter 链路，
基准层已经由 k8s-sigs 立项做标准化。

## 2. 空白在哪

把三个方向的 README 逐段读完，共同点是：**都假设部署已经能跑**。
- 监控工具告诉你显存占用 97%，不告诉你"这 97% 是不是在产生有效 token"。
- 基准工具测出 TTFT/TPOT，但跑不动的时候它只会超时。
- 没有工具回答"这条 `Failed core proc(s): {}` 到底是真因还是假线索"。

`容量规划` 方向几乎是空的：GitHub 上搜 `llm serving capacity planning` 只得到 1★/0★ 的小仓库
（如 `infersize`、`capacity-planning-sim`），说明需求存在但没形成项目。

> 诚实的口径：`inference troubleshooting cli`、`gpu oom diagnosis tool`、`vllm metrics prometheus grafana`
> 三组查询这次返回 0 条，**这可能是查询措辞问题，不能据此断言"不存在"**（GitHub 搜索对长查询很敏感）。
> 能站住的陈述只有一条：上面四个高星项目的 README 都没有提供 verdict / evidence 层。

## 3. 从这些项目里学到的三条具体做法

1. **nvitop 的 `nvisel`** —— 同一个仓库里除了监控 TUI，还塞了一个"按显存挑卡并导出
   `CUDA_VISIBLE_DEVICES`"的小工具。这证明"围绕同一份硬件事实做多个小而独立的工具"是被社区接受的形态，
   不必做成一个大而全的平台。→ llmtriage 保持 5 个子命令、零依赖、单文件即可用。
2. **inference-perf 的 goodput** —— 它不只报吞吐，而是报"满足 SLO 的请求比例"。
   这提示：**任何性能结论不绑 SLO 就没有决策价值**。→ llmtriage 的 `budget` 直接把
   "tokens / tok/s / deadline" 折成 GO / NO_GO，而不是丢一个 tok/s 让人自己算。
3. **llmperf 的 caveats 段落** —— 它明写"不同后端不可比、结果随时段/负载变化、与实际工作负载可能不相关"。
   这种写法把不可比说在前面，反而更容易被信任。→ 抄进 llmtriage 的 README limitations
   （单请求口径 ≠ 服务吞吐上限；不支持 ROCm 就返回 UNKNOWN）。

## 4. 与就业岗位的关系

这份 JD 的第 4 条职责是"基于 profiling、日志、监控和压测数据分析应用性能瓶颈，输出评估报告、
优化建议和问题复盘"。上面三层工具能覆盖"采集"，但**"评估报告 + 优化建议 + 问题复盘"这一段没有现成工具**——
`llmtriage` 就是把这最后一段做成可执行、可复现、带证据的东西。
