import { FormEvent, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Search, ShieldCheck, User, X } from 'lucide-react';
import { SimRecord } from '../hooks/useCommandCenter';

interface SimAccessDialogProps {
  open: boolean;
  records: SimRecord[];
  isLoading: boolean;
  queryMasked: string;
  status: string;
  message: string;
  onClose: () => void;
  onSearch: (query: string) => void;
}

const SimAccessDialog = ({
  open,
  records,
  isLoading,
  queryMasked,
  status,
  message,
  onClose,
  onSearch,
}: SimAccessDialogProps) => {
  const [query, setQuery] = useState('');
  const record = records[0];

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!query.trim() || isLoading) return;
    onSearch(query);
  };

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="modal-backdrop no-drag"
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

            <form onSubmit={submit} className="sim-search">
              <input
                value={query}
                onChange={event => setQuery(event.target.value)}
                placeholder="Enter authorized number..."
              />
              <button type="submit" disabled={!query.trim() || isLoading} title="Start lookup">
                <Search size={15} />
              </button>
            </form>

            <div className="sim-result">
              <div className="sim-avatar">
                <User size={18} />
              </div>
              <div className="min-w-0 flex-1">
                <span className="panel-kicker">Identity</span>
                <h3>{isLoading ? 'Checking source...' : record?.full_name || 'Authorized source required'}</h3>
                <div className="sim-meta">
                  <span>{record?.cnic || 'CNIC masked'}</span>
                  <span>{record?.phone || queryMasked || status}</span>
                </div>
                <p>{record?.address || message || 'No personal records are shown without authorized backend data.'}</p>
              </div>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
};

export default SimAccessDialog;
