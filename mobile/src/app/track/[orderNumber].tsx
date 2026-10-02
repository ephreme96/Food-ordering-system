import { Ionicons } from '@expo/vector-icons';
import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Colors, Radii, Spacing, Type } from '@/constants/theme';
import { trackOrder, type PaymentMethod, type TrackedOrder } from '@/lib/api';
import { formatETB } from '@/lib/format';

const STEPS = [
  { label: 'Order Placed', icon: 'document-text' as const },
  { label: 'Payment Confirmed', icon: 'card' as const },
  { label: 'Being Prepared', icon: 'restaurant' as const },
  { label: 'Ready for Pickup', icon: 'notifications' as const },
  { label: 'Collected', icon: 'checkmark-circle' as const },
];

const STATUS_MESSAGES: Record<string, string> = {
  PENDING: 'Waiting for payment confirmation…',
  CASH_PENDING: 'Your order is queued. Please pay at the counter.',
  PAID: 'Payment confirmed! Your food is being prepared.',
  PREPARING: 'Our kitchen is working on your order right now.',
  READY: 'Your order is ready! Come to the counter to pick it up.',
  PICKED_UP: 'Order collected. Enjoy your meal!',
  COMPLETED: 'Order completed. Thank you!',
  CANCELLED: 'This order has been cancelled.',
  FAILED: 'Payment failed. Please try again.',
};

const STATUS_COLORS: Record<string, string> = {
  PENDING: '#ca8a04',
  CASH_PENDING: '#2563eb',
  PAID: '#16a34a',
  PREPARING: '#ea580c',
  READY: '#9333ea',
  PICKED_UP: '#6b7280',
  COMPLETED: '#6b7280',
  CANCELLED: '#dc2626',
  FAILED: '#dc2626',
};

const METHOD_LABELS: Record<PaymentMethod, string> = {
  online: 'Chapa / Online',
  cash: 'Cash',
  telebirr: 'Telebirr',
  cbebirr: 'CBE Birr',
  bank_transfer: 'Bank Transfer',
};

const TERMINAL = new Set(['PICKED_UP', 'COMPLETED', 'CANCELLED', 'FAILED']);

const wsUrl = (orderNumber: string) => {
  const base = process.env.EXPO_PUBLIC_API_BASE_URL ?? 'http://localhost:8000';
  return base.replace(/^http/, 'ws') + `/ws/track/${encodeURIComponent(orderNumber)}`;
};

export default function TrackOrderScreen() {
  const { orderNumber } = useLocalSearchParams<{ orderNumber: string }>();
  const [order, setOrder] = useState<TrackedOrder | null>(null);
  const [notFound, setNotFound] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    let cancelled = false;

    const fetchOrder = async () => {
      try {
        const data = await trackOrder(orderNumber);
        if (cancelled) return;
        setOrder(data);
        if (TERMINAL.has(data.status) && pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
      } catch {
        if (!cancelled) setNotFound(true);
      }
    };

    fetchOrder();
    pollRef.current = setInterval(fetchOrder, 8000);

    const ws = new WebSocket(wsUrl(orderNumber));
    wsRef.current = ws;
    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data as string);
        if (data.event === 'status_change') fetchOrder();
      } catch {
        // ignore
      }
    };
    ws.onopen = () => {
      const keepAlive = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send('ping');
      }, 30000);
      ws.onclose = () => clearInterval(keepAlive);
    };

    return () => {
      cancelled = true;
      if (pollRef.current) clearInterval(pollRef.current);
      ws.close();
    };
  }, [orderNumber]);

  if (notFound) {
    return (
      <SafeAreaView style={styles.screen}>
        <Header />
        <View style={styles.centered}>
          <Ionicons name="alert-circle" size={40} color={Colors.danger} />
          <Text style={styles.errorTitle}>Order Not Found</Text>
          <Text style={styles.errorSub}>The order number could not be found.</Text>
        </View>
      </SafeAreaView>
    );
  }

  const step = order?.step ?? -2;
  const isError = step === -1;

  return (
    <SafeAreaView style={styles.screen}>
      <Header />
      <View style={styles.body}>
        <Text style={styles.orderLabel}>Order Number</Text>
        <Text style={styles.orderNumber}>{orderNumber}</Text>

        {isError ? (
          <View style={[styles.detailsBox, { alignItems: 'center', gap: Spacing.sm }]}>
            <Ionicons name="close-circle" size={40} color={Colors.danger} />
            <Text style={styles.errorTitle}>
              {order?.status === 'CANCELLED' ? 'Order Cancelled' : 'Payment Failed'}
            </Text>
            <Text style={styles.errorSub}>{STATUS_MESSAGES[order?.status ?? '']}</Text>
          </View>
        ) : (
          <>
            <View style={styles.stepperCard}>
              <View style={styles.stepperRow}>
                {STEPS.map((s, i) => {
                  const done = i < step;
                  const active = i === step;
                  return (
                    <View key={s.label} style={styles.stepItem}>
                      <View style={styles.stepConnectorRow}>
                        {i > 0 && (
                          <View style={[styles.connector, i <= step && styles.connectorDone]} />
                        )}
                      </View>
                      <View
                        style={[
                          styles.stepCircle,
                          done && styles.stepCircleDone,
                          active && styles.stepCircleActive,
                        ]}>
                        <Ionicons
                          name={s.icon}
                          size={16}
                          color={done || active ? '#fff' : Colors.border}
                        />
                      </View>
                      <Text
                        style={[
                          styles.stepLabel,
                          done && { color: Colors.success },
                          active && { color: Colors.brand },
                        ]}>
                        {s.label}
                      </Text>
                    </View>
                  );
                })}
              </View>

              {order && (
                <View style={styles.statusBanner}>
                  <View style={styles.statusBadge}>
                    <View style={[styles.statusDot, { backgroundColor: STATUS_COLORS[order.status] }]} />
                    <Text style={[styles.statusBadgeText, { color: STATUS_COLORS[order.status] }]}>
                      {order.status.replace(/_/g, ' ')}
                    </Text>
                  </View>
                  <Text style={styles.statusMessage}>{STATUS_MESSAGES[order.status]}</Text>
                </View>
              )}
            </View>

            {order && (
              <View style={styles.detailsBox}>
                <Text style={styles.detailsTitle}>Order Summary</Text>
                <Row label="Customer" value={order.customer_name} />
                <Row label="Items" value={`${order.items_count} item${order.items_count !== 1 ? 's' : ''}`} />
                <Row label="Payment" value={METHOD_LABELS[order.payment_method]} />
                <View style={styles.detailsDivider} />
                <Row label="Total" value={formatETB(order.total_amount)} bold />
              </View>
            )}

            <Text style={styles.liveHint}>
              {order && TERMINAL.has(order.status) ? '' : 'Updates automatically — no need to refresh'}
            </Text>
          </>
        )}
      </View>
    </SafeAreaView>
  );
}

function Header() {
  return (
    <View style={styles.header}>
      <Pressable onPress={() => (router.canGoBack() ? router.back() : router.replace('/(tabs)'))}>
        <Ionicons name="chevron-back" size={24} color={Colors.ink} />
      </Pressable>
      <Text style={styles.headerTitle}>Track Order</Text>
      <View style={{ width: 24 }} />
    </View>
  );
}

function Row({ label, value, bold }: { label: string; value: string; bold?: boolean }) {
  return (
    <View style={styles.row}>
      <Text style={styles.rowLabel}>{label}</Text>
      <Text style={[styles.rowValue, bold && { fontWeight: '800', fontSize: 15 }]}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: Colors.warm },
  header: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: Spacing.lg, paddingTop: Spacing.sm, paddingBottom: Spacing.sm,
    borderBottomWidth: 1, borderBottomColor: Colors.border,
  },
  headerTitle: { fontSize: 16, fontWeight: '700', color: Colors.ink },
  body: { padding: Spacing.lg, gap: Spacing.lg },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: Spacing.sm },
  orderLabel: { textAlign: 'center', color: Colors.muted, fontSize: 12 },
  orderNumber: { textAlign: 'center', fontSize: 26, fontWeight: '800', color: Colors.ink, letterSpacing: 1 },
  stepperCard: {
    backgroundColor: Colors.surface, borderRadius: Radii.lg, borderWidth: 1, borderColor: Colors.border,
    padding: Spacing.lg,
  },
  stepperRow: { flexDirection: 'row', alignItems: 'flex-start' },
  stepItem: { flex: 1, alignItems: 'center' },
  stepConnectorRow: { position: 'absolute', top: 17, left: '-50%', right: '50%', height: 2 },
  connector: { flex: 1, height: 2, backgroundColor: Colors.border },
  connectorDone: { backgroundColor: Colors.success },
  stepCircle: {
    width: 34, height: 34, borderRadius: 17, borderWidth: 2, borderColor: Colors.border,
    backgroundColor: Colors.surface, alignItems: 'center', justifyContent: 'center', marginBottom: 6,
  },
  stepCircleDone: { backgroundColor: Colors.success, borderColor: Colors.success },
  stepCircleActive: { backgroundColor: Colors.brand, borderColor: Colors.brand },
  stepLabel: { fontSize: 10, fontWeight: '600', color: Colors.muted, textAlign: 'center' },
  statusBanner: { alignItems: 'center', marginTop: Spacing.lg, gap: 6 },
  statusBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: Spacing.md, paddingVertical: 6,
    borderRadius: Radii.pill, backgroundColor: '#f3f4f6',
  },
  statusDot: { width: 7, height: 7, borderRadius: 4 },
  statusBadgeText: { fontSize: 12.5, fontWeight: '700' },
  statusMessage: { color: Colors.muted, fontSize: 13, textAlign: 'center' },
  detailsBox: {
    backgroundColor: Colors.surface, borderRadius: Radii.lg, borderWidth: 1, borderColor: Colors.border,
    padding: Spacing.lg, gap: 8,
  },
  detailsTitle: { fontSize: 13, fontWeight: '700', color: Colors.ink, marginBottom: 4 },
  detailsDivider: { height: 1, backgroundColor: Colors.border, marginVertical: 4 },
  row: { flexDirection: 'row', justifyContent: 'space-between' },
  rowLabel: { color: Colors.muted, fontSize: 13 },
  rowValue: { color: Colors.ink, fontSize: 13, fontWeight: '600' },
  liveHint: { textAlign: 'center', color: Colors.muted, fontSize: 11 },
  errorTitle: { fontSize: 16, fontWeight: '800', color: Colors.danger },
  errorSub: { color: Colors.muted, fontSize: 13, textAlign: 'center' },
});
