export const commandTheme = {
  colors: {
    background: '#05070A',
    panel: '#0B1116',
    panelSoft: '#101820',
    border: 'rgba(255,255,255,0.07)',
    text: '#E6EDF3',
    muted: '#7D8896',
    accent: '#24E0A4',
    accentBlue: '#38BDF8',
    danger: '#EF4444',
    warning: '#F59E0B',
  },
  motion: {
    standard: [0.16, 1, 0.3, 1] as const,
  },
};

export const personaAccent = (persona: 'jarvis' | 'anna') =>
  persona === 'jarvis'
    ? {
        text: 'text-command-accent',
        bg: 'bg-command-accent',
        border: 'border-command-accent/30',
        shadow: 'shadow-[0_0_24px_rgba(36,224,164,0.16)]',
        hex: commandTheme.colors.accent,
        soft: 'bg-command-accent/10',
      }
    : {
        text: 'text-command-blue',
        bg: 'bg-command-blue',
        border: 'border-command-blue/30',
        shadow: 'shadow-[0_0_24px_rgba(56,189,248,0.16)]',
        hex: commandTheme.colors.accentBlue,
        soft: 'bg-command-blue/10',
      };
