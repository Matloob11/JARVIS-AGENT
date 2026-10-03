import { useEffect, useRef } from 'react';
import { toast } from '@/toast';
import { useSocketEvent } from '@/shared/realtime/subscriptions';

const DESKTOP_CODE = /^DESKTOP_[A-Z0-9_]+$/;

function desktopToastMessage(code: string, title?: string, details?: string): string {
  if (title?.trim()) return title.trim();
  if (details?.trim()) return details.trim();
  return code.replace(/_/g, ' ');
}

export function useDesktopAlertToasts(): void {
  const toasted = useRef(new Set<string>());

  useEffect(() => {
    toasted.current.clear();
  }, []);

  useSocketEvent('system_alert', (raw: unknown) => {
    if (!raw || typeof raw !== 'object') return;
    const row = raw as Record<string, unknown>;
    const code = row.code != null ? String(row.code) : '';
    if (!DESKTOP_CODE.test(code)) return;
    const id = row.id != null ? String(row.id) : code;
    const dedupeKey = `${id}:${row.count ?? 1}`;
    if (toasted.current.has(dedupeKey)) return;
    toasted.current.add(dedupeKey);
    const title = row.title != null ? String(row.title) : undefined;
    toast(desktopToastMessage(code, title), {
      icon: '💻',
      duration: 9000,
      id: `desktop-alert-${dedupeKey}`,
    });
  });

  useSocketEvent('tool_update', (raw: unknown) => {
    if (!raw || typeof raw !== 'object') return;
    const row = raw as Record<string, unknown>;
    const code =
      row.error_code != null
        ? String(row.error_code)
        : row.errorCode != null
          ? String(row.errorCode)
          : '';
    if (!DESKTOP_CODE.test(code)) return;
    const tool = row.tool != null ? String(row.tool) : 'tool';
    const ts = row.ts ?? row.timestamp ?? Date.now();
    const dedupeKey = `${code}-${tool}-${ts}`;
    if (toasted.current.has(dedupeKey)) return;
    toasted.current.add(dedupeKey);
    const details =
      row.details != null
        ? String(row.details)
        : row.result_summary != null
          ? String(row.result_summary)
          : row.message != null
            ? String(row.message)
            : undefined;
    toast(`${tool}: ${desktopToastMessage(code, undefined, details)}`, {
      icon: '⚠️',
      duration: 9000,
      id: `desktop-tool-${dedupeKey}`,
    });
  });
}
