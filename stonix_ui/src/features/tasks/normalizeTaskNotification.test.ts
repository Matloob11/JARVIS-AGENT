import { describe, expect, it } from 'vitest';
import { normalizeTaskNotification } from './useTasks';

describe('normalizeTaskNotification spoken (C-56 ready)', () => {
  it('keeps spoken true/false and omits non-boolean', () => {
    expect(
      normalizeTaskNotification({ id: 'a', description: 'x', spoken: true })?.spoken,
    ).toBe(true);
    expect(
      normalizeTaskNotification({ id: 'b', description: 'x', spoken: false })?.spoken,
    ).toBe(false);
    expect(
      normalizeTaskNotification({ id: 'c', description: 'x', spoken: 'yes' })?.spoken,
    ).toBeUndefined();
  });
});
