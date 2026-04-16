import React, { useEffect } from 'react';
import { MapPin, Compass } from 'lucide-react';
import { motion } from 'framer-motion';
import { MapContainer, TileLayer, Marker, useMap } from 'react-leaflet';
import L from 'leaflet';
import { LocationData, useNeuralNetwork } from '@/hooks/useNeuralNetwork';

// Fix for default marker icon in Leaflet + Webpack/Vite
import markerIcon from 'leaflet/dist/images/marker-icon.png';
import markerShadow from 'leaflet/dist/images/marker-shadow.png';

const DefaultIcon = L.icon({
  iconUrl: markerIcon,
  shadowUrl: markerShadow,
  iconSize: [25, 41],
  iconAnchor: [12, 41],
});

L.Marker.prototype.options.icon = DefaultIcon;

interface LocationBoxProps {
  location: LocationData;
}

// Sub-component to handle map re-centering
const MapController: React.FC<{ center: [number, number] }> = ({ center }) => {
  const map = useMap();
  useEffect(() => {
    map.flyTo(center, 13, { duration: 1.5 });
  }, [center, map]);
  return null;
};

const LocationBox: React.FC<LocationBoxProps> = ({ location }) => {
  const { activePersona } = useNeuralNetwork();
  const isJarvis = activePersona === 'jarvis';
  
  const accentColor = isJarvis ? 'text-[#00f2ff]' : 'text-[#ff8c00]';
  const bgColor = isJarvis ? 'bg-[#00f2ff]' : 'bg-[#ff8c00]';

  const center: [number, number] = [location.lat || 0, location.lng || 0];

  return (
    <div className={`relative overflow-hidden rounded-xl bg-[#050608] border border-white/[0.05] h-full flex flex-col justify-between p-4 transition-all duration-700 shadow-2xl`}>
      {/* Background Interactive Map - Hardware Integrated Feel */}
      <div className="absolute inset-0 opacity-[0.25] grayscale contrast-[1.1] brightness-[0.8] mix-blend-screen pointer-events-none">
        <MapContainer 
          center={center} 
          zoom={13} 
          scrollWheelZoom={false} 
          zoomControl={false}
          attributionControl={false}
          className="h-full w-full bg-[#050608]"
        >
          <TileLayer
            url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
          />
          <Marker position={center} />
          <MapController center={center} />
        </MapContainer>
      </div>

      {/* Internal Grid Overlay */}
      <div className="absolute inset-0 opacity-[0.03] pointer-events-none neural-grid scale-[0.5]" />

      <div className="relative z-10 pointer-events-none flex-1">
        <div className="flex items-center justify-between mb-4">
           <div className="flex items-center gap-2.5">
             <div className={`w-1.5 h-1.5 rounded-full ${bgColor} animate-pulse shadow-2xl`} />
             <span className="text-[9px] font-orbitron font-black tracking-[0.3em] text-white/30 uppercase">NAV_REGISTRY</span>
           </div>
           <Compass size={12} className="text-white/10" />
        </div>

        <div className="space-y-4 pt-2">
          <div className="flex flex-col gap-1">
             <div className="flex items-center gap-2">
                <MapPin size={12} className={`${accentColor} opacity-70`} />
                <h4 className="text-[13px] font-bold text-white tracking-wide truncate">
                  {location.city || 'Detecting Area...'}
                </h4>
             </div>
             <p className="text-[9px] font-mono text-white/20 tracking-widest pl-5 uppercase">Target_Point_Alpha</p>
          </div>

          <div className="flex items-center gap-6 pl-5">
             <div className="flex flex-col gap-0.5">
                <span className="text-[8px] font-orbitron text-white/20 uppercase tracking-tighter">Latitude</span>
                <span className="text-[11px] font-mono text-white/80 font-bold">{location.lat.toFixed(4)}°N</span>
             </div>
             <div className="flex flex-col gap-0.5">
                <span className="text-[8px] font-orbitron text-white/20 uppercase tracking-tighter">Longitude</span>
                <span className="text-[11px] font-mono text-white/80 font-bold">{location.lng.toFixed(4)}°E</span>
             </div>
          </div>
        </div>
      </div>
      
      {/* Real-time Tracking Progress Bar */}
      <div className="relative z-10 mt-6 h-1 w-full bg-white/[0.03] rounded-full overflow-hidden border border-white/[0.05]">
        <motion.div 
          animate={{ x: ["-100%", "100%"] }}
          transition={{ duration: 4, repeat: Infinity, ease: "linear" }}
          className={`h-full w-1/3 bg-gradient-to-r from-transparent ${isJarvis ? 'via-[#00f2ff]/40' : 'via-[#ff8c00]/40'} to-transparent`}
        />
      </div>
    </div>
  );
};

export default LocationBox;
