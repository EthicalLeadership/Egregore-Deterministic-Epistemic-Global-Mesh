import torch
from safetensors import safe_open
import time, os

base = "/media/kark/MODELS_2TB3/models/kimi-k2-base"
shard = "model-2-of-61.safetensors"
layer = 1
expert_id = 0

prefix = f"model.layers.{layer}.mlp.experts.{expert_id}"
weight_name = f"{prefix}.gate_proj.weight"
scale_name = f"{prefix}.gate_proj.weight_scale_inv"

print(f"Loading {weight_name} from {shard}")

with safe_open(os.path.join(base, shard), framework="pt", device="cpu") as f:
    weight_fp8 = f.get_tensor(weight_name)
    scale_inv = f.get_tensor(scale_name)

print(f"weight_fp8 dtype: {weight_fp8.dtype}, shape: {weight_fp8.shape}")
print(f"scale_inv dtype: {scale_inv.dtype}, shape: {scale_inv.shape}")

# Reinterpret as float8_e4m3fn if needed
if weight_fp8.dtype == torch.uint8:
    weight_fp8 = weight_fp8.view(torch.float8_e4m3fn)
weight_fp32 = weight_fp8.float()

rows, cols = weight_fp32.shape
block_r, block_c = 128, 128
n_r = (rows + block_r - 1) // block_r
n_c = (cols + block_c - 1) // block_c
scale_matrix = torch.zeros(rows, cols)
for i in range(n_r):
    for j in range(n_c):
        r0, r1 = i*block_r, min((i+1)*block_r, rows)
        c0, c1 = j*block_c, min((j+1)*block_c, cols)
        scale_matrix[r0:r1, c0:c1] = scale_inv[i, j]

weight_dequant = weight_fp32 * scale_matrix
print(f"Dequantized shape: {weight_dequant.shape}")

x = torch.randn(1, 7168)
y = x @ weight_dequant.T
print(f"Output shape: {y.shape}")
print(f"First 5 output values: {y[0, :5].tolist()}")
print("Single expert computation successful.")
