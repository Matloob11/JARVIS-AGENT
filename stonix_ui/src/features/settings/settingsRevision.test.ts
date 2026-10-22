import { describe, expect, it } from 'vitest';
import { shouldApplySettingsRevision } from './settingsRevision';

describe('shouldApplySettingsRevision (C-60)', () => {
  it('ignores same-epoch smaller revision', () => {
    expect(
      shouldApplySettingsRevision(
        { revision: 2, revision_epoch: 100 },
        { revision: 5, revision_epoch: 100 },
      ),
    ).toBe(false);
  });

  it('applies same-epoch equal or newer revision', () => {
    expect(
      shouldApplySettingsRevision(
        { revision: 5, revision_epoch: 100 },
        { revision: 5, revision_epoch: 100 },
      ),
    ).toBe(true);
    expect(
      shouldApplySettingsRevision(
        { revision: 6, revision_epoch: 100 },
        { revision: 5, revision_epoch: 100 },
      ),
    ).toBe(true);
  });

  it('applies when epoch changes or meta missing', () => {
    expect(
      shouldApplySettingsRevision(
        { revision: 1, revision_epoch: 200 },
        { revision: 99, revision_epoch: 100 },
      ),
    ).toBe(true);
    expect(shouldApplySettingsRevision({}, { revision: 9, revision_epoch: 1 })).toBe(
      true,
    );
  });
});
