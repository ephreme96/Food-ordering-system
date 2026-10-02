import { Ionicons } from '@expo/vector-icons';
import { useCallback, useEffect, useState } from 'react';
import QRCode from 'react-native-qrcode-svg';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Button } from '@/components/Button';
import { Colors, Radii, Spacing, Type } from '@/constants/theme';
import {
  ApiError,
  claimReward,
  getLoyaltyBalance,
  getRewards,
  loyaltyJoin,
  type LoyaltyBalance,
  type RewardItem,
} from '@/lib/api';
import {
  addStoredClaim,
  clearStoredIdentifier,
  getStoredClaims,
  getStoredIdentifier,
  setStoredIdentifier,
  type StoredClaim,
} from '@/lib/loyaltyStorage';

export default function RewardsTab() {
  const [loading, setLoading] = useState(true);
  const [identifier, setIdentifier] = useState<string | null>(null);
  const [displayName, setDisplayName] = useState<string | null>(null);
  const [balance, setBalance] = useState<LoyaltyBalance | null>(null);
  const [rewards, setRewards] = useState<RewardItem[]>([]);
  const [claims, setClaims] = useState<StoredClaim[]>([]);
  const [claimingId, setClaimingId] = useState<number | null>(null);

  const refresh = useCallback(async (ident: string) => {
    const [bal, rew, storedClaims] = await Promise.all([
      getLoyaltyBalance(ident),
      getRewards(ident),
      getStoredClaims(ident),
    ]);
    setBalance(bal);
    setRewards(rew);
    setClaims(storedClaims);
  }, []);

  useEffect(() => {
    (async () => {
      const stored = await getStoredIdentifier();
      if (stored) {
        setIdentifier(stored.identifier);
        setDisplayName(stored.displayName);
        await refresh(stored.identifier);
      }
      setLoading(false);
    })();
  }, [refresh]);

  const handleClaim = async (reward: RewardItem) => {
    if (!identifier) return;
    setClaimingId(reward.id);
    try {
      const result = await claimReward(reward.id, identifier, displayName);
      await addStoredClaim(identifier, {
        claim_code: result.claim_code,
        reward_name: result.reward_name,
        points_spent: result.points_spent,
        claimed_at: new Date().toISOString(),
      });
      await refresh(identifier);
    } catch (e) {
      // surfaced inline via disabled state / can_claim already prevents most failures
    } finally {
      setClaimingId(null);
    }
  };

  if (loading) {
    return (
      <SafeAreaView style={styles.screen}>
        <View style={styles.centered}>
          <ActivityIndicator color={Colors.brand} />
        </View>
      </SafeAreaView>
    );
  }

  if (!identifier) {
    return (
      <SignInForm
        onSignedIn={async (ident, name) => {
          setIdentifier(ident);
          setDisplayName(name);
          await refresh(ident);
        }}
      />
    );
  }

  return (
    <SafeAreaView style={styles.screen} edges={['top']}>
      <ScrollView contentContainerStyle={{ padding: Spacing.lg, gap: Spacing.lg, paddingBottom: 60 }}>
        <View style={styles.headerRow}>
          <View>
            <Text style={styles.title}>Rewards</Text>
            <Text style={styles.subtitle}>{displayName || identifier}</Text>
          </View>
          <Pressable
            onPress={async () => {
              await clearStoredIdentifier();
              setIdentifier(null);
              setBalance(null);
              setRewards([]);
              setClaims([]);
            }}>
            <Text style={styles.signOut}>Sign out</Text>
          </Pressable>
        </View>

        <View style={styles.balanceCard}>
          <Text style={styles.balanceValue}>{balance?.points_balance ?? 0}</Text>
          <Text style={styles.balanceLabel}>points</Text>
          <Text style={styles.balanceStats}>
            {balance?.total_earned ?? 0} earned · {balance?.total_redeemed ?? 0} redeemed
          </Text>
          {!!balance?.next_reward_pts && balance.next_reward_pts > 0 && (
            <Text style={styles.nextRewardHint}>{balance.next_reward_pts} more points to your next reward</Text>
          )}
        </View>

        <View>
          <Text style={styles.sectionTitle}>Available Rewards</Text>
          {rewards.length === 0 ? (
            <Text style={styles.emptyText}>No rewards available right now.</Text>
          ) : (
            <View style={{ gap: Spacing.sm }}>
              {rewards.map((r) => (
                <View key={r.id} style={styles.rewardRow}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.rewardName}>{r.name}</Text>
                    {!!r.description && <Text style={styles.rewardDesc}>{r.description}</Text>}
                    <Text style={styles.rewardPoints}>{r.points_required} pts</Text>
                  </View>
                  <Button
                    label="Claim"
                    variant={r.can_claim ? 'secondary' : 'outline'}
                    disabled={!r.can_claim}
                    loading={claimingId === r.id}
                    onPress={() => handleClaim(r)}
                    style={{ height: 38, paddingHorizontal: Spacing.md, minWidth: 84 }}
                  />
                </View>
              ))}
            </View>
          )}
        </View>

        <View>
          <View style={styles.headerRow}>
            <Text style={styles.sectionTitle}>My Rewards</Text>
          </View>
          {claims.length === 0 ? (
            <View style={styles.emptyRewardsBox}>
              <Ionicons name="qr-code-outline" size={28} color={Colors.border} />
              <Text style={styles.emptyText}>You have no claimed rewards yet.</Text>
            </View>
          ) : (
            <View style={{ gap: Spacing.md }}>
              {claims.map((c) => (
                <View key={c.claim_code} style={styles.claimCard}>
                  <View style={styles.qrWrap}>
                    <QRCode value={c.claim_code} size={110} color={Colors.ink} backgroundColor="#fff" />
                  </View>
                  <Text style={styles.claimName}>{c.reward_name}</Text>
                  <Text style={styles.claimCode}>{c.claim_code}</Text>
                  <Text style={styles.claimHint}>Show this QR code at the counter to redeem</Text>
                </View>
              ))}
            </View>
          )}
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

function SignInForm({ onSignedIn }: { onSignedIn: (identifier: string, name: string | null) => void }) {
  const [identifier, setIdentifierInput] = useState('');
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const handleSignIn = async () => {
    if (identifier.trim().length < 3) {
      setError('Enter a valid phone number or email');
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await loyaltyJoin(identifier.trim(), name.trim() || null);
      await setStoredIdentifier(identifier.trim(), name.trim() || null);
      onSignedIn(identifier.trim(), name.trim() || null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not sign in. Please try again.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <SafeAreaView style={styles.screen} edges={['top']}>
      <View style={styles.signInBody}>
        <View style={styles.signInIconWrap}>
          <Ionicons name="star" size={28} color={Colors.gold} />
        </View>
        <Text style={styles.title}>Loyalty Rewards</Text>
        <Text style={styles.subtitle}>
          Earn 1 point for every ETB 10 spent. Sign in with your phone or email to track your points and
          claim rewards.
        </Text>

        <View style={{ width: '100%', gap: Spacing.md, marginTop: Spacing.md }}>
          <TextInput
            style={styles.input}
            placeholder="+251 9xx xxx xxx or email@example.com"
            placeholderTextColor={Colors.muted}
            autoCapitalize="none"
            value={identifier}
            onChangeText={setIdentifierInput}
          />
          <TextInput
            style={styles.input}
            placeholder="Your Name (optional)"
            placeholderTextColor={Colors.muted}
            value={name}
            onChangeText={setName}
          />
          {error && <Text style={styles.errorText}>{error}</Text>}
          <Button label="Sign In / Join" variant="secondary" onPress={handleSignIn} loading={submitting} />
        </View>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: Colors.warm },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  signInBody: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: Spacing.xl },
  signInIconWrap: {
    width: 64, height: 64, borderRadius: 32, backgroundColor: '#fef9ec',
    alignItems: 'center', justifyContent: 'center', marginBottom: Spacing.sm,
  },
  title: { ...Type.heading, color: Colors.ink, textAlign: 'center' },
  subtitle: { color: Colors.muted, fontSize: 13, textAlign: 'center', marginTop: 4 },
  input: {
    width: '100%', borderWidth: 1, borderColor: Colors.border, backgroundColor: Colors.surface,
    borderRadius: Radii.md, paddingHorizontal: Spacing.md, paddingVertical: 13, fontSize: 14, color: Colors.ink,
  },
  errorText: { color: Colors.danger, fontSize: 12.5, textAlign: 'center' },
  headerRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  signOut: { color: Colors.muted, fontSize: 12.5, fontWeight: '600' },
  balanceCard: {
    backgroundColor: Colors.dark, borderRadius: Radii.xl, padding: Spacing.xl, alignItems: 'center',
  },
  balanceValue: { color: Colors.gold, fontSize: 40, fontWeight: '800' },
  balanceLabel: { color: 'rgba(255,255,255,0.5)', fontSize: 12, marginTop: -4 },
  balanceStats: { color: 'rgba(255,255,255,0.4)', fontSize: 11.5, marginTop: Spacing.sm },
  nextRewardHint: { color: Colors.goldLight, fontSize: 11.5, marginTop: 6 },
  sectionTitle: { fontSize: 15, fontWeight: '700', color: Colors.ink, marginBottom: Spacing.sm },
  emptyText: { color: Colors.muted, fontSize: 13 },
  rewardRow: {
    flexDirection: 'row', alignItems: 'center', gap: Spacing.md,
    backgroundColor: Colors.surface, borderRadius: Radii.md, borderWidth: 1, borderColor: Colors.border,
    padding: Spacing.md,
  },
  rewardName: { fontSize: 14, fontWeight: '700', color: Colors.ink },
  rewardDesc: { fontSize: 12, color: Colors.muted, marginTop: 2 },
  rewardPoints: { fontSize: 12, color: Colors.gold, fontWeight: '700', marginTop: 4 },
  emptyRewardsBox: {
    alignItems: 'center', gap: Spacing.sm, paddingVertical: Spacing.xl,
    backgroundColor: Colors.surface, borderRadius: Radii.md, borderWidth: 1, borderColor: Colors.border,
  },
  claimCard: {
    alignItems: 'center', backgroundColor: Colors.surface, borderRadius: Radii.lg,
    borderWidth: 1, borderColor: Colors.border, padding: Spacing.lg, gap: 4,
  },
  qrWrap: { padding: Spacing.sm, backgroundColor: '#fff', borderRadius: Radii.sm, marginBottom: Spacing.sm },
  claimName: { fontSize: 14, fontWeight: '700', color: Colors.ink },
  claimCode: { fontSize: 16, fontWeight: '800', color: Colors.gold, letterSpacing: 1, fontFamily: 'monospace' },
  claimHint: { fontSize: 11, color: Colors.muted, textAlign: 'center' },
});
