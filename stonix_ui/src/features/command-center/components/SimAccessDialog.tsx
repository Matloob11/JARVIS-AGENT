import { FormEvent, useMemo, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Search, ShieldCheck, User, X } from 'lucide-react';
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
  idle: 'Idle',
  loading: 'Loading',
  blocked: 'Blocked',
  validation_error: 'Validation error',
  rate_limited: 'Rate limited',
  success: 'Success',
  offline: 'Offline',
};

function statusChipLabel(status: string): string {
  return STATUS_LABELS[status] ?? status.replace(/_/g, ' ');
}

function maskedCnic(record?: SimRecord): string {
  if (!record) return 'CNIC masked';
  return record.cnic_masked || record.cnic || 'CNIC masked';
}

function maskedPhone(record: SimRecord | undefined, queryMasked: string): string {
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

  const providerBanner = useMemo(
    () => simProviderBannerText(provider, providerConfigured),
    [provider, providerConfigured],
  );

  const purposeValid = !requiresPurpose || purpose.trim().length >= 5;
  const canSubmit =
    agentOnline && query.trim().length > 0 && purposeValid && !isLoading && status !== 'offline';

  const identityTitle = useMemo(() => {
    if (isLoading) return queryMasked ? `Query ${queryMasked}` : 'Awaiting masked result';
    if (record?.full_name) return record.full_name;
    if (status === 'offline') return 'Lookup timed out';
    if (status === 'blocked' || status === 'validation_error' || status === 'rate_limited') {
      return message || 'Lookup blocked';
    }
    if (status === 'success' && !record) return 'No masked match';
    return 'Authorized source required';
  }, [isLoading, message, queryMasked, record, status]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    onSearch(query, requiresPurpose ? purpose : undefined);
  };

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="modal-backdrop no-drag"
          data-testid="sim-dialog"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
        >
          <motion.div
            className="sim-dialog"
            initial={{ opacity: 0, y: 16, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 16, scale: 0.98 }}
          >
            <div className="sim-dialog__header">
              <div className="flex items-center gap-3">
                <div className="sim-dialog__icon">
                  <ShieldCheck size={16} />
                </div>
                <div>
                  <h2>SIM Access</h2>
                  <p>Authorized lookup workspace</p>
                </div>
              </div>
              <button onClick={onClose} className="icon-button" title="Close SIM access">
                <X size={15} />
              </button>
            </div>

            <div
              className="sim-provider-banner"
              data-testid="sim-provider-banner"
              data-configured={
                isSimProviderConfigured(provider, providerConfigured) ? 'true' : 'false'
              }
            >
              {providerBanner}
            </div>

            <div
              className="sim-status-chip"
              data-testid="sim-status-chip"
              data-status={status}
            >
              {statusChipLabel(status)}
            </div>

            {requiresPurpose && (
              <label className="sim-purpose">
                <span>Purpose (authorized audit)</span>
                <input
                  data-testid="sim-purpose-input"
                  value={purpose}
                  onChange={event => setPurpose(event.target.value)}
                  placeholder="Why is this lookup needed?"
                  minLength={5}
                />
              </label>
            )}

            <form onSubmit={submit} className="sim-search">
              <input
                data-testid="sim-query-input"
                value={query}
                onChange={event => setQuery(event.target.value)}
                placeholder="Enter authorized number..."
                disabled={!agentOnline}
              />
              <button
                type="submit"
                disabled={!canSubmit}
                title={agentOnline ? 'Start lookup' : 'Agent offline — lookup disabled'}
              >
                <Search size={15} />
              </button>
            </form>

            {!agentOnline && (
              <p className="sim-offline-hint" data-testid="sim-agent-offline-hint">
                Agent offline — lookup disabled until the agent is online again.
              </p>
            )}

            <div className="sim-result">
              <div className="sim-avatar">
                <User size={18} />
              </div>
              <div className="min-w-0 flex-1">
                <span className="panel-kicker">Identity</span>
                <h3 data-testid="sim-identity-title">{identityTitle}</h3>
                <div className="sim-meta">
                  <span data-testid="sim-cnic-masked">{maskedCnic(record)}</span>
                  <span data-testid="sim-phone-masked">{maskedPhone(record, queryMasked)}</span>
                </div>
                <p data-testid="sim-result-message">
                  {message ||
                    'No personal records are shown without authorized backend data.'}
                </p>
              </div>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
};

export default SimAccessDialog;
