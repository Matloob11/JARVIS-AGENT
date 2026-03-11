export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        jarvis: {
          cyan: '#00f2ff',
        },
        anna: {
          magenta: '#ff00ff',
        },
        accent: {
          purple: '#7000ff',
        },
        bg: {
          deep: '#020205',
        }
      },
      boxShadow: {
        'neon-blue': '0 0 10px rgba(0, 242, 255, 0.5), 0 0 20px rgba(0, 242, 255, 0.2)',
        'neon-purple': '0 0 10px rgba(112, 0, 255, 0.5), 0 0 20px rgba(112, 0, 255, 0.2)',
      },
      fontFamily: {
        mono: ['Space Mono', 'Roboto Mono', 'monospace'],
        orbitron: ['Orbitron', 'sans-serif'],
      },
      animation: {
        'pulse-slow': 'pulse 4s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'spin-slow': 'spin 8s linear infinite',
      }
    },
  },
  plugins: [],
};
