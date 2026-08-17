import json, os, torch
from safetensors import safe_open
from kimi_pager import ExpertPager

class ResidentWeights:
    def __init__(self, base_path, device="cpu"):
        self.base = base_path
        self.device = device
        self.pager = ExpertPager(base_path, cache_size=16, device=device)
        with open(os.path.join(base_path, "model.safetensors.index.json")) as f:
            self.index = json.load(f)["weight_map"]
        self.tensors = {}
        self.load_embeddings()
        self.load_router_and_attention()
        self.load_shared_experts()

    def _load_tensor(self, name):
        shard = self.index[name]
        with safe_open(os.path.join(self.base, shard), framework="pt", device="cpu") as f:
            t = f.get_tensor(name)
        return t.to(self.device)

    def _dequant(self, w, s):
        # same as pager
        if w.dtype == torch.uint8:
            w = w.view(torch.float8_e4m3fn)
        w = w.float()
        rows, cols = w.shape
        block_r, block_c = 128,128
        scale_matrix = torch.zeros(rows, cols)
        n_r = (rows+block_r-1)//block_r
        n_c = (cols+block_c-1)//block_c
        for i in range(n_r):
            for j in range(n_c):
                r0, r1 = i*block_r, min((i+1)*block_r, rows)
                c0, c1 = j*block_c, min((j+1)*block_c, cols)
                scale_matrix[r0:r1,c0:c1] = s[i,j]
        return w * scale_matrix

    def load_embeddings(self):
        w = self._load_tensor("model.embed_tokens.weight")
        self.tensors["embed"] = w.float()

    def load_router_and_attention(self):
        # iterate layers 0..60; load attention and router and norms
        self.attn = {}
        self.routers = {}
        self.norms = {}
        for L in range(61):
            # attention projections
            att = {}
            for name in ["q_a_proj","q_b_proj","kv_a_proj_with_mqa","kv_b_proj","o_proj"]:
                w = self._load_tensor(f"model.layers.{L}.self_attn.{name}.weight")
                s = self._load_tensor(f"model.layers.{L}.self_attn.{name}.weight_scale_inv")
                att[name] = self._dequant(w, s)
            # norms
            n1 = self._load_tensor(f"model.layers.{L}.input_layernorm.weight")
            n2 = self._load_tensor(f"model.layers.{L}.post_attention_layernorm.weight")
            self.attn[L] = att
            self.norms[L] = (n1, n2)
            # router
            rw = self._load_tensor(f"model.layers.{L}.mlp.gate.weight")
            rb = self._load_tensor(f"model.layers.{L}.mlp.gate.e_score_correction_bias")
            self.routers[L] = (rw.float(), rb.float())
        # final norm
        self.final_norm = self._load_tensor("model.norm.weight").float()

    def load_shared_experts(self):
        self.shared = {}
        for L in range(1, 61):
            shared = {}
            for proj in ["gate_proj","up_proj","down_proj"]:
                w = self._load_tensor(f"model.layers.{L}.mlp.shared_experts.{proj}.weight")
                s = self._load_tensor(f"model.layers.{L}.mlp.shared_experts.{proj}.weight_scale_inv")
                shared[proj] = self._dequant(w, s)
            self.shared[L] = shared
