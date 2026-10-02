import AgentVpsCard from '@/features/agents/AgentVpsCard';
import BrowserCard from '@/features/agents/BrowserCard';
import LaptopCard from '@/features/agents/LaptopCard';
import type { CameraState } from './cameraState';
import type { LocationData } from '../hooks/useCommandCenter';
import type { VortexStatusSnapshot } from '@/features/shell/useVortexStatusPoll';

interface OperationsRailProps {
  persona: 'jarvis' | 'anna';
  bridgeConnected: boolean;
  location: LocationData;
  onCameraStateChange?: (state: CameraState) => void;
  onReconnect: () => void | Promise<void>;
  vortex: VortexStatusSnapshot;
  sessionRestartBanner: boolean;
  onSessionRestartBannerChange: (active: boolean) => void;
}

const OperationsRail = ({
  persona,
  bridgeConnected,
  location,
  onCameraStateChange,
  onReconnect,
  vortex,
  sessionRestartBanner,
  onSessionRestartBannerChange,
}: OperationsRailProps) => (
  <aside className="operations-rail agents-devices-rail layout-zone layout-zone--ops" data-testid="operations-rail">
    <div className="agents-devices-grid">
      <AgentVpsCard
        bridgeConnected={bridgeConnected}
        vortex={vortex}
        sessionRestartBanner={sessionRestartBanner}
        onSessionRestartBannerChange={onSessionRestartBannerChange}
      />
      <LaptopCard persona={persona} location={location} />
    </div>
    <BrowserCard
      bridgeConnected={bridgeConnected}
      onReconnect={onReconnect}
      onCameraStateChange={onCameraStateChange}
    />
  </aside>
);

export default OperationsRail;
