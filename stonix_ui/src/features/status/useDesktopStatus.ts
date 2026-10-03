import { useSyncExternalStore } from 'react';
import {
  getDesktopStatusSnapshot,
  subscribeDesktopStatus,
  type DesktopStatusSnapshot,
} from './desktopStatusStore';

export function useDesktopStatus(): DesktopStatusSnapshot {
  return useSyncExternalStore(subscribeDesktopStatus, getDesktopStatusSnapshot, getDesktopStatusSnapshot);
}
