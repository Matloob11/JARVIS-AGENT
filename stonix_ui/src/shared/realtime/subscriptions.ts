import { useEffect, useRef, useSyncExternalStore } from 'react';
import type { Socket } from 'socket.io-client';

type EventHandler = (...args: unknown[]) => void;

interface Subscription {
  event: string;
  handler: EventHandler;
}

let nextSubId = 1;
const subscriptions = new Map<number, Subscription>();
let boundSocket: Socket | null = null;
let socketGeneration = 0;
const generationListeners = new Set<() => void>();

function notifyGeneration() {
  for (const listener of generationListeners) {
    listener();
  }
}

function emitRequestState() {
  if (!boundSocket?.connected) return;
  boundSocket.emit('ui_command', { type: 'request_state', payload: {} });
}

const requestStateSent = new WeakMap<Socket, boolean>();

function onSocketConnect() {
  if (!boundSocket) return;
  if (requestStateSent.get(boundSocket)) return;
  requestStateSent.set(boundSocket, true);
  emitRequestState();
}

function onSocketDisconnect() {
  if (boundSocket) {
    requestStateSent.delete(boundSocket);
  }
}

function bindSubscription(sub: Subscription) {
  if (!boundSocket) return;
  boundSocket.off(sub.event, sub.handler);
  boundSocket.on(sub.event, sub.handler);
}

function unbindSubscription(sub: Subscription, socket: Socket) {
  socket.off(sub.event, sub.handler);
}

/** Attach all registry listeners to a new socket instance (after manual reconnect). */
export function attachSocketRegistry(socket: Socket): void {
  if (boundSocket === socket) return;

  if (boundSocket) {
    for (const sub of subscriptions.values()) {
      unbindSubscription(sub, boundSocket);
    }
    boundSocket.off('connect', onSocketConnect);
    boundSocket.off('disconnect', onSocketDisconnect);
  }

  boundSocket = socket;
  requestStateSent.delete(socket);
  socketGeneration += 1;
  notifyGeneration();

  for (const sub of subscriptions.values()) {
    bindSubscription(sub);
  }
  socket.on('connect', onSocketConnect);
  socket.on('disconnect', onSocketDisconnect);
  if (socket.connected) {
    onSocketConnect();
  }
}

/** Detach registry from the current socket without removing subscription entries. */
export function detachSocketRegistry(): void {
  if (!boundSocket) return;
  for (const sub of subscriptions.values()) {
    unbindSubscription(sub, boundSocket);
  }
  boundSocket.off('connect', onSocketConnect);
  boundSocket.off('disconnect', onSocketDisconnect);
  requestStateSent.delete(boundSocket);
  boundSocket = null;
}

export function subscribeSocketEvent(event: string, handler: EventHandler): () => void {
  const id = nextSubId++;
  subscriptions.set(id, { event, handler });
  if (boundSocket) {
    bindSubscription(subscriptions.get(id)!);
  }
  return () => {
    const sub = subscriptions.get(id);
    if (sub && boundSocket) {
      unbindSubscription(sub, boundSocket);
    }
    subscriptions.delete(id);
  };
}

export function useSocketEvent(event: string, handler: EventHandler): void {
  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  useEffect(() => {
    const wrapped = (...args: unknown[]) => {
      handlerRef.current(...args);
    };
    return subscribeSocketEvent(event, wrapped);
  }, [event]);
}

export function getSocketGeneration(): number {
  return socketGeneration;
}

function subscribeSocketGeneration(onStoreChange: () => void): () => void {
  generationListeners.add(onStoreChange);
  return () => {
    generationListeners.delete(onStoreChange);
  };
}

export function useSocketGeneration(): number {
  return useSyncExternalStore(
    subscribeSocketGeneration,
    getSocketGeneration,
    getSocketGeneration,
  );
}
