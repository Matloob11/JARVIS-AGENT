export function agentHostTitle(brain: string | null | undefined): 'Agent (PC)' | 'Agent (VPS)' {
  return brain === 'pc' ? 'Agent (PC)' : 'Agent (VPS)';
}
