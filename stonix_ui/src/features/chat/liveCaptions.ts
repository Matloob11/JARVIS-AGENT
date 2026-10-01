import type { Room } from 'livekit-client';
import type { LiveCaption } from './types';

const TRANSCRIPTION_TOPIC = 'lk.transcription';

export type TranscriptionStreamReader = {
  info: { attributes?: Record<string, string> };
  [Symbol.asyncIterator](): AsyncIterator<string>;
};

/** Read one lk.transcription stream (user = snapshot chunks; agent = deltas). */
export async function readTranscriptionStream(
  reader: TranscriptionStreamReader,
  isLocalUser: boolean,
  onUpdate: (caption: LiveCaption) => void,
): Promise<LiveCaption> {
  const role: LiveCaption['role'] = isLocalUser ? 'user' : 'assistant';
  let text = '';
  let final = false;

  for await (const chunk of reader) {
    if (isLocalUser) {
      text = chunk;
      final = reader.info.attributes?.['lk.transcription_final'] === 'true';
    } else {
      text += chunk;
      final = false;
    }
    onUpdate({ role, text, final });
  }

  if (!isLocalUser) {
    final = true;
    onUpdate({ role, text, final });
  }

  return { role, text, final };
}

export type LiveCaptionsController = {
  user: LiveCaption | null;
  assistant: LiveCaption | null;
};

export function registerLiveCaptions(
  room: Room,
  localIdentity: string,
  onChange: (captions: LiveCaptionsController) => void,
): () => void {
  let user: LiveCaption | null = null;
  let assistant: LiveCaption | null = null;

  const emit = () => onChange({ user, assistant });

  const handler = (reader: TranscriptionStreamReader, participantInfo: { identity: string }) => {
    const isLocalUser = participantInfo.identity === localIdentity;
    void readTranscriptionStream(reader, isLocalUser, caption => {
      if (caption.role === 'user') {
        user = caption;
      } else {
        assistant = caption;
      }
      emit();
    }).catch(err => {
      console.warn('[liveCaptions] stream read failed', err);
    });
  };

  room.registerTextStreamHandler(TRANSCRIPTION_TOPIC, handler);

  return () => {
    room.unregisterTextStreamHandler(TRANSCRIPTION_TOPIC);
    user = null;
    assistant = null;
    emit();
  };
}
