import { useState, useEffect, useRef } from 'react';
import Vortex from '@/components/Vortex';
import Transcription from '@/components/Transcription';
import { io } from 'socket.io-client';
import { 
  Shield, 
  Cpu, 
  Activity, 
  Zap, 
  Database, 
  MessageSquare, 
  Settings,
  User
} from 'lucide-react';

interface Message {
  id: string;
  role: 'agent' | 'user';
  text: string;
  timestamp: string;
}

const socket = io('http://localhost:5001');

const Dashboard = () => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [spectralData, setSpectralData] = useState<number[]>([]);

  useEffect(() => {
    socket.on('init_state', (state) => {
      setMessages(state.messages || []);
      setIsSpeaking(state.speaking || false);
    });

    socket.on('status_change', (data) => {
      setIsSpeaking(data.speaking);
    });

    socket.on('new_message', (msg) => {
      setMessages((prev) => [...prev, msg].slice(-50));
    });

    socket.on('frequency_data', (data) => {
      setSpectralData(data);
    });

    return () => {
      socket.off('init_state');
      socket.off('status_change');
      socket.off('new_message');
      socket.off('frequency_data');
    };
  }, []);

  return (
    <div className="flex h-screen w-screen bg-stonix-bg text-white font-mono selection:bg-stonix-primary/30 overflow-hidden">
      
      {/* Sidebar Navigation */}
      <nav className="w-16 border-r border-stonix-border flex flex-col items-center py-6 space-y-8 bg-black/40 backdrop-blur-xl z-10">
        <div className="w-10 h-10 bg-stonix-primary/10 rounded-lg flex items-center justify-center border border-stonix-primary/30 shadow-neon-blue">
          <Cpu className="text-stonix-primary w-6 h-6" />
        </div>
        
        <div className="flex-1 flex flex-col space-y-6">
          <div className="p-2 cursor-pointer hover:bg-stonix-primary/10 rounded-lg transition-colors group relative">
            <Activity className="text-stonix-primary/60 group-hover:text-stonix-primary w-6 h-6" />
            <span className="absolute left-20 bg-stonix-primary text-black text-[10px] px-2 py-1 rounded opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap">Intelligence</span>
          </div>
          <div className="p-2 cursor-pointer hover:bg-stonix-primary/10 rounded-lg transition-colors group relative">
            <Database className="text-stonix-primary/60 group-hover:text-stonix-primary w-6 h-6" />
            <span className="absolute left-20 bg-stonix-primary text-black text-[10px] px-2 py-1 rounded opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap">Archives</span>
          </div>
          <div className="p-2 cursor-pointer hover:bg-stonix-primary/10 rounded-lg transition-colors group relative">
            <MessageSquare className="text-stonix-primary/60 group-hover:text-stonix-primary w-6 h-6" />
            <span className="absolute left-20 bg-stonix-primary text-black text-[10px] px-2 py-1 rounded opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap">Communications</span>
          </div>
          <div className="p-2 cursor-pointer hover:bg-stonix-primary/10 rounded-lg transition-colors group relative">
            <Zap className="text-stonix-primary/60 group-hover:text-stonix-primary w-6 h-6" />
            <span className="absolute left-20 bg-stonix-primary text-black text-[10px] px-2 py-1 rounded opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap">Power</span>
          </div>
        </div>

        <div className="space-y-4">
          <Settings className="text-stonix-primary/40 hover:text-stonix-primary w-6 h-6 cursor-pointer" />
          <User className="text-stonix-primary/40 hover:text-stonix-primary w-6 h-6 cursor-pointer" />
        </div>
      </nav>

      {/* Main Content Area */}
      <main className="flex-1 relative flex flex-col">
        
        {/* Header Bar */}
        <header className="h-14 border-b border-stonix-border flex items-center justify-between px-6 bg-black/20 backdrop-blur-md">
          <div className="flex items-center space-x-4">
            <span className="text-stonix-primary font-orbitron text-sm tracking-widest font-bold">STONIX // CORE</span>
            <div className="h-4 w-[1px] bg-stonix-border" />
            <span className="text-[10px] text-stonix-primary/60">SECTOR: 01 // NEURAL: STABLE</span>
          </div>
          
          <div className="flex items-center space-x-6">
            <div className="flex space-x-4 text-[10px] text-white/40">
              <span className="flex items-center"><Shield className="w-3 h-3 mr-1 text-green-500" /> SECURE</span>
              <span className="flex items-center"><Zap className="w-3 h-3 mr-1 text-yellow-500" /> OPTIMAL</span>
            </div>
            <div className="w-24 h-2 bg-stonix-primary/10 rounded-full border border-stonix-border overflow-hidden">
               <div className="w-2/3 h-full bg-stonix-primary shadow-neon-blue animate-pulse" />
            </div>
          </div>
        </header>

        {/* Primary View */}
        <div className="flex-1 flex overflow-hidden p-6 gap-6 relative">
          
          {/* Background Grid/Decorative elements */}
          <div className="absolute inset-0 opacity-[0.03] pointer-events-none" 
            style={{ 
              backgroundImage: 'radial-gradient(circle, #00f2ff 1px, transparent 1px)',
              backgroundSize: '40px 40px'
            }} 
          />

          {/* Center Hub (Vortex) */}
          <section className="flex-[1.5] flex flex-col gap-6">
            <div className="flex-1 bg-black/40 border border-stonix-border rounded-xl relative overflow-hidden group shadow-inner">
               <Vortex data={spectralData} isSpeaking={isSpeaking} />
               {/* Decorative corner accents */}
               <div className="absolute top-0 left-0 w-8 h-8 border-l-2 border-t-2 border-stonix-primary/40 m-2 group-hover:border-stonix-primary transition-colors" />
               <div className="absolute bottom-0 right-0 w-8 h-8 border-r-2 border-b-2 border-stonix-primary/40 m-2 group-hover:border-stonix-primary transition-colors" />
            </div>
            
            <div className="h-32 bg-black/40 border border-stonix-border rounded-xl flex items-center px-6 gap-6">
              <div className="w-16 h-16 rounded-full border border-stonix-primary/20 flex items-center justify-center bg-stonix-primary/5">
                <Activity className="text-stonix-primary w-8 h-8 animate-pulse" />
              </div>
              <div className="flex-1">
                <div className="text-[10px] text-stonix-primary/50 mb-2 uppercase tracking-widest">Neural Frequency Spectrum</div>
                <div className="h-8 flex items-end gap-[2px]">
                   {[...Array(40)].map((_, i) => (
                     <div 
                       key={i} 
                       className="w-1 bg-stonix-primary/40 hover:bg-stonix-primary transition-all cursor-pointer" 
                       style={{ height: `${Math.random() * 100}%` }}
                     />
                   ))}
                </div>
              </div>
            </div>
          </section>

          {/* Right Panel (Transcription & Widgets) */}
          <section className="flex-1 flex flex-col gap-6">
            <div className="flex-1">
              <Transcription messages={messages} />
            </div>
            <div className="h-48 bg-stonix-primary/5 border border-stonix-border rounded-xl p-4 flex flex-col glass">
               <span className="text-[10px] text-stonix-primary/60 font-orbitron uppercase mb-3">Environmental Intelligence</span>
               <div className="grid grid-cols-2 gap-4">
                  <div className="px-3 py-2 border border-stonix-border/30 rounded bg-black/20">
                     <div className="text-[9px] text-gray-400">TEMPERATURE</div>
                     <div className="text-lg text-stonix-primary">24.5°C</div>
                  </div>
                  <div className="px-3 py-2 border border-stonix-border/30 rounded bg-black/20">
                     <div className="text-[9px] text-gray-400">SYSTEM LOAD</div>
                     <div className="text-lg text-stonix-secondary">32%</div>
                  </div>
               </div>
               <div className="mt-4 flex-1 flex items-center justify-center opacity-30 text-[10px] border-t border-stonix-border/20">
                  SCANNING FOR ANOMALIES...
               </div>
            </div>
          </section>

        </div>

      </main>

    </div>
  );
};

export default Dashboard;
