import json
import os
import torch
from safetensors import safe_open
from kimi_pager import ExpertPager

base = "/media/kark/MODELS_2TB3/ExecutiveIntelligenceservices.-main/models/kimi-k2-base"
pager = ExpertPager(base, cache_size=16)

layer = 1
with open(os.path.join(base, "model.safetensors.index.json")) as f:
    index = json.load(f)["weight_map"]
shard = index[f"model.layers.{layer}.mlp.gate.weight"]
with safe_open(os.path.join(base, shard), framework="pt", device="cpu") as f:
    gate_w = f.get_tensor(f"model.layers.{layer}.mlp.gate.weight").float()
    gate_b = f.get_tensor(f"model.layers.{layer}.mlp.gate.e_score_correction_bias").float()

b, s, h = 1, 1, 7168
x = torch.randn(b, s, h)

logits = torch.matmul(x, gate_w.T) + gate_b
topk = torch.topk(logits, k=8, dim=-1)
expert_ids = topk.indices[0,0].tolist()
print("Selected experts:", expert_ids)

output = torch.zeros_like(x)
for eid in expert_ids:
    y = pager.expert_forward(layer, eid, x)
    output += y

print("Output shape:", output.shape)
print("First values:", output[0,0,:5])
