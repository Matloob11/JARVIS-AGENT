import { useState } from 'react';
import { AnimatePresence } from 'framer-motion';
import AssistantCore from './components/AssistantCore';
import ChatPanel from './components/ChatPanel';
import FlowMap from './components/FlowMap';
import OperationsRail from './components/OperationsRail';
import { defaultCameraState, type CameraState } from './components/cameraState';
import SettingsPanel from './components/SettingsPanel';
import SimAccessDialog from './components/SimAccessDialog';
import ConfirmDialog from '@/features/confirm/ConfirmDialog';
import SystemTrace from './components/SystemTrace';
import TopBar from './components/TopBar';
import { useCommandCenter } from './hooks/useCommandCenter';
import { ErrorBoundary } from '@/shared/ui/ErrorBoundary';
import { useAuth } from '@/features/auth/AuthProvider';
import { useLiveKit } from '@/features/voice/LiveKitProvider';
import { useAgentStatus } from '@/features/status/useAgentStatus';
import IntelligencePanel from '@/features/intelligence/IntelligencePanel';

const Dashboard = () => {
  const { logout } = useAuth();
  const liveKit = useLiveKit();
  const agentStatus = useAgentStatus();
  const command = useCommandCenter();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [cameraState, setCameraState] = useState<CameraState>(defaultCameraState);

  const handleReconnect = () =>
    command.reconnectBridge(liveKit, agentStatus.online);

  return (
    <div className="command-root">
      <ErrorBoundary name="ConfirmDialog">
        <ConfirmDialog
          current={command.confirmCurrent}
          queueCount={command.confirmQueueCount}
          cancelNotice={command.confirmCancelNotice}
          onAllow={command.respondConfirmAllow}
          onDeny={command.respondConfirmDeny}
        />
      </ErrorBoundary>
      <div className="command-background" />
      <ErrorBoundary name="TopBar">
        <TopBar
          persona={command.activePersona}
          isConnected={command.isConnected}
          onReconnect={handleReconnect}
          onOpenSettings={() => setSettingsOpen(true)}
          onOpenSim={command.openSimPanel}
          onLogout={() => {
            void liveKit.disconnectRoom();
            void logout();
          }}
        />
      </ErrorBoundary>

      <main className="command-layout">
        <ErrorBoundary name="OperationsRail">
          <OperationsRail
            persona={command.activePersona}
            location={command.location}
            onCameraStateChange={setCameraState}
            mapFocus={command.mapFocus}
            vitalsPc={command.vitalsPc}
            vitalsVps={command.vitalsVps}
            telemetry={command.telemetry}
            systemAlerts={command.systemAlerts}
            logs={command.vortexLogs}
          />
        </ErrorBoundary>

        <section className="command-center-stage">
          <ErrorBoundary name="FlowMap">
            <FlowMap
              persona={command.activePersona}
              isConnected={command.isConnected}
              isMuted={command.isMuted}
              isThinking={command.isThinking}
              cameraState={cameraState}
            />
          </ErrorBoundary>
          <ErrorBoundary name="AssistantCore">
            <AssistantCore
              persona={command.activePersona}
              isConnected={command.isConnected}
              isSpeaking={command.isSpeaking}
              isThinking={command.isThinking}
              voiceMatch={command.voiceMatch}
            />
          </ErrorBoundary>
          <ErrorBoundary name="IntelligencePanel">
            <IntelligencePanel
              latest={command.intelligence}
              history={command.intelligenceHistory}
            />
          </ErrorBoundary>
          <ErrorBoundary name="SystemTrace">
            <SystemTrace
              logs={command.vortexLogs}
              reasoning={command.reasoning}
              isConnected={command.isConnected}
            />
          </ErrorBoundary>
        </section>

        <ErrorBoundary name="ChatPanel">
          <ChatPanel
            persona={command.activePersona}
            messages={command.messages}
            logs={command.vortexLogs}
            memories={command.memories}
            tools={command.toolLogs}
            onSend={command.sendMessage}
            liveCaptions={command.chatLiveCaptions}
            fallbackTranscription={command.transcription}
            voiceConnected={liveKit.roomState === 'connected'}
            hasMore={command.chatHasMore}
            onLoadOlder={command.loadOlderChatMessages}
            onRetry={command.retryChatMessage}
          />
        </ErrorBoundary>
      </main>

      <AnimatePresence>
        {settingsOpen && (
          <ErrorBoundary name="SettingsPanel">
            <SettingsPanel
              isConnected={command.isConnected}
              onClose={() => setSettingsOpen(false)}
              onReconnect={handleReconnect}
            />
          </ErrorBoundary>
        )}
      </AnimatePresence>

      <ErrorBoundary name="SimAccessDialog">
        <SimAccessDialog
          open={command.isSimPanelOpen}
          records={command.simRecords}
          isLoading={command.simLoading}
          queryMasked={command.simQueryMasked}
          status={command.simStatus}
          message={command.simMessage}
          onClose={command.closeSimPanel}
          onSearch={command.requestSimLookup}
        />
      </ErrorBoundary>
    </div>
  );
};

export default Dashboard;
