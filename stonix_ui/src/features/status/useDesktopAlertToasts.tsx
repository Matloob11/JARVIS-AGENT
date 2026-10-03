import { useEffect, useRef } from 'react';
import { toast } from '@/toast';
import { useSocketEvent } from '@/shared/realtime/subscriptions';
import { isRelayMediaFileUrl, resolveMediaUrl } from '@/features/intelligence/mediaUrl';

const DESKTOP_CODE = /^DESKTOP_[A-Z0-9_]+$/;

function desktopToastMessage(code: string, title?: string, details?: string): string {
  if (title?.trim()) return title.trim();
  if (details?.trim()) return details.trim();
  return code.replace(/_/g, ' ');
}

function asRecord(raw: unknown): Record<string, unknown> | null {
  return raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : null;
}

function pickUrl(raw: unknown): string | undefined {
  if (typeof raw !== 'string' || !raw.trim()) return undefined;
  const resolved = resolveMediaUrl(raw.trim());
  if (isRelayMediaFileUrl(resolved) || /^https?:\/\//i.test(resolved) || resolved.startsWith('/')) {
    return resolved;
  }
  return undefined;
}

function mediaActions(row: Record<string, unknown>): { play?: string; download?: string } {
  const artifact = asRecord(row.artifact);
  const play =
    pickUrl(row.play_url) ||
    pickUrl(row.url) ||
    pickUrl(artifact?.url) ||
    pickUrl(artifact?.play_url);
  const download =
    pickUrl(row.download_url) ||
    pickUrl(artifact?.download_url) ||
    pickUrl(artifact?.url) ||
    play;
  return { play, download };
}

function DesktopToastBody({
  text,
  play,
  download,
}: {
  text: string;
  play?: string;
  download?: string;
}) {
  return (
    <div className="neon-toast__stack">
      <p className="neon-toast__text">{text}</p>
      {play || download ? (
        <p className="neon-toast__actions">
          {play ? (
            <a className="neon-toast__action" href={play} target="_blank" rel="noreferrer">
              Play
            </a>
          ) : null}
          {download ? (
            <a className="neon-toast__action" href={download} download>
              Download
            </a>
          ) : null}
        </p>
      ) : null}
    </div>
  );
}

export function useDesktopAlertToasts(): void {
  const toasted = useRef(new Set<string>());

  useEffect(() => {
    toasted.current.clear();
  }, []);

  useSocketEvent('system_alert', (raw: unknown) => {
    const row = asRecord(raw);
    if (!row) return;
    const code = row.code != null ? String(row.code) : '';
    if (!DESKTOP_CODE.test(code)) return;
    const id = row.id != null ? String(row.id) : code;
    const dedupeKey = `${id}:${row.count ?? 1}`;
    if (toasted.current.has(dedupeKey)) return;
    toasted.current.add(dedupeKey);
    const title = row.title != null ? String(row.title) : undefined;
    const media = mediaActions(row);
    toast(<DesktopToastBody text={desktopToastMessage(code, title)} {...media} />, {
      duration: 9000,
      id: `desktop-alert-${dedupeKey}`,
    });
  });

  useSocketEvent('tool_update', (raw: unknown) => {
    const row = asRecord(raw);
    if (!row) return;
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
    const media = mediaActions(row);
    toast(
      <DesktopToastBody text={`${tool}: ${desktopToastMessage(code, undefined, details)}`} {...media} />,
      {
        duration: 9000,
        id: `desktop-tool-${dedupeKey}`,
      },
    );
  });
}
