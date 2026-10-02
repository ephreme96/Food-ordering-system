import * as WebBrowser from 'expo-web-browser';
import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Button } from '@/components/Button';
import { Colors, Radii, Spacing, Type } from '@/constants/theme';
import { getPaymentStatus, verifyChapaPayment, type PaymentMethod } from '@/lib/api';
import { formatETB } from '@/lib/format';

const COUNTDOWN_SECONDS = 30 * 60;
const POLL_INTERVAL_MS = 5000;

const METHOD_COPY: Partial<Record<PaymentMethod, string>> = {
  telebirr: 'Open the Telebirr app on your phone and approve the payment request.',
  cbebirr: 'Open the CBE Birr app or scan the QR code to complete payment.',
  online: 'Complete your payment in the browser, then return here — we\'ll confirm automatically.',
};

export default function PayWaitScreen() {
  const params = useLocalSearchParams<{
    orderNumber: string;
    txRef: string;
    totalAmount: string;
    method: PaymentMethod;
    checkoutUrl?: string;
  }>();
  const { orderNumber, txRef, method } = params;
  const totalAmount = Number(params.totalAmount);

  const [secondsLeft, setSecondsLeft] = useState(COUNTDOWN_SECONDS);
  const [failed, setFailed] = useState(false);
  const browserOpened = useRef(false);

  useEffect(() => {
    let cancelled = false;

    async function openBrowserIfNeeded() {
      if (method === 'online' && params.checkoutUrl && !browserOpened.current) {
        browserOpened.current = true;
        await WebBrowser.openBrowserAsync(params.checkoutUrl);
      }
    }

    async function poll() {
      try {
        if (method === 'online') {
          const result = await verifyChapaPayment(txRef);
          if (result.status === 'paid' || result.status === 'already_paid') {
            if (!cancelled) {
              router.replace({
                pathname: '/confirmation',
                params: {
                  orderNumber,
                  totalAmount: params.totalAmount,
                  method,
                  receiptCode: result.receipt_code ?? '',
                  receiptToken: result.receipt_token ?? '',
                },
              });
            }
            return true;
          }
        } else {
          const result = await getPaymentStatus(orderNumber, txRef);
          if (result.paid) {
            if (!cancelled) {
              router.replace({
                pathname: '/confirmation',
                params: {
                  orderNumber,
                  totalAmount: params.totalAmount,
                  method,
                  receiptCode: result.receipt_code ?? '',
                },
              });
            }
            return true;
          }
          if (result.status === 'FAILED' || result.status === 'CANCELLED') {
            if (!cancelled) setFailed(true);
            return true;
          }
        }
      } catch {
        // transient network error — keep polling
      }
      return false;
    }

    openBrowserIfNeeded();
    const pollTimer = setInterval(async () => {
      const done = await poll();
      if (done) clearInterval(pollTimer);
    }, POLL_INTERVAL_MS);
    poll();

    return () => {
      cancelled = true;
      clearInterval(pollTimer);
    };
  }, [method, orderNumber, txRef, params.checkoutUrl, params.totalAmount]);

  useEffect(() => {
    if (failed) return;
    const countdownTimer = setInterval(() => {
      setSecondsLeft((s) => Math.max(0, s - 1));
    }, 1000);
    return () => clearInterval(countdownTimer);
  }, [failed]);

  const mm = String(Math.floor(secondsLeft / 60)).padStart(2, '0');
  const ss = String(secondsLeft % 60).padStart(2, '0');
  const methodLabel = method === 'telebirr' ? 'Telebirr' : method === 'cbebirr' ? 'CBE Birr' : 'Online Payment';

  if (failed || secondsLeft === 0) {
    return (
      <SafeAreaView style={styles.screen}>
        <View style={styles.card}>
          <Text style={styles.failTitle}>Payment {secondsLeft === 0 ? 'Timed Out' : 'Failed'}</Text>
          <Text style={styles.failSub}>
            {secondsLeft === 0
              ? 'We stopped waiting for confirmation. You can try again.'
              : 'Payment failed. Please try again or choose a different method.'}
          </Text>
          <Button label="Back to Checkout" onPress={() => router.replace('/checkout')} />
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.screen}>
      <View style={styles.card}>
        <View style={styles.spinnerWrap}>
          <ActivityIndicator size="large" color={Colors.brand} />
        </View>
        <Text style={styles.title}>Waiting for {methodLabel}</Text>
        <Text style={styles.sub}>Complete payment of {formatETB(totalAmount)}</Text>

        <View style={styles.countdownBox}>
          <Text style={styles.countdownLabel}>Expires in</Text>
          <Text style={styles.countdownValue}>{mm}:{ss}</Text>
        </View>

        <Text style={styles.instruction}>{METHOD_COPY[method] ?? ''}</Text>

        <Button
          label={`Track Order ${orderNumber}`}
          variant="outline"
          onPress={() => router.push({ pathname: '/track/[orderNumber]', params: { orderNumber } })}
        />
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: Colors.warm, alignItems: 'center', justifyContent: 'center', padding: Spacing.lg },
  card: {
    width: '100%', backgroundColor: Colors.surface, borderRadius: Radii.xl, padding: Spacing.xl,
    alignItems: 'center', gap: Spacing.md,
  },
  spinnerWrap: {
    width: 64, height: 64, borderRadius: 32, backgroundColor: '#fff5f5',
    alignItems: 'center', justifyContent: 'center', marginBottom: Spacing.xs,
  },
  title: { ...Type.heading, color: Colors.ink, textAlign: 'center' },
  sub: { color: Colors.muted, fontSize: 13, textAlign: 'center' },
  countdownBox: {
    width: '100%', backgroundColor: '#f8fafc', borderRadius: Radii.md, borderWidth: 1, borderColor: Colors.border,
    alignItems: 'center', paddingVertical: Spacing.md, marginVertical: Spacing.sm,
  },
  countdownLabel: { fontSize: 11.5, color: Colors.muted, marginBottom: 2 },
  countdownValue: { fontSize: 26, fontWeight: '800', color: Colors.ink, fontVariant: ['tabular-nums'] },
  instruction: { fontSize: 12.5, color: Colors.muted, textAlign: 'center', marginBottom: Spacing.sm },
  failTitle: { ...Type.heading, color: Colors.danger, textAlign: 'center' },
  failSub: { color: Colors.muted, fontSize: 13, textAlign: 'center', marginBottom: Spacing.md },
});
