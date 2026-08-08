import threading, time, random, numpy as np
from collections import deque

class P:
    def __init__(self):
        self.dt, self.dtc, self.dtg, self.tu = 151.0, 24.0, 127.0, 16
        self.scl, self.ml, self.vr = 2000.0, 28*1024**3, 10*1024**3
        self.du, self.duc, self.dug, self.tuu = 0, 0, 0, 0
        self.sclu, self.mlu, self.vru = 0, 0, 0
        self.lk = threading.Lock()
    def a(self, dt, dtc, dtg, tu, scl, ml, vr):
        with self.lk:
            h = 0.9
            if self.du + dt > self.dt * h: return False, "dt"
            if self.duc + dtc > self.dtc * h: return False, "dtc"
            if self.dug + dtg > self.dtg * h: return False, "dtg"
            if self.tuu + tu > int(self.tu * h): return False, "tu"
            if self.sclu + scl > self.scl * h: return False, "scl"
            if self.mlu + ml > int(self.ml * h): return False, "ml"
            if self.vru + vr > int(self.vr * h): return False, "vr"
            self.du += dt; self.duc += dtc; self.dug += dtg
            self.tuu += tu; self.sclu += scl; self.mlu += ml; self.vru += vr
            return True, ""
    def r(self, dt, dtc, dtg, tu, scl, ml, vr):
        with self.lk:
            self.du -= dt; self.duc -= dtc; self.dug -= dtg
            self.tuu -= tu; self.sclu -= scl; self.mlu -= ml; self.vru -= vr
    def u(self):
        with self.lk:
            h = 0.9
            return {"dt": self.du/(self.dt*h), "dtc": self.duc/(self.dtc*h),
                    "dtg": self.dug/(self.dtg*h), "tu": self.tuu/(self.tu*h),
                    "scl": self.sclu/(self.scl*h), "ml": self.mlu/(self.ml*h),
                    "vr": self.vru/(self.vr*h)}

class E(threading.Thread):
    def __init__(self, i, b, p):
        super().__init__(daemon=True)
        self.i, self.b, self.p = i, b, p
        self.run = True; self.k = True; self.tf = 0; self.tc = 0
    def run(self):
        while self.run:
            w = self.b.popleft() if len(self.b) > 0 else None
            if w is None:
                if self.k:
                    a = np.random.rand(1024, 1024).astype(np.float32)
                    b = np.random.rand(1024, 1024).astype(np.float32)
                    np.dot(a, b); time.sleep(0.001)
                continue
            dt, dtc, dtg, tu, scl, ml, vr = w
            a = np.random.rand(1024, 1024).astype(np.float32)
            b = np.random.rand(1024, 1024).astype(np.float32)
            np.dot(a, b); time.sleep(0.003)
            self.p.r(dt, dtc, dtg, tu, scl, ml, vr)
            self.tc += 1; self.tf += 2*1024**3
    def stop(self): self.run = False

def main():
    print("=" * 50); print("EGREGORE BCF — PIONEER 1"); print("=" * 50)
    p = P()
    print(f"[4P] DT={p.dt}(C={p.dtc},G={p.dtg}) TU={p.tu}")
    g = False
    try:
        import torch
        if torch.cuda.is_available():
            f, t = torch.cuda.mem_get_info()
            print(f"[CUDA] {torch.cuda.get_device_name(0)} | {f/1024**3:.1f}G/{t/1024**3:.1f}G")
            if f > 2*1024**3: g = True; print("[CUDA] GPU ON")
            else: print(f"[CUDA] Only {f/1024**3:.1f}G free")
        else: print("[CUDA] No CUDA")
    except ImportError: print("[CUDA] No PyTorch")
    b = deque(maxlen=2048)
    cells = [E(i, b, p) for i in range(16)]
    for c in cells: c.start()
    T = 20.0; s = time.time(); tc = 0
    print(f"[Main] Running {T}s...")
    while time.time() - s < T:
        time.sleep(0.05)
        for _ in range(random.randint(2, 10)):
            tc += 1
            pr = random.choice([
                (0.5, 0.4, 0.1, 1, 2.0, 2*1024**2, 0),
                (1.0, 0.2, 0.8, 1, 5.0, 4*1024**2, 512*1024**2),
                (3.0, 0.5, 2.5, 2, 10.0, 8*1024**2, 1024*1024**2),
                (8.0, 1.0, 7.0, 4, 25.0, 16*1024**2, 2*1024**3),
            ])
            dt, dtc, dtg, tu, scl, ml, vr = pr
            if not g: vr = 0
            a, r = p.a(dt, dtc, dtg, tu, scl, ml, vr)
            if a: b.append((dt, dtc, dtg, tu, scl, ml, vr))
            elif tc % 20 == 0: print(f"  [REJECT] {tc}({r})")
        if int(time.time() - s) % 3 == 0 and random.random() < 0.3:
            u = p.u()
            print(f"[t={time.time()-s:.1f}s] DT={u['dt']:.0%} C={u['dtc']:.0%} G={u['dtg']:.0%} TU={u['tu']:.0%} VR={u['vr']:.0%}")
    for c in cells: c.stop()
    for c in cells: c.join(timeout=2.0)
    print(); print("=" * 50); print("DONE")
    print(f"Tasks: {tc} | Final: {p.u()}")
    print("=" * 50)

if __name__ == "__main__":
    main()

