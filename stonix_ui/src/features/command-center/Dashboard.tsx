import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import AssistantCore from './components/AssistantCore';
import OverviewPage from '@/features/overview/OverviewPage';
import QuickStatsCard from '@/features/overview/QuickStatsCard';
import RecentActivityCard from '@/features/overview/RecentActivityCard';
import { useLaptopOnline } from '@/features/overview/useLaptopOnline';
import { ensureActivityFeedSubscriptions } from '@/features/activity/feedStore';
import {
  connectedDeviceCount,
  useVortexStatusPoll,
} from '@/features/shell/useVortexStatusPoll';
import {
  getPersonaSwitchBridge,
  setPersonaSwitchBridge,
} from '@/features/overview/personaSwitchBridge';
import { SwarmModeProvider } from '@/features/panel/useSwarmMode';
import ChatPanel from './components/ChatPanel';
import OperationsRail from './components/OperationsRail';
import AgentsMountPortal from '@/features/agents/AgentsMountPortal';
import MachinesCards from '@/features/agents/MachinesCards';
import MapCard from '@/features/agents/MapCard';
import ActivityPage from '@/features/activity/ActivityPage';
import { defaultCameraState, type CameraState } from './components/cameraState';
import SettingsPage from '@/features/settings/SettingsPage';
import SimAccessDialog from './components/SimAccessDialog';
import VoiceEnrollDialog from '@/features/voice/VoiceEnrollDialog';
import ConfirmDialog from '@/features/confirm/ConfirmDialog';
import ConfirmBar from '@/features/confirm/ConfirmBar';
import ConfirmPhoneLayer from '@/features/confirm/ConfirmPhoneLayer';
import { useConfirmPhoneSheet } from '@/features/confirm/useConfirmPhoneSheet';
import { toast } from '@/toast';
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
import OrbCanvas from '@/features/voice/orb/OrbCanvas';
import VoiceAnnouncer from '@/features/voice/VoiceAnnouncer';
import { useAgentListeningMuted } from '@/features/status/AgentControls';
import { useVoiceUi } from '@/features/voice/voiceState';
import AgentsPage from '@/features/agents/AgentsPage';

const DashboardInner = () => {
  useEffect(() => {
    if (!__E2E_HOOKS__) return;
    void import('@/features/voice/e2eHooks').then(m => m.recordDashboardCommit());
  }, []);

  const layout = useLayout();
  const route = useHashRouter(layout);
  const { logout } = useAuth();
  const liveKit = useLiveKit();
  const agentStatus = useAgentStatus();
  const command = useCommandCenter();
  const [voiceEnrollOpen, setVoiceEnrollOpen] = useState(false);
  const [cameraState, setCameraState] = useState<CameraState>(defaultCameraState);
  const [mobileTab, setMobileTab] = useState<MobileTab>('core');
  const navMarks = useSidebarNavMarks(route);
  const vortex = useVortexStatusPoll(command.isConnected);
  const laptopOnline = useLaptopOnline();
  const agentListeningMuted = useAgentListeningMuted();
  const heroWaveCanvasRef = useRef<HTMLCanvasElement>(null);
  const phoneConfirm = useConfirmPhoneSheet(
    command.confirmCurrent?.id ?? null,
    command.confirmQueueCount,
  );
  const deferToastShownRef = useRef(false);
  const confirmCardCommon = {
    current: command.confirmCurrent,
    queue: command.confirmQueue,
    queueCount: command.confirmQueueCount,
    cancelNotice: command.confirmCancelNotice,
    onAllow: command.respondConfirmAllow,
    onDeny: command.respondConfirmDeny,
  };
  const deviceCount = useMemo(
    () => connectedDeviceCount(vortex.uiClients, agentStatus.online, vortex.laptopOnline),
    [vortex.uiClients, vortex.laptopOnline, agentStatus.online],
  );

  useEffect(() => {
    ensureActivityFeedSubscriptions();
  }, []);

  const shellMobileTab: MobileTab =
    route.page === 'chat'
      ? 'chat'
      : route.page === 'settings'
        ? 'settings'
        : route.page === 'agents'
          ? 'ops'
          : route.page === 'activity'
            ? 'trace'
            : mobileTab;

  useEffect(() => {
    if (layout !== 'ph' || !phoneConfirm.deferred || command.confirmQueueCount === 0) {
      deferToastShownRef.current = false;
      return;
    }
    if (shellMobileTab !== 'chat' && !deferToastShownRef.current) {
      toast('Approval is waiting in Chat — Open Chat to decide.', {
        icon: '◆',
        duration: 6000,
      });
      deferToastShownRef.current = true;
    }
    if (shellMobileTab === 'chat') {
      deferToastShownRef.current = false;
    }
  }, [command.confirmQueueCount, layout, phoneConfirm.deferred, shellMobileTab]);

  const overviewOrbActive = layout !== 'ph' || shellMobileTab === 'core';

  const handleMobileTabChange = useCallback((tab: MobileTab) => {
    if (tab === 'chat') {
      window.location.hash = '/chat';
      return;
    }
    if (tab === 'settings') {
      window.location.hash = '/settings';
      return;
    }
    if (tab === 'ops') {
      window.location.hash = '/agents';
      return;
    }
    if (tab === 'trace') {
      window.location.hash = '/activity';
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

  const openVoiceEnroll = () => {
    setVoiceEnrollOpen(true);
  };

  const handleReconnect = () =>
    command.reconnectBridge(liveKit, agentStatus.online);

  useEffect(() => {
    setPersonaSwitchBridge({
      ...getPersonaSwitchBridge(),
      appendChatSystemLine: command.appendChatSystemLine,
    });
    return () => {
      const { appendChatSystemLine: _drop, ...rest } = getPersonaSwitchBridge();
      setPersonaSwitchBridge(rest);
    };
  }, [command.appendChatSystemLine]);

  const voiceUi = useVoiceUi(command.activePersona, {
    isSpeaking: command.isSpeaking,
    isThinking: command.isThinking,
    isUserSpeaking: command.isUserSpeaking,
  });

  const overviewMain = (
    <>
      <main className="command-layout" data-mobile-tab={shellMobileTab}>
        <section className="command-center-stage">
          <OverviewPage
            persona={command.activePersona}
            isSpeaking={command.isSpeaking}
            isThinking={command.isThinking}
            isUserSpeaking={command.isUserSpeaking}
            waveformSlot={
              <canvas
                ref={heroWaveCanvasRef}
                className="overview-hero__wave-canvas"
                aria-hidden
                width={216}
                height={40}
              />
            }
            orbSlot={
              <OrbCanvas
                persona={command.activePersona}
                statusLine={voiceUi.statusLine}
                voiceUnavailable={liveKit.voiceUnavailable}
                roomState={liveKit.roomState}
                duplicateBlocked={liveKit.duplicateBlocked}
                linkedElsewhere={liveKit.linkedElsewhere}
                agentPresent={liveKit.agentPresent}
                agentMissing={liveKit.agentMissing}
                agentRestarting={agentStatus.restarting}
                agentOnline={agentStatus.online}
                playbackBlocked={liveKit.playbackBlocked}
                agentState={liveKit.agentState}
                agentListeningMuted={agentListeningMuted}
                micEnabled={liveKit.micEnabled}
                pushToTalkActive={liveKit.pushToTalkActive}
                isSpeaking={command.isSpeaking}
                isThinking={command.isThinking}
                isUserSpeaking={command.isUserSpeaking}
                pageActive={overviewOrbActive}
                waveCanvasRef={heroWaveCanvasRef}
              />
            }
            legacyStrip={
              <ErrorBoundary name="AssistantCore">
                <AssistantCore
                  persona={command.activePersona}
                  isConnected={command.isConnected}
                  isThinking={command.isThinking}
                />
              </ErrorBoundary>
            }
            quickStats={
              <ErrorBoundary name="QuickStats">
                <QuickStatsCard
                  vitalsPc={command.vitalsPc}
                  vitalsVps={command.vitalsVps}
                  laptopOnline={laptopOnline}
                  deviceCount={deviceCount}
                />
              </ErrorBoundary>
            }
            recentActivity={
              <ErrorBoundary name="RecentActivity">
                <RecentActivityCard />
              </ErrorBoundary>
            }
            belowHero={
              <ErrorBoundary name="IntelligencePanel">
                <IntelligencePanel
                  latest={command.intelligence}
                  history={command.intelligenceHistory}
                />
              </ErrorBoundary>
            }
          />
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
    <SwarmModeProvider>
      <VoiceAnnouncer persona={command.activePersona} state={voiceUi.state} />
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
                onReconnect={handleReconnect}
                cameraState={cameraState}
                voiceMatchLive={command.voiceMatchLive}
                onOpenVoiceEnroll={openVoiceEnroll}
              />
            </ErrorBoundary>
            <ErrorBoundary name="TopBar">
              <TopBar
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
              <ErrorBoundary name="AgentsPage">
                <AgentsPage
                  anchor={route.anchor}
                  voiceMatchLive={command.voiceMatchLive}
                  onOpenVoiceEnroll={openVoiceEnroll}
                  systemFlow={{
                    roomState: liveKit.roomState,
                    micEnabled: liveKit.micEnabled,
                    agentState: liveKit.agentState,
                    isMuted: command.isMuted,
                    isUserSpeaking: command.isUserSpeaking,
                    cameraState,
                    moduleHealth: command.moduleHealth,
                    lastMemorySyncMs: command.lastMemorySyncMs,
                    toolRunningCount: command.toolRunningCount,
                    toolLastError: command.toolLastError,
                    agentOnline: agentStatus.online,
                    agentPaused: agentStatus.paused,
                    agentSession: agentStatus.session,
                  }}
                />
              </ErrorBoundary>
            </ShellPage>
            <ShellPage page="activity" active={route.page === 'activity'}>
              <ErrorBoundary name="ActivityPage">
                <ActivityPage
                  route={route}
                  timelineAlertCount={
                    navMarks.showActivityBadge ? navMarks.activityCount : 0
                  }
                  systemAlerts={command.systemAlerts}
                  logs={command.vortexLogs}
                  tasks={command.tasks}
                  taskNotifications={command.taskNotifications}
                  onCancelTask={command.cancelTask}
                  onRefreshTasks={command.refreshTasks}
                  memories={command.memories}
                  onClearMemory={command.clearShortTermMemory}
                />
              </ErrorBoundary>
            </ShellPage>
            <ShellPage page="settings" active={route.page === 'settings'} className="settings-drawer-host">
              <ErrorBoundary name="SettingsPage">
                <SettingsPage
                  isConnected={command.isConnected}
                  onReconnect={handleReconnect}
                  onOpenVoiceEnroll={openVoiceEnroll}
                  onLogout={() => {
                    void liveKit.disconnectRoom();
                    void logout();
                  }}
                  onOpenSim={command.openSimPanel}
                  simProvider={command.simProvider}
                  simProviderConfigured={command.simProviderConfigured}
                />
              </ErrorBoundary>
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
              confirmQueue={command.confirmQueue}
              confirmDecisions={command.confirmDecisions}
              confirmBar={
                layout === 'ph' &&
                phoneConfirm.deferred &&
                command.confirmCurrent &&
                command.confirmQueueCount > 0 ? (
                  <ConfirmBar
                    queueCount={command.confirmQueueCount}
                    current={command.confirmCurrent}
                    onReview={phoneConfirm.reopenSheet}
                  />
                ) : null
              }
              confirmSlot={
                layout !== 'ph' ? <ConfirmDialog {...confirmCardCommon} /> : null
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

      {layout === 'ph' ? (
        <ConfirmPhoneLayer
          {...confirmCardCommon}
          open={phoneConfirm.sheetOpen}
          onDecideLater={phoneConfirm.decideLater}
        />
      ) : null}

      <ErrorBoundary name="VoiceEnrollDialog">
        <VoiceEnrollDialog open={voiceEnrollOpen} onClose={() => setVoiceEnrollOpen(false)} />
      </ErrorBoundary>

      <ErrorBoundary name="OperationsRail">
        <AgentsMountPortal selector=".agents-devices-mount">
          <OperationsRail
            persona={command.activePersona}
            bridgeConnected={command.isConnected}
            location={command.location}
            onCameraStateChange={setCameraState}
            onReconnect={handleReconnect}
            vortex={vortex}
            sessionRestartBanner={command.sessionRestartBanner}
            onSessionRestartBannerChange={command.setSessionRestartBanner}
          />
        </AgentsMountPortal>
      </ErrorBoundary>

      <ErrorBoundary name="MachinesCards">
        <AgentsMountPortal selector=".agents-machines-mount">
          <>
            <MachinesCards
              vitalsPc={command.vitalsPc}
              vitalsVps={command.vitalsVps}
              telemetry={command.telemetry}
              brain={vortex.brain}
            />
            <MapCard persona={command.activePersona} focus={command.mapFocus} />
          </>
        </AgentsMountPortal>
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
    </SwarmModeProvider>
  );
};

const Dashboard = () => <DashboardInner />;

export default Dashboard;
