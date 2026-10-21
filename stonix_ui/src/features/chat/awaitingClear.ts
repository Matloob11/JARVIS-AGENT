/** Stop speaking clears typed awaitingReply (RESCUE / K-59). Registered by useChat. */

type ClearFn = () => void;

let clearer: ClearFn | null = null;

export function registerAwaitingClearer(fn: ClearFn | null): void {
  clearer = fn;
}

export function clearChatAwaitingOnStop(): void {
  clearer?.();
}
