import { ShieldCheck } from 'lucide-react';
import { formatMmSs } from '@/shared/time';
import type { ConfirmDecision, QueuedConfirm } from '@/features/confirm/useConfirmQueue';
import { useAgentStatusStore } from '@/features/status/agentStatusStore';
import { PanelOfflineHint } from '@/shared/ui/PanelState';

type ApprovalsTabProps = {
  queue: QueuedConfirm[];
  decisions: ConfirmDecision[];
};

function riskLabel(risk?: string): { text: string; tone: 'high' | 'medium' } {
  const level = (risk ?? '').toLowerCase();
  if (level.includes('high')) return { text: 'High risk', tone: 'high' };
  return { text: 'Medium risk', tone: 'medium' };
}

function secondsRemaining(item: QueuedConfirm): number {
  return Math.max(0, Math.ceil((item.deadlineMs - Date.now()) / 1000));
}

function firstCommandLine(args?: string): string {
  const line = args?.split('\n')[0]?.trim();
  return line || '—';
}

function decisionMeta(decision: ConfirmDecision): { mark: string; label: string } {
  const reason = (decision.reason ?? '').toLowerCase();
  if (decision.expired || reason.includes('expir') || reason === 'timeout') {
    return { mark: '○', label: 'Expired, nothing ran' };
  }
  if (reason.includes('cancel')) {
    return { mark: '◆', label: 'Cancelled by voice' };
  }
  if (decision.allow) {
    return { mark: '●', label: 'Allowed' };
  }
  return { mark: '◆', label: 'Denied' };
}

function formatDecisionTime(ts: number): string {
  return new Date(ts).toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  });
}

const ApprovalsTab = ({ queue, decisions }: ApprovalsTabProps) => {
  const waitingTail = queue.slice(1);
  const total = queue.length;
  const agentOnline = useAgentStatusStore().status.online;

  if (total === 0 && decisions.length === 0) {
    return (
      <div className="approvals-tab-pane" data-testid="approvals-panel">
        <PanelOfflineHint online={agentOnline} />
        <div className="approvals-tab-pane__empty" data-testid="approvals-empty">
          <ShieldCheck size={40} className="approvals-tab-pane__icon" aria-hidden />
          <p className="approvals-tab-pane__title">No approvals waiting</p>
          <p>Guarded tools always ask first.</p>
          <a className="approvals-tab-pane__link" href="#/settings/confirmations">
            See guarded tools ›
          </a>
        </div>
      </div>
    );
  }

  return (
    <div className="approvals-tab-pane" data-testid="approvals-panel">
      <PanelOfflineHint online={agentOnline} />
      {total > 0 ? (
        <section className="approvals-section">
          <h3 className="approvals-section__title">
            Waiting{' '}
            <small>
              {total === 1 ? 'Only the request above' : `${total} requests, oldest first`}
            </small>
          </h3>
          {waitingTail.map((item, index) => {
            const risk = riskLabel(item.risk);
            return (
              <article
                key={item.id}
                className="approvals-row"
                data-testid="approvals-queue-row"
              >
                <div className="approvals-row__top">
                  <strong>{`Request ${index + 2} of ${total}`}</strong>
                  <span className={`approvals-row__pill approvals-row__pill--${risk.tone}`}>
                    {risk.text}
                  </span>
                </div>
                <p className="approvals-row__summary">{item.summary || item.tool}</p>
                <p className="approvals-row__meta">
                  Tool <code>{item.tool ?? 'unknown'}</code>, expires in{' '}
                  {formatMmSs(secondsRemaining(item))}
                </p>
              </article>
            );
          })}
        </section>
      ) : (
        <div className="approvals-tab-pane__empty approvals-tab-pane__empty--compact">
          <ShieldCheck size={32} className="approvals-tab-pane__icon" aria-hidden />
          <p className="approvals-tab-pane__title">No approvals waiting</p>
          <p>Guarded tools always ask first.</p>
          <a className="approvals-tab-pane__link" href="#/settings/confirmations">
            See guarded tools ›
          </a>
        </div>
      )}

      <section className="approvals-section">
        <h3 className="approvals-section__title">
          Decisions <small>Last 20</small>
        </h3>
        {decisions.length === 0 ? (
          <p className="approvals-section__empty">No decisions yet.</p>
        ) : (
          decisions.map(decision => {
            const meta = decisionMeta(decision);
            return (
              <article
                key={`${decision.id}-${decision.ts}`}
                className="approvals-row"
                data-testid="approvals-history-row"
              >
                <div className="approvals-row__top">
                  <strong>{decision.tool ?? 'unknown'}</strong>
                  <span className="approvals-row__pill">
                    {meta.mark} {meta.label}
                  </span>
                </div>
                <p className="approvals-row__meta">
                  <code>{firstCommandLine(decision.args_preview) || decision.summary || '—'}</code>
                </p>
                <p className="approvals-row__time">{formatDecisionTime(decision.ts)}</p>
              </article>
            );
          })
        )}
      </section>
    </div>
  );
};

export default ApprovalsTab;
