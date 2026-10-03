import { Activity } from 'lucide-react';
import { personaAccent } from '@/shared/theme/commandTheme';
import { useLiveKit } from '@/features/voice/LiveKitProvider';

interface AssistantCoreProps {
  persona: 'jarvis' | 'anna';
  isConnected: boolean;
  isThinking: boolean;
}

const AssistantCore = ({ persona, isConnected, isThinking }: AssistantCoreProps) => {
  const accent = personaAccent(persona);
  const voice = useLiveKit();
  const voiceConnecting = voice.roomState === 'connecting' || voice.roomState === 'reconnecting';
  const voiceLive = voice.roomState === 'connected';
  const agentLabel = voice.agentState ? voice.agentState : voiceLive ? 'linked' : 'offline';

  const stateLabel = voiceConnecting
    ? 'Joining voice…'
    : voice.voiceUnavailable
      ? 'Voice unavailable'
      : voiceLive
        ? `Voice (${voice.roomName || 'jarvis-hybrid'}) · ${agentLabel}`
        : isThinking
          ? 'Processing'
          : isConnected
            ? 'System active'
            : 'Offline';

  return (
    <div className="overview-legacy-strip assistant-core assistant-core--legacy" data-legacy>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-xs font-semibold text-command-text">
          <Activity size={14} className={accent.text} />
          {stateLabel}
        </div>
        <p className="mt-1 text-xs text-command-muted">
          {voice.voiceUnavailable
            ? 'LiveKit abhi available nahi — chat aur bridge controls chalte rahenge.'
            : voice.micEnabled
              ? 'Mic live — bolo, agent sunega aur bolega. Typed sawal ka jawab text me aur (voice on ho to) awaaz me.'
              : voiceLive
                ? 'Mic off — room linked hai. Mic on dabao jab bolna ho; Agent listening alag control hai (mute = sirf awaaz band, typed chat chalti rahegi).'
                : 'Login ke baad room auto-link hota hai (mic off). Mic on = permission allow.'}
        </p>
        {voice.error ? (
          <p className="mt-1 max-w-md text-xs text-red-400" role="alert">
            Voice: {voice.error}
          </p>
        ) : null}
      </div>
    </div>
  );
};

export default AssistantCore;
