export const OWNER = {
  name: 'Matloob',
  initial: 'M',
  label: 'Matloob (owner)',
} as const;

export const QUOTE = '“Ideas become reality, faster.”';

/** Local-time greeting prefix (includes trailing comma and space). */
export function greeting(at: Date = new Date()): string {
  const h = at.getHours();
  if (h < 12) return 'Good morning,';
  if (h < 17) return 'Good afternoon,';
  return 'Good evening,';
}
