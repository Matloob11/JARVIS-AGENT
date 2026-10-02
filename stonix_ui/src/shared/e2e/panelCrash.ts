let crashTarget: string | null = null;
const listeners = new Set<() => void>();

export function getE2EPanelCrashTarget(): string | null {
  return crashTarget;
}

export function setE2EPanelCrashTarget(name: string | null): void {
  crashTarget = name;
  listeners.forEach(listener => listener());
}

export function clearE2EPanelCrash(): void {
  setE2EPanelCrashTarget(null);
}

export function subscribeE2EPanelCrash(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
