import React, { useEffect } from 'react';
import { MapPin, Globe } from 'lucide-react';
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
  const themeClass = activePersona === 'jarvis' ? 'text-jarvis-cyan' : 'text-anna-magenta';
  const themeBgClass = activePersona === 'jarvis' ? 'bg-jarvis-cyan' : 'bg-anna-magenta';
  const themeBorderClass = activePersona === 'jarvis' ? 'border-jarvis-cyan' : 'border-anna-magenta';

  const center: [number, number] = [location.lat || 0, location.lng || 0];

  return (
    <div className="glass-panel relative overflow-hidden rounded-xl p-3 flex flex-col justify-between min-h-[160px] transition-colors duration-500">
      {/* Real Interactive Map */}
      <div className="absolute inset-0 opacity-40 grayscale contrast-[1.2] brightness-[0.7] hover:opacity-70 transition-opacity duration-700">
        <MapContainer 
          center={center} 
          zoom={13} 
          scrollWheelZoom={false} 
          zoomControl={false}
          attributionControl={false}
          className="h-full w-full bg-transparent"
        >
          <TileLayer
            url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
          />
          <Marker position={center} />
          <MapController center={center} />
        </MapContainer>
      </div>

      <div className="relative z-10 pointer-events-none">
        <div className="flex items-center gap-2 mb-3">
          <div className={`p-1 rounded-md ${themeBgClass}/10 border ${themeBorderClass}/20 backdrop-blur-md`}>
            <Globe size={10} className={themeClass} />
          </div>
          <span className="text-[9px] font-black tracking-[0.2em] text-white/50 uppercase drop-shadow-lg">Geo-Spatial Hub</span>
        </div>

        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <MapPin size={12} className={`${themeClass} flex-shrink-0 drop-shadow-md`} />
            <p className="text-[14px] font-bold text-white tracking-wide truncate drop-shadow-md">
              {location.city || 'Detecting Area...'}
            </p>
          </div>
          <div className="flex items-center gap-2 pl-5">
            <div className={`h-1 w-1 rounded-full ${themeBgClass} animate-pulse`} />
            <p className="text-[10px] font-mono text-white/60 tracking-tighter drop-shadow-md">
              {location.lat.toFixed(4)}°N / {location.lng.toFixed(4)}°E
            </p>
          </div>
        </div>
      </div>
      
      {/* Real-time Sync Progress */}
      <div className="relative z-10 mt-2 h-0.5 w-full bg-white/5 rounded-full overflow-hidden">
        <motion.div 
          initial={{ width: 0 }}
          animate={{ width: "100%" }}
          transition={{ duration: 3, repeat: Infinity, ease: "linear" }}
          className={`h-full bg-gradient-to-r from-transparent via-${activePersona === 'jarvis' ? 'cyan-400' : 'magenta-400'} to-transparent opacity-80`}
        />
      </div>
    </div>
  );
};

export default LocationBox;
