# 事后复盘：vLLM 起服失败，整批任务零产出

| 项目 | 值 |
|---|---|
| 日期 | 2026-10-31 |
| 分诊结论 | `FAKE_LEAD_SUBPROCESS_ERROR` |
| 根因状态 | `CONFIRMED` |
| 证据条数 | 1 |

## 摘要

本次事件「vLLM 起服失败，整批任务零产出」共采集 1 条证据。 异常项：FAKE_LEAD_SUBPROCESS_ERROR。

## 影响

评测集 100 个任务全部未产出；服务未在预算窗口内提供 /v1/models

## 时间线

| 时刻 | 事件 |
|---|---|
| 10:00:01 | 启动 vllm serve |
| 10:00:09 | 父进程报 Failed core proc(s) |
| 10:02 | 取得子进程 stderr，定位 libflash_attn_2_cuda.so 缺失 |
| 10:05 | 屏蔽残缺包并重启，服务就绪 |

## 分诊发现

### logs → `FAKE_LEAD_SUBPROCESS_ERROR`（OBSERVED）

```
父进程假线索 + 子进程真实异常：
(EngineCore_DP0 pid=48213) ImportError: libflash_attn_2_cuda.so: cannot open shared object file: No such file or directory
```

- **child_process**：EngineCore_DP0
- **parent_lead**：ERROR 10:00:09 Engine core initialization failed. Failed core proc(s): {}
- **child_exceptions**：['(EngineCore_DP0 pid=48213) ImportError: libflash_attn_2_cuda.so: cannot open shared object file: No such file or directory']
- **next_step**：以子进程异常为准去查（缺包/驱动/权限），不要照父进程提示去查

## 根因分析

**状态：`CONFIRMED`** —— 有证据闭合：按该原因处置后，验证项全部通过。

镜像内 flash_attn 2.8.3 缺少 flash_attn_2_cuda 扩展；vLLM 仅用 find_spec() 判断可导入，实际 import 时在 EngineCore 子进程崩溃

## 处置动作

1. 屏蔽残缺包：mv flash_attn flash_attn.disabled
2. 引擎退回 Triton Attention 后端重启服务

## 验证

- [x] 重启后 GET /v1/models 返回 200 并列出模型
- [x] 同一 prompt 单请求吞吐回到预期量级

## 未解决项

（无）

## 附录：证据清单

```
[logs/FAKE_LEAD_SUBPROCESS_ERROR] 父进程假线索 + 子进程真实异常：
[logs/FAKE_LEAD_SUBPROCESS_ERROR] (EngineCore_DP0 pid=48213) ImportError: libflash_attn_2_cuda.so: cannot open shared object file: No such file or directory
```

---

状态说明：`CONFIRMED` 有证据闭合 / `SUSPECTED` 有线索未闭合 / `UNRESOLVED` 根因未确定。
本报告由 llmtriage 生成；每条结论的原始证据见上文代码块。