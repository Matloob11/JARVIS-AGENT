import psutil
import os
import signal

def kill_ui_bridge():
    print("Searching for ui_bridge.py process...")
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info.get('cmdline')
            if cmdline and 'ui_bridge.py' in ' '.join(cmdline):
                pid = proc.info['pid']
                print(f"Found UI Bridge at PID: {pid}. Terminating...")
                os.kill(pid, signal.SIGTERM)
                print("Signal sent.")
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    print("UI Bridge process not found.")
    return False

if __name__ == "__main__":
    kill_ui_bridge()
