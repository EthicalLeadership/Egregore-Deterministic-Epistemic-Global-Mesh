#!/usr/bin/env python3
# ============================================================
# PIONEER 1 HARDWARE PROBE
# ============================================================

import subprocess
import json
import os

def run(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, text=True).strip()
    except:
        return "N/A"

def probe_cpu():
    model = run("cat /proc/cpuinfo | grep 'model name' | head -1 | cut -d: -f2 | xargs")
    cores = run("nproc --all")
    threads = run("nproc")
    freq = run("cat /proc/cpuinfo | grep 'cpu MHz' | head -1 | cut -d: -f2 | xargs")
    return {
        "model": model,
        "cores": int(cores) if cores.isdigit() else 0,
        "threads": int(threads) if threads.isdigit() else 0,
        "freq_mhz": float(freq) if freq.replace('.','').isdigit() else 0,
    }

def probe_ram():
    total_kb = run("cat /proc/meminfo | grep MemTotal | awk '{print $2}'")
    available_kb = run("cat /proc/meminfo | grep MemAvailable | awk '{print $2}'")
    total_mb = int(total_kb) // 1024 if total_kb.isdigit() else 0
    available_mb = int(available_kb) // 1024 if available_kb.isdigit() else 0
    return {"total_mb": total_mb, "available_mb": available_mb, "total_gb": round(total_mb / 1024, 1)}

def probe_gpu():
    nvidia = run("nvidia-smi --query-gpu=name,memory.total,memory.free,utilization.gpu,clocks.current.sm --format=csv,noheader 2>/dev/null")
    if nvidia and nvidia != "N/A":
        parts = [p.strip() for p in nvidia.split(",")]
        return {
            "vendor": "NVIDIA",
            "model": parts[0] if len(parts) > 0 else "Unknown",
            "vram_total_mb": int(parts[1].replace(" MiB","")) if len(parts) > 1 else 0,
            "vram_free_mb": int(parts[2].replace(" MiB","")) if len(parts) > 2 else 0,
            "utilization": parts[3] if len(parts) > 3 else "N/A",
            "clock_mhz": int(parts[4].replace(" MHz","")) if len(parts) > 4 else 0,
        }
    return {"vendor": "None", "model": "No GPU detected", "vram_total_mb": 0}

def probe_os():
    return {"kernel": run("uname -r"), "distro": run("cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2 | tr -d '\\\"'")}

def main():
    print("=" * 60)
    print("PIONEER 1 HARDWARE PROBE")
    print("=" * 60)
    
    profile = {
        "hostname": run("hostname"),
        "os": probe_os(),
        "cpu": probe_cpu(),
        "ram": probe_ram(),
        "gpu": probe_gpu(),
    }
    
    print(json.dumps(profile, indent=2))
    
    # Estimate DT
    cpu_gflops = profile["cpu"]["cores"] * 15.0
    gpu_gflops = 6500.0 if "2060" in profile["gpu"]["model"] else 0
    total_dt = (cpu_gflops + gpu_gflops) / 10.0
    
    print()
    print("=" * 60)
    print("FOUR-PILLAR CAPACITY")
    print("=" * 60)
    fp = {
        "dt_total": round(total_dt, 1),
        "tu_total": profile["cpu"]["cores"],
        "scl_total": 1000.0,
        "ml_total_bytes": profile["ram"]["total_mb"] * 1024 * 1024,
        "gpu_vram_bytes": profile["gpu"].get("vram_total_mb", 0) * 1024 * 1024,
    }
    print(json.dumps(fp, indent=2))
    
    with open("pioneer1_profile.json", "w") as f:
        json.dump(profile, f, indent=2)
    with open("pioneer1_capacity.json", "w") as f:
        json.dump(fp, f, indent=2)
    
    print()
    print("Saved: pioneer1_profile.json")
    print("Saved: pioneer1_capacity.json")

if __name__ == "__main__":
    main()

