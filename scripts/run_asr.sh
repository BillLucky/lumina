#!/usr/bin/env bash
# 服务器端 ASR 批跑启动器：在指定空闲 GPU 上批量转写 lumina 音频，断点续传 + 崩溃自动重启。
# 用官方 qwen_asr 包 + Qwen3-ASR-1.7B 权重；产出 <ASR_DATA>/<series>/<eid>.asr.json。
#
# 所有机器相关路径走环境变量（本仓库开源，不硬编码任何具体路径）：
#   ASR_PY     跑得动 qwen_asr 的 python 解释器（默认 python3）
#   ASR_MODEL  Qwen3-ASR-1.7B 权重目录（runner 读取，默认 ./models/Qwen3-ASR-1.7B）
#   ASR_DATA   音频根目录（默认 ./data）
#   GPU        钉到哪张空闲卡（默认 3）· BATCH  分块批大小（默认 12）
#
# 用法（建议 tmux 内）：
#   ASR_PY=/path/to/venv/bin/python ASR_MODEL=/path/to/Qwen3-ASR-1.7B \
#   ASR_DATA=/path/to/data GPU=3 bash run_asr.sh
#   ASR_PY=... GPU=3 bash run_asr.sh --series a16z      # 只跑某系列
set -uo pipefail
cd "$(dirname "$0")"
export CUDA_VISIBLE_DEVICES="${GPU:-3}"
PY="${ASR_PY:-python3}"

echo "==================== ASR 批跑开始 $(date) · GPU=$CUDA_VISIBLE_DEVICES ===================="
for i in $(seq 1 100); do
  # runner 已断点续传：已存在 asr.json 的自动跳过。正常跑完(含无待办)返回 0 → 退出循环。
  "$PY" asr_server_runner.py --batch "${BATCH:-12}" "$@" && { echo "==================== ASR 全部完成 $(date) ===================="; exit 0; }
  echo "[wrap] runner 异常退出，10s 后断点续跑（第 $i 次）"; sleep 10
done
echo "[wrap] 重试上限，退出。"; exit 1
