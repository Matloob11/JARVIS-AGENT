import { describe, expect, it } from 'vitest';
import {
  MAX_CONTENT_CHARS,
  dedupeMemoriesById,
  normalizeMemory,
  type MemoryItem,
} from './useMemories';

describe('normalizeMemory (K-07 v44)', () => {
  it('keeps extractor shape with role, user_id and ts', () => {
    const row = normalizeMemory({
      id: 'turn-1',
      content: 'hello',
      role: 'user',
      source: 'extractor',
      user_id: 'matloob',
      ts: 1_700_000_000,
    });
    expect(row).toMatchObject({
      id: 'turn-1',
      content: 'hello',
      role: 'user',
      source: 'extractor',
      userId: 'matloob',
    });
    expect(row?.ts).toBe(1_700_000_000_000);
  });

  it('accepts fact/vector without role or user_id and invents an id', () => {
    const row = normalizeMemory({
      content: 'a remembered fact',
      source: 'fact',
      ts: 1_700_000_001,
    });
    expect(row).not.toBeNull();
    expect(row!.id.length).toBeGreaterThan(8);
    expect(row!.role).toBeUndefined();
    expect(row!.userId).toBeUndefined();
    expect(row!.source).toBe('fact');
    expect(row!.ts).toBe(1_700_000_001_000);
  });

  it('falls back to now when ts is missing', () => {
    const before = Date.now();
    const row = normalizeMemory({ id: 'x1', content: 'no ts', source: 'vector' });
    const after = Date.now();
    expect(row!.ts).toBeGreaterThanOrEqual(before);
    expect(row!.ts).toBeLessThanOrEqual(after);
  });

  it('truncates content to 500 chars', () => {
    const row = normalizeMemory({
      id: 'long',
      content: 'x'.repeat(MAX_CONTENT_CHARS + 80),
      source: 'extractor',
      role: 'assistant',
      ts: 1,
    });
    expect(row!.content).toHaveLength(MAX_CONTENT_CHARS);
  });

  it('truncates by Unicode code points (not UTF-16 code units)', () => {
    const emoji = '😀'; // one code point, two UTF-16 units
    const content = emoji.repeat(MAX_CONTENT_CHARS + 10);
    const row = normalizeMemory({
      id: 'emoji',
      content,
      source: 'extractor',
      role: 'user',
      ts: 1,
    });
    expect(Array.from(row!.content)).toHaveLength(MAX_CONTENT_CHARS);
    expect(row!.content).not.toMatch(/\uD800|\uDFFF/);
  });
});

describe('dedupeMemoriesById', () => {
  it('keeps one row per id, newest first, cap 200', () => {
    const rows: MemoryItem[] = [];
    for (let i = 0; i < 250; i += 1) {
      rows.push({
        id: `id-${i % 210}`,
        content: `c${i}`,
        ts: i * 1000,
        source: 'extractor',
        role: 'user',
      });
    }
    const out = dedupeMemoriesById(rows);
    expect(out.length).toBeLessThanOrEqual(200);
    const ids = out.map(r => r.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(out[0].ts).toBeGreaterThanOrEqual(out[out.length - 1].ts);
  });

  it('dedupes the same id from init_state and live sync', () => {
    const out = dedupeMemoriesById([
      { id: 'same', content: 'from live', ts: 2000, role: 'user', source: 'extractor' },
      { id: 'same', content: 'from init', ts: 1000, role: 'user', source: 'extractor' },
    ]);
    expect(out).toHaveLength(1);
    expect(out[0].content).toBe('from live');
  });
});
