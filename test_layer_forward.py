import json, os, torch
from safetensors import safe_open
from kimi_pager import ExpertPager

base = "/media/kark/MODELS_2TB3/models/kimi-k2-base"
pager = ExpertPager(base, cache_size=16)

with open(os.path.join(base, "model.safetensors.index.json")) as f:
    index = json.load(f)["weight_map"]

def load_tensor(name):
    shard = index[name]
    with safe_open(os.path.join(base, shard), framework="pt", device="cpu") as f:
        return f.get_tensor(name)

def dequant(w, s):
    if w.dtype == torch.uint8:
        w = w.view(torch.float8_e4m3fn)
    w = w.float()
    rows, cols = w.shape
    block_r, block_c = 128, 128
    n_r = (rows+block_r-1)//block_r
    n_c = (cols+block_c-1)//block_c
    scale_matrix = torch.zeros(rows, cols)
    for i in range(n_r):
        for j in range(n_c):
            r0, r1 = i*block_r, min((i+1)*block_r, rows)
            c0, c1 = j*block_c, min((j+1)*block_c, cols)
            scale_matrix[r0:r1, c0:c1] = s[i, j]
    return w * scale_matrix

L = 1
gate_w = load_tensor(f"model.layers.{L}.mlp.gate.weight").float()
gate_b = load_tensor(f"model.layers.{L}.mlp.gate.e_score_correction_bias").float()

shared = {}
for proj in ["gate_proj","up_proj","down_proj"]:
    w = load_tensor(f"model.layers.{L}.mlp.shared_experts.{proj}.weight")
    s = load_tensor(f"model.layers.{L}.mlp.shared_experts.{proj}.weight_scale_inv")
    shared[proj] = dequant(w, s)

b, s, h = 1, 1, 7168
x = torch.randn(b, s, h)

logits = torch.matmul(x, gate_w.T) + gate_b
topk = torch.topk(logits, k=8, dim=-1)
expert_ids = topk.indices[0,0].tolist()
print("Selected experts:", expert_ids)

shared_out = torch.matmul(x, shared["gate_proj"].T)
shared_out = torch.nn.functional.silu(shared_out) * torch.matmul(x, shared["up_proj"].T)
shared_out = torch.matmul(shared_out, shared["down_proj"].T)

routed_out = torch.zeros_like(x)
for eid in expert_ids:
    routed_out += pager.expert_forward(L, eid, x)

out = shared_out + routed_out
print("Shared out shape:", shared_out.shape)
print("Routed out shape:", routed_out.shape)
print("Final layer out:", out.shape)
print("First values:", out[0,0,:5])
