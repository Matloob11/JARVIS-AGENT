import { describe, expect, it } from 'vitest';
import {
  FIELD_ERROR_PREVIEW_CHARS,
  fieldStatusLabel,
  resolveFieldApplyState,
  truncateFieldError,
  voiceFromSettingsSync,
} from './settingsApplyState';

describe('resolveFieldApplyState', () => {
  it('maps only settings_sync.errors to error', () => {
    expect(
      resolveFieldApplyState({
        hasError: true,
        localPending: false,
        serverPending: false,
        applied: 'applied',
      }),
    ).toBe('error');
  });

  it('maps applied unsupported to unsupported, never error', () => {
    expect(
      resolveFieldApplyState({
        hasError: false,
        localPending: false,
        serverPending: false,
        applied: 'unsupported',
      }),
    ).toBe('unsupported');
  });

  it('maps pending_restart to pending', () => {
    expect(
      resolveFieldApplyState({
        hasError: false,
        localPending: false,
        serverPending: false,
        applied: 'pending_restart',
      }),
    ).toBe('pending');
  });

  it('maps applied to applied', () => {
    expect(
      resolveFieldApplyState({
        hasError: false,
        localPending: false,
        serverPending: false,
        applied: 'applied',
      }),
    ).toBe('applied');
  });

  it('prefers local pending over unsupported', () => {
    expect(
      resolveFieldApplyState({
        hasError: false,
        localPending: true,
        serverPending: false,
        applied: 'unsupported',
      }),
    ).toBe('pending');
  });
});

describe('fieldStatusLabel', () => {
  it('uses calm copy for unsupported', () => {
    expect(fieldStatusLabel('unsupported')).toBe('Saved · abhi active nahi');
  });

  it('notes pending_restart', () => {
    expect(fieldStatusLabel('pending', { appliedRaw: 'pending_restart' })).toBe(
      'Saved · restart ke baad',
    );
  });

  it('passes through error text', () => {
    expect(fieldStatusLabel('error', { error: 'Voice x nahi mili' })).toBe('Voice x nahi mili');
  });
});

describe('truncateFieldError', () => {
  it('keeps short messages intact', () => {
    expect(truncateFieldError('short')).toEqual({ preview: 'short', truncated: false });
  });

  it('truncates long voice name lists with an ellipsis', () => {
    const long = `Voice 'alloyx' nahi mili. Valid voices: ${'A,'.repeat(80)}`;
    const { preview, truncated } = truncateFieldError(long);
    expect(truncated).toBe(true);
    expect(preview.endsWith('…')).toBe(true);
    expect(preview.length).toBeLessThanOrEqual(FIELD_ERROR_PREVIEW_CHARS + 1);
  });
});

describe('voiceFromSettingsSync', () => {
  const resolve = (raw: unknown, fallback: string) => String(raw ?? fallback);

  it('keeps sync voice when present', () => {
    expect(
      voiceFromSettingsSync({
        settings: { persona: 'anna', voice: 'Puck' },
        prevVoice: 'Charon',
        personaDefault: 'Aoede',
        resolveVoice: resolve,
      }),
    ).toBe('Puck');
  });

  it('drops saved voice on persona-only sync', () => {
    expect(
      voiceFromSettingsSync({
        settings: { persona: 'anna' },
        prevVoice: 'Charon',
        personaDefault: 'Aoede',
        resolveVoice: resolve,
      }),
    ).toBe('Aoede');
  });

  it('leaves voice alone when sync has neither', () => {
    expect(
      voiceFromSettingsSync({
        settings: { language: 'ur' },
        prevVoice: 'Charon',
        personaDefault: 'Aoede',
        resolveVoice: resolve,
      }),
    ).toBe('Charon');
  });
});
