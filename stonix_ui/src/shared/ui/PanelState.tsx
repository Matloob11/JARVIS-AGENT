/**
 * UI-H06: shared empty / offline copy for Activity, Tools, Approvals, Agents.
 * Offline never removes controls — only explains why the feed may stall.
 */

export const PANEL_OFFLINE_HINT =
  'Agent offline — this panel stays open; new events wait until the worker is back.';

export function PanelOfflineHint({ online }: { online: boolean }) {
  if (online) return null;
  return (
    <p className="panel-offline-hint n-muted" data-testid="panel-offline-hint" role="status">
      {PANEL_OFFLINE_HINT}
    </p>
  );
}
