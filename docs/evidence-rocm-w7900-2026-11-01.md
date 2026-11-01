# 真机证据：AMD Radeon PRO W7900（ROCm）上的 `llmtriage gpu`

采集时间：2026-11-01 00:38（UTC-7），AMD Radeon Cloud 中国站，单卡实例，用完即销毁。
留存这份文件的目的是：**ROCm 分支不是照着想象写的**，它的解析结果来自一次真机运行。

## 环境（原文照抄，未做加工）

```
### rocm-smi --version
ROCM-SMI version: 4.0.0+e1a6bc5663
ROCM-SMI-LIB version: 7.8.0
### rocminfo gfx
  Marketing Name:          AMD EPYC 9334 32-Core Processor
  Compute Unit:            64
### PY
Python 3.12.3 (main, Mar  3 2026, 12:15:18) [GCC 13.3.0]
### NVSMI-ABSENT
nvidia-smi absent
```

注：`rocminfo` 的 Marketing Name 在这里报的是 CPU（EPYC 9334），GPU 侧型号需要看
`rocm-smi --showproductname`；这次没抓，所以本文不断言 GPU 的具体 marketing name，
只断言下面这条 CSV 里的 VRAM 总量与 48GB 卡相符。

## 原始 CSV（就是解析器的输入，已作为测试 fixture 存档）

`tests/fixtures/rocm_smi_w7900_2026-11-01.csv`：

```
device,GPU use (%),VRAM Total Memory (B),VRAM Total Used Memory (B)
card0,1,51522830336,28004352
```

## 工具的判定

```
# GPU 快照
**总体结论：`DEGRADED`（证据状态：OBSERVED）** —— 1 项异常（gpu）

- backend: rocm-smi
- gpus: [{'index': 0, 'mem_used_mb': 27, 'mem_total_mb': 49136, 'util_pct': 0, 'class': 'idle'}]
- hint: 所有卡都空闲/显存接近 0：如果服务自称在生成，token 很可能在 CPU 上产生，
        应先修层放置/显存配置，而不是继续砍 token 预算。
exit=1
```

- `51522830336 B` → **49136 MB ≈ 47.98 GiB**，与这台卡的实际显存一致 ⇒ 单位（字节 → MB）换算正确。
- 空载实例判 `DEGRADED` 且 `exit=1`（发现问题），符合退出码契约。
- `nvidia-smi` 不存在时自动落到 `rocm-smi` 分支，不需要用户指定。

## 一个实测到的细节：利用率是抖的

同一台实例上，相隔约 2 秒的两次采样分别读到 `GPU use (%) = 1` 和 `0`。
所以 `tests/fixtures/` 里的真机样本只把"量级合理"钉进断言，不钉死具体数值——
把抖动钉进测试，测试就会变成看运气。判定阈值（利用率 ≥ 20% 或显存 ≥ 15% 才算在用）
远高于这种抖动幅度，不影响结论。

## 这份证据**没有**证明的事

- **没有**验证"GPU 真的在跑"那条路径（`in_use`）在 AMD 上的表现：实例空载，
  要造这个场景需要真起一个 vLLM 服务并压流量，本次没做。
- **没有**验证多卡（`card1`+）、MIG 切分、以及更老/更新的 `rocm-smi` 版本。
  解析器按列名匹配，理论上兼容，但"理论上"不算证据。
- 没有验证 ROCm 下的 `probe` / `budget`（与 GPU 后端无关，属另一条路径）。

要补哪一条，就照 `docs/` 里这份文件的格式再采一次；证据的标准是能被别人照着重跑。
