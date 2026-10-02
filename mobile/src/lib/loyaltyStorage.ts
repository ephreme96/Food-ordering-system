// The backend has no endpoint to list a customer's past reward claims (only
// POST /api/loyalty/rewards/{id}/claim, which returns a claim code once). So claimed
// rewards are cached locally, keyed by the signed-in identifier, so the customer can
// still see & re-show their QR codes on later app opens.
import AsyncStorage from '@react-native-async-storage/async-storage';

const IDENTIFIER_KEY = 'loyalty:identifier';
const DISPLAY_NAME_KEY = 'loyalty:displayName';

export interface StoredClaim {
  claim_code: string;
  reward_name: string;
  points_spent: number;
  claimed_at: string;
}

const claimsKey = (identifier: string) => `loyalty:claims:${identifier}`;

export async function getStoredIdentifier(): Promise<{ identifier: string; displayName: string | null } | null> {
  const identifier = await AsyncStorage.getItem(IDENTIFIER_KEY);
  if (!identifier) return null;
  const displayName = await AsyncStorage.getItem(DISPLAY_NAME_KEY);
  return { identifier, displayName };
}

export async function setStoredIdentifier(identifier: string, displayName: string | null) {
  await AsyncStorage.setItem(IDENTIFIER_KEY, identifier);
  if (displayName) await AsyncStorage.setItem(DISPLAY_NAME_KEY, displayName);
}

export async function clearStoredIdentifier() {
  await AsyncStorage.multiRemove([IDENTIFIER_KEY, DISPLAY_NAME_KEY]);
}

export async function getStoredClaims(identifier: string): Promise<StoredClaim[]> {
  const raw = await AsyncStorage.getItem(claimsKey(identifier));
  return raw ? JSON.parse(raw) : [];
}

export async function addStoredClaim(identifier: string, claim: StoredClaim) {
  const existing = await getStoredClaims(identifier);
  await AsyncStorage.setItem(claimsKey(identifier), JSON.stringify([claim, ...existing]));
}
