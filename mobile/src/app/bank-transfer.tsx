import { router, useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { StyleSheet, Text, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Button } from '@/components/Button';
import { Colors, Radii, Spacing, Type } from '@/constants/theme';
import { ApiError, claimBankTransfer, type BankInfo } from '@/lib/api';
import { formatETB } from '@/lib/format';

export default function BankTransferScreen() {
  const params = useLocalSearchParams<{
    orderNumber: string;
    txRef: string;
    totalAmount: string;
    bankInfo: string;
  }>();
  const bankInfo: BankInfo = JSON.parse(params.bankInfo);
  const totalAmount = Number(params.totalAmount);

  const [bankRef, setBankRef] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const handleClaim = async () => {
    if (!bankRef.trim()) {
      setError('Please enter your bank transaction reference number');
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await claimBankTransfer({
        order_number: params.orderNumber,
        tx_ref: params.txRef,
        bank_ref: bankRef.trim(),
      });
      router.replace({
        pathname: '/confirmation',
        params: { orderNumber: params.orderNumber, totalAmount: params.totalAmount, method: 'bank_transfer' },
      });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Failed to submit transfer claim');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <SafeAreaView style={styles.screen}>
      <View style={styles.card}>
        <Text style={styles.title}>Bank Transfer</Text>

        <View style={styles.detailsBox}>
          <Row label="Bank" value={bankInfo.bank_name} />
          <Row label="Account" value={bankInfo.account_number} mono />
          <Row label="Name" value={bankInfo.account_name} />
          <Row label="Reference" value={params.orderNumber} mono accent />
          <Row label="Amount" value={formatETB(totalAmount)} bold />
          {!!bankInfo.instructions && <Text style={styles.instructions}>{bankInfo.instructions}</Text>}
        </View>

        <Text style={styles.fieldLabel}>Your Bank Reference</Text>
        <TextInput
          style={styles.input}
          placeholder="TXN-12345"
          value={bankRef}
          onChangeText={setBankRef}
        />
        {error && <Text style={styles.errorText}>{error}</Text>}

        <Button label="I've Made the Transfer" variant="secondary" onPress={handleClaim} loading={submitting} />
        <Button label="Cancel" variant="outline" onPress={() => router.replace('/(tabs)')} />
      </View>
    </SafeAreaView>
  );
}

function Row({ label, value, mono, bold, accent }: { label: string; value: string; mono?: boolean; bold?: boolean; accent?: boolean }) {
  return (
    <View style={styles.row}>
      <Text style={styles.rowLabel}>{label}</Text>
      <Text
        style={[
          styles.rowValue,
          mono && { fontFamily: 'monospace' },
          bold && { fontWeight: '800' },
          accent && { color: '#7c3aed' },
        ]}>
        {value}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: Colors.warm, alignItems: 'center', justifyContent: 'center', padding: Spacing.lg },
  card: { width: '100%', backgroundColor: Colors.surface, borderRadius: Radii.xl, padding: Spacing.xl, gap: Spacing.md },
  title: { ...Type.heading, color: Colors.ink },
  detailsBox: {
    backgroundColor: '#f8fafc', borderRadius: Radii.md, borderWidth: 1, borderColor: Colors.border,
    padding: Spacing.md, gap: Spacing.sm,
  },
  row: { flexDirection: 'row', justifyContent: 'space-between' },
  rowLabel: { color: Colors.muted, fontSize: 13 },
  rowValue: { color: Colors.ink, fontSize: 13, fontWeight: '700' },
  instructions: { color: Colors.muted, fontSize: 11.5, marginTop: 4 },
  fieldLabel: { fontSize: 13, fontWeight: '600', color: Colors.ink },
  input: {
    borderWidth: 1, borderColor: Colors.border, backgroundColor: Colors.surface,
    borderRadius: Radii.md, paddingHorizontal: Spacing.md, paddingVertical: 12, fontSize: 14, color: Colors.ink,
    fontFamily: 'monospace',
  },
  errorText: { color: Colors.danger, fontSize: 12.5 },
});
