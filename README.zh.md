# llmtriage —— 一条命令给"跑不起来的 LLM 推理部署"做分诊

**它回答的问题**：这个部署坏没坏？坏在哪一层？
**它不回答的问题**：它有多快（那是 benchmark 工具的事）。

## 为什么做

真实排障里最贵的三种情况，基准测试和监控都看不见：

| 现象 | 你看到的 | 真实原因 |
|---|---|---|
| 假线索 | `Engine core initialization failed. Failed core proc(s): {}` | 真异常在**子进程** stderr（`EngineCore_DP0 pid=…`），照父进程提示查会走很远弯路 |
| 悄悄退回 CPU | 吞吐极低（如 4.32 tok/s） | 每卡显存接近 0。检查会给 `DEGRADED`，并**提示** token 可能在 CPU 上；它没有测量 CPU |
| 预算超墙 | 跑满墙钟却没落地任何结果 | tokens ÷ tok/s × 请求数 早已超过 deadline，这个数应该在**启动前**算 |

## 安装

**没有发布到 PyPI。** 2026-11-05 查询 `https://pypi.org/pypi/llmtriage/json` 得到 **404**。`pyproject.toml` 里 `[tool.llmtriage] distribution = "source"`。索引上没有这个包。`tests/test_readme_honesty.py` 会在 README 写出索引安装命令、而该字段仍是 `"source"` 时失败。

```bash
git clone https://github.com/aidless/llmtriage.git
cd llmtriage
python -m pip install -e ".[dev]"   # 这份检出，含 pytest
python -m pip install -e .          # 这份检出，只要命令
```

```bash
python -m pip install "git+https://github.com/aidless/llmtriage.git"
```

运行时依赖只有标准库。CI 做的是可编辑安装加 `pytest`，不构建 wheel，也不上传。更完整的证据边界和失败模式在 [README.md](README.md) 的 Evidence boundaries 与 Failure modes。

## 用法

```bash
llmtriage probe --base-url http://127.0.0.1:8000 --measure --model qwen3_4b
llmtriage gpu
llmtriage logs --file examples/vllm_fake_lead.log
llmtriage budget --tokens 12000 --tok-per-s 4.32 --deadline 4500 --requests 2
# 服务是在排队、还是 KV 缓存要打满？（只读 /metrics）
llmtriage metrics --url http://127.0.0.1:8000
llmtriage metrics --file metrics.txt        # 离线分析：贴一份已抓下来的文本

# 不看 SLO 的 tok/s 没有意义：多少比例的请求真的同时满足了 TTFT 与 TPOT
llmtriage slo --csv samples.csv --ttft-slo 1.0 --tpot-slo 0.1

# 把上面收集到的证据压成"最可能的原因 + 下一句敲什么"
llmtriage explain --log vllm.log --metrics metrics.txt

llmtriage triage --base-url http://127.0.0.1:8000 --log vllm.log \
                 --tokens 12000 --tok-per-s 4.32 --deadline 4500 --requests 2 --explain --out triage.md
# 把分诊结果变成可直接交付的事故复盘
llmtriage postmortem --from triage.json --title "事件名" --root-cause-state UNRESOLVED \
                     --action "限制影响的动作" --verify "确认恢复的证据" --out postmortem.md
```

> 注意：退出码是真的，所以 `llmtriage logs ... && 下一步` 在发现问题时会**中断**（exit 1）。
> 脚本里需要继续执行就用 `;` 或 `|| true`。

## 指标、SLO 与"下一步"

三个新子命令都**只读、可离线跑**，CI 里没有 GPU 也能覆盖。

- `llmtriage metrics --url|--file`：解析 Prometheus 文本，判 `SATURATED`（排队 + KV 打满）/
  `KV_PRESSURE` / `QUEUE_BACKLOG` / `OK` / `UNKNOWN`（缺 KV 指标时**不**判通过）。
  兼容 vLLM 的指标改名（`gpu_cache_usage_perc` → `kv_cache_usage_perc`），并跨 engine 求和。
- `llmtriage slo --csv --ttft-slo --tpot-slo`：算 **goodput**（同时满足 TTFT 与 TPOT 的请求占比，
  定义取自 DistServe arXiv 2401.09670）、p50/p95/p99，并指出是 TTFT 还是 TPOT 在拖后腿。
- `llmtriage explain`：把证据压成**排序假设**，每条都必须写明来自哪条 finding，并给一句可执行的
  检查命令。没有证据就不许产出假设（有测试钉住）。

阈值（KV 0.90 / 排队 8 / 显存 15%…）都是**我们定的启发式，不是论文给出的分界线**；
换集群先测自己的基线。

两个可以直接试的样本：`examples/vllm_metrics_saturated.txt`、`examples/slo_samples.csv`。

## 事故复盘报告

`llmtriage postmortem` 把分诊结果变成这份岗位真正要交的东西：评估报告、优化建议、问题复盘。
它只在**一个方向**上强约束 —— 不许让"根因未知"读起来像"已修复"：

- 根因三态声明：`CONFIRMED`（证据闭合）/ `SUSPECTED`（有线索未闭合）/ `UNRESOLVED`（不知道）。
  选 `UNRESOLVED` 时报告会写"根因未确定：以下处置只用于限制影响，不是根因修复"。
- 没有验证证据时，验证小节打印 **未验证** 与"在补充验证前，不得把该事件标记为已解决"；
  生成物里**不会出现"已修复"三个字** —— 修没修由验证小节回答，不由措辞回答。
- 每条发现的原始 `evidence` 逐行进入附录"证据清单"。

仓库里有两个由 CLI 实跑生成的样例（不是手写）：
[`examples/postmortem-example.md`](examples/postmortem-example.md)（CONFIRMED）与
[`examples/postmortem-unresolved-example.md`](examples/postmortem-unresolved-example.md)（UNRESOLVED）。

## 两个契约

**退出码**：`0` 通过 / `1` 发现问题 / `2` 用法错误 / `3` **数据不足（UNKNOWN，绝不静默当通过）**

**三态证据**：`OBSERVED` 本次实测（附命令与原始输出）/ `INFERRED` 由实测推断 / `UNKNOWN` 数据不足。
报告聚合器会保留 finding 上的 `INFERRED`，但**当前没有子命令会写出这个标签**，实际只有 `OBSERVED` 和 `UNKNOWN`。CPU 那句提示挂在 `OBSERVED` 的 GPU 结论上，不是一条 `INFERRED` 结论。
日志干净只会报 `NO_ERROR_FOUND`，不会报"健康"；没有 `nvidia-smi` 只会报 `UNKNOWN`，不会报"0 张卡"。

## 不做什么

不是 benchmark（用 inference-perf / llmperf）、不是监控（用 nvitop / nvtop / dcgm-exporter）、
不是容量仿真器。`gpu` 先试 `nvidia-smi`，找不到再试 `rocm-smi`；两个都在 `PATH` 上时只读 NVIDIA。ROCm 解析**按列名而非列位置**映射，列名认不出时返回 UNKNOWN。`amd-smi` 未接入。

2026-11-01 的记录是一份空载 CSV：[`tests/fixtures/rocm_smi_w7900_2026-11-01.csv`](tests/fixtures/rocm_smi_w7900_2026-11-01.csv)，说明在 [`docs/evidence-rocm-w7900-2026-11-01.md`](docs/evidence-rocm-w7900-2026-11-01.md)。CI **重放**这份 CSV，不执行 `rocm-smi`。CSV 里没有产品名；W7900 是租用的实例类型，`rocm-smi --showproductname` 没有采集，`rocminfo` 贴出来的 Marketing Name 是 CPU。AMD 上的 `in_use`（有模型在跑）、多卡、MIG、NVIDIA 真机，仓库里都没有捕获。

`probe --measure` 是单请求口径，只能当预算输入，不能当服务吞吐上限。

生态调研与差异化依据见 [docs/landscape-2026-10-31.md](docs/landscape-2026-10-31.md)。

## 失败模式

结论可以在证据行被原样抄下时仍然是错的：

1. **日志模式很窄。** 认不出的栈、被截断的文件、没有 traceback 的父进程错误，会变成 `UNKNOWN` 或 `NO_ERROR_FOUND`。`NO_ERROR_FOUND` 不是「健康」。
2. **GPU 空闲是弱信号。** 显存 <15% 且利用率 <20% 就标 `DEGRADED`，并附上「可能在 CPU 上算」的提示。阈值是 `gpu.py` 里的常数，不是一次对 CPU 的测量。
3. **ROCm 夹具只有一条空载样本。** 不覆盖 `in_use`、多卡、MIG、别的 `rocm-smi` 版本。产品名不在文件里。
4. **预算相信你喂进去的 tok/s。** 没测到或不是正数时退出码是 3，不是通过。20% 余量是常数。`probe --measure` 是单请求。
5. **指标和 SLO 的分界线是本地启发式**（KV 0.90、排队 8），不是论文给出的标准。缺 KV 序列是 `UNKNOWN`，不是 `OK`。
6. **假设是下一句检查，不是修复。** 退出码 1 表示某项检查触发了，不是生产根因，也不该直接拿去告警。

## 测试

CI（[`.github/workflows/ci.yml`](.github/workflows/ci.yml)）在 Ubuntu 上对 Python 3.10 和 3.12 执行可编辑安装，然后 `python -m pytest -q`。这里不写死条数：同一页曾经同时出现过 29 和别的数字。这个 job 看不到 GPU。

测试先于实现（RED → GREEN）。反例包括：父进程自身 traceback 不得判成假线索；同一份 ROCm 数据换列序必须解出相同结果；根因 UNRESOLVED 时输出不得出现「已修复」。

License: MIT
