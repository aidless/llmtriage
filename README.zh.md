# llmtriage —— 一条命令给"跑不起来的 LLM 推理部署"做分诊

**它回答的问题**：这个部署坏没坏？坏在哪一层？
**它不回答的问题**：它有多快（那是 benchmark 工具的事）。

## 为什么做

真实排障里最贵的三种情况，基准测试和监控都看不见：

| 现象 | 你看到的 | 真实原因 |
|---|---|---|
| 假线索 | `Engine core initialization failed. Failed core proc(s): {}` | 真异常在**子进程** stderr（`EngineCore_DP0 pid=…`），照父进程提示查会走很远弯路 |
| 悄悄退回 CPU | 吞吐极低（如 4.32 tok/s） | 每卡显存接近 0，token 其实在 CPU 上生成——该修层放置，不是砍 token 预算 |
| 预算超墙 | 跑满墙钟却没落地任何结果 | tokens ÷ tok/s × 请求数 早已超过 deadline，这个数应该在**启动前**算 |

## 用法

```bash
llmtriage probe --base-url http://127.0.0.1:8000 --measure --model qwen3_4b
llmtriage gpu
llmtriage logs --file examples/vllm_fake_lead.log
llmtriage budget --tokens 12000 --tok-per-s 4.32 --deadline 4500 --requests 2
llmtriage triage --base-url http://127.0.0.1:8000 --log vllm.log \
                 --tokens 12000 --tok-per-s 4.32 --deadline 4500 --requests 2 --out triage.md
```

## 两个契约

**退出码**：`0` 通过 / `1` 发现问题 / `2` 用法错误 / `3` **数据不足（UNKNOWN，绝不静默当通过）**

**三态证据**：`OBSERVED` 本次实测（附命令与原始输出）/ `INFERRED` 由实测推断 / `UNKNOWN` 数据不足。
日志干净只会报 `NO_ERROR_FOUND`，不会报"健康"；没有 `nvidia-smi` 只会报 `UNKNOWN`，不会报"0 张卡"。

## 不做什么

不是 benchmark（用 inference-perf / llmperf）、不是监控（用 nvitop / nvtop / dcgm-exporter）、
不是容量仿真器。`gpu` 先试 `nvidia-smi`，找不到再试 `rocm-smi`；ROCm 解析**按列名而非列位置**映射（`rocm-smi` 各版本列序会变），列名认不出时返回 UNKNOWN 而不是猜一个数；`amd-smi` 尚未接入；**只用桩程序验证过，未在真实 AMD 硬件上实跑**；
`probe --measure` 是单请求口径，只能当预算输入，不能当服务吞吐上限。

生态调研与差异化依据见 [docs/landscape-2026-10-31.md](docs/landscape-2026-10-31.md)。

## 测试

```bash
python -m pytest -q     # 23 项
```

测试先于实现（RED → GREEN），其中两条是反证而非顺证：①日志分类器——父进程自身 traceback 不得被判为假线索；②ROCm 解析器——同一份数据换一种列顺序必须解出完全相同的结果，这才让「按列名映射」成为可检验的断言而不是一句注释。

License: MIT
