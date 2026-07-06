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

const Dashboard = () => {
  const command = useCommandCenter();
  const [settingsOpen, setSettingsOpen] = useState(false);

  return (
    <div className="command-root">
      <div className="command-background" />
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
      />

      <main className="command-layout">
        <OperationsRail
          persona={command.activePersona}
          isMuted={command.isMuted}
          location={command.location}
          vitals={command.vitals}
          logs={command.vortexLogs}
        />

        <section className="command-center-stage">
          <FlowMap
            persona={command.activePersona}
            isConnected={command.isConnected}
            isMuted={command.isMuted}
            isThinking={command.isThinking}
          />
          <AssistantCore
            persona={command.activePersona}
            isConnected={command.isConnected}
            isSpeaking={command.isSpeaking}
            isThinking={command.isThinking}
            voiceMatch={command.voiceMatch}
          />
          <SystemTrace
            logs={command.vortexLogs}
            reasoning={command.reasoning}
            isConnected={command.isConnected}
          />
        </section>

        <ChatPanel
          persona={command.activePersona}
          messages={command.messages}
          logs={command.vortexLogs}
          memories={command.memories}
          tools={command.toolLogs}
          onSend={command.sendMessage}
        />
      </main>

      <AnimatePresence>
        {settingsOpen && (
          <SettingsPanel
            isConnected={command.isConnected}
            onClose={() => setSettingsOpen(false)}
            onReconnect={command.reconnect}
          />
        )}
      </AnimatePresence>

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
    </div>
  );
};

export default Dashboard;
