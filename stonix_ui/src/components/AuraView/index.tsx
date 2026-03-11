import { useEffect, useRef, useState } from 'react';
import { useNeuralNetwork, getSocket } from '../../hooks/useNeuralNetwork';
import { Map, Scan, AlertCircle } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

const AuraView = () => {
  const { activePersona } = useNeuralNetwork();
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [error, setError] = useState<string | null>(null);

  const isAnna = activePersona === 'anna';
  const accentClass = isAnna ? 'text-anna-magenta' : 'text-jarvis-cyan';
  const bgAccentClass = isAnna ? 'bg-anna-magenta' : 'bg-jarvis-cyan';

  const borderAccentClass = isAnna ? 'border-anna-magenta' : 'border-jarvis-cyan';
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
         
         {/* Visual Intelligence Overlays */}
         <div className="absolute inset-0 pointer-events-none">
            {/* HUD Scanning Line */}
            <motion.div 
               animate={{ top: ['0%', '100%', '0%'] }}
               transition={{ duration: 4, repeat: Infinity, ease: "linear" }}
               className={`absolute left-0 w-full h-[1px] ${bgAccentClass}/30 shadow-[0_0_15px_rgba(0,255,255,0.8)] z-20`}
            />

            {/* Corner Targeting Brackets */}
            <motion.div 
               animate={{ scale: [1, 1.05, 1] }} 
               transition={{ duration: 2, repeat: Infinity }}
               className={`absolute top-4 right-4 w-6 h-6 border-t border-r ${borderAccentClass}/40 rounded-tr-lg`}
            />
            <motion.div 
               animate={{ scale: [1, 1.05, 1] }} 
               transition={{ duration: 2, repeat: Infinity }}
               className={`absolute bottom-4 left-4 w-6 h-6 border-b border-l ${borderAccentClass}/40 rounded-bl-lg`}
            />

            {/* HUD Labels */}
            <div className="absolute top-4 left-4 flex flex-col gap-1 drop-shadow-lg">
               <span className={`text-[10px] font-black tracking-widest ${accentClass} ${shadowNeonClass} uppercase`}>Aura View</span>
               <div className="flex items-center gap-2">
                  <span className="inline-block w-1 h-1 rounded-full bg-red-500 animate-pulse" />
                  <span className="text-[8px] font-mono text-white/60 uppercase">Rec // Cam_01_Input</span>
               </div>
            </div>

            <div className="absolute bottom-4 right-4 hidden group-hover:block transition-opacity">
               <span className={`text-[8px] font-mono ${accentClass}/60 tracking-tighter uppercase`}>Data_Stream: Encrypted</span>
            </div>
         </div>
      </div>

      {/* Map Synchronization View */}
      <div className="aspect-square rounded-3xl overflow-hidden glass-card relative bg-white/[0.02]">
         <div className="absolute inset-0 opacity-20 pointer-events-none">
            <div className="w-full h-full border-white/10 border-dashed border-[0.5px]" style={{ backgroundSize: '20px 20px', backgroundImage: 'radial-gradient(circle, #fff 1px, transparent 1px)' }} />
         </div>
         <div className="absolute inset-0 flex items-center justify-center">
            <Map size={32} className="text-white/10" />
         </div>
         <div className="absolute top-4 right-4 text-[9px] font-mono text-white/20 uppercase tracking-[0.2em]">Map Sync: active</div>
         
         <div className="absolute bottom-4 left-4 flex flex-col gap-0.5">
            <span className="text-[8px] font-black text-white/20 tracking-widest uppercase">Geo-Position</span>
            <span className="text-[10px] font-mono text-white/40 tracking-tighter">40.7128° N, 74.0060° W</span>
         </div>
      </div>
    </div>
  );
};

export default AuraView;
