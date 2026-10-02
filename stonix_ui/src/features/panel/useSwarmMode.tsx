import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

type SwarmModeContextValue = {
  swarmMode: boolean;
  setSwarmMode: (next: boolean | ((prev: boolean) => boolean)) => void;
  toggleSwarmMode: () => void;
};

const SwarmModeContext = createContext<SwarmModeContextValue | null>(null);

export function SwarmModeProvider({ children }: { children: ReactNode }) {
  const [swarmMode, setSwarmMode] = useState(false);
  const toggleSwarmMode = useCallback(() => {
    setSwarmMode(prev => !prev);
  }, []);

  const value = useMemo(
    () => ({ swarmMode, setSwarmMode, toggleSwarmMode }),
    [swarmMode, toggleSwarmMode],
  );

  return createElement(SwarmModeContext.Provider, { value }, children);
}

export function useSwarmMode(): SwarmModeContextValue {
  const ctx = useContext(SwarmModeContext);
  if (!ctx) {
    throw new Error('useSwarmMode must be used within SwarmModeProvider');
  }
  return ctx;
}
