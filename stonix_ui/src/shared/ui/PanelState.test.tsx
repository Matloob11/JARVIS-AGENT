import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { PANEL_OFFLINE_HINT, PanelOfflineHint } from './PanelState';

describe('UI-H06 PanelOfflineHint', () => {
  it('hides when the agent is online', () => {
    const { container } = render(<PanelOfflineHint online />);
    expect(container.firstChild).toBeNull();
  });

  it('shows the shared offline sentence when offline', () => {
    render(<PanelOfflineHint online={false} />);
    expect(screen.getByTestId('panel-offline-hint').textContent).toBe(PANEL_OFFLINE_HINT);
  });
});
