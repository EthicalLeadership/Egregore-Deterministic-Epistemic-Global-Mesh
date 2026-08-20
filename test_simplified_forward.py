import json, os, torch, time
from safetensors import safe_open
from kimi_pager import ExpertPager

base = "/media/kark/MODELS_2TB3/ExecutiveIntelligenceservices.-main/models/kimi-k2-base"
pager = ExpertPager(base, cache_size=8, device="cpu")  # lower cache to save RAM

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

def rms_norm(x, weight, eps=1e-6):
    # x: [..., D]
    variance = x.pow(2).mean(-1, keepdim=True)
    x_norm = x * torch.rsqrt(variance + eps)
    return x_norm * weight

# Load embeddings and final norm
print("Loading embedding and final norm...")
embed = load_tensor("model.embed_tokens.weight").float()          # [vocab, 7168]
final_norm = load_tensor("model.norm.weight").float()

# Load dense MLP for layer 0 (dense layer)
print("Loading dense layer 0 MLP...")
dense_mlp = {}
for proj in ["gate_proj", "up_proj", "down_proj"]:
    w = load_tensor(f"model.layers.0.mlp.{proj}.weight")
    s = load_tensor(f"model.layers.0.mlp.{proj}.weight_scale_inv")
    dense_mlp[proj] = dequant(w, s)

# Load routers, shared experts, and norms for layers 1-60
print("Loading routers, shared experts, and norms for MoE layers...")
routers = {}
shared_experts = {}
norms = {}
for L in range(1, 61):
    gate_w = load_tensor(f"model.layers.{L}.mlp.gate.weight").float()
    gate_b = load_tensor(f"model.layers.{L}.mlp.gate.e_score_correction_bias").float()
    routers[L] = (gate_w, gate_b)

    shared = {}
    for proj in ["gate_proj","up_proj","down_proj"]:
        w = load_tensor(f"model.layers.{L}.mlp.shared_experts.{proj}.weight")
        s = load_tensor(f"model.layers.{L}.mlp.shared_experts.{proj}.weight_scale_inv")
        shared[proj] = dequant(w, s)
    shared_experts[L] = shared

    n1 = load_tensor(f"model.layers.{L}.input_layernorm.weight").float()
    n2 = load_tensor(f"model.layers.{L}.post_attention_layernorm.weight").float()
    norms[L] = (n1, n2)

# Load dense layer norms
n0_in = load_tensor("model.layers.0.input_layernorm.weight").float()
n0_post = load_tensor("model.layers.0.post_attention_layernorm.weight").float()

print("All resident weights loaded. Starting simplified forward pass.")
print("This will be very slow. Get a coffee.")

# Tokenize a simple input (use tokenizer later; for now use random token id 1)
input_ids = torch.tensor([[1]], dtype=torch.long)  # could be BOS

# Embedding
hidden = embed[input_ids]  # [1, 1, 7168]
print("Embedding shape:", hidden.shape)

# Layer 0: dense MLP (skip attention)
residual = hidden
h = rms_norm(hidden, n0_in)
# Dense MLP
g = torch.matmul(h, dense_mlp["gate_proj"].T)
g = torch.nn.functional.silu(g)
u = torch.matmul(h, dense_mlp["up_proj"].T)
d = torch.matmul(g * u, dense_mlp["down_proj"].T)
h = rms_norm(d, n0_post)
hidden = residual + h

# MoE layers 1-60
for L in range(1, 61):
    residual = hidden
    h = rms_norm(hidden, norms[L][0])
    # Router
    gate_w, gate_b = routers[L]
    logits = torch.matmul(h, gate_w.T) + gate_b
    topk = torch.topk(logits, k=8, dim=-1)
    expert_ids = topk.indices[0,0].tolist()
    # Shared expert
    sh = shared_experts[L]
    sg = torch.matmul(h, sh["gate_proj"].T)
    sg = torch.nn.functional.silu(sg)
    su = torch.matmul(h, sh["up_proj"].T)
    so = torch.matmul(sg * su, sh["down_proj"].T)
    # Routed experts
    ro = torch.zeros_like(h)
    for eid in expert_ids:
        ro += pager.expert_forward(L, eid, h)
    h = so + ro
    h = rms_norm(h, norms[L][1])
    hidden = residual + h
    if L % 10 == 0:
        print(f"Layer {L} done. hidden mean: {hidden.mean().item():.4f}")

# Final norm and lm_head
hidden = rms_norm(hidden, final_norm)
# lm_head weight may be tied? Config tie_word_embeddings false, so separate.
# Check if exists
lm_head = load_tensor("lm_head.weight").float()
logits = torch.matmul(hidden, lm_head.T)
print("Logits shape:", logits.shape)
print("Top 5 predicted token ids:", torch.topk(logits[0,0], k=5).indices.tolist())
print("Simplified full forward pass complete!")
