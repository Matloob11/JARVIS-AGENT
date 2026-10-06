import { FormEvent, useEffect, useMemo, useState } from 'react';
import { Info, Search, ShieldCheck, User, X } from 'lucide-react';
import { Dialog } from '@/shared/ui/Dialog';
import {
  SimRecord,
  isSimProviderConfigured,
  simProviderBannerText,
} from '../hooks/useCommandCenter';

interface SimAccessDialogProps {
  open: boolean;
  records: SimRecord[];
  isLoading: boolean;
  queryMasked: string;
  status: string;
  message: string;
  provider: string;
  providerConfigured?: boolean;
  requiresPurpose: boolean;
  agentOnline: boolean;
  onClose: () => void;
  onSearch: (query: string, purpose?: string) => void;
}

const STATUS_LABELS: Record<string, string> = {
  loading: 'Looking up…',
  blocked: 'Blocked',
  validation_error: 'Check your input',
  rate_limited: 'Slow down — try again soon',
  success: 'Match found',
  empty: 'No match',
  offline: 'Agent offline',
  timeout: 'Timed out',
  denied: 'Denied',
  expired: 'Expired',
  cancelled: 'Cancelled',
  error: 'Error',
  provider_error: 'Provider error',
};

const TERMINAL_TITLES: Record<string, string> = {
  denied: 'Lookup denied',
  expired: 'Lookup expired',
  cancelled: 'Lookup cancelled',
  error: 'Lookup failed',
  timeout: 'Lookup timed out',
  offline: 'Agent offline',
};

const PRIVACY_NOTE =
  'No personal records are shown without authorized backend data. Results stay masked.';

const QUERY_MAX_LEN = 13;

function statusChipLabel(status: string): string {
  const key = status.trim().toLowerCase();
  if (!key || key === 'idle') return '';
  return STATUS_LABELS[key] ?? key.replace(/_/g, ' ');
}

function digitsOnly(value: string): string {
  return value.replace(/\D/g, '').slice(0, QUERY_MAX_LEN);
}

function maskedCnic(record?: SimRecord): string {
  if (!record) return '—';
  return record.cnic_masked || record.cnic || '—';
}

function maskedPhone(
  record: SimRecord | undefined,
  queryMasked: string,
  showResult: boolean,
): string {
  if (!showResult) return '—';
  if (record?.phone_masked) return record.phone_masked;
  if (record?.phone) return record.phone;
  return queryMasked || '—';
}

const SimAccessDialog = ({
  open,
  records,
  isLoading,
  queryMasked,
  status,
  message,
  provider,
  providerConfigured,
  requiresPurpose,
  agentOnline,
  onClose,
  onSearch,
}: SimAccessDialogProps) => {
  const [query, setQuery] = useState('');
  const [purpose, setPurpose] = useState('');
  const record = records[0];
  const configured = isSimProviderConfigured(provider, providerConfigured);
  const statusKey = status.trim().toLowerCase();
  const chipLabel = statusChipLabel(status);
  const showStatusChip = Boolean(chipLabel) && statusKey !== 'idle';
  const showResult =
    isLoading ||
    Boolean(record) ||
    (statusKey !== '' && statusKey !== 'idle');

  const providerBanner = useMemo(
    () => simProviderBannerText(provider, providerConfigured),
    [provider, providerConfigured],
  );

  const purposeLen = purpose.trim().length;
  const purposeValid = !requiresPurpose || purposeLen >= 5;
  const canSubmit =
    agentOnline && query.trim().length > 0 && purposeValid && !isLoading;

  useEffect(() => {
    if (!open) return;
    setQuery('');
    setPurpose('');
  }, [open]);

  const searchDisableReason = useMemo(() => {
    if (!agentOnline) return 'Agent offline — lookup disabled';
    if (isLoading) return 'Lookup in progress';
    if (!query.trim()) return 'Enter a phone (11 digits) or CNIC (13 digits)';
    if (requiresPurpose && purposeLen < 5) {
      return `Purpose kam az kam 5 harf — ${purposeLen}/5`;
    }
    return 'Start lookup';
  }, [agentOnline, isLoading, purposeLen, query, requiresPurpose]);

  const identityTitle = useMemo(() => {
    if (isLoading) return queryMasked ? `Query ${queryMasked}` : 'Awaiting masked result';
    if (record?.full_name) return record.full_name;
    if (statusKey === 'offline' && !agentOnline) return 'Agent offline';
    if (statusKey === 'timeout' || (statusKey === 'offline' && agentOnline)) {
      return 'Lookup timed out';
    }
    if (statusKey === 'blocked' || statusKey === 'validation_error' || statusKey === 'rate_limited') {
      return message || 'Lookup blocked';
    }
    if (statusKey in TERMINAL_TITLES) {
      return TERMINAL_TITLES[statusKey];
    }
    if (statusKey === 'success' && !record) return 'No masked match';
    if (statusKey === 'empty') return message || 'No masked match';
    if (!configured) return 'Authorized source required';
    return 'Ready for lookup';
  }, [agentOnline, configured, isLoading, message, queryMasked, record, statusKey]);

  const resultMessage = useMemo(() => {
    if (message) return message;
    if (isLoading) return 'Waiting for a masked result…';
    if (statusKey === 'timeout') return 'No reply in time — try again.';
    if (statusKey === 'offline' && agentOnline) return 'No reply in time — try again.';
    if (statusKey === 'offline' && !agentOnline) {
      return 'Agent offline — lookup disabled until the agent is online again.';
    }
    if (statusKey in TERMINAL_TITLES) {
      return TERMINAL_TITLES[statusKey];
    }
    if (statusKey === 'success' || statusKey === 'empty' || record) return '';
    if (!configured) return PRIVACY_NOTE;
    return '';
  }, [agentOnline, configured, isLoading, message, record, statusKey]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    onSearch(query, requiresPurpose ? purpose : undefined);
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      data-testid="sim-dialog"
      ariaLabelledBy="sim-dialog-title"
      ariaDescribedBy="sim-dialog-desc"
    >
      <div className="n-dlg-hd">
        <div className="mh" aria-hidden="true">
          <ShieldCheck size={22} />
        </div>
        <div>
          <h2 id="sim-dialog-title">SIM Access</h2>
          <p id="sim-dialog-desc">Authorized lookup workspace</p>
        </div>
        <button
          type="button"
          className="icon-button"
          title="Close SIM access"
          aria-label="Close SIM access"
          onClick={onClose}
        >
          <X size={15} />
        </button>
      </div>

      <div className="n-dlg-bd">
        <div
          className="sim-provider-banner"
          data-testid="sim-provider-banner"
          data-configured={configured ? 'true' : 'false'}
        >
          <span className="sim-provider-banner__text">{providerBanner}</span>
          {configured && (
            <button
              type="button"
              className="sim-privacy-tip"
              title={PRIVACY_NOTE}
              aria-label={PRIVACY_NOTE}
              data-testid="sim-privacy-tip"
              tabIndex={-1}
            >
              <Info size={14} aria-hidden="true" />
            </button>
          )}
        </div>

        {showStatusChip ? (
          <div className="sim-status-chip" data-testid="sim-status-chip" data-status={statusKey}>
            {chipLabel}
          </div>
        ) : (
          <div
            className="sim-status-chip sim-status-chip--hidden"
            data-testid="sim-status-chip"
            data-status={statusKey || 'idle'}
            hidden
            aria-hidden="true"
          />
        )}

        {requiresPurpose && (
          <label className="sim-purpose">
            <span>Purpose (authorized audit)</span>
            <input
              data-testid="sim-purpose-input"
              data-dialog-autofocus
              value={purpose}
              onChange={event => setPurpose(event.target.value)}
              placeholder="Why is this lookup needed?"
              minLength={5}
              maxLength={200}
            />
            {purposeLen < 5 && (
              <p className="sim-purpose-hint" data-testid="sim-purpose-hint">
                kam az kam 5 harf — {purposeLen}/5
              </p>
            )}
          </label>
        )}

        <form onSubmit={submit} className="sim-search">
          <input
            data-testid="sim-query-input"
            data-dialog-autofocus={requiresPurpose ? undefined : true}
            aria-label="Authorized number"
            inputMode="numeric"
            autoComplete="off"
            spellCheck={false}
            value={query}
            onChange={event => setQuery(digitsOnly(event.target.value))}
            onPaste={event => {
              event.preventDefault();
              const text = event.clipboardData.getData('text');
              setQuery(digitsOnly(`${query}${text}`));
            }}
            placeholder="03XXXXXXXXX or 13-digit CNIC"
            disabled={!agentOnline}
          />
          <button
            type="submit"
            disabled={!canSubmit}
            title={searchDisableReason}
            aria-label={searchDisableReason}
          >
            <Search size={15} />
          </button>
        </form>

        {!agentOnline && (
          <p className="sim-offline-hint" data-testid="sim-agent-offline-hint">
            Agent offline — lookup disabled until the agent is online again.
          </p>
        )}

        <div className="sim-result" data-empty={showResult ? 'false' : 'true'}>
          <div className="sim-avatar">
            <User size={18} />
          </div>
          <div className="min-w-0 flex-1">
            <span className="panel-kicker">Identity</span>
            <h3 data-testid="sim-identity-title">{identityTitle}</h3>
            <div className="sim-meta">
              <span data-testid="sim-cnic-masked">{maskedCnic(record)}</span>
              <span data-testid="sim-phone-masked">
                {maskedPhone(record, queryMasked, showResult)}
              </span>
            </div>
            {resultMessage ? (
              <p data-testid="sim-result-message">{resultMessage}</p>
            ) : (
              <p data-testid="sim-result-message" className="sim-result-message--empty" />
            )}
          </div>
        </div>
      </div>
    </Dialog>
  );
};

export default SimAccessDialog;
