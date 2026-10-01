import { useState } from 'react';
import { AnimatePresence } from 'framer-motion';
import AssistantCore from './components/AssistantCore';
import ChatPanel from './components/ChatPanel';
import FlowMap from './components/FlowMap';
import OperationsRail from './components/OperationsRail';
import SettingsPanel from './components/SettingsPanel';
import SimAccessDialog from './components/SimAccessDialog';
import SystemTrace from './components/SystemTrace';
import TopBar from './components/TopBar';
import { useCommandCenter } from './hooks/useCommandCenter';
import { ErrorBoundary } from '@/shared/ui/ErrorBoundary';
import { useAuth } from '@/features/auth/AuthProvider';

const Dashboard = () => {
  const { logout } = useAuth();
  const command = useCommandCenter();
  const [settingsOpen, setSettingsOpen] = useState(false);

  return (
    <div className="command-root">
      <div className="command-background" />
      <ErrorBoundary name="TopBar">
        <TopBar
          persona={command.activePersona}
          isConnected={command.isConnected}
          isMuted={command.isMuted}
          isWakeWordActive={command.isWakeWordActive}
          status={command.status}
          onReconnect={command.reconnect}
          onOpenSettings={() => setSettingsOpen(true)}
          onOpenSim={command.openSimPanel}
          onToggleMute={command.toggleMute}
          onLogout={() => void logout()}
        />
      </ErrorBoundary>

      <main className="command-layout">
        <ErrorBoundary name="OperationsRail">
          <OperationsRail
            persona={command.activePersona}
            isMuted={command.isMuted}
            location={command.location}
            vitals={command.vitals}
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
            />
          </ErrorBoundary>
          <ErrorBoundary name="AssistantCore">
            <AssistantCore
              persona={command.activePersona}
              isConnected={command.isConnected}
              isSpeaking={command.isSpeaking}
              isThinking={command.isThinking}
              voiceMatch={command.voiceMatch}
              isMuted={command.isMuted}
              onEnsureUnmuted={() => {
                if (command.isMuted) command.toggleMute();
              }}
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
          />
        </ErrorBoundary>
      </main>

      <AnimatePresence>
        {settingsOpen && (
          <ErrorBoundary name="SettingsPanel">
            <SettingsPanel
              isConnected={command.isConnected}
              onClose={() => setSettingsOpen(false)}
              onReconnect={command.reconnect}
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
