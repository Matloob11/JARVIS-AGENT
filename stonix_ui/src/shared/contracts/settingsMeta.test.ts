import { describe, expect, it } from 'vitest';
import {
  normalizeSettings,
  omitSettingsMetaKeys,
  stripSettingsMeta,
} from './events';

describe('stripSettingsMeta', () => {
  it('removes saved and updated_ts from the settings object', () => {
    const { settings, savedFields, updated_ts } = stripSettingsMeta({
      voice: 'Puck',
      persona: 'jarvis',
      saved: ['voice', 'persona'],
      updated_ts: 1729000000.5,
    });
    expect(settings).toEqual({ voice: 'Puck', persona: 'jarvis' });
    expect(settings).not.toHaveProperty('saved');
    expect(settings).not.toHaveProperty('updated_ts');
    expect(savedFields).toEqual(['voice', 'persona']);
    expect(updated_ts).toBe(1729000000.5);
  });
});

describe('omitSettingsMetaKeys', () => {
  it('never lets saved or updated_ts ride out in settings_update', () => {
    expect(
      omitSettingsMetaKeys({
        voice: 'Puck',
        saved: ['voice'],
        updated_ts: 1,
      }),
    ).toEqual({ voice: 'Puck' });
  });
});

describe('normalizeSettings meta', () => {
  it('lifts saved/updated_ts out of nested settings', () => {
    const norm = normalizeSettings({
      settings: {
        voice: 'Aoede',
        language: 'ur',
        saved: ['voice'],
        updated_ts: 42,
      },
      applied: { voice: 'applied', language: 'unsupported' },
    });
    expect(norm.settings).toEqual({ voice: 'Aoede', language: 'ur' });
    expect(norm.savedFields).toEqual(['voice']);
    expect(norm.updated_ts).toBe(42);
  });

  it('strips meta from a flat settings_sync too', () => {
    const norm = normalizeSettings({
      voice: 'Charon',
      saved: ['voice'],
      updated_ts: 7,
      applied: true,
    });
    expect(norm.settings).toEqual({ voice: 'Charon' });
    expect(norm.savedFields).toEqual(['voice']);
    expect(norm.updated_ts).toBe(7);
  });
});
