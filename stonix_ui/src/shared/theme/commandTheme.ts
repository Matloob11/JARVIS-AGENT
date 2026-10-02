export const commandTheme = {
  colors: {
    background: 'var(--bg)',
    panel: 'var(--bubble-ai)',
    panelSoft: 'var(--tile)',
    border: 'var(--divider)',
    text: 'var(--t1)',
    muted: 'var(--t4)',
    accent: 'var(--acc-fill)',
    accentBlue: 'var(--acc-line)',
    danger: 'var(--err)',
    warning: 'var(--warn)',
  },
  motion: {
    standard: [0.16, 1, 0.3, 1] as const,
  },
};

const accentClasses = {
  text: 'text-acc-text',
  bg: 'bg-acc',
  border: 'border-acc-edge-soft',
  shadow: 'shadow-glow-primary',
  hex: 'var(--acc-fill)',
  soft: 'bg-acc-tint',
} as const;

/** Persona follows `html[data-persona]` tokens; classes are shared. */
export const personaAccent = (persona: 'jarvis' | 'anna') => {
  void persona;
  return accentClasses;
};
