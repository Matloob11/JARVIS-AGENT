import { useCallback, useEffect, useState } from 'react';
import { AnimatePresence } from 'framer-motion';
import AssistantCore from './components/AssistantCore';
import ChatPanel from './components/ChatPanel';
import FlowMap from './components/FlowMap';
import OperationsRail from './components/OperationsRail';
import { defaultCameraState, type CameraState } from './components/cameraState';
import SettingsPanel from './components/SettingsPanel';
import SimAccessDialog from './components/SimAccessDialog';
import VoiceEnrollDialog from '@/features/voice/VoiceEnrollDialog';
import ConfirmDialog from '@/features/confirm/ConfirmDialog';
import SystemTrace from './components/SystemTrace';
import TopBar from './components/TopBar';
import PageHeader from '@/features/shell/PageHeader';
import MobileTabBar, { type MobileTab } from './components/MobileTabBar';
import { useSidebarNavMarks } from '@/features/shell/useSidebarNavMarks';
import { useCommandCenter } from './hooks/useCommandCenter';
import { ErrorBoundary } from '@/shared/ui/ErrorBoundary';
import { useAuth } from '@/features/auth/AuthProvider';
import { useLiveKit } from '@/features/voice/LiveKitProvider';
import { useAgentStatus } from '@/features/status/useAgentStatus';
import IntelligencePanel from '@/features/intelligence/IntelligencePanel';
import AppShell from '@/features/shell/AppShell';
import { ShellPage } from '@/features/shell/ShellPage';
import { useLayout } from '@/features/shell/useLayout';
import { useHashRouter } from '@/features/shell/useHashRouter';

const DashboardInner = () => {
  useEffect(() => {
    if (!__E2E_HOOKS__) return;
    void import('@/features/voice/e2eHooks').then(m => m.recordDashboardCommit());
  });

  const layout = useLayout();
  const route = useHashRouter(layout);
  const { logout } = useAuth();
  const liveKit = useLiveKit();
  const agentStatus = useAgentStatus();
  const command = useCommandCenter();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [voiceEnrollOpen, setVoiceEnrollOpen] = useState(false);
  const [cameraState, setCameraState] = useState<CameraState>(defaultCameraState);
  const [mobileTab, setMobileTab] = useState<MobileTab>('core');
  const [opsDrawerOpen, setOpsDrawerOpen] = useState(false);
  const navMarks = useSidebarNavMarks(route);

  const shellMobileTab: MobileTab =
    route.page === 'chat' ? 'chat' : route.page === 'settings' ? 'settings' : mobileTab;

  const handleMobileTabChange = useCallback((tab: MobileTab) => {
    if (tab === 'chat') {
      window.location.hash = '/chat';
      return;
    }
    if (tab === 'settings') {
      window.location.hash = '/settings';
      return;
    }
    if (window.location.hash.replace(/^#/, '').split('/')[0] !== 'overview') {
      window.location.hash = '/overview';
    }
    setMobileTab(tab);
  }, []);

  useEffect(() => {
    if (layout !== 'ph') return;
    if (route.page === 'chat') {
      setMobileTab('chat');
    } else if (route.page === 'settings') {
      setMobileTab('settings');
    } else if (route.page === 'overview') {
      setMobileTab(prev => (prev === 'chat' || prev === 'settings' ? 'core' : prev));
    }
  }, [layout, route.page]);

  const toggleOpsDrawer = useCallback(() => {
    setOpsDrawerOpen(prev => !prev);
  }, []);

  const closeOpsDrawer = useCallback(() => {
    setOpsDrawerOpen(false);
  }, []);

  const openVoiceEnroll = () => {
    setVoiceEnrollOpen(true);
    setSettingsOpen(false);
  };

  const handleReconnect = () =>
    command.reconnectBridge(liveKit, agentStatus.online);

  const overviewMain = (
    <>
      {opsDrawerOpen ? (
        <button
          type="button"
          className="ops-drawer-backdrop"
          aria-label="Close operations panel"
          data-testid="ops-drawer-backdrop"
          onClick={closeOpsDrawer}
        />
      ) : null}

      <main
        className="command-layout"
        data-mobile-tab={shellMobileTab}
        data-ops-drawer={opsDrawerOpen ? 'open' : 'closed'}
      >
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
          <div className="layout-zone layout-zone--core" data-testid="layout-zone-core">
            <ErrorBoundary name="FlowMap">
              <FlowMap
                persona={command.activePersona}
                roomState={liveKit.roomState}
                micEnabled={liveKit.micEnabled}
                agentState={liveKit.agentState}
                isMuted={command.isMuted}
                cameraState={cameraState}
                moduleHealth={command.moduleHealth}
                lastMemorySyncMs={command.lastMemorySyncMs}
                toolRunningCount={command.toolRunningCount}
                toolLastError={command.toolLastError}
                agentOnline={agentStatus.online}
                agentPaused={agentStatus.paused}
                agentSession={agentStatus.session}
              />
            </ErrorBoundary>
            <ErrorBoundary name="AssistantCore">
              <AssistantCore
                persona={command.activePersona}
                isConnected={command.isConnected}
                isSpeaking={command.isSpeaking}
                isThinking={command.isThinking}
                isUserSpeaking={command.isUserSpeaking}
                agentAudioActive={command.agentAudioActive}
                voiceMatchLive={command.voiceMatchLive}
                onOpenVoiceEnroll={openVoiceEnroll}
                sessionRestartBanner={command.sessionRestartBanner}
                onSessionRestartBannerChange={command.setSessionRestartBanner}
              />
            </ErrorBoundary>
            <ErrorBoundary name="IntelligencePanel">
              <IntelligencePanel
                latest={command.intelligence}
                history={command.intelligenceHistory}
              />
            </ErrorBoundary>
          </div>
          <div className="layout-zone layout-zone--trace" data-testid="layout-zone-trace">
            <ErrorBoundary name="SystemTrace">
              <SystemTrace
                logs={command.vortexLogs}
                reasoning={command.reasoning}
                reasoningHistory={command.reasoningHistory}
                planProgress={command.planProgress}
                systemFeed={command.systemFeed}
                logDroppedCount={command.logDroppedCount}
                isConnected={command.isConnected}
                onLogsPausedChange={command.setTraceLogsPaused}
              />
            </ErrorBoundary>
          </div>
        </section>
      </main>
    </>
  );

  return (
    <>
      <AppShell
        route={route}
        mobileTab={shellMobileTab}
        head={
          <>
            <ErrorBoundary name="PageHeader">
              <PageHeader
                route={route}
                layout={layout}
                bridgeConnected={command.isConnected}
                isUserSpeaking={command.isUserSpeaking}
                taskUnreadCount={command.taskUnreadCount}
                taskNotifications={command.taskNotifications}
                onOpenTaskBell={command.openTaskBell}
              />
            </ErrorBoundary>
            <ErrorBoundary name="TopBar">
              <TopBar
                isConnected={command.isConnected}
                onReconnect={handleReconnect}
                onOpenSettings={() => setSettingsOpen(true)}
                onToggleOpsDrawer={toggleOpsDrawer}
                opsDrawerOpen={opsDrawerOpen}
                onLogout={() => {
                  void liveKit.disconnectRoom();
                  void logout();
                }}
              />
            </ErrorBoundary>
          </>
        }
        main={
          <>
            <ShellPage page="overview" active={route.page === 'overview'}>
              {overviewMain}
            </ShellPage>
            <ShellPage page="agents" active={route.page === 'agents'}>
              <div className="command-panel p-4" data-legacy>
                <p className="text-sm text-command-muted">Agents page (legacy content remains on Overview).</p>
              </div>
            </ShellPage>
            <ShellPage page="activity" active={route.page === 'activity'}>
              <div className="command-panel p-4" data-legacy>
                <p className="text-sm text-command-muted">Activity page (legacy trace remains on Overview).</p>
              </div>
            </ShellPage>
            <ShellPage page="settings" active={route.page === 'settings'} className="settings-drawer-host">
              <div className="command-panel p-4" data-legacy>
                <p className="text-sm text-command-muted">Settings page (open Settings from the header).</p>
              </div>
            </ShellPage>
          </>
        }
        panel={
          <ErrorBoundary name="ChatPanel">
            <ChatPanel
              persona={command.activePersona}
              messages={command.messages}
              logs={command.vortexLogs}
              memories={command.memories}
              onClearMemory={command.clearShortTermMemory}
              toolLogs={command.toolLogs}
              jobs={command.jobs}
              onCancelJob={command.cancelJob}
              onOpenJobArtifact={command.openJobArtifact}
              tasks={command.tasks}
              onCancelTask={command.cancelTask}
              onRefreshTasks={command.refreshTasks}
              onSend={command.sendMessage}
              onSendSwarm={command.sendSwarmTask}
              onFeedback={command.sendAnswerFeedback}
              liveCaptions={command.chatLiveCaptions}
              fallbackTranscription={command.transcription}
              voiceConnected={liveKit.roomState === 'connected'}
              hasMore={command.chatHasMore}
              onLoadOlder={command.loadOlderChatMessages}
              onRetry={command.retryChatMessage}
              isThinking={command.isThinking}
              approvalsWaiting={command.confirmQueueCount}
              onOpenSim={command.openSimPanel}
              confirmSlot={
                <ConfirmDialog
                  current={command.confirmCurrent}
                  queue={command.confirmQueue}
                  queueCount={command.confirmQueueCount}
                  cancelNotice={command.confirmCancelNotice}
                  onAllow={command.respondConfirmAllow}
                  onDeny={command.respondConfirmDeny}
                />
              }
            />
          </ErrorBoundary>
        }
        tabs={
          <MobileTabBar
            active={shellMobileTab}
            onChange={handleMobileTabChange}
            approvalCount={command.confirmQueueCount}
            activityAlert={navMarks.showActivityBadge}
          />
        }
      />

      <AnimatePresence>
        {settingsOpen && (
          <ErrorBoundary name="SettingsPanel">
            <SettingsPanel
              isConnected={command.isConnected}
              onClose={() => setSettingsOpen(false)}
              onReconnect={handleReconnect}
              onOpenVoiceEnroll={openVoiceEnroll}
              onLogout={() => {
                void liveKit.disconnectRoom();
                void logout();
              }}
            />
          </ErrorBoundary>
        )}
      </AnimatePresence>

      <ErrorBoundary name="VoiceEnrollDialog">
        <VoiceEnrollDialog open={voiceEnrollOpen} onClose={() => setVoiceEnrollOpen(false)} />
      </ErrorBoundary>

      <ErrorBoundary name="SimAccessDialog">
        <SimAccessDialog
          open={command.isSimPanelOpen}
          records={command.simRecords}
          isLoading={command.simLoading}
          queryMasked={command.simQueryMasked}
          status={command.simStatus}
          message={command.simMessage}
          provider={command.simProvider}
          providerConfigured={command.simProviderConfigured}
          requiresPurpose={command.simRequiresPurpose}
          agentOnline={agentStatus.online}
          onClose={command.closeSimPanel}
          onSearch={command.requestSimLookup}
        />
      </ErrorBoundary>
    </>
  );
};

const Dashboard = () => <DashboardInner />;

export default Dashboard;
