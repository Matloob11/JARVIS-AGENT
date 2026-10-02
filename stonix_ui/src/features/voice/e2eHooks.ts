import {
  clearE2EPanelCrash,
  setE2EPanelCrashTarget,
} from '@/shared/e2e/panelCrash';
import { seedReferenceActivityRows, type ActivityFeedRow } from '@/features/activity/feedStore';
import { toast } from '@/toast';
import { audioEngine } from './audioEngine';
import { getActiveOrbRenderer } from './orb/orbRenderer';
export type { E2EVoiceInputs, E2EVoiceState } from './e2eVoiceOverride';
import type { E2EVoiceInputs, E2EVoiceState } from './e2eVoiceOverride';
import {
  getE2EVoiceOverrides,
  setE2EVoiceInputsOverride,
  setE2EVoiceStateOverride,
} from './e2eVoiceOverride';

type JarvisTestApi = {
  crashPanel: (boundaryName: string) => void;
  clearPanelCrash: () => void;
  setVoiceState: (state: E2EVoiceState | null) => void;
  setVoiceInputs: (inputs: E2EVoiceInputs) => void;
  feedAgent: (
    level: number | 'speech' | null,
    bands?: number[],
    opts?: { dbfs?: number; f0?: number; seed?: number },
  ) => void;
  feedUser: (
    level: number | 'speech' | null,
    bands?: number[],
    opts?: { dbfs?: number; f0?: number; seed?: number },
  ) => void;
  bindAgentStream: (stream: MediaStream) => Promise<void>;
  unbindAgentStream: () => Promise<void>;
  stats: () => {
    loop: string;
    draws: number;
    roomWrites: number;
    engineLoop: string;
    engineTicks: number;
    commits: number;
  };
  readAgentDbfs: () => number;
  readAgentBands: () => number[];
  agentContextState: () => string;
  referenceFrame: (on: boolean) => void;
  frameReady: () => boolean;
};

declare global {
  interface Window {
    __jarvisTest?: JarvisTestApi;
  }
}

let dashboardCommits = 0;

const REFERENCE_ACTIVITY: ActivityFeedRow[] = [
  {
    id: 'ref-act-1',
    kind: 'system',
    level: 'ok',
    title: 'System online',
    subtitle: 'All systems operational',
    ts: Date.parse('2026-10-02T21:41:00+05:00'),
  },
  {
    id: 'ref-act-2',
    kind: 'voice',
    level: 'info',
    title: 'Voice service ready',
    subtitle: 'Microphone initialized',
    ts: Date.parse('2026-10-02T21:40:00+05:00'),
  },
  {
    id: 'ref-act-3',
    kind: 'device',
    level: 'info',
    title: 'Laptop connected',
    subtitle: 'Windows 11 • 3 devices online',
    ts: Date.parse('2026-10-02T21:38:00+05:00'),
  },
  {
    id: 'ref-act-4',
    kind: 'sync',
    level: 'neutral',
    title: 'Agent sync complete',
    subtitle: 'JARVIS & ANNA ready',
    ts: Date.parse('2026-10-02T21:37:00+05:00'),
  },
];

function setConfirmMetaHidden(hidden: boolean) {
  document.querySelectorAll<HTMLElement>('.confirm-dialog__meta').forEach(el => {
    if (hidden) {
      el.dataset.refHidden = '1';
      el.style.display = 'none';
    } else if (el.dataset.refHidden) {
      delete el.dataset.refHidden;
      el.style.display = '';
    }
  });
}

export { getE2EVoiceOverrides };

export function recordDashboardCommit() {
  dashboardCommits += 1;
}

export function install() {
  const api: JarvisTestApi = {
    crashPanel(boundaryName) {
      setE2EPanelCrashTarget(boundaryName);
    },
    clearPanelCrash() {
      clearE2EPanelCrash();
    },
    setVoiceState(state) {
      setE2EVoiceStateOverride(state);
      if (state === 'speaking') {
        audioEngine.setAgentFeed('speech');
      } else if (state === 'listening') {
        audioEngine.setUserFeed('speech');
      } else if (state === null) {
        audioEngine.setAgentFeed(null);
        audioEngine.setUserFeed(null);
      }
    },
    setVoiceInputs(inputs) {
      setE2EVoiceInputsOverride(inputs);
    },
    feedAgent(level, bands, opts) {
      if (level === null) {
        audioEngine.setAgentFeed(null);
        return;
      }
      if (level === 'speech') {
        audioEngine.setAgentFeed('speech', undefined, opts);
        return;
      }
      audioEngine.setAgentFeed(level, bands, opts);
    },
    feedUser(level, bands, opts) {
      if (level === null) {
        audioEngine.setUserFeed(null);
        return;
      }
      if (level === 'speech') {
        audioEngine.setUserFeed('speech', undefined, opts);
        return;
      }
      audioEngine.setUserFeed(level, bands, opts);
    },
    async bindAgentStream(stream) {
      await audioEngine.bindAgentStream(stream);
    },
    async unbindAgentStream() {
      await audioEngine.unbindAgent();
    },
    stats() {
      const engine = audioEngine.getStats();
      const orb = getActiveOrbRenderer()?.stats() ?? {
        loop: 'stop' as const,
        draws: 0,
        glowWrites: 0,
      };
      return {
        loop: orb.loop,
        draws: orb.draws,
        hasOrb: getActiveOrbRenderer() != null,
        roomWrites: orb.glowWrites,
        engineLoop: engine.engineLoop,
        engineTicks: engine.engineTicks,
        commits: dashboardCommits,
      };
    },
    readAgentDbfs() {
      return audioEngine.readAgent().dbfs;
    },
    readAgentBands() {
      return Array.from(audioEngine.readAgent().bands);
    },
    agentContextState() {
      return audioEngine.getAgentContextState();
    },
    referenceFrame(on) {
      if (on) {
        seedReferenceActivityRows(REFERENCE_ACTIVITY);
        setConfirmMetaHidden(true);
        toast.dismiss();
        document.documentElement.setAttribute('data-reference-frame', '1');
        getActiveOrbRenderer()?.setReducedMotion(true);
      } else {
        setConfirmMetaHidden(false);
        document.documentElement.removeAttribute('data-reference-frame');
      }
    },
    frameReady() {
      const orb = getActiveOrbRenderer();
      if (!orb) return false;
      const st = orb.stats();
      return st.draws >= 1;
    },
  };

  window.__jarvisTest = api;
}

export function requireJarvisTest(page: { evaluate: <T>(fn: () => T) => Promise<T> }): Promise<void> {
  return page.evaluate(() => {
    if (!window.__jarvisTest) {
      throw new Error('e2e hook missing: rebuild with VITE_E2E_HOOKS=1');
    }
  });
}
