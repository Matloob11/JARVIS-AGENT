import { Wifi } from 'lucide-react';
import { Card } from '@/shared/ui/Card';
import { Pill } from '@/shared/ui/Pill';
import { BRIDGE_BASE } from '@/shared/config/runtime';
import { useLiveKit } from '@/features/voice/LiveKitProvider';
import CameraPanel from '@/features/command-center/components/CameraPanel';
import type { CameraState } from '@/features/command-center/components/cameraState';

function voiceRoomLabel(voice: ReturnType<typeof useLiveKit>): string {
  if (voice.linkedElsewhere || voice.duplicateBlocked) return 'Linked to another device';
  if (voice.roomState === 'connecting' || voice.roomState === 'reconnecting') return 'Joining…';
  if (voice.voiceUnavailable || voice.roomState === 'error') return 'Unavailable';
  if (voice.roomState === 'connected') return 'Joined';
  return 'Offline';
}

export type BrowserCardProps = {
  bridgeConnected: boolean;
  onReconnect: () => void | Promise<void>;
  onCameraStateChange?: (state: CameraState) => void;
};

export function BrowserCard({ bridgeConnected, onReconnect, onCameraStateChange }: BrowserCardProps) {
  const voice = useLiveKit();
  const basePath = BRIDGE_BASE.startsWith('http') ? '/vortex' : BRIDGE_BASE || '/vortex';
  const dup = voice.linkedElsewhere || voice.duplicateBlocked;

  return (
    <div data-testid="browser-card" data-anchor="browser" className="agents-anchor">
      <Card
        icon="globe"
        title="This browser"
        subtitle="Your dashboard link, the voice room and the camera."
        boundaryName="This browser"
        className="agents-card agents-card--browser"
      >
        <div className="agents-browser-grid">
          <div className="agents-browser-meta">
            <div className="agents-row">
              <div className="agents-row__label">
                <b>Bridge</b>
                <small>
                  {bridgeConnected
                    ? `Base ${basePath}. Secure link active`
                    : 'The dashboard cannot reach the VPS'}
                </small>
              </div>
              <Pill tone={bridgeConnected ? 'ok' : 'err'}>{bridgeConnected ? 'Live' : 'Offline'}</Pill>
            </div>

            <div className="agents-row">
              <div className="agents-row__label">
                <b>Voice room</b>
                <small>{dup ? 'Voice is active on another device' : voice.identity || '—'}</small>
              </div>
              {dup ? (
                <button type="button" className="n-btn xs primary" onClick={() => void voice.takeOver()}>
                  Take over
                </button>
              ) : (
                <Pill
                  tone={
                    voice.roomState === 'connected' && !voice.voiceUnavailable ? 'ok' : 'warn'
                  }
                >
                  {voiceRoomLabel(voice)}
                </Pill>
              )}
            </div>

            <div className="agents-row">
              <div className="agents-row__label">
                <b>Audio playback</b>
                <small>
                  {voice.playbackBlocked
                    ? 'The browser blocked autoplay'
                    : 'Allowed'}
                </small>
              </div>
              {voice.playbackBlocked ? (
                <button
                  type="button"
                  className="n-btn xs primary"
                  data-testid="enable-audio"
                  onClick={() => void voice.enablePlayback()}
                >
                  Enable audio
                </button>
              ) : (
                <Pill tone="ok">Allowed</Pill>
              )}
            </div>

            <div className="agents-actions">
              <button
                type="button"
                className="n-btn sm"
                data-testid="topbar-reconnect"
                title="Reconnect: rejoins bridge and voice without reloading the dashboard"
                onClick={() => void onReconnect()}
              >
                <Wifi size={15} />
                Reconnect
              </button>
            </div>
          </div>

          <div className="agents-browser-camera">
            <CameraPanel embedded onCameraStateChange={onCameraStateChange} />
          </div>
        </div>
      </Card>
    </div>
  );
}

export default BrowserCard;
