import React, { useEffect, useState } from 'react';
import { MapContainer, TileLayer, Marker, Popup, useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

// Fix for default marker icon issue in Leaflet with Webpack/Vite
import markerIcon2x from 'leaflet/dist/images/marker-icon-2x.png';
import markerIcon from 'leaflet/dist/images/marker-icon.png';
import markerShadow from 'leaflet/dist/images/marker-shadow.png';

const DefaultIcon = L.icon({
  iconUrl: markerIcon,
  iconRetinaUrl: markerIcon2x,
  shadowUrl: markerShadow,
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
  shadowSize: [41, 41]
});

// Remove the global override and use the icon prop instead for better stability
// delete (L.Icon.Default.prototype as any)._getIconUrl;

interface MapComponentProps {
  center?: [number, number];
  zoom?: number;
  markers?: Array<{
    id: string;
    position: [number, number];
    label: string;
  }>;
}

// Helper component to center the map when props change
const RecenterMap = ({ center }: { center: [number, number] }) => {
  const map = useMap();
  useEffect(() => {
    map.setView(center);
  }, [center, map]);
  return null;
};

const MapComponent: React.FC<MapComponentProps> = ({ 
  center = [31.5204, 74.3587], // Default to Lahore
  zoom = 13,
  markers = []
}) => {
  const [isMounted, setIsMounted] = useState(false);

  useEffect(() => {
    setIsMounted(true);
  }, []);

  if (!isMounted) return <div className="map-placeholder">Loading Map...</div>;

  return (
    <div style={{ width: '100%', height: '100%', borderRadius: '12px', overflow: 'hidden', border: '1px solid rgba(255, 255, 255, 0.1)' }}>
      <MapContainer 
        center={center} 
        zoom={zoom} 
        style={{ width: '100%', height: '100%' }}
        zoomControl={false}
      >
        {/* Premium Dark Theme Tiles from CartoDB */}
        <TileLayer
          url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
        />
        
        {/* Recenter helper */}
        <RecenterMap center={center} />

        {/* Render Markers */}
        {markers.map((marker) => (
          <Marker key={marker.id} position={marker.position} icon={DefaultIcon}>
            <Popup>
              <div style={{ color: '#000' }}>
                <strong>{marker.label}</strong>
              </div>
            </Popup>
          </Marker>
        ))}

        {/* Always add current user location marker if center is provided and no markers exist */}
        {markers.length === 0 && (
          <Marker position={center} icon={DefaultIcon}>
            <Popup>
              <div style={{ color: '#000' }}>
                Your Current Location
              </div>
            </Popup>
          </Marker>
        )}
      </MapContainer>
    </div>
  );
};

export default MapComponent;
