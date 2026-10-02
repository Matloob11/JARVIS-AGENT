import { useEffect, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

type AgentsMountPortalProps = {
  selector: string;
  children: ReactNode;
};

/** Portal into Agents page mount slots while keeping the React tree mounted from Dashboard. */
export function AgentsMountPortal({ selector, children }: AgentsMountPortalProps) {
  const [target, setTarget] = useState<HTMLElement | null>(null);

  useEffect(() => {
    const sync = () => setTarget(document.querySelector<HTMLElement>(selector));
    sync();
    const observer = new MutationObserver(sync);
    observer.observe(document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [selector]);

  if (!target) return null;
  return createPortal(children, target);
}

export default AgentsMountPortal;
