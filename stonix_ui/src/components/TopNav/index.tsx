import { motion } from 'framer-motion';

const TopNav = ({ activeTab, onTabChange }: { activeTab: string, onTabChange: (tab: string) => void }) => {
  const tabs = [
    { id: 'intelligence', label: 'INTELLIGENCE' }
  ];


  return (
    <div className="flex items-center justify-center p-2 gap-2">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          onClick={() => onTabChange(tab.id)}
          className="relative px-6 py-2 group overflow-hidden"
        >
          <span className={`text-[11px] font-black tracking-[0.3em] transition-colors duration-300 ${
            activeTab === tab.id ? 'text-jarvis-cyan' : 'text-white/30 group-hover:text-white/60'
          }`}>
            {tab.label}
          </span>
          
          {activeTab === tab.id && (
            <>
              <motion.div 
                layoutId="activeTab"
                className="absolute inset-0 border-b-2 border-jarvis-cyan shadow-neon-cyan"
              />
              <div className="absolute -bottom-1 left-1/2 -translate-x-1/2 w-1 h-1 bg-jarvis-cyan rounded-full" />
            </>
          )}
        </button>
      ))}
    </div>
  );
};

export default TopNav;
