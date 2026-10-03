import { useCallback, useEffect, useState, type RefObject } from 'react';
import { Volume2, VolumeX } from 'lucide-react';
import { Popover } from './popovers/Popover';
import { useAuth } from '@/features/auth/AuthProvider';
import { isNotificationAcked, sendNotificationAck } from '@/features/notifications/notificationAck';
import type { TaskNotification } from '@/features/tasks/useTasks';
import { StatusShape } from '@/shared/ui/StatusShape';

function formatNotifTime(tsMs: number): string {
  return new Date(tsMs).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

function formatSessionEnd(expiresAt: number | undefined): string {
  if (expiresAt == null || !Number.isFinite(expiresAt)) return 'Session end unknown';
  const ms = expiresAt > 1e12 ? expiresAt : expiresAt * 1000;
  const clock = new Date(ms).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
  return `Session ends ${clock}`;
}

function notificationPermission(): NotificationPermission | 'unsupported' {
  if (typeof Notification === 'undefined') return 'unsupported';
  return Notification.permission;
}

export type AccountMenuProps = {
  open: boolean;
  onClose: () => void;
  triggerRef: RefObject<HTMLButtonElement | null>;
  sheet: boolean;
  taskNotifications: TaskNotification[];
  onLogout: () => void;
};

export function AccountMenu({
  open,
  onClose,
  triggerRef,
  sheet,
  taskNotifications,
  onLogout,
}: AccountMenuProps) {
  const { user } = useAuth();
  const [perm, setPerm] = useState<NotificationPermission | 'unsupported'>(notificationPermission);
  const [ackedTick, setAckedTick] = useState(0);

  const rowIds = taskNotifications
    .slice(0, 20)
    .map(note => note.id)
    .join('|');

  useEffect(() => {
    if (!open) return;
    for (const id of rowIds.split('|')) {
      if (id) sendNotificationAck(id);
    }
    setAckedTick(n => n + 1);
  }, [open, rowIds]);

  const requestDesktop = useCallback(() => {
    if (typeof Notification === 'undefined') return;
    void Notification.requestPermission().then(next => setPerm(next));
  }, []);

  const permKind = perm === 'granted' ? 'ok' : perm === 'denied' ? 'err' : perm === 'unsupported' ? 'off' : 'warn';
  const permWord =
    perm === 'granted' ? 'on' : perm === 'denied' ? 'blocked in browser settings' : perm === 'unsupported' ? 'off' : 'off';

  return (
    <Popover
      open={open}
      onClose={onClose}
      triggerRef={triggerRef}
      ariaLabel="Account and reminders"
      sheet={sheet}
      data-testid="task-bell-menu"
    >
      <div className="neon-pop-sec">
        <b>Matloob (owner)</b>
        <p className="neon-pop-note m-0 mt-1">{formatSessionEnd(user?.expires_at)}</p>
      </div>

      <div className="neon-pop-sec">
        <b>Reminders</b>
        {taskNotifications.slice(0, 20).length === 0 ? (
          <p className="neon-pop-note m-0 mt-2">No task alerts yet</p>
        ) : (
          <ul className="task-bell-menu__list mt-2">
            {taskNotifications.slice(0, 20).map(note => {
              const delivered = ackedTick >= 0 && isNotificationAcked(note.id);
              return (
                <li key={`${note.id}-${note.ts}`} className="task-bell-row" data-testid="task-notif-row">
                  <p className="task-bell-row__text">{note.description}</p>
                  <p className="task-bell-row__meta">
                    {note.due_at ? `Due ${formatNotifTime(note.due_at)}` : formatNotifTime(note.ts)}
                    {note.spoken === true ? (
                      <span className="task-bell-row__spoken" data-testid="task-notif-spoken">
                        <Volume2 size={12} aria-hidden />
                        Spoken aloud
                      </span>
                    ) : null}
                    {note.spoken === false ? (
                      <span className="task-bell-row__spoken" data-testid="task-notif-screen-only">
                        <VolumeX size={12} aria-hidden />
                        Screen only
                      </span>
                    ) : null}
                    <span className="task-bell-row__ack">
                      {delivered ? '✓ Delivered' : '▲ Not acknowledged yet'}
                    </span>
                  </p>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <div className="neon-pop-foot">
        <p className="neon-pop-note m-0 flex items-center gap-2">
          Desktop notifications{' '}
          <StatusShape s={permKind} label={permWord} />
        </p>
        {perm === 'default' ? (
          <button type="button" className="n-btn sm mt-2" onClick={requestDesktop}>
            Turn on
          </button>
        ) : null}
        <a className="neon-pop-link mt-2" href="#/settings">
          Settings
        </a>
        <button type="button" className="n-btn sm danger mt-2" data-testid="logout" onClick={onLogout}>
          Sign out
        </button>
      </div>
    </Popover>
  );
}

export default AccountMenu;
