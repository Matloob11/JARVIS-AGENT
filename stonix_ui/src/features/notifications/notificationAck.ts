import { getSocket } from '@/shared/realtime/socket';

const ackedIds = new Set<string>();

export function isNotificationAcked(id: string): boolean {
  return ackedIds.has(id.trim());
}

export function sendNotificationAck(id: string): void {
  const trimmed = id.trim();
  if (!trimmed || ackedIds.has(trimmed)) return;
  ackedIds.add(trimmed);
  getSocket().emit('ui_command', { type: 'notification_ack', payload: { id: trimmed } });
}
