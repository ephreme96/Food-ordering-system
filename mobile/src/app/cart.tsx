import { Ionicons } from '@expo/vector-icons';
import { router } from 'expo-router';
import { useMemo } from 'react';
import { FlatList, Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Button } from '@/components/Button';
import { Colors, Radii, Spacing, Type } from '@/constants/theme';
import { formatETB } from '@/lib/format';
import { useCartStore, type CartEntry } from '@/store/cart';

export default function CartScreen() {
  // Select the stable entries object, then derive the array. Returning
  // Object.values() straight from the selector allocates a new array on every
  // store read, which zustand compares by reference -> infinite render loop.
  const entriesMap = useCartStore((s) => s.entries);
  const entries = useMemo(() => Object.values(entriesMap), [entriesMap]);
  const totalAmount = useCartStore((s) => s.totalAmount());
  const changeQtyByKey = useCartStore((s) => s.changeQtyByKey);
  const pointsPreview = Math.floor(totalAmount / 10);

  return (
    <SafeAreaView style={styles.screen}>
      <View style={styles.header}>
        <View>
          <Text style={styles.title}>Your Cart</Text>
          {pointsPreview > 0 && (
            <Text style={styles.subtitle}>Earn ~{pointsPreview} pts with this order</Text>
          )}
        </View>
        <Pressable style={styles.closeBtn} onPress={() => router.back()}>
          <Ionicons name="close" size={20} color={Colors.ink} />
        </Pressable>
      </View>

      <FlatList
        data={entries}
        keyExtractor={(e) => e.cartKey}
        contentContainerStyle={{ padding: Spacing.lg, gap: Spacing.md }}
        ListEmptyComponent={
          <View style={styles.empty}>
            <Ionicons name="cart-outline" size={40} color={Colors.border} />
            <Text style={styles.emptyText}>Your cart is empty</Text>
          </View>
        }
        renderItem={({ item }) => (
          <CartRow entry={item} onChangeQty={(delta) => changeQtyByKey(item.cartKey, delta)} />
        )}
      />

      <View style={styles.footer}>
        <View style={styles.totalRow}>
          <Text style={styles.totalLabel}>Total</Text>
          <Text style={styles.totalValue}>{formatETB(totalAmount)}</Text>
        </View>
        <Button
          label="Proceed to Checkout"
          disabled={entries.length === 0}
          onPress={() => router.push('/checkout')}
        />
      </View>
    </SafeAreaView>
  );
}

function CartRow({ entry, onChangeQty }: { entry: CartEntry; onChangeQty: (delta: number) => void }) {
  const { item, qty, adjustedPrice, customizations, removals, specialInstructions } = entry;
  return (
    <View style={styles.row}>
      <View style={styles.rowInfo}>
        <Text style={styles.rowName} numberOfLines={1}>{item.name}</Text>
        {customizations.length > 0 && (
          <Text style={styles.rowMeta} numberOfLines={2}>
            {customizations.map((c) => c.name).join(' · ')}
          </Text>
        )}
        {removals.length > 0 && (
          <Text style={styles.rowMetaRemoval}>No: {removals.map((r) => r.name).join(', ')}</Text>
        )}
        {!!specialInstructions && <Text style={styles.rowMetaNote}>"{specialInstructions}"</Text>}
        <Text style={styles.rowPrice}>{formatETB(adjustedPrice * qty)}</Text>
      </View>
      <View style={styles.qtyStepper}>
        <Pressable style={styles.qtyBtnOutline} onPress={() => onChangeQty(-1)}>
          <Text style={styles.qtyBtnOutlineText}>−</Text>
        </Pressable>
        <Text style={styles.qtyValue}>{qty}</Text>
        <Pressable style={styles.qtyBtnFilled} onPress={() => onChangeQty(1)}>
          <Text style={styles.qtyBtnFilledText}>+</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: Colors.warm },
  header: {
    flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between',
    paddingHorizontal: Spacing.lg, paddingTop: Spacing.md,
  },
  title: { ...Type.heading, color: Colors.ink },
  subtitle: { color: Colors.muted, fontSize: 12, marginTop: 2 },
  closeBtn: {
    width: 32, height: 32, borderRadius: 16, backgroundColor: Colors.border,
    alignItems: 'center', justifyContent: 'center',
  },
  empty: { alignItems: 'center', justifyContent: 'center', paddingTop: 80, gap: Spacing.sm },
  emptyText: { color: Colors.muted, fontSize: 14 },
  row: {
    flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between',
    backgroundColor: Colors.surface, borderRadius: Radii.md, padding: Spacing.md,
    borderWidth: 1, borderColor: Colors.border,
  },
  rowInfo: { flex: 1, paddingRight: Spacing.md },
  rowName: { fontSize: 14.5, fontWeight: '700', color: Colors.ink },
  rowMeta: { fontSize: 12, color: Colors.muted, marginTop: 3 },
  rowMetaRemoval: { fontSize: 12, color: Colors.danger, marginTop: 3 },
  rowMetaNote: { fontSize: 12, color: Colors.muted, fontStyle: 'italic', marginTop: 3 },
  rowPrice: { fontSize: 12.5, color: Colors.muted, fontWeight: '600', marginTop: 6 },
  qtyStepper: { flexDirection: 'row', alignItems: 'center', gap: Spacing.sm },
  qtyBtnOutline: {
    width: 28, height: 28, borderRadius: 14, borderWidth: 1, borderColor: Colors.border,
    alignItems: 'center', justifyContent: 'center',
  },
  qtyBtnOutlineText: { color: Colors.muted, fontSize: 16, fontWeight: '700' },
  qtyValue: { fontSize: 14, fontWeight: '800', color: Colors.ink, width: 18, textAlign: 'center' },
  qtyBtnFilled: {
    width: 28, height: 28, borderRadius: 14, backgroundColor: Colors.brand,
    alignItems: 'center', justifyContent: 'center',
  },
  qtyBtnFilledText: { color: '#fff', fontSize: 16, fontWeight: '700' },
  footer: {
    borderTopWidth: 1, borderTopColor: Colors.border, backgroundColor: Colors.surface,
    padding: Spacing.lg, paddingBottom: Spacing.xl, gap: Spacing.md,
  },
  totalRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline' },
  totalLabel: { ...Type.heading, color: Colors.ink },
  totalValue: { fontSize: 20, fontWeight: '800', color: Colors.ink },
});
