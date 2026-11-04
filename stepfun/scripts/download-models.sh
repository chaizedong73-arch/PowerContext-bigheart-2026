#!/bin/bash
# 下载模型权重（Xenova/all-MiniLM-L6-v2）与 ONNX Runtime Web wasm 运行时到 public/vendor/。
# 仓库中已自带 q8 / fp16 权重与 wasm 运行时；本脚本用于补齐 fp32 基线或在干净环境重建 vendor/。
# 默认走 hf-mirror.com（国内可达）；有代理或可直连时可改 MIRROR=https://huggingface.co。
set -e
cd "$(dirname "$0")/.."
MIRROR=${MIRROR:-https://hf-mirror.com}
MODEL_ID=Xenova/all-MiniLM-L6-v2
BASE=public/vendor/models/$MODEL_ID
mkdir -p $BASE/onnx public/vendor/transformers

dl() { local url="$1" out="$2"; [ -s "$out" ] && { echo "skip $out"; return; }; echo "== $out =="; curl -L --retry 3 -o "$out" "$url"; }

# 1) 模型权重（三档精度：fp32 基线 86MB / fp16 43MB / q8 INT8 22MB）
dl $MIRROR/$MODEL_ID/resolve/main/config.json            $BASE/config.json
dl $MIRROR/$MODEL_ID/resolve/main/tokenizer.json         $BASE/tokenizer.json
dl $MIRROR/$MODEL_ID/resolve/main/tokenizer_config.json  $BASE/tokenizer_config.json
dl $MIRROR/$MODEL_ID/resolve/main/onnx/model.onnx            $BASE/onnx/model.onnx
dl $MIRROR/$MODEL_ID/resolve/main/onnx/model_fp16.onnx       $BASE/onnx/model_fp16.onnx
dl $MIRROR/$MODEL_ID/resolve/main/onnx/model_quantized.onnx  $BASE/onnx/model_quantized.onnx

# 2) transformers.js 4.3.2 发行包 + ONNX Runtime Web 1.31.1 wasm 运行时（浏览器只需这 3 个文件）
npm pack @huggingface/transformers@4.3.2
tar -xzf huggingface-transformers-4.3.2.tgz package/dist/transformers.min.js
cp package/dist/transformers.min.js public/vendor/transformers/
npm pack onnxruntime-web@1.31.1
tar -xzf onnxruntime-web-1.31.1.tgz package/dist/ort-wasm-simd-threaded.jsep.mjs package/dist/ort-wasm-simd-threaded.jsep.wasm
cp package/dist/ort-wasm-simd-threaded.jsep.mjs package/dist/ort-wasm-simd-threaded.jsep.wasm public/vendor/transformers/
rm -rf package *.tgz

echo "== 完成 =="
ls -la public/vendor/transformers $BASE $BASE/onnx
