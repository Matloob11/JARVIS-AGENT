import { useCallback, useState } from 'react';
import { toast } from '@/toast';
import { getSocket } from '@/shared/realtime/socket';
import { handleUiCommandAck } from '@/features/status/AgentControls';
import type { DesktopStatusSnapshot } from '@/features/status/desktopStatusStore';
import { laptopStopDisabled, laptopStopVisible } from '@/features/status/laptopChipPresentation';

type UiCommandAck = {
  status: 'sent' | 'queued' | 'agent_offline' | 'no_agent' | 'rejected';
  reason?: string;
};

export type StopDesktopButtonProps = {
  desktop: DesktopStatusSnapshot;
  testId: 'desktop-stop-menu' | 'desktop-stop';
  className?: string;
};

const STOP_HINT =
  'Stop switches the laptop to read-only. Only the laptop tray can switch back to Full.';

export function StopDesktopButton({ desktop, testId, className }: StopDesktopButtonProps) {
  // Inventory anchors: data-testid="desktop-stop" data-testid="desktop-stop-menu"
  const [pending, setPending] = useState(false);

  const visible = laptopStopVisible(desktop);
  const disabled = laptopStopDisabled(desktop) || pending;

  const onStop = useCallback(() => {
    if (disabled) return;
    setPending(true);
    getSocket().emit('ui_command', { type: 'desktop_stop', payload: {} }, (ack: UiCommandAck) => {
      setPending(false);
      handleUiCommandAck(ack);
      if (ack.status === 'sent' || ack.status === 'queued') {
        toast('Desktop switched to read-only on the laptop.', { icon: '⏸', duration: 8000 });
      }
    });
  }, [disabled]);

  if (!visible) return null;

  return (
    <div className={className}>
      <button
        type="button"
        className="n-btn sm danger"
        data-testid={testId}
        disabled={disabled}
        title={STOP_HINT}
        onClick={() => onStop()}
      >
        {pending ? 'Stopping…' : 'Stop desktop'}
      </button>
      <p className="neon-pop-note m-0 mt-2">{STOP_HINT}</p>
    </div>
  );
}

export default StopDesktopButton;
