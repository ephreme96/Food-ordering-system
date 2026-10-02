import { Ionicons } from '@expo/vector-icons';
import { router, useLocalSearchParams } from 'expo-router';
import QRCode from 'react-native-qrcode-svg';
import { StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Button } from '@/components/Button';
import { Colors, Radii, Spacing, Type } from '@/constants/theme';
import type { PaymentMethod } from '@/lib/api';
import { formatETB } from '@/lib/format';

const CASH_LIKE: PaymentMethod[] = ['cash', 'bank_transfer'];

export default function ConfirmationScreen() {
  const params = useLocalSearchParams<{
    orderNumber: string;
    totalAmount: string;
    method: PaymentMethod;
    receiptCode?: string;
    receiptToken?: string;
  }>();
  const totalAmount = Number(params.totalAmount);
  const isPaid = !CASH_LIKE.includes(params.method) && !!params.receiptToken;
  const isBank = params.method === 'bank_transfer';

  return (
    <SafeAreaView style={styles.screen}>
      <View style={styles.card}>
        <View style={[styles.iconWrap, { backgroundColor: Colors.successBg }]}>
          <Ionicons name="checkmark" size={32} color={Colors.success} />
        </View>

        <Text style={styles.title}>
          {isPaid ? 'Payment Confirmed!' : isBank ? 'Transfer Claim Received' : 'Order Placed!'}
        </Text>
        <Text style={styles.subtitle}>
          {isPaid
            ? 'Your food is being prepared.'
            : isBank
              ? 'A cashier will verify your transfer and confirm your order shortly.'
              : 'Please pay at the counter when you pick up your order.'}
        </Text>

        <View style={styles.detailsBox}>
          <Row label="Order" value={params.orderNumber} mono />
          <Row label="Total" value={formatETB(totalAmount)} bold />
        </View>

        {isPaid && params.receiptToken && (
          <View style={styles.qrSection}>
            <View style={styles.qrWrap}>
              <QRCode value={params.receiptToken} size={150} color={Colors.ink} backgroundColor="#fff" />
            </View>
            <Text style={styles.receiptLabel}>Receipt Code</Text>
            <Text style={styles.receiptCode}>{params.receiptCode}</Text>
            <Text style={styles.receiptHint}>Show this QR code at the counter to collect your order.</Text>
          </View>
        )}

        <Button
          label="Track My Order"
          onPress={() =>
            router.replace({ pathname: '/track/[orderNumber]', params: { orderNumber: params.orderNumber } })
          }
        />
        <Button label="Back to Menu" variant="outline" onPress={() => router.replace('/(tabs)')} />
      </View>
    </SafeAreaView>
  );
}

function Row({ label, value, mono, bold }: { label: string; value: string; mono?: boolean; bold?: boolean }) {
  return (
    <View style={styles.row}>
      <Text style={styles.rowLabel}>{label}</Text>
      <Text style={[styles.rowValue, mono && { fontFamily: 'monospace' }, bold && { fontWeight: '800' }]}>
        {value}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: Colors.warm, alignItems: 'center', justifyContent: 'center', padding: Spacing.lg },
  card: {
    width: '100%', backgroundColor: Colors.surface, borderRadius: Radii.xl, padding: Spacing.xl,
    alignItems: 'center', gap: Spacing.md,
  },
  iconWrap: { width: 64, height: 64, borderRadius: 32, alignItems: 'center', justifyContent: 'center' },
  title: { ...Type.heading, fontSize: 22, color: Colors.ink, textAlign: 'center' },
  subtitle: { color: Colors.muted, fontSize: 13, textAlign: 'center' },
  detailsBox: {
    width: '100%', backgroundColor: '#f9f7f4', borderRadius: Radii.md, borderWidth: 1, borderColor: Colors.border,
    padding: Spacing.md, gap: 6,
  },
  row: { flexDirection: 'row', justifyContent: 'space-between' },
  rowLabel: { color: Colors.muted, fontSize: 13 },
  rowValue: { color: Colors.ink, fontSize: 13, fontWeight: '700' },
  qrSection: { alignItems: 'center', gap: 4, paddingVertical: Spacing.sm },
  qrWrap: { padding: Spacing.md, backgroundColor: '#fff', borderRadius: Radii.md, borderWidth: 1, borderColor: Colors.border },
  receiptLabel: { color: Colors.muted, fontSize: 11.5, marginTop: Spacing.sm },
  receiptCode: { color: Colors.ink, fontSize: 20, fontWeight: '800', letterSpacing: 2, fontFamily: 'monospace' },
  receiptHint: { color: Colors.muted, fontSize: 11.5, textAlign: 'center', marginTop: 2 },
});
