import React, { useEffect, useRef, useState } from 'react';
import { CameraOff, Camera } from 'lucide-react';
import { useNeuralNetwork, getSocket } from '@/hooks/useNeuralNetwork';

const CameraView: React.FC = () => {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [hasCamera, setHasCamera] = useState(false);
  const [isCameraOff, setIsCameraOff] = useState(false);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [debugMsg, setDebugMsg] = useState('Starting...');
  const { activePersona, isMuted } = useNeuralNetwork();

  const themeClass = activePersona === 'jarvis' ? 'border-jarvis-cyan/40' : 'border-anna-magenta/40';
  const textClass = activePersona === 'jarvis' ? 'text-jarvis-cyan/40' : 'text-anna-magenta/40';

  useEffect(() => {
    const setupStream = async () => {
      try {
        setDebugMsg('Requesting Ultra HD...');
        const mediaStream = await navigator.mediaDevices.getUserMedia({ 
          video: { 
            width: { ideal: 1920 }, 
            height: { ideal: 1080 },
            frameRate: { ideal: 30 }
          } 
        });
        
        setStream(mediaStream);
        if (!isCameraOff) setHasCamera(true);
        setDebugMsg('System Online');
      } catch (err: any) {
        console.error("Camera Error:", err);
        setError(err.name || 'Unknown Error');
        setDebugMsg(`Failed: ${err.name}`);
      }
    };

    if (!isCameraOff) {
      setupStream();
    } else {
      if (stream) {
        stream.getTracks().forEach(track => track.stop());
        setStream(null);
      }
      setHasCamera(false);
    }

    return () => {
      if (stream) {
        stream.getTracks().forEach(track => track.stop());
      }
    };
  }, [isCameraOff]);

  useEffect(() => {
    if (hasCamera && stream && videoRef.current) {
      videoRef.current.srcObject = stream;
      videoRef.current.onloadedmetadata = () => {
        videoRef.current?.play().catch(e => console.error("Auto-play failed:", e));
      };
    }
  }, [hasCamera, stream, isCameraOff]);

  useEffect(() => {
    let frameInterval: ReturnType<typeof setInterval>;
    
    // Only send frames if camera is ON and not muted
    if (hasCamera && !isMuted && !isCameraOff && videoRef.current) {
      const canvas = document.createElement('canvas');
      const context = canvas.getContext('2d');
      
      frameInterval = setInterval(() => {
        if (videoRef.current && context && videoRef.current.readyState >= 2) {
          canvas.width = 640;
          canvas.height = 480;
          context.drawImage(videoRef.current, 0, 0, canvas.width, canvas.height);
          const frameData = canvas.toDataURL('image/jpeg', 0.8);
          
          const socket = getSocket();
          if (socket.connected) {
            socket.emit('vision_frame', { frame: frameData });
          }
        }
      }, 2000);
    }

    return () => {
      if (frameInterval) clearInterval(frameInterval);
    };
  }, [hasCamera, isMuted, isCameraOff]);

  return (
    <div className="glass-panel camera-container relative overflow-hidden rounded-xl bg-black/40 flex-shrink-0 transition-all duration-500 ring-1 ring-white/10">
      {/* Corner Accents */}
      <div className={`absolute top-0 left-0 w-3 h-3 border-t-2 border-l-2 ${themeClass} z-20`} />
      <div className={`absolute top-0 right-0 w-3 h-3 border-t-2 border-r-2 ${themeClass} z-20`} />
      <div className={`absolute bottom-0 left-0 w-3 h-3 border-b-2 border-l-2 ${themeClass} z-20`} />
      <div className={`absolute bottom-0 right-0 w-3 h-3 border-b-2 border-r-2 ${themeClass} z-20`} />

      {isCameraOff ? (
        <div className="w-full h-full relative group">
          <img 
            src="/jarvis-anna-off.png" 
            alt="JARVIS & ANNA" 
            className="w-full h-full object-cover opacity-80 brightness-75 scale-105 transition-transform duration-1000 group-hover:scale-110"
          />
          <div className="absolute inset-0 bg-gradient-to-t from-black/80 to-transparent flex flex-col items-center justify-end pb-8">
            <span className="text-[10px] font-black tracking-[0.5em] text-white/40 uppercase">System Secure</span>
            <span className="text-[8px] font-mono text-jarvis-cyan animate-pulse mt-1">MADE BY MATLOOB</span>
          </div>
        </div>
      ) : hasCamera ? (
        <video
          ref={videoRef}
          autoPlay
          muted
          playsInline
          className="w-full h-full object-cover brightness-105 contrast-105 saturate-110 video-mirror"
        />
      ) : (
        <div className="w-full h-full flex flex-col items-center justify-center gap-2">
          <CameraOff size={24} className={textClass} />
          <div className="text-center">
            <div className="text-[10px] font-black tracking-widest text-white/40 uppercase">System Camera</div>
            <div className={`text-[8px] font-mono ${textClass} mt-1`}>
              {error ? `STATUS: ${error}` : debugMsg}
            </div>
          </div>
        </div>
      )}

      {/* Interface Overlays */}
      <div className="absolute top-4 left-4 flex items-center justify-between w-[calc(100%-32px)] z-30">
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${hasCamera ? 'bg-red-600 animate-pulse shadow-[0_0_10px_rgba(220,38,38,0.8)]' : 'bg-white/10'}`} />
          <span className="text-[10px] font-black text-white uppercase tracking-widest drop-shadow-md">
            {isCameraOff ? 'System Paused' : hasCamera ? 'Ultra HD // Live' : 'Offline'}
          </span>
        </div>
        
        {/* Toggle Button */}
        <button 
          onClick={() => setIsCameraOff(!isCameraOff)}
          className={`p-1.5 rounded-md border backdrop-blur-md transition-all duration-300 ${
            isCameraOff 
            ? 'bg-jarvis-cyan/20 border-jarvis-cyan/50 text-white shadow-[0_0_15px_rgba(0,242,254,0.3)]' 
            : 'bg-black/40 border-white/10 text-white/60 hover:border-white/40 hover:text-white'
          }`}
          title={isCameraOff ? "Turn Camera ON" : "Turn Camera OFF"}
        >
          {isCameraOff ? <Camera size={14} /> : <CameraOff size={14} />}
        </button>
      </div>

      <div className="absolute bottom-4 right-4 z-30 flex flex-col items-end opacity-40">
        <span className="text-[8px] font-mono text-white tracking-tighter">PHASE_SYNC_ACTIVE</span>
        <span className="text-[8px] font-mono text-white tracking-widest uppercase">
          {isCameraOff ? 'Security Protocol 01' : 'Cam_ID: J-01'}
        </span>
      </div>
    </div>
  );
};

export default CameraView;
