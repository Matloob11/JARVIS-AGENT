import psutil
import time
import os

def profile_production_processes():
    print("--- JARVIS PRODUCTION MEMORY PROFILER ---")
    print("Monitoring UI_BRIDGE and AGENT_RUNNER for 60 seconds...")
    
    targets = ["ui_bridge.py", "agent_runner.py"]
    profiles = {name: [] for name in targets}
    
    start_time = time.time()
    while time.time() - start_time < 60:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'memory_info']):
            try:
                cmdline = proc.info.get('cmdline')
                if cmdline:
                    cmd_str = ' '.join(cmdline)
                    for target in targets:
                        if target in cmd_str:
                            mem_mb = proc.info['memory_info'].rss / (1024 * 1024)
                            profiles[target].append(mem_mb)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        
        time.sleep(5)
        print(f"Sampling... {int(60 - (time.time() - start_time))}s remaining")

    print("\n--- PERFORMANCE SNAPSHOT ---")
    for name, data in profiles.items():
        if data:
            start_mem = data[0]
            end_mem = data[-1]
            diff = end_mem - start_mem
            print(f"Service: {name}")
            print(f"  Start Memory: {start_mem:.2f} MB")
            print(f"  End Memory:   {end_mem:.2f} MB")
            print(f"  Drift:       {diff:+.2f} MB")
            if diff > 5.0:
                print("  ALERT: Possible memory leak detected!")
            else:
                print("  STATUS: Memory stable.")
        else:
            print(f"Service: {name} - NOT FOUND")

if __name__ == "__main__":
    profile_production_processes()
