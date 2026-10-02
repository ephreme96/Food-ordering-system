import { Ionicons } from '@expo/vector-icons';
import { router } from 'expo-router';
import { useMemo, useState } from 'react';
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
  checkPromo,
  createOrder,
  getBankInfo,
  initChapaPayment,
  initiateMobilePayment,
  type PaymentMethod,
  type PromoCheckResult,
} from '@/lib/api';
import { formatETB } from '@/lib/format';
import { useCartStore } from '@/store/cart';

const PAY_METHODS: Array<{ id: PaymentMethod; label: string; sub: string; icon: keyof typeof Ionicons.glyphMap }> = [
  { id: 'telebirr', label: 'Telebirr', sub: 'Ethio Telecom', icon: 'phone-portrait' },
  { id: 'cbebirr', label: 'CBE Birr', sub: 'Commercial Bank', icon: 'card' },
  { id: 'cash', label: 'Cash', sub: 'Pay at counter', icon: 'cash' },
  { id: 'bank_transfer', label: 'Bank Transfer', sub: 'Manual transfer', icon: 'business' },
  { id: 'online', label: 'Online Payment', sub: 'Chapa gateway (card / other)', icon: 'globe' },
];

export default function CheckoutScreen() {
  // Select the stable entries object, then derive the array. Returning
  // Object.values() straight from the selector allocates a new array on every
  // store read, which zustand compares by reference -> infinite render loop.
  const entriesMap = useCartStore((s) => s.entries);
  const entries = useMemo(() => Object.values(entriesMap), [entriesMap]);
  const totalAmount = useCartStore((s) => s.totalAmount());
  const clearCart = useCartStore((s) => s.clear);

  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [email, setEmail] = useState('');
  const [notes, setNotes] = useState('');
  const [method, setMethod] = useState<PaymentMethod>('telebirr');
  const [promoCode, setPromoCode] = useState('');
  const [promo, setPromo] = useState<PromoCheckResult | null>(null);
  const [promoError, setPromoError] = useState<string | null>(null);
  const [promoLoading, setPromoLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const finalTotal = promo?.new_total ?? totalAmount;
  const pointsEarn = Math.floor(finalTotal / 10);

  const applyPromo = async () => {
    if (!promoCode.trim()) return;
    setPromoLoading(true);
    setPromoError(null);
    try {
      const result = await checkPromo(promoCode.trim().toUpperCase(), totalAmount);
      setPromo(result);
    } catch (e) {
      setPromo(null);
      setPromoError(e instanceof ApiError ? e.message : 'Could not apply promo code');
    } finally {
      setPromoLoading(false);
    }
  };

  const validate = (): string | null => {
    if (!name.trim()) return 'Please enter your name';
    if ((method === 'telebirr' || method === 'cbebirr') && !phone.trim()) {
      return 'Phone number is required for mobile payment';
    }
    if (method === 'online' && !email.trim()) return 'Email is required for online payment';
    return null;
  };

  const handlePlaceOrder = async () => {
    const validationError = validate();
    if (validationError) {
      setError(validationError);
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const order = await createOrder({
        customer_name: name.trim(),
        customer_phone: phone.trim() || null,
        customer_email: email.trim() || null,
        notes: notes.trim() || null,
        payment_method: method,
        promo_code: promo?.code ?? null,
        items: entries.map((e) => ({
          menu_item_id: e.item.id,
          quantity: e.qty,
          customizations: e.customizations.map((c) => c.id),
          removals: e.removals.map((r) => r.id),
          special_instructions: e.specialInstructions,
        })),
      });

      if (method === 'cash') {
        clearCart();
        router.replace({
          pathname: '/confirmation',
          params: { orderNumber: order.order_number, totalAmount: String(order.total_amount), method },
        });
        return;
      }

      if (method === 'bank_transfer') {
        const bankInfo = await getBankInfo();
        clearCart();
        router.replace({
          pathname: '/bank-transfer',
          params: {
            orderNumber: order.order_number,
            txRef: order.tx_ref ?? '',
            totalAmount: String(order.total_amount),
            bankInfo: JSON.stringify(bankInfo),
          },
        });
        return;
      }

      if (method === 'telebirr' || method === 'cbebirr') {
        const payData = await initiateMobilePayment({
          order_number: order.order_number,
          tx_ref: order.tx_ref ?? '',
          method,
          customer_phone: phone.trim(),
        });
        clearCart();
        if (payData.status === 'sandbox_paid') {
          router.replace({
            pathname: '/confirmation',
            params: {
              orderNumber: order.order_number,
              totalAmount: String(order.total_amount),
              method,
              receiptCode: payData.receipt_code ?? '',
            },
          });
          return;
        }
        router.replace({
          pathname: '/pay-wait',
          params: {
            orderNumber: order.order_number,
            txRef: order.tx_ref ?? '',
            totalAmount: String(order.total_amount),
            method,
          },
        });
        return;
      }

      // online (Chapa)
      const payment = await initChapaPayment(order.id, order.tx_ref ?? '');
      clearCart();
      router.replace({
        pathname: '/pay-wait',
        params: {
          orderNumber: order.order_number,
          txRef: order.tx_ref ?? '',
          totalAmount: String(order.total_amount),
          method,
          checkoutUrl: payment.checkout_url,
        },
      });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Something went wrong. Please try again.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <SafeAreaView style={styles.screen}>
      <View style={styles.header}>
        <Text style={styles.title}>Checkout</Text>
        <Pressable style={styles.closeBtn} onPress={() => router.back()}>
          <Ionicons name="close" size={20} color={Colors.ink} />
        </Pressable>
      </View>

      <ScrollView contentContainerStyle={{ padding: Spacing.lg, gap: Spacing.lg, paddingBottom: 40 }}>
        <View style={styles.summaryCard}>
          <Text style={styles.summaryLabel}>Order Summary</Text>
          {entries.map((e) => (
            <View key={e.cartKey} style={styles.summaryRow}>
              <Text style={styles.summaryItem} numberOfLines={1}>
                {e.item.name} × {e.qty}
              </Text>
              <Text style={styles.summaryItemPrice}>{formatETB(e.adjustedPrice * e.qty)}</Text>
            </View>
          ))}
          {promo && (
            <View style={styles.summaryRow}>
              <Text style={[styles.summaryItem, { color: Colors.success }]}>Promo ({promo.code})</Text>
              <Text style={[styles.summaryItemPrice, { color: Colors.success }]}>
                -{formatETB(promo.discount_etb)}
              </Text>
            </View>
          )}
          <View style={styles.summaryDivider} />
          <View style={styles.summaryRow}>
            <Text style={styles.summaryTotalLabel}>Total</Text>
            <Text style={styles.summaryTotalValue}>{formatETB(finalTotal)}</Text>
          </View>
        </View>

        <View style={styles.fieldGroup}>
          <Field label="Full Name *" value={name} onChangeText={setName} placeholder="Abebe Girma" />
          <Field
            label="Phone Number"
            value={phone}
            onChangeText={setPhone}
            placeholder="+251 9xx xxx xxx"
            keyboardType="phone-pad"
          />
          <Field
            label="Email (required for online pay)"
            value={email}
            onChangeText={setEmail}
            placeholder="abebe@example.com"
            keyboardType="email-address"
            autoCapitalize="none"
          />
          <Field
            label="Special Instructions"
            value={notes}
            onChangeText={setNotes}
            placeholder="Any dietary requirements or notes…"
            multiline
          />
        </View>

        <View>
          <Text style={styles.sectionTitle}>Payment Method</Text>
          <View style={styles.payGrid}>
            {PAY_METHODS.map((pm) => {
              const active = method === pm.id;
              return (
                <Pressable
                  key={pm.id}
                  style={[styles.payTile, active && styles.payTileActive]}
                  onPress={() => setMethod(pm.id)}>
                  <View style={[styles.payIconWrap, active && { backgroundColor: Colors.brand }]}>
                    <Ionicons name={pm.icon} size={16} color={active ? '#fff' : Colors.muted} />
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.payTileLabel}>{pm.label}</Text>
                    <Text style={styles.payTileSub}>{pm.sub}</Text>
                  </View>
                </Pressable>
              );
            })}
          </View>
        </View>

        <View>
          <Text style={styles.sectionTitle}>Promo Code</Text>
          <View style={styles.promoRow}>
            <TextInput
              style={styles.promoInput}
              placeholder="DISCOUNT20"
              autoCapitalize="characters"
              value={promoCode}
              onChangeText={setPromoCode}
            />
            <Pressable style={styles.promoBtn} onPress={applyPromo} disabled={promoLoading}>
              {promoLoading ? (
                <ActivityIndicator color="#fff" size="small" />
              ) : (
                <Text style={styles.promoBtnText}>Apply</Text>
              )}
            </Pressable>
          </View>
          {promo && <Text style={styles.promoSuccess}>{promo.description} applied</Text>}
          {promoError && <Text style={styles.promoErrorText}>{promoError}</Text>}
        </View>

        {error && (
          <View style={styles.errorBox}>
            <Text style={styles.errorText}>{error}</Text>
          </View>
        )}

        {pointsEarn > 0 && <Text style={styles.pointsHint}>You'll earn ~{pointsEarn} loyalty points</Text>}

        <Button label="Place Order" onPress={handlePlaceOrder} loading={submitting} />
      </ScrollView>
    </SafeAreaView>
  );
}

function Field(props: {
  label: string;
  value: string;
  onChangeText: (v: string) => void;
  placeholder: string;
  keyboardType?: 'default' | 'phone-pad' | 'email-address';
  autoCapitalize?: 'none' | 'sentences';
  multiline?: boolean;
}) {
  return (
    <View>
      <Text style={styles.fieldLabel}>{props.label}</Text>
      <TextInput
        style={[styles.input, props.multiline && styles.inputMultiline]}
        value={props.value}
        onChangeText={props.onChangeText}
        placeholder={props.placeholder}
        placeholderTextColor={Colors.muted}
        keyboardType={props.keyboardType}
        autoCapitalize={props.autoCapitalize}
        multiline={props.multiline}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: Colors.warm },
  header: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: Spacing.lg, paddingTop: Spacing.md,
  },
  title: { ...Type.heading, color: Colors.ink },
  closeBtn: {
    width: 32, height: 32, borderRadius: 16, backgroundColor: Colors.border,
    alignItems: 'center', justifyContent: 'center',
  },
  summaryCard: {
    backgroundColor: Colors.surface, borderRadius: Radii.md, padding: Spacing.md,
    borderWidth: 1, borderColor: Colors.border, gap: 6,
  },
  summaryLabel: { fontSize: 11, fontWeight: '700', color: Colors.muted, textTransform: 'uppercase', marginBottom: 4 },
  summaryRow: { flexDirection: 'row', justifyContent: 'space-between' },
  summaryItem: { fontSize: 13, color: Colors.ink, flex: 1, marginRight: Spacing.sm },
  summaryItemPrice: { fontSize: 13, fontWeight: '600', color: Colors.ink },
  summaryDivider: { height: 1, backgroundColor: Colors.border, marginVertical: 4 },
  summaryTotalLabel: { fontSize: 14, fontWeight: '800', color: Colors.ink },
  summaryTotalValue: { fontSize: 14, fontWeight: '800', color: Colors.ink },
  fieldGroup: { gap: Spacing.md },
  fieldLabel: { fontSize: 13, fontWeight: '600', color: Colors.ink, marginBottom: 6 },
  input: {
    borderWidth: 1, borderColor: Colors.border, backgroundColor: Colors.surface,
    borderRadius: Radii.md, paddingHorizontal: Spacing.md, paddingVertical: 12, fontSize: 14, color: Colors.ink,
  },
  inputMultiline: { minHeight: 64, textAlignVertical: 'top' },
  sectionTitle: { fontSize: 14, fontWeight: '700', color: Colors.ink, marginBottom: Spacing.sm },
  payGrid: { gap: Spacing.sm },
  payTile: {
    flexDirection: 'row', alignItems: 'center', gap: Spacing.sm,
    borderWidth: 1.5, borderColor: Colors.border, borderRadius: Radii.md,
    padding: Spacing.md, backgroundColor: Colors.surface,
  },
  payTileActive: { borderColor: Colors.brand, backgroundColor: '#fff5f5' },
  payIconWrap: {
    width: 32, height: 32, borderRadius: Radii.sm, backgroundColor: Colors.border,
    alignItems: 'center', justifyContent: 'center',
  },
  payTileLabel: { fontSize: 13.5, fontWeight: '700', color: Colors.ink },
  payTileSub: { fontSize: 11.5, color: Colors.muted },
  promoRow: { flexDirection: 'row', gap: Spacing.sm },
  promoInput: {
    flex: 1, borderWidth: 1, borderColor: Colors.border, backgroundColor: Colors.surface,
    borderRadius: Radii.md, paddingHorizontal: Spacing.md, fontSize: 14, fontWeight: '600', color: Colors.ink,
  },
  promoBtn: {
    backgroundColor: '#7c3aed', borderRadius: Radii.md, paddingHorizontal: Spacing.lg,
    alignItems: 'center', justifyContent: 'center', minWidth: 76,
  },
  promoBtnText: { color: '#fff', fontWeight: '700', fontSize: 13.5 },
  promoSuccess: { color: Colors.success, fontSize: 12.5, marginTop: 6, fontWeight: '600' },
  promoErrorText: { color: Colors.danger, fontSize: 12.5, marginTop: 6 },
  errorBox: { backgroundColor: Colors.dangerBg, borderRadius: Radii.md, padding: Spacing.md },
  errorText: { color: Colors.danger, fontSize: 13, fontWeight: '600' },
  pointsHint: { textAlign: 'center', color: Colors.muted, fontSize: 12 },
});
