import { Component, type ErrorInfo, type ReactNode } from 'react';
import { clearE2EPanelCrash } from '@/shared/e2e/panelCrash';
import { Icon } from '@/shared/ui/icons/Icon';
import { StatusShape } from '@/shared/ui/StatusShape';

interface ErrorBoundaryProps {
  name?: string;
  children: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
}

export function PanelErrorFallback({
  label,
  onRetry,
}: {
  label: string;
  onRetry: () => void;
}) {
  return (
    <div className="panel-error" role="alert" data-testid="panel-error">
      <b>
        <StatusShape s="err" label="Error" hideLabel />
        {label} stopped working
      </b>
      <span>The rest of the dashboard keeps running.</span>
      <button type="button" className="n-btn sm" onClick={onRetry} data-testid="panel-error-retry">
        <Icon name="refresh" />
        Retry
      </button>
    </div>
  );
}

/**
 * Per-panel error boundary — one panel crash must not blank the dashboard.
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error(`[ErrorBoundary${this.props.name ? `:${this.props.name}` : ''}]`, error, info);
  }

  private retry = () => {
    if (__E2E_HOOKS__) {
      clearE2EPanelCrash();
    }
    this.setState({ hasError: false });
  };

  render(): ReactNode {
    if (this.state.hasError) {
      const label = this.props.name?.trim() || 'Panel';
      return <PanelErrorFallback label={label} onRetry={this.retry} />;
    }
    return this.props.children;
  }
}

export default ErrorBoundary;
