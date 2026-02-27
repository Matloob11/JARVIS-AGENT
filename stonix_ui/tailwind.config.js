export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        stonix: {
          bg: '#050505',
          primary: '#00f2ff',
          secondary: '#7000ff',
          accent: '#ff0055',
          glass: 'rgba(15, 23, 42, 0.6)',
          border: 'rgba(0, 242, 255, 0.2)',
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
