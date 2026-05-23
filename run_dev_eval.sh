#!/usr/bin/env bash
# Run all (model × prompt_style) combinations on the dev set, then evaluate.
#
# Usage:
#   ./run_dev_eval.sh                    # use defaults below
#   ./run_dev_eval.sh audit_dev.db       # specify DB path
#
# All results go into a single DB keyed by (model, prompt_style).
# Skips any (synset, model, prompt_style) already in the DB, so
# it is safe to re-run after an interruption.

set -euo pipefail

DB="${1:-audit_dev.db}"
LMF="wnja-2.0.xml"
REF_LMF="wn-ntumc-eng.xml"
DEVSET="audit/dev_set.tsv"
GOLD="audit/dev_set.tsv"

MODELS=(
    "mlx-community/gemma-4-31b-it-4bit"
    "mlx-community/Qwen3-32B-4bit"
)
STYLES=(
    "zero-shot"
    "one-shot"
    "few-shot"
)

echo "=== wnja definition audit: dev set evaluation ==="
echo "DB:     $DB"
echo "Models: ${MODELS[*]}"
echo "Styles: ${STYLES[*]}"
echo ""

for MODEL in "${MODELS[@]}"; do
    for STYLE in "${STYLES[@]}"; do
        echo "------------------------------------------------------------"
        echo "Running: $MODEL  /  $STYLE"
        echo "------------------------------------------------------------"
        uv run python -m audit.cli \
            --lmf "$LMF" \
            --ref-lmf "$REF_LMF" \
            --check definitions \
            --synset-file "$DEVSET" \
            --model "$MODEL" \
            --prompt-style "$STYLE" \
            --db "$DB"
        echo ""
    done
done

echo "============================================================"
echo "=== Evaluation results ==="
echo "============================================================"

# Build --run arguments for all combinations
RUN_ARGS=()
for MODEL in "${MODELS[@]}"; do
    for STYLE in "${STYLES[@]}"; do
        RUN_ARGS+=(--run "${MODEL}/${STYLE}")
    done
done

uv run python -m audit.dev evaluate \
    --gold "$GOLD" \
    --db "$DB" \
    "${RUN_ARGS[@]}"
