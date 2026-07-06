import { AlertTriangle, Cpu, HardDrive, MapPin, MemoryStick } from 'lucide-react';
import CameraPanel from './CameraPanel';
import { LocationData, VortexLog } from '../hooks/useCommandCenter';
import { Panel, StatusDot } from '@/shared/ui/Panel';
import { personaAccent } from '@/shared/theme/commandTheme';

interface OperationsRailProps {
  persona: 'jarvis' | 'anna';
  isMuted: boolean;
  location: LocationData;
  vitals: { cpu: number; ram: number; disk: number };
  logs: VortexLog[];
}

const OperationsRail = ({ persona, isMuted, location, vitals, logs }: OperationsRailProps) => {
  const accent = personaAccent(persona);
  const alerts = logs
    .filter(log => ['error', 'warning', 'critical'].includes(log.category?.toLowerCase() || ''))
    .slice(0, 3);

  return (
    <aside className="operations-rail">
      <CameraPanel isMuted={isMuted} />

      <Panel title="Location">
        <div className="location-card">
          <MapPin size={16} className={accent.text} />
          <div>
            <p className="text-sm font-semibold text-command-text">{location.city || 'Detecting...'}</p>
            <p className="mt-1 font-mono text-[11px] text-command-muted">
              {location.lat.toFixed(4)}, {location.lng.toFixed(4)}
            </p>
          </div>
        </div>
      </Panel>

      <Panel title="Vitals">
        <div className="space-y-4">
          <Metric icon={Cpu} label="CPU" value={vitals.cpu} />
          <Metric icon={MemoryStick} label="Memory" value={vitals.ram} />
          <Metric icon={HardDrive} label="Disk" value={vitals.disk} />
        </div>
      </Panel>

      <Panel title="Alerts" className="min-h-0 flex-1">
        <div className="space-y-2">
          {alerts.length === 0 ? (
            <div className="empty-compact">
              <StatusDot active tone="accent" />
              Normal
            </div>
          ) : (
            alerts.map((alert, index) => (
              <div key={`${alert.timestamp}-${index}`} className="alert-row">
                <AlertTriangle size={13} className="mt-0.5 text-command-warning" />
                <span>{alert.text}</span>
              </div>
            ))
          )}
        </div>
      </Panel>
    </aside>
  );
};

const Metric = ({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Cpu;
  label: string;
  value: number;
}) => (
  <div>
    <div className="mb-2 flex items-center justify-between">
      <div className="flex items-center gap-2 text-xs text-command-muted">
        <Icon size={13} />
        {label}
      </div>
      <span className="font-mono text-xs text-command-text">{value.toFixed(1)}%</span>
    </div>
    <div className="metric-track">
      <div className="metric-fill" style={{ width: `${Math.min(100, Math.max(0, value))}%` }} />
    </div>
  </div>
);

export default OperationsRail;
