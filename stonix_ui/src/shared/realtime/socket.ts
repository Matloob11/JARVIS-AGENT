import { io, type Socket } from 'socket.io-client';
import { SOCKET_ORIGIN, SOCKET_PATH } from '@/shared/config/runtime';
import { attachSocketRegistry, detachSocketRegistry } from '@/shared/realtime/subscriptions';

let sharedSocket: Socket | null = null;

/**
 * Cookie-session Socket.IO client (K-01). No auth token in the handshake.
 */
export function getSocket(): Socket {
  if (!sharedSocket) {
    sharedSocket = io(SOCKET_ORIGIN || undefined, {
      path: SOCKET_PATH,
      withCredentials: true,
      transports: ['websocket', 'polling'],
      tryAllTransports: true,
      reconnection: true,
      reconnectionAttempts: Infinity,
      reconnectionDelayMax: 10000,
    });
    attachSocketRegistry(sharedSocket);
  }
  return sharedSocket;
}

export function disconnectSocket(): void {
  if (sharedSocket) {
    detachSocketRegistry();
    sharedSocket.disconnect();
    sharedSocket = null;
  }
}

/** Register a connect_error listener (session expiry / network). */
export function onConnectError(cb: (err: Error) => void): () => void {
  const socket = getSocket();
  const handler = (err: Error) => cb(err);
  socket.on('connect_error', handler);
  return () => {
    socket.off('connect_error', handler);
  };
}
