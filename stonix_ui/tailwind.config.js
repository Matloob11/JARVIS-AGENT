export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        jarvis: {
          cyan: '#00f2ff',
          deep: '#0a0d14',
        },
        anna: {
          amber: '#ffb300',
          orange: '#ff8c00',
        },
        bg: {
          deep: '#050608',
          panel: '#0e1117',
        }
      },
      boxShadow: {
        'hardware': '0 8px 32px rgba(0, 0, 0, 0.4), inset 0 1px 1px rgba(255, 255, 255, 0.05)',
        'neon-cyan': '0 0 15px rgba(0, 242, 255, 0.25), 0 0 30px rgba(0, 242, 255, 0.1)',
        'neon-amber': '0 0 15px rgba(255, 179, 0, 0.25), 0 0 30px rgba(255, 179, 0, 0.1)',
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Space Mono', 'monospace'],
        orbitron: ['Orbitron', 'sans-serif'],
        outfit: ['Outfit', 'sans-serif'],
        rajdhani: ['Rajdhani', 'sans-serif'],
      },
      animation: {
        'pulse-slow': 'pulse 6s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'spin-slow': 'spin 15s linear infinite',
        'scan': 'scan 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      },
    },
  },
  plugins: [],
};
