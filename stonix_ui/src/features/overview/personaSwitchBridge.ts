export type PersonaSwitchBridge = {
  appendChatSystemLine?: (text: string) => void;
  pushPersonaFeedRow?: (persona: 'jarvis' | 'anna', voice: string) => void;
  snapOrbPalette?: () => void;
};

let bridge: PersonaSwitchBridge = {};

export function setPersonaSwitchBridge(next: PersonaSwitchBridge) {
  bridge = next;
}

export function getPersonaSwitchBridge(): PersonaSwitchBridge {
  return bridge;
}
