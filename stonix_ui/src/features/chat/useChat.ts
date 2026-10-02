import { useCallback, useEffect, useRef, useState } from 'react';
import { normalizeMessage } from '@/shared/contracts/events';
import { bridgeUrl } from '@/shared/config/runtime';
import { getSocket } from '@/shared/realtime/socket';
import { useSocketEvent } from '@/shared/realtime/subscriptions';
import { useSettings } from '@/features/settings/useSettings';
import { useLiveKit } from '@/features/voice/LiveKitProvider';
import { asPersonaKey, replyStatusHint } from '@/shared/persona';
import { registerLiveCaptions } from './liveCaptions';
import type { ChatMessage, LiveCaption } from './types';

const MAX_MESSAGES = 300;
const DELIVERY_TIMEOUT_MS = 20_000;
const CAPTION_CLEAR_AFTER_FINAL_MS = 10_000;

type UiCommandAck = {
  status: 'sent' | 'queued' | 'agent_offline' | 'no_agent' | 'rejected';
  reason?: string;
};

function rawToChatMessage(raw: unknown, existing?: ChatMessage): ChatMessage {
  const n = normalizeMessage(raw);
  const id = n.id || existing?.id || crypto.randomUUID();
  const data = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const personaRaw = data.persona ?? existing?.persona;

  return {
    id,
    role: n.role,
    text: n.text,
    ts: n.ts,
    source: n.source ?? existing?.source,
    turn_id: n.turn_id ?? existing?.turn_id,
    final: n.final ?? existing?.final,
    status: existing?.status,
    statusHint: existing?.statusHint,
    persona: personaRaw != null ? asPersonaKey(personaRaw) : existing?.persona,
  };
}

function sortMessages(list: ChatMessage[]): ChatMessage[] {
  return [...list].sort((a, b) => a.ts - b.ts || a.id.localeCompare(b.id));
}

function trimMessages(map: Map<string, ChatMessage>): ChatMessage[] {
  const sorted = sortMessages([...map.values()]);
  if (sorted.length <= MAX_MESSAGES) return sorted;
  const drop = sorted.length - MAX_MESSAGES;
  for (let i = 0; i < drop; i += 1) {
    map.delete(sorted[i].id);
  }
  return sortMessages([...map.values()]);
}

export function useChat() {
  const mapRef = useRef(new Map<string, ChatMessage>());
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [liveCaptions, setLiveCaptions] = useState<{
    user: LiveCaption | null;
    assistant: LiveCaption | null;
  }>({ user: null, assistant: null });

  const pendingDeliveryRef = useRef(new Map<string, number>());
  const awaitingReplyRef = useRef(new Set<string>());
  const resendWhenOnlineRef = useRef(new Set<string>());
  const captionTimersRef = useRef(new Map<'user' | 'assistant', number>());
  const agentOnlineRef = useRef(true);
  const voice = useLiveKit();
  const { persona } = useSettings();
  const personaRef = useRef(persona);
  personaRef.current = persona;

  const syncFromMap = useCallback(() => {
    setMessages(trimMessages(mapRef.current));
  }, []);

  const clearCaptionTimer = useCallback((role: 'user' | 'assistant') => {
    const t = captionTimersRef.current.get(role);
    if (t != null) {
      window.clearTimeout(t);
      captionTimersRef.current.delete(role);
    }
  }, []);

  const clearLiveCaption = useCallback(
    (role: 'user' | 'assistant') => {
      clearCaptionTimer(role);
      setLiveCaptions(prev => (prev[role] ? { ...prev, [role]: null } : prev));
    },
    [clearCaptionTimer],
  );

  const scheduleCaptionClear = useCallback(
    (role: 'user' | 'assistant') => {
      clearCaptionTimer(role);
      captionTimersRef.current.set(
        role,
        window.setTimeout(() => clearLiveCaption(role), CAPTION_CLEAR_AFTER_FINAL_MS),
      );
    },
    [clearCaptionTimer, clearLiveCaption],
  );

  const upsertRaw = useCallback(
    (raw: unknown) => {
      const n = normalizeMessage(raw);
      if (!n.id) return;
      const existing = mapRef.current.get(n.id);
      let next = rawToChatMessage(raw, existing);

      if (next.role === 'assistant' && !next.persona) {
        next = { ...next, persona: personaRef.current };
      }

      if (next.role === 'user' && awaitingReplyRef.current.has(next.id)) {
        next = {
          ...next,
          status: 'delivered',
          statusHint: replyStatusHint(personaRef.current),
        };
      }

      if (next.role === 'user' && pendingDeliveryRef.current.has(next.id)) {
        window.clearTimeout(pendingDeliveryRef.current.get(next.id));
        pendingDeliveryRef.current.delete(next.id);
        next = {
          ...next,
          status: 'delivered',
          statusHint: replyStatusHint(personaRef.current),
        };
        awaitingReplyRef.current.add(next.id);
      }

      if (next.role === 'assistant' && next.turn_id && awaitingReplyRef.current.has(next.turn_id)) {
        awaitingReplyRef.current.delete(next.turn_id);
        const userRow = mapRef.current.get(next.turn_id);
        if (userRow?.statusHint) {
          mapRef.current.set(next.turn_id, { ...userRow, statusHint: undefined });
        }
      }

      mapRef.current.set(next.id, { ...existing, ...next, text: next.text || existing?.text || '' });
      if (next.role === 'user' || next.role === 'assistant') {
        clearLiveCaption(next.role);
      }
      syncFromMap();
    },
    [clearLiveCaption, syncFromMap],
  );

  const mergeHistory = useCallback(
    (rows: unknown[]) => {
      for (const row of rows) {
        const n = normalizeMessage(row);
        if (!n.id) continue;
        const existing = mapRef.current.get(n.id);
        if (!existing) {
          mapRef.current.set(n.id, rawToChatMessage(row));
        } else {
          mapRef.current.set(n.id, rawToChatMessage(row, existing));
        }
      }
      syncFromMap();
    },
    [syncFromMap],
  );

  const fetchPage = useCallback(
    async (cursor?: string | null) => {
      const params = new URLSearchParams({ limit: '50' });
      if (cursor) params.set('cursor', cursor);
      const res = await fetch(bridgeUrl(`/messages?${params.toString()}`), {
        credentials: 'include',
      });
      if (!res.ok) return;
      const body = (await res.json()) as {
        messages?: unknown[];
        has_more?: boolean;
        next_cursor?: string | null;
      };
      mergeHistory(body.messages ?? []);
      if (!cursor) {
        setHasMore(Boolean(body.has_more));
        setNextCursor(body.next_cursor ?? null);
      } else {
        setHasMore(Boolean(body.has_more));
        setNextCursor(body.next_cursor ?? null);
      }
    },
    [mergeHistory],
  );

  useEffect(() => {
    void fetchPage();
  }, [fetchPage]);

  useSocketEvent('init_state', (state: unknown) => {
    if (!state || typeof state !== 'object') return;
    const row = state as { messages?: unknown[]; agent_status?: { online?: boolean } };
    if (Array.isArray(row.messages)) mergeHistory(row.messages);
    if (row.agent_status && typeof row.agent_status.online === 'boolean') {
      agentOnlineRef.current = row.agent_status.online;
    }
  });

  useSocketEvent('new_message', (msg: unknown) => upsertRaw(msg));
  useSocketEvent('update_message', (msg: unknown) => upsertRaw(msg));

  useEffect(() => {
    if (voice.roomState !== 'connected') return undefined;
    const room = voice.getRoom();
    if (!room || !voice.identity) return undefined;
    return registerLiveCaptions(room, voice.identity, caps => {
      setLiveCaptions(caps);
      if (caps.user?.final) scheduleCaptionClear('user');
      if (caps.assistant?.final) scheduleCaptionClear('assistant');
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- stable voice hook slices; full `voice` retriggers every render
  }, [voice.roomState, voice.identity, voice.getRoom, scheduleCaptionClear]);

  const markFailed = useCallback(
    (id: string, hint: string) => {
      const row = mapRef.current.get(id);
      if (!row) return;
      mapRef.current.set(id, { ...row, status: 'failed', statusHint: hint });
      syncFromMap();
    },
    [syncFromMap],
  );

  const sendInternal = useCallback(
    (id: string, text: string) => {
      const ts = Date.now();
      mapRef.current.set(id, {
        id,
        role: 'user',
        text,
        ts,
        source: 'text',
        status: 'pending',
        statusHint: 'Sending',
      });
      syncFromMap();

      const timer = window.setTimeout(() => {
        if (pendingDeliveryRef.current.has(id)) {
          pendingDeliveryRef.current.delete(id);
          markFailed(id, 'Not delivered — Retry');
        }
      }, DELIVERY_TIMEOUT_MS);
      pendingDeliveryRef.current.set(id, timer);

      getSocket().emit(
        'ui_command',
        { type: 'chat', payload: { id, text } },
        (ack: UiCommandAck) => {
          const row = mapRef.current.get(id);
          if (!row) return;

          if (ack?.status === 'sent') {
            pendingDeliveryRef.current.delete(id);
            window.clearTimeout(timer);
            mapRef.current.set(id, {
              ...row,
              status: 'delivered',
              statusHint: 'Delivered ✓',
            });
            awaitingReplyRef.current.add(id);
            syncFromMap();
            return;
          }

          if (ack?.status === 'queued') {
            pendingDeliveryRef.current.delete(id);
            window.clearTimeout(timer);
            mapRef.current.set(id, {
              ...row,
              status: 'queued',
              statusHint: 'Queued (agent offline)',
            });
            syncFromMap();
            return;
          }

          if (ack?.status === 'agent_offline' || ack?.status === 'no_agent') {
            pendingDeliveryRef.current.delete(id);
            window.clearTimeout(timer);
            resendWhenOnlineRef.current.add(id);
            mapRef.current.set(id, {
              ...row,
              status: 'queued',
              statusHint: 'Queued (agent offline)',
            });
            syncFromMap();
            return;
          }

          if (ack?.status === 'rejected') {
            pendingDeliveryRef.current.delete(id);
            window.clearTimeout(timer);
            resendWhenOnlineRef.current.delete(id);
            mapRef.current.set(id, {
              ...row,
              status: 'failed',
              statusHint: ack.reason ? `Failed — ${ack.reason}` : 'Not delivered — Retry',
            });
            syncFromMap();
          }
        },
      );
    },
    [markFailed, syncFromMap],
  );

  const flushResendQueue = useCallback(() => {
    if (!agentOnlineRef.current) return;
    for (const id of [...resendWhenOnlineRef.current]) {
      const row = mapRef.current.get(id);
      if (!row || row.status !== 'queued') {
        resendWhenOnlineRef.current.delete(id);
        continue;
      }
      resendWhenOnlineRef.current.delete(id);
      sendInternal(id, row.text);
    }
  }, [sendInternal]);

  useSocketEvent('agent_status', (payload: unknown) => {
    const row = payload as { online?: boolean };
    const wasOnline = agentOnlineRef.current;
    const nextOnline = row?.online === true;
    agentOnlineRef.current = nextOnline;
    if (!wasOnline && nextOnline) {
      flushResendQueue();
    }
  });

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      sendInternal(crypto.randomUUID(), trimmed);
    },
    [sendInternal],
  );

  const retrySend = useCallback(
    (id: string) => {
      const row = mapRef.current.get(id);
      if (!row || row.role !== 'user') return;
      sendInternal(id, row.text);
    },
    [sendInternal],
  );

  const loadOlderMessages = useCallback(async () => {
    if (!hasMore || !nextCursor) return;
    await fetchPage(nextCursor);
  }, [fetchPage, hasMore, nextCursor]);

  const refetchMessages = useCallback(async () => {
    mapRef.current.clear();
    awaitingReplyRef.current.clear();
    pendingDeliveryRef.current.forEach(t => window.clearTimeout(t));
    pendingDeliveryRef.current.clear();
    resendWhenOnlineRef.current.clear();
    await fetchPage();
  }, [fetchPage]);

  const appendSystemLine = useCallback(
    (text: string) => {
      const id = `system-local-${crypto.randomUUID()}`;
      mapRef.current.set(id, {
        id,
        role: 'system',
        text,
        ts: Date.now(),
        source: 'system',
      });
      syncFromMap();
    },
    [syncFromMap],
  );

  return {
    messages,
    hasMore,
    loadOlderMessages,
    refetchMessages,
    send,
    retrySend,
    liveCaptions,
    clearLiveCaption,
    appendSystemLine,
  };
}

export type { ChatMessage };
