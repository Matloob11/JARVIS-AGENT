import type { PersonaKey } from '@/shared/persona';

export type ChatMessageStatus = 'pending' | 'queued' | 'delivered' | 'failed';

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  text: string;
  /** Epoch milliseconds (normalizeMessage ts). */
  ts: number;
  source?: 'voice' | 'text' | 'system';
  turn_id?: string;
  /**
   * K-59 v50: only `typed_no_reply` is a calm centre system note.
   * Other source 'system' messages (diffs, offline, swarm) stay assistant bubbles.
   */
  kind?: string;
  final?: boolean;
  status?: ChatMessageStatus;
  /** Persona that spoke (assistant rows keep their original persona). */
  persona?: PersonaKey;
  /** Pending delivery, waiting for reply, or failure hint. */
  statusHint?: string;
}

export interface LiveCaption {
  role: 'user' | 'assistant';
  text: string;
  final: boolean;
}
