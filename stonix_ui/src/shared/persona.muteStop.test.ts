import { describe, expect, it } from 'vitest';
import {
  AGENT_LISTENING_HINT_EN,
  listeningMutedTitle,
  MIC_OFF_TYPED_CHAT_HINT_EN,
  MUTE_TYPED_CHAT_HINT_EN,
  stopTitle,
} from './persona';

describe('UI-H12 mute vs stop copy', () => {
  it('mute titles say typed chat still works', () => {
    for (const key of ['jarvis', 'anna'] as const) {
      const title = listeningMutedTitle(key);
      expect(title.toLowerCase()).toContain('typed chat');
      expect(title.toLowerCase()).not.toMatch(/deaf|cannot hear chat|typed.*band|chat.*band/);
    }
    expect(MUTE_TYPED_CHAT_HINT_EN.toLowerCase()).toContain('typed chat');
    expect(MIC_OFF_TYPED_CHAT_HINT_EN.toLowerCase()).toContain('typed chat');
  });

  it('stop is interrupt speech, not mute mic', () => {
    expect(stopTitle('jarvis')).toContain('chup');
    expect(stopTitle('anna')).toContain('chup');
    expect(stopTitle('jarvis').toLowerCase()).not.toContain('typed');
    expect(MUTE_TYPED_CHAT_HINT_EN.toLowerCase()).not.toContain('stop');
    expect(AGENT_LISTENING_HINT_EN.toLowerCase()).toContain('mic');
  });
});
