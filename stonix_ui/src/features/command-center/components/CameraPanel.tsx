import { useEffect, useRef, useState } from 'react';
import { Camera, CameraOff } from 'lucide-react';
import { getSocket } from '../hooks/useCommandCenter';
import { Panel, StatusDot } from '@/shared/ui/Panel';

interface CameraPanelProps {
  isMuted: boolean;
}

const CameraPanel = ({ isMuted }: CameraPanelProps) => {
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [hasCamera, setHasCamera] = useState(false);
  const [isCameraOff, setIsCameraOff] = useState(false);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const stopStream = () => {
      streamRef.current?.getTracks().forEach(track => track.stop());
      streamRef.current = null;
      setStream(null);
      setHasCamera(false);
    };

    const setupStream = async () => {
      try {
        const mediaStream = await navigator.mediaDevices.getUserMedia({
          video: {
            width: { ideal: 1280 },
            height: { ideal: 720 },
            frameRate: { ideal: 24 },
          },
        });
        streamRef.current = mediaStream;
        setStream(mediaStream);
        setHasCamera(true);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.name : 'Camera unavailable');
        setHasCamera(false);
      }
    };

    if (isCameraOff) {
      stopStream();
      return undefined;
    }

    setupStream();

    return stopStream;
  }, [isCameraOff]);

  useEffect(() => {
    if (!hasCamera || !stream || !videoRef.current) return;
    videoRef.current.srcObject = stream;
    videoRef.current.onloadedmetadata = () => {
      videoRef.current?.play().catch(() => undefined);
    };
  }, [hasCamera, stream]);

  useEffect(() => {
    if (!hasCamera || isMuted || isCameraOff || !videoRef.current) return undefined;

    const canvas = document.createElement('canvas');
    const context = canvas.getContext('2d');
    const frameInterval = window.setInterval(() => {
      if (!videoRef.current || !context || videoRef.current.readyState < 2) return;
      canvas.width = 640;
      canvas.height = 480;
      context.drawImage(videoRef.current, 0, 0, canvas.width, canvas.height);
      const frameData = canvas.toDataURL('image/jpeg', 0.8);
      const socket = getSocket();
      if (socket.connected) {
        socket.emit('vision_frame', { frame: frameData });
      }
    }, 2000);

    return () => window.clearInterval(frameInterval);
  }, [hasCamera, isCameraOff, isMuted]);

  return (
    <Panel
      className="camera-panel"
      title="Camera"
      action={
        <button
          className="icon-button h-7 w-7"
          onClick={() => setIsCameraOff(value => !value)}
          title={isCameraOff ? 'Turn camera on' : 'Turn camera off'}
        >
          {isCameraOff ? <Camera size={14} /> : <CameraOff size={14} />}
        </button>
      }
    >
      <div className="camera-frame">
        {isCameraOff ? (
          <div className="camera-empty">
            <CameraOff size={22} />
            <span>Camera paused</span>
          </div>
        ) : hasCamera ? (
          <video ref={videoRef} autoPlay muted playsInline className="video-mirror h-full w-full object-cover" />
        ) : (
          <div className="camera-empty">
            <CameraOff size={22} />
            <span>{error || 'Requesting camera'}</span>
          </div>
        )}
        <div className="camera-status">
          <StatusDot active={hasCamera && !isCameraOff} tone={hasCamera ? 'danger' : 'muted'} />
          {hasCamera && !isCameraOff ? 'Live' : 'Offline'}
        </div>
      </div>
    </Panel>
  );
};

export default CameraPanel;
