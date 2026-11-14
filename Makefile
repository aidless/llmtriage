# --- acceptance_backfill (EVAL_ACCEPTANCE_STANDARD.md P4) ---
# 证据来源: ci.yml
# 命令逐字取自该来源，未增删任何参数。
# 归一: python → python3（本机无 python 别名，实测 Error 127）
# 追加: python3 eval/run_cases.py（题库验收，20/20 才算过；见 README「验收标准与复算」）
.PHONY: eval

eval:
	python3 eval/run_cases.py
	python3 -m pytest -q
