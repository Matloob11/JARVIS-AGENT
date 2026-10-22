/** C-60: same epoch, smaller revision → ignore (stale bridge view). */

export type SettingsRevisionView = {
  revision?: number;
  revision_epoch?: number;
};

export function shouldApplySettingsRevision(
  incoming: SettingsRevisionView,
  current: SettingsRevisionView,
): boolean {
  if (
    typeof incoming.revision !== 'number' ||
    typeof incoming.revision_epoch !== 'number'
  ) {
    return true;
  }
  if (
    typeof current.revision !== 'number' ||
    typeof current.revision_epoch !== 'number'
  ) {
    return true;
  }
  if (incoming.revision_epoch !== current.revision_epoch) return true;
  return incoming.revision >= current.revision;
}
