import json, os, torch
from collections import OrderedDict
from safetensors import safe_open

class ExpertPager:
    def __init__(self, base_path, cache_size=8, device="cpu"):
        self.base = base_path
        self.cache_size = cache_size
        self.device = device
        with open(os.path.join(base_path, "model.safetensors.index.json")) as f:
            self.index = json.load(f)["weight_map"]
        self.cache = OrderedDict()

    def _load_tensor(self, tensor_name):
        shard = self.index[tensor_name]
        with safe_open(os.path.join(self.base, shard), framework="pt", device="cpu") as f:
            return f.get_tensor(tensor_name)

    def _dequant(self, weight_fp8, scale_inv):
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
        return weight_fp32 * scale_matrix

    def load_expert(self, layer, expert_id):
        key = f"layer{layer}_expert{expert_id}"
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]

        prefix = f"model.layers.{layer}.mlp.experts.{expert_id}"
        tensors = {}
        for proj in ["gate_proj", "up_proj", "down_proj"]:
            w = self._load_tensor(f"{prefix}.{proj}.weight")
            s = self._load_tensor(f"{prefix}.{proj}.weight_scale_inv")
            tensors[proj] = self._dequant(w, s).to(self.device)

        self.cache[key] = tensors
        if len(self.cache) > self.cache_size:
            self.cache.popitem(last=False)
        return tensors

    def expert_forward(self, layer, expert_id, hidden_states):
        expert = self.load_expert(layer, expert_id)
        gate_out = torch.matmul(hidden_states, expert["gate_proj"].T)
        gate_out = torch.nn.functional.silu(gate_out)
        up_out = torch.matmul(hidden_states, expert["up_proj"].T)
        h = gate_out * up_out
        out = torch.matmul(h, expert["down_proj"].T)
        return out
