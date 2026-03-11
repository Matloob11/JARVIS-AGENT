import { motion } from 'framer-motion';
import { Plus, Database, History, User, FileText } from 'lucide-react';

const NodeMatrix = () => {
  const nodes = [
    { id: 'add', label: 'ADD FILES', sub: 'EXTERNAL INPUT', icon: <Plus size={16} />, color: 'cyan' },
    { id: 'memory', label: 'MEMORY', sub: 'NEURAL INDEX', icon: <Database size={16} />, color: 'red' },
    { id: 'history', label: 'HISTORY', sub: 'EVENT LOGS', icon: <History size={16} />, color: 'blue' },
    { id: 'user', label: 'USER', sub: 'BIOMETRIC DATA', icon: <User size={16} />, color: 'white' }
  ];

  return (
    <div className="flex items-center gap-12 p-8">
      {/* Import Control */}
      <div className="flex flex-col gap-4">
        <div className="glass-card flex flex-col items-center p-6 rounded-2xl w-24">
           <button className="w-12 h-12 rounded-xl bg-jarvis-cyan/10 border border-jarvis-cyan/30 flex items-center justify-center text-jarvis-cyan shadow-neon-cyan mb-4 hover:scale-110 transition-transform">
              <Plus size={24} />
           </button>
           <span className="text-[10px] font-bold text-jarvis-cyan tracking-widest uppercase opacity-60">Import</span>
        </div>
        
        <div className="glass-card flex flex-col gap-4 p-4 rounded-2xl opacity-50">
           <div className="w-12 h-12 bg-white/5 rounded-lg flex items-center justify-center text-white/40">
              <FileText size={20} />
           </div>
           <div className="w-12 h-12 bg-white/5 rounded-lg flex items-center justify-center text-white/40">
              <FileText size={20} />
           </div>
        </div>
      </div>

      {/* Connection Lines (Static for now) */}
      <div className="flex flex-col gap-8 flex-1">
        {nodes.map((node, i) => (
          <motion.div 
            key={node.id}
            initial={{ opacity: 0, x: -20 }}
            whileInView={{ opacity: 1, x: 0 }}
            whileHover={{ scale: 1.02, x: 5 }}
            transition={{ delay: i * 0.1 }}
            className="flex items-center gap-6 group cursor-pointer"
          >
            <div className={`w-40 glass-card p-3 rounded-xl flex items-center gap-4 group-hover:border-white/20 transition-all`}>
               <div className={`p-2 rounded-lg bg-white/5 text-${node.color === 'white' ? 'white' : node.color + '-400'}`}>
                  {node.icon}
               </div>
               <div>
                  <p className="text-[10px] font-black tracking-widest text-white/90">{node.label}</p>
                  <p className="text-[8px] font-mono text-white/30 uppercase">{node.sub}</p>
               </div>
            </div>
            
            {/* Visual Line */}
            <div className="h-px bg-gradient-to-r from-white/10 to-jarvis-cyan/40 flex-1 relative overflow-hidden">
               <motion.div 
                 animate={{ x: ['100%', '-100%'] }}
                 transition={{ duration: 2, repeat: Infinity, ease: 'linear' }}
                 className="absolute inset-0 bg-gradient-to-r from-transparent via-jarvis-cyan/60 to-transparent"
               />
            </div>
          </motion.div>
        ))}
      </div>

      {/* Convergence Point */}
      <div className="w-px h-64 bg-gradient-to-b from-transparent via-jarvis-cyan/40 to-transparent relative">
         <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-2 h-2 rounded-full bg-jarvis-cyan shadow-neon-cyan" />
      </div>
    </div>
  );
};

export default NodeMatrix;
