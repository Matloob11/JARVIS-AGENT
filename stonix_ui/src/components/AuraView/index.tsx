import { useEffect, useRef, useState } from 'react';
import { useNeuralNetwork, getSocket } from '../../hooks/useNeuralNetwork';
import { Scan, AlertCircle } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import MapComponent from './MapComponent';

const AuraView = () => {
  const { activePersona, location } = useNeuralNetwork();
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [error, setError] = useState<string | null>(null);

  const isAnna = activePersona === 'anna';
  const accentClass = isAnna ? 'text-anna-magenta' : 'text-jarvis-cyan';
  const shadowNeonClass = isAnna ? 'shadow-neon-magenta' : 'shadow-neon-cyan';

  useEffect(() => {
    let currentStream: MediaStream | null = null;

    async function startCamera() {
      try {
        const mediaStream = await navigator.mediaDevices.getUserMedia({ 
          video: { 
            width: { ideal: 1280 },
            height: { ideal: 720 },
            facingMode: "user"
          } 
        });
        currentStream = mediaStream;
        setStream(mediaStream);
        if (videoRef.current) {
          videoRef.current.srcObject = mediaStream;
        }
      } catch (err) {
        console.error("Camera access error:", err);
        setError("VISION_OFFLINE: ACCESS_DENIED");
      }
    }

    startCamera();

    return () => {
      if (currentStream) {
        currentStream.getTracks().forEach(track => track.stop());
      }
    };
  }, []);

  useEffect(() => {
    if (!stream) return;

    const interval = setInterval(() => {
      if (videoRef.current && canvasRef.current) {
        const video = videoRef.current;
        const canvas = canvasRef.current;
        const context = canvas.getContext('2d');
        if (context && video.videoWidth > 0) {
          canvas.width = 640; 
          canvas.height = 360;
          context.drawImage(video, 0, 0, canvas.width, canvas.height);
          const frame = canvas.toDataURL('image/jpeg', 0.5);
          getSocket().emit('vision_frame', frame);
          console.debug("AuraView: Vision frame emitted");
        }
      }
    }, 5000); 

    return () => clearInterval(interval);
  }, [stream]);

  return (
    <div className="flex flex-col gap-4">
      <div className="relative aspect-video rounded-3xl overflow-hidden glass-card group bg-black">
         {/* Camera Feed */}
         <AnimatePresence>
            {!stream && !error && (
               <motion.div 
                 exit={{ opacity: 0 }}
                 className="absolute inset-0 bg-black/80 flex flex-col items-center justify-center z-10"
               >
                  <Scan size={32} className={`${accentClass}/40 animate-pulse mb-2`} />
                  <span className={`text-[8px] font-mono ${accentClass}/40 tracking-[0.3em] animate-pulse uppercase`}>Initializing Vision...</span>
               </motion.div>
            )}
            {error && (
               <motion.div 
                 initial={{ opacity: 0 }}
                 animate={{ opacity: 1 }}
                 className="absolute inset-0 bg-red-950/20 flex flex-col items-center justify-center z-10 border border-red-500/20"
               >
                  <AlertCircle size={32} className="text-red-500/40 mb-2" />
                  <span className="text-[8px] font-mono text-red-500/60 tracking-[0.2em]">{error}</span>
               </motion.div>
            )}
         </AnimatePresence>

         <video
            ref={videoRef}
            autoPlay
            playsInline
            muted
            className="w-full h-full object-cover opacity-60 grayscale-[0.2] contrast-[1.2]"
         />
         <canvas ref={canvasRef} className="hidden" />
         
         <div className="absolute inset-0 pointer-events-none">
            {/* HUD Labels */}
            <div className="absolute top-4 left-4 flex flex-col gap-1 drop-shadow-lg">
               <span className={`text-[10px] font-black tracking-widest ${accentClass} ${shadowNeonClass} uppercase`}>Aura View</span>
               <div className="flex items-center gap-2">
                  <span className="inline-block w-1 h-1 rounded-full bg-red-500 animate-pulse" />
                  <span className="text-[8px] font-mono text-white/60 uppercase">Vision Feed Live</span>
               </div>
            </div>
         </div>
      </div>

      {/* Map Synchronization View */}
      <div className="aspect-square rounded-3xl overflow-hidden glass-card relative bg-white/[0.02]">
         {/* Functional Map Component */}
         <div className="absolute inset-0">
            <MapComponent 
               center={[location.lat, location.lng]} 
               zoom={13}
            />
         </div>
         
         {/* Overlay Grid/Visual Polish */}
         <div className="absolute inset-0 opacity-10 pointer-events-none">
            <div className="w-full h-full border-white/10 border-dashed border-[0.5px]" style={{ backgroundSize: '40px 40px', backgroundImage: 'radial-gradient(circle, rgba(255,255,255,0.1) 1px, transparent 1px)' }} />
         </div>

         <div className="absolute top-4 right-4 text-[9px] font-mono text-white/40 uppercase tracking-[0.2em] bg-black/40 px-2 py-1 rounded-full backdrop-blur-md border border-white/10">
            Map Sync: {location.city !== 'Detecting...' ? 'Online' : 'Active'}
         </div>
         
         <div className="absolute bottom-4 left-4 flex flex-col gap-0.5 bg-black/60 p-3 rounded-2xl backdrop-blur-lg border border-white/10 shadow-2xl">
            <span className={`text-[8px] font-black tracking-widest uppercase ${accentClass} ${shadowNeonClass}`}>{location.city} // Position</span>
            <span className="text-[10px] font-mono text-white/80 tracking-tighter">
               {location.lat.toFixed(4)}° {location.lat >= 0 ? 'N' : 'S'}, {location.lng.toFixed(4)}° {location.lng >= 0 ? 'E' : 'W'}
            </span>
         </div>
      </div>
    </div>
  );
};

export default AuraView;
