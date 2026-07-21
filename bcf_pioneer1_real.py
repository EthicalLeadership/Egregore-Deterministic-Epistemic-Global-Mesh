cd ~/egregore
cat > bcf_pioneer1_real.py << 'EOF'
#!/usr/bin/env python3
# BLACKSTAR BCF — PIONEER 1 REAL HARDWARE
import threading, time, random, numpy as np
from collections import deque
from dataclasses import dataclass, field
import os, psutil

REAL_CAPACITY = {
    "dt_total": 151.0, "dt_cpu": 24.0, "dt_gpu": 127.0,
    "tu_total": 16, "scl_total": 2000.0,
    "ml_total_bytes": 28 * 1024**3, "gpu_vram_total_bytes": 10 * 1024**3,
}

class BCFConfig:
    CELL_COUNT = 16; BUFFER_SIZE = 2048
    BACKPRESSURE_THRESHOLD = 0.85; HEADROOM_FACTOR = 0.9

@dataclass
class FourPillarCapacity:
    dt_total: float = 151.0; dt_cpu: float = 24.0; dt_gpu: float = 127.0
    tu_total: int = 16; scl_total: float = 2000.0
    ml_total_bytes: int = 28 * 1024**3; gpu_vram_total_bytes: int = 10 * 1024**3
    dt_used: float = 0.0; dt_cpu_used: float = 0.0; dt_gpu_used: float = 0.0
    tu_used: int = 0; scl_used: float = 0.0
    ml_used_bytes: int = 0; gpu_vram_used_bytes: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def admit(self, dt, dt_cpu, dt_gpu, tu, scl, ml, gpu_vram):
        with self.lock:
            h = BCFConfig.HEADROOM_FACTOR
            if self.dt_used + dt > self.dt_total * h: return False, "dt"
            if self.dt_cpu_used + dt_cpu > self.dt_cpu * h: return False, "dt_cpu"
            if self.dt_gpu_used + dt_gpu > self.dt_gpu * h: return False, "dt_gpu"
            if self.tu_used + tu > int(self.tu_total * h): return False, "tu"
            if self.scl_used + scl > self.scl_total * h: return False, "scl"
            if self.ml_used_bytes + ml > int(self.ml_total_bytes * h): return False, "ml"
            if self.gpu_vram_used_bytes + gpu_vram > int(self.gpu_vram_total_bytes * h): return False, "gpu_vram"
            self.dt_used += dt; self.dt_cpu_used += dt_cpu; self.dt_gpu_used += dt_gpu
            self.tu_used += tu; self.scl_used += scl; self.ml_used_bytes += ml
            self.gpu_vram_used_bytes += gpu_vram
            return True, ""

    def release(self, dt, dt_cpu, dt_gpu, tu, scl, ml, gpu_vram):
        with self.lock:
            self.dt_used = max(0.0, self.dt_used - dt)
            self.dt_cpu_used = max(0.0, self.dt_cpu_used - dt_cpu)
            self.dt_gpu_used = max(0.0, self.dt_gpu_used - dt_gpu)
            self.tu_used = max(0, self.tu_used - tu)
            self.scl_used = max(0.0, self.scl_used - scl)
            self.ml_used_bytes = max(0, self.ml_used_bytes - ml)
            self.gpu_vram_used_bytes = max(0, self.gpu_vram_used_bytes - gpu_vram)

    def utilization(self):
        with self.lock:
            h = BCFConfig.HEADROOM_FACTOR
            return {
                "dt": self.dt_used / (self.dt_total * h), "dt_cpu": self.dt_cpu_used / (self.dt_cpu * h),
                "dt_gpu": self.dt_gpu_used / (self.dt_gpu * h), "tu": self.tu_used / (self.tu_total * h),
                "scl": self.scl_used / (self.scl_total * h), "ml": self.ml_used_bytes / (self.ml_total_bytes * h),
                "gpu_vram": self.gpu_vram_used_bytes / (self.gpu_vram_total_bytes * h),
            }

class WorkUnit:
    def __init__(self, task_id, payload, dt_cost=0.5, dt_cpu=0.1, dt_gpu=0.4, tu_cost=1, scl_cost=2.0, ml_cost=2*1024**2, gpu_vram_cost=0):
        self.task_id = task_id; self.payload = payload; self.dt_cost = dt_cost; self.dt_cpu = dt_cpu; self.dt_gpu = dt_gpu
        self.tu_cost = tu_cost; self.scl_cost = scl_cost; self.ml_cost_bytes = ml_cost; self.gpu_vram_cost = gpu_vram_cost
        self.submit_time = time.time(); self.start_time = None; self.end_time = None; self.admitted = False

class ExecutionCell(threading.Thread):
    def __init__(self, cell_id, buffer, capacity):
        super().__init__(name=f"Cell-{cell_id}", daemon=True)
        self.cell_id = cell_id; self.buffer = buffer; self.capacity = capacity
        self.work_buf_a = np.random.rand(1024, 1024).astype(np.float32)
        self.work_buf_b = np.random.rand(1024, 1024).astype(np.float32)
        self.result_buf = np.zeros((1024, 1024), dtype=np.float32)
        self.running = True; self.keepalive_mode = True; self.total_flops = 0; self.tasks_completed = 0; self.lock = threading.Lock()

    def run(self):
        while self.running:
            wu = self.buffer.pull()
            if wu is None:
                if self.keepalive_mode: self._keepalive_compute()
                time.sleep(0.001); continue
            wu.start_time = time.time()
            if self.keepalive_mode: self._keepalive_compute()
            else: self._real_compute(wu)
            wu.end_time = time.time()
            self.capacity.release(wu.dt_cost, wu.dt_cpu, wu.dt_gpu, wu.tu_cost, wu.scl_cost, wu.ml_cost_bytes, wu.gpu_vram_cost)
            with self.lock: self.tasks_completed += 1; self.total_flops += 2 * (1024 ** 3)
            self.buffer.push_result(wu)

    def _keepalive_compute(self):
        self.work_buf_a[:] = np.random.rand(1024, 1024).astype(np.float32)
        self.work_buf_b[:] = np.random.rand(1024, 1024).astype(np.float32)
        self.result_buf = np.dot(self.work_buf_a, self.work_buf_b)
        time.sleep(0.001)

    def _real_compute(self, wu):
        self.work_buf_a[:] = wu.payload
        self.work_buf_b[:] = np.random.rand(1024, 1024).astype(np.float32)
        self.result_buf = np.dot(self.work_buf_a, self.work_buf_b)
        time.sleep(0.003)

    def stop(self): self.running = False
    def get_metrics(self):
        with self.lock: return {"cell_id": self.cell_id, "tasks_completed": self.tasks_completed, "total_flops": self.total_flops, "keepalive_mode": self.keepalive_mode}

class GPUExecutionCell(ExecutionCell):
    def __init__(self, cell_id, buffer, capacity):
        super().__init__(cell_id, buffer, capacity)
        self.device = None; self.has_gpu = False; self._init_gpu()

    def _init_gpu(self):
        try:
            import torch
            if torch.cuda.is_available():
                self.device = torch.device("cuda:0"); self.has_gpu = True
                print(f"  [GPUCell-{self.cell_id}] RTX 3060 active")
            else:
                self.device = torch.device("cpu"); print(f"  [GPUCell-{self.cell_id}] CPU fallback")
        except ImportError:
            self.device = None; print(f"  [GPUCell-{self.cell_id}] PyTorch missing")

    def _real_compute(self, wu):
        if self.has_gpu and self.device:
            import torch
            a = torch.from_numpy(wu.payload).float().to(self.device)
            b = torch.randn(1024, 1024, device=self.device, dtype=torch.float32)
            result = torch.matmul(a, b); torch.cuda.synchronize()
            self.result_buf = result.cpu().numpy()
        else:
            super()._real_compute(wu)

class InterCellBuffer:
    def __init__(self, size): self.size = size; self.buffer = deque(maxlen=size); self.lock = threading.Lock()
    def push(self, wu): 
        with self.lock: self.buffer.append(wu)
    def pull(self):
        with self.lock:
            if len(self.buffer) == 0: return None
            return self.buffer.popleft()
    def push_result(self, wu): 
        with self.lock: pass
    def occupancy(self): 
        with self.lock: return len(self.buffer) / self.size

class FabricController:
    def __init__(self, cell_count, buffer, capacity, use_gpu=False):
        self.cell_count = cell_count; self.buffer = buffer; self.capacity = capacity; self.use_gpu = use_gpu
        self.cells = []; self.keepalive_mode = True; self.admitted_count = 0; self.rejected_count = 0
        self.rejected_by_pillar = {"dt": 0, "dt_cpu": 0, "dt_gpu": 0, "tu": 0, "scl": 0, "ml": 0, "gpu_vram": 0}
        self.lock = threading.Lock()

    def start_cells(self):
        if self.use_gpu:
            self.cells = [GPUExecutionCell(i, self.buffer, self.capacity) for i in range(self.cell_count)]
        else:
            self.cells = [ExecutionCell(i, self.buffer, self.capacity) for i in range(self.cell_count)]
        for c in self.cells: c.start()
        print(f"[Fabric] Started {self.cell_count} {'GPU' if self.use_gpu else 'CPU'} cells")

    def stop_cells(self):
        for c in self.cells: c.stop()
        for c in self.cells: c.join(timeout=2.0)
        print("[Fabric] All cells stopped")

    def toggle_keepalive(self, mode):
        with self.lock: self.keepalive_mode = mode
        for c in self.cells: c.keepalive_mode = mode

    def inject_work(self, wu):
        with self.lock:
            if self.buffer.occupancy() > BCFConfig.BACKPRESSURE_THRESHOLD:
                self.rejected_count += 1; return False, "buffer_full"
            admitted, reason = self.capacity.admit(wu.dt_cost, wu.dt_cpu, wu.dt_gpu, wu.tu_cost, wu.scl_cost, wu.ml_cost_bytes, wu.gpu_vram_cost)
            if not admitted:
                self.rejected_count += 1; self.rejected_by_pillar[reason] += 1; return False, reason
            wu.admitted = True; self.admitted_count += 1; self.buffer.push(wu); return True, ""

    def get_metrics(self):
        with self.lock:
            cm = [c.get_metrics() for c in self.cells]
            return {
                "cell_count": len(self.cells), "total_flops": sum(c["total_flops"] for c in cm),
                "total_tasks": sum(c["tasks_completed"] for c in cm),
                "buffer_occupancy": self.buffer.occupancy(), "admitted": self.admitted_count,
                "rejected": self.rejected_count, "rejected_by_pillar": dict(self.rejected_by_pillar),
            }

class CrossFabricArbitrator:
    def __init__(self): self.fabrics = {}; self.tokens = {}; self.lock = threading.Lock()
    def register_fabric(self, name, fabric):
        with self.lock: self.fabrics[name] = fabric; self.tokens[name] = 100.0
    def admit_work(self, name, wu):
        with self.lock:
            f = self.fabrics.get(name)
            if not f: return False, "no_fabric"
            if self.tokens[name] < 1.0: return False, "token_bucket"
            self.tokens[name] -= 1.0; return f.inject_work(wu)
    def replenish_tokens(self):
        with self.lock:
            for n in self.tokens: self.tokens[n] = min(100.0, self.tokens[n] + 10.0)

def main():
    print("=" * 70); print("BLACKSTAR BCF — PIONEER 1"); print("=" * 70)
    capacity = FourPillarCapacity(**REAL_CAPACITY)
    print(f"[4P] DT={capacity.dt_total} (CPU={capacity.dt_cpu}, GPU={capacity.dt_gpu}) TU={capacity.tu_total}")

    use_gpu = False
    try:
        import torch
        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info()
            print(f"[CUDA] {torch.cuda.get_device_name(0)} | Free: {free/1024**3:.1f}GB / {total/1024**3:.1f}GB")
            if free > 2 * 1024**3: use_gpu = True; print("[CUDA] GPU mode ENABLED")
            else: print(f"[CUDA] Only {free/1024**3:.1f}GB free — free VRAM first")
        else: print("[CUDA] Not available")
    except ImportError: print("[CUDA] PyTorch not installed")

    buffer = InterCellBuffer(BCFConfig.BUFFER_SIZE)
    fabric = FabricController(BCFConfig.CELL_COUNT, buffer, capacity, use_gpu=use_gpu)
    fabric.start_cells()
    arb = CrossFabricArbitrator()
    arb.register_fabric("pioneer1", fabric)

    RUN_TIME = 20.0; start = time.time(); task_counter = 0
    print(f"[Main] Running {RUN_TIME}s...")

    while time.time() - start < RUN_TIME:
        time.sleep(0.05)
        for _ in range(random.randint(2, 10)):
            task_counter += 1
            profile = random.choice([
                {"dt": 0.5, "dt_cpu": 0.4, "dt_gpu": 0.1, "tu": 1, "scl": 2.0, "ml": 2*1024**2, "vram": 0, "label": "cpu_light"},
                {"dt": 1.0, "dt_cpu": 0.2, "dt_gpu": 0.8, "tu": 1, "scl": 5.0, "ml": 4*1024**2, "vram": 512*1024**2, "label": "gpu_medium"},
                {"dt": 3.0, "dt_cpu": 0.5, "dt_gpu": 2.5, "tu": 2, "scl": 10.0, "ml": 8*1024**2, "vram": 1024*1024**2, "label": "gpu_heavy"},
                {"dt": 8.0, "dt_cpu": 1.0, "dt_gpu": 7.0, "tu": 4, "scl": 25.0, "ml": 16*1024**2, "vram": 2*1024**3, "label": "training"},
            ])
            payload = np.random.rand(1024, 1024).astype(np.float32)
            wu = WorkUnit(task_counter, payload, dt_cost=profile["dt"], dt_cpu=profile["dt_cpu"], dt_gpu=profile["dt_gpu"],
                          tu_cost=profile["tu"], scl_cost=profile["scl"], ml_cost=profile["ml"], gpu_vram_cost=profile["vram"] if use_gpu else 0)
            admitted, reason = arb.admit_work("pioneer1", wu)
            if not admitted and task_counter % 20 == 0: print(f"  [REJECT] {task_counter} ({profile['label']}): {reason}")

        if random.random() < 0.03: fabric.toggle_keepalive(not fabric.keepalive_mode)
        arb.replenish_tokens()

        if int(time.time() - start) % 3 == 0 and random.random() < 0.3:
            u = capacity.utilization()
            print(f"[t={time.time()-start:.1f}s] 4P: DT={u['dt']:.0%} CPU={u['dt_cpu']:.0%} GPU={u['dt_gpu']:.0%} TU={u['tu']:.0%} VRAM={u['gpu_vram']:.0%}")

    fabric.stop_cells()
    fm = fabric.get_metrics(); total_time = time.time() - start
    gflops = (fm["total_flops"] / total_time) / 1e9

    print(); print("=" * 70); print("COMPLETE")
    print(f"Submitted: {task_counter} | Admitted: {fm['admitted']} | Rejected: {fm['rejected']}")
    print(f"Rejection rate: {fm['rejected']/(task_counter or 1):.1%}")
    for pillar, count in fm['rejected_by_pillar'].items():
        if count > 0: print(f"  {pillar}: {count}")
    print(f"GFLOPS: {gflops:.2f} | Buffer: {fm['buffer_occupancy']:.1%}")
    print(f"Final: {capacity.utilization()}")
    print("=" * 70)

if __name__ == "__main__":
    main()
EOF
