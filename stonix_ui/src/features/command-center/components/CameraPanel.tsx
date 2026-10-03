import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Camera, CameraOff } from 'lucide-react';
import { getSocket } from '@/shared/realtime/socket';
import { useLiveKit } from '@/features/voice/LiveKitProvider';
import { Panel, StatusDot } from '@/shared/ui/Panel';
import { Switch } from '@/shared/ui/Switch';
import {
  cameraBadgeLabel,
  defaultCameraState,
  type CameraBadge,
  type CameraState,
} from './cameraState';

interface CameraPanelProps {
  embedded?: boolean;
  onCameraStateChange?: (state: CameraState) => void;
}

const FALLBACK_VIDEO: MediaTrackConstraints = {
  width: { ideal: 640 },
  height: { ideal: 480 },
  frameRate: { ideal: 10 },
};

function attachStream(video: HTMLVideoElement | null, stream: MediaStream | null) {
  if (!video) return;
  video.srcObject = stream;
  if (stream) {
    const onReady = () => {
      void video.play().catch(() => undefined);
    };
    video.onloadedmetadata = onReady;
    if (video.readyState >= 1) onReady();
  }
}

const CameraPanel = ({ embedded = false, onCameraStateChange }: CameraPanelProps) => {
  const voice = useLiveKit();
  const videoRef = useRef<HTMLVideoElement>(null);
  const captureVideoRef = useRef<HTMLVideoElement>(null);
  const fallbackStreamRef = useRef<MediaStream | null>(null);

  const [isCameraOn, setIsCameraOn] = useState(false);
  const [shareWithAgent, setShareWithAgent] = useState(false);
  const [fallbackActive, setFallbackActive] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const liveKitReady = voice.roomState === 'connected' && !voice.voiceUnavailable;

  const primaryVideo = () => captureVideoRef.current ?? videoRef.current;

  const stopFallbackStream = useCallback(() => {
    fallbackStreamRef.current?.getTracks().forEach(track => track.stop());
    fallbackStreamRef.current = null;
    setFallbackActive(false);
    attachStream(videoRef.current, null);
    attachStream(captureVideoRef.current, null);
  }, []);

  const startFallbackStream = useCallback(async () => {
    try {
      const mediaStream = await navigator.mediaDevices.getUserMedia({
        video: FALLBACK_VIDEO,
      });
      fallbackStreamRef.current = mediaStream;
      setFallbackActive(true);
      setError(null);
      attachStream(captureVideoRef.current, mediaStream);
      attachStream(videoRef.current, mediaStream);
    } catch (err) {
      setError(err instanceof Error ? err.name : 'Camera unavailable');
      setFallbackActive(false);
      setShareWithAgent(false);
    }
  }, []);

  const stopCamera = useCallback(async () => {
    await voice.publishCamera(false, primaryVideo());
    stopFallbackStream();
    setIsCameraOn(false);
    setShareWithAgent(false);
    setError(null);
  }, [stopFallbackStream, voice]);

  useEffect(() => {
    if (!isCameraOn) return undefined;
    let cancelled = false;

    const syncCamera = async () => {
      if (liveKitReady) {
        stopFallbackStream();
        if (cancelled) return;
        const published = await voice.publishCamera(true, primaryVideo());
        if (!published && !cancelled) {
          await startFallbackStream();
        }
        return;
      }
      await voice.publishCamera(false, primaryVideo());
      if (!cancelled && !fallbackStreamRef.current) {
        await startFallbackStream();
      }
    };

    void syncCamera();
    return () => {
      cancelled = true;
    };
  }, [isCameraOn, liveKitReady, startFallbackStream, stopFallbackStream, voice]);

  useEffect(() => {
    if (voice.roomState === 'idle') {
      void stopCamera();
    }
  }, [stopCamera, voice.roomState]);

  useEffect(() => {
    if (!isCameraOn || liveKitReady || !shareWithAgent || !fallbackActive) {
      return undefined;
    }

    const canvas = document.createElement('canvas');
    const context = canvas.getContext('2d');

    const emitFrame = () => {
      const video = captureVideoRef.current ?? videoRef.current;
      if (!video || !context || video.videoWidth <= 0 || video.videoHeight <= 0) return;
      canvas.width = 640;
      canvas.height = 480;
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      const frameData = canvas.toDataURL('image/jpeg', 0.8);
      const socket = getSocket();
      if (socket.connected) {
        socket.emit('vision_frame', { frame: frameData, ts: Date.now() });
      }
    };

    emitFrame();
    const frameInterval = window.setInterval(emitFrame, 2000);

    return () => window.clearInterval(frameInterval);
  }, [fallbackActive, isCameraOn, liveKitReady, shareWithAgent]);

  const badge: CameraBadge = useMemo(() => {
    if (!isCameraOn) return 'off';
    if (liveKitReady) {
      if (voice.linkedElsewhere || (voice.linkedIdentity && voice.linkedIdentity !== voice.identity)) {
        return 'not_linked';
      }
      return 'livekit';
    }
    if (shareWithAgent && fallbackActive) return 'fallback';
    return 'local_preview';
  }, [
    fallbackActive,
    isCameraOn,
    liveKitReady,
    shareWithAgent,
    voice.identity,
    voice.linkedElsewhere,
    voice.linkedIdentity,
  ]);

  const cameraState: CameraState = useMemo(
    () => ({
      enabled: isCameraOn && (liveKitReady || fallbackActive),
      sharing: badge === 'livekit' || badge === 'fallback',
      badge,
    }),
    [badge, fallbackActive, isCameraOn, liveKitReady],
  );

  useEffect(() => {
    onCameraStateChange?.(cameraState);
  }, [cameraState, onCameraStateChange]);

  useEffect(() => {
    return () => {
      void voice.publishCamera(false, primaryVideo());
      stopFallbackStream();
      onCameraStateChange?.(defaultCameraState);
    };
  }, [onCameraStateChange, stopFallbackStream, voice]);

  const previewReady = isCameraOn && (liveKitReady || fallbackActive);

  const toggleCamera = () => {
    if (isCameraOn) {
      void stopCamera();
    } else {
      setIsCameraOn(true);
      setError(null);
    }
  };

  const captureSink = createPortal(
    <video
      ref={captureVideoRef}
      autoPlay
      muted
      playsInline
      className="camera-capture-sink"
      aria-hidden
      tabIndex={-1}
    />,
    document.body,
  );

  const previewBlock = (
    <div className="camera-frame">
      {!isCameraOn ? (
        <div className="camera-empty">
          <CameraOff size={22} />
          <span>Camera off</span>
        </div>
      ) : (
        <>
          <video
            ref={videoRef}
            autoPlay
            muted
            playsInline
            className={`video-mirror h-full w-full object-cover ${previewReady ? '' : 'opacity-0'}`}
          />
          {!previewReady ? (
            <div className="camera-empty absolute inset-0">
              <CameraOff size={22} />
              <span>{error || 'Starting camera…'}</span>
            </div>
          ) : null}
        </>
      )}
      <div className="camera-status">
        <StatusDot
          active={cameraState.enabled}
          tone={cameraState.sharing ? 'danger' : cameraState.enabled ? 'accent' : 'muted'}
        />
        <span data-testid="camera-link-badge">{cameraBadgeLabel(badge)}</span>
      </div>
    </div>
  );

  const shareRow =
    isCameraOn && !liveKitReady ? (
      embedded ? (
        <div className="agents-row">
          <div className="agents-row__label">
            <b>Share with agent</b>
            <small>Vision uses the frames</small>
          </div>
          <Switch
            checked={shareWithAgent}
            onCheckedChange={setShareWithAgent}
            aria-label="Share with agent"
            data-testid="camera-share-switch"
            disabled={!previewReady}
          />
        </div>
      ) : (
        <label className="flex cursor-pointer items-center justify-between gap-3 border-t border-command-border px-4 py-2 text-xs text-command-muted">
          <span>Share with agent</span>
          <input
            type="checkbox"
            data-testid="camera-share-switch"
            checked={shareWithAgent}
            onChange={event => setShareWithAgent(event.target.checked)}
          />
        </label>
      )
    ) : null;

  if (embedded) {
    return (
      <>
        {captureSink}
        {previewBlock}
        <div className="agents-row">
          <div className="agents-row__label">
            <b>Camera</b>
            <small>{isCameraOn ? 'Sends 640×480 at 10 fps while shared' : 'Off'}</small>
          </div>
          <Switch
            checked={isCameraOn}
            onCheckedChange={checked => {
              if (checked) {
                setIsCameraOn(true);
                setError(null);
              } else {
                void stopCamera();
              }
            }}
            aria-label="Camera"
            data-testid="camera-toggle"
          />
        </div>
        {shareRow}
      </>
    );
  }

  return (
    <>
      {captureSink}
      <Panel
        className="camera-panel"
        title="Camera"
        action={
          <button
            type="button"
            className="icon-button h-7 w-7"
            data-testid="camera-toggle"
            onClick={toggleCamera}
            title={isCameraOn ? 'Turn camera off' : 'Turn camera on'}
          >
            <span className="vh">{isCameraOn ? 'Turn camera off' : 'Turn camera on'}</span>
            {isCameraOn ? <CameraOff size={14} aria-hidden /> : <Camera size={14} aria-hidden />}
          </button>
        }
      >
        {previewBlock}
        {shareRow}
      </Panel>
    </>
  );
};

export default CameraPanel;
