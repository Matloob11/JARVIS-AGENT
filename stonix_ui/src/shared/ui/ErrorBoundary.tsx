import { Component, type ErrorInfo, type ReactNode } from 'react';

interface ErrorBoundaryProps {
  name?: string;
  children: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
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
    this.setState({ hasError: false });
  };

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        <div className="panel-error-fallback" role="alert" data-testid="panel-error">
          <p>Panel failed - Reload panel</p>
          <button type="button" onClick={this.retry} data-testid="panel-error-retry">
            Reload panel
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

export default ErrorBoundary;
