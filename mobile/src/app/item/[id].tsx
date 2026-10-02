import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { Image } from 'expo-image';
import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Button } from '@/components/Button';
import { Colors, Radii, Spacing, Type } from '@/constants/theme';
import { getMenu, type MenuIngredient, type MenuItem } from '@/lib/api';
import { categoryGradient, categoryIcon } from '@/lib/categoryStyle';
import { formatETB } from '@/lib/format';
import { useCartStore } from '@/store/cart';

// The menu screen passes the whole item as a JSON param to avoid a round trip.
// On a deep link or page reload that param is absent, so fall back to fetching
// the menu and resolving by route id rather than crashing on JSON.parse.
export default function ItemDetailScreen() {
  const { id, item: itemParam } = useLocalSearchParams<{ id: string; item?: string }>();

  const fromParam = useMemo(() => {
    if (!itemParam) return null;
    try {
      return JSON.parse(itemParam) as MenuItem;
    } catch {
      return null;
    }
  }, [itemParam]);

  const [item, setItem] = useState<MenuItem | null>(fromParam);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    if (item) return;
    let cancelled = false;
    getMenu()
      .then((menu) => {
        if (cancelled) return;
        const found = menu.find((m) => String(m.id) === String(id));
        if (found) setItem(found);
        else setNotFound(true);
      })
      .catch(() => {
        if (!cancelled) setNotFound(true);
      });
    return () => {
      cancelled = true;
    };
  }, [item, id]);

  if (notFound) {
    return (
      <View style={styles.fallback}>
        <Ionicons name="alert-circle-outline" size={40} color={Colors.border} />
        <Text style={styles.fallbackText}>This item is no longer available.</Text>
        <Pressable style={styles.fallbackBtn} onPress={() => router.replace('/')}>
          <Text style={styles.fallbackBtnText}>Back to menu</Text>
        </Pressable>
      </View>
    );
  }

  if (!item) {
    return (
      <View style={styles.fallback}>
        <ActivityIndicator color={Colors.brand} />
      </View>
    );
  }

  return <ItemDetail item={item} />;
}

function ItemDetail({ item }: { item: MenuItem }) {
  const addSelection = useCartStore((s) => s.addSelection);

  const groups = useMemo(() => {
    const map = new Map<string, MenuIngredient[]>();
    for (const ing of item.ingredients) {
      if (ing.input_type === 'toggle') continue;
      if (!map.has(ing.group_name)) map.set(ing.group_name, []);
      map.get(ing.group_name)!.push(ing);
    }
    return Array.from(map.entries());
  }, [item]);

  const toggles = useMemo(
    () => item.ingredients.filter((i) => i.input_type === 'toggle'),
    [item],
  );

  const [selected, setSelected] = useState<Record<number, boolean>>(() => {
    const init: Record<number, boolean> = {};
    for (const ing of item.ingredients) {
      if (ing.input_type === 'toggle') continue;
      if (ing.is_default) init[ing.id] = true;
    }
    return init;
  });
  const [removedToggles, setRemovedToggles] = useState<Set<number>>(new Set());
  const [specialInstructions, setSpecialInstructions] = useState('');
  const [qty, setQty] = useState(1);
  const [showRequiredHint, setShowRequiredHint] = useState(false);

  const selectRadio = (groupName: string, ingId: number) => {
    setSelected((prev) => {
      const next = { ...prev };
      for (const g of groups.find(([name]) => name === groupName)?.[1] ?? []) {
        delete next[g.id];
      }
      next[ingId] = true;
      return next;
    });
  };

  const toggleCheckbox = (ingId: number) => {
    setSelected((prev) => ({ ...prev, [ingId]: !prev[ingId] }));
  };

  const toggleIncluded = (ingId: number) => {
    setRemovedToggles((prev) => {
      const next = new Set(prev);
      if (next.has(ingId)) next.delete(ingId);
      else next.add(ingId);
      return next;
    });
  };

  const selectedIngredients = useMemo(
    () => item.ingredients.filter((i) => selected[i.id]),
    [item, selected],
  );
  const removedIngredients = useMemo(
    () => toggles.filter((t) => removedToggles.has(t.id)),
    [toggles, removedToggles],
  );

  const unitPrice = useMemo(
    () => item.price + selectedIngredients.reduce((sum, i) => sum + i.price_delta, 0),
    [item, selectedIngredients],
  );

  const requiredGroupsSatisfied = useMemo(
    () =>
      groups
        .filter(([, ings]) => ings.some((i) => i.is_required))
        .every(([, ings]) => ings.some((i) => selected[i.id])),
    [groups, selected],
  );

  const [from, to] = categoryGradient(item.category);

  const handleAddToCart = () => {
    if (!requiredGroupsSatisfied) {
      setShowRequiredHint(true);
      return;
    }
    addSelection({
      item,
      qty,
      adjustedPrice: unitPrice,
      customizations: selectedIngredients,
      removals: removedIngredients,
      specialInstructions: specialInstructions.trim() || null,
    });
    router.back();
  };

  return (
    <View style={styles.screen}>
      <ScrollView bounces={false}>
        <View style={styles.heroWrap}>
          {item.image_url ? (
            <Image source={{ uri: item.image_url }} style={styles.hero} contentFit="cover" />
          ) : (
            <LinearGradient colors={[from, to]} style={styles.hero}>
              <Ionicons name={categoryIcon(item.category)} size={64} color="rgba(255,255,255,0.3)" />
            </LinearGradient>
          )}
          <Pressable style={styles.closeBtn} onPress={() => router.back()}>
            <Ionicons name="close" size={20} color="#fff" />
          </Pressable>
          <View style={styles.heroOverlay}>
            <Text style={styles.heroCategory}>{item.category}</Text>
            <Text style={styles.heroName}>{item.name}</Text>
          </View>
        </View>

        <SafeAreaView edges={['bottom']} style={styles.body}>
          {!!item.description && <Text style={styles.description}>{item.description}</Text>}

          {toggles.length > 0 && (
            <View style={styles.section}>
              <View style={styles.sectionHeaderRow}>
                <Text style={styles.sectionTitle}>Includes</Text>
                <Text style={styles.sectionHint}>tap to remove</Text>
              </View>
              {toggles.map((t) => {
                const isRemoved = removedToggles.has(t.id);
                return (
                  <View key={t.id} style={[styles.toggleRow, isRemoved && styles.toggleRowRemoved]}>
                    <Text style={[styles.toggleLabel, isRemoved && styles.toggleLabelRemoved]}>
                      {t.name}
                    </Text>
                    <Switch
                      value={!isRemoved}
                      onValueChange={() => toggleIncluded(t.id)}
                      trackColor={{ true: Colors.brand, false: '#d0d0d0' }}
                    />
                  </View>
                );
              })}
            </View>
          )}

          {groups.map(([groupName, ings]) => {
            const isRequired = ings.some((i) => i.is_required);
            const isRadio = ings[0]?.input_type === 'radio';
            return (
              <View key={groupName} style={styles.section}>
                <View style={styles.sectionHeaderRow}>
                  <Text style={styles.sectionTitle}>{groupName}</Text>
                  {isRequired && <Text style={styles.requiredTag}>Required</Text>}
                </View>
                {ings.map((ing) => {
                  const isSelected = !!selected[ing.id];
                  return (
                    <Pressable
                      key={ing.id}
                      style={[styles.optionRow, isSelected && styles.optionRowSelected]}
                      onPress={() =>
                        isRadio ? selectRadio(groupName, ing.id) : toggleCheckbox(ing.id)
                      }>
                      <View style={styles.optionLeft}>
                        <View
                          style={[
                            isRadio ? styles.radioOuter : styles.checkboxOuter,
                            isSelected && styles.optionMarkSelected,
                          ]}>
                          {isSelected && (
                            <View style={isRadio ? styles.radioInner : styles.checkboxInner} />
                          )}
                        </View>
                        <Text style={styles.optionLabel}>{ing.name}</Text>
                      </View>
                      {ing.price_delta > 0 && (
                        <Text style={styles.optionPrice}>+{formatETB(ing.price_delta)}</Text>
                      )}
                    </Pressable>
                  );
                })}
              </View>
            );
          })}

          <View style={styles.section}>
            <View style={styles.sectionHeaderRow}>
              <Text style={styles.sectionTitle}>Special Instructions</Text>
              <Text style={styles.sectionHint}>optional</Text>
            </View>
            <TextInput
              style={styles.textarea}
              placeholder="Any allergies, preferences, or special requests…"
              placeholderTextColor="rgba(255,255,255,0.28)"
              multiline
              numberOfLines={2}
              maxLength={500}
              value={specialInstructions}
              onChangeText={setSpecialInstructions}
            />
          </View>
        </SafeAreaView>
      </ScrollView>

      <SafeAreaView edges={['bottom']} style={styles.footer}>
        <View style={styles.footerTop}>
          <View>
            <Text style={styles.footerPriceLabel}>Total</Text>
            <Text style={styles.footerPrice}>{formatETB(unitPrice * qty)}</Text>
          </View>
          <View style={styles.qtyStepper}>
            <Pressable
              style={styles.qtyBtnOutline}
              onPress={() => setQty((q) => Math.max(1, q - 1))}>
              <Text style={styles.qtyBtnOutlineText}>−</Text>
            </Pressable>
            <Text style={styles.qtyValue}>{qty}</Text>
            <Pressable style={styles.qtyBtnFilled} onPress={() => setQty((q) => q + 1)}>
              <Text style={styles.qtyBtnFilledText}>+</Text>
            </Pressable>
          </View>
        </View>
        <Button label="Add to Cart" onPress={handleAddToCart} />
        {showRequiredHint && !requiredGroupsSatisfied && (
          <Text style={styles.requiredHint}>Please complete required selections above</Text>
        )}
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: Colors.dark },
  fallback: {
    flex: 1, alignItems: 'center', justifyContent: 'center',
    backgroundColor: Colors.warm, gap: Spacing.md, padding: Spacing.lg,
  },
  fallbackText: { color: Colors.muted, fontSize: 14, textAlign: 'center' },
  fallbackBtn: {
    paddingHorizontal: Spacing.lg, paddingVertical: Spacing.sm,
    borderRadius: Radii.md, backgroundColor: Colors.brand,
  },
  fallbackBtnText: { color: '#fff', fontSize: 14, fontWeight: '700' },
  heroWrap: { height: 220, position: 'relative' },
  hero: { width: '100%', height: '100%', alignItems: 'center', justifyContent: 'center' },
  closeBtn: {
    position: 'absolute', top: 52, right: Spacing.lg,
    width: 34, height: 34, borderRadius: 17,
    backgroundColor: 'rgba(0,0,0,0.5)', alignItems: 'center', justifyContent: 'center',
  },
  heroOverlay: { position: 'absolute', bottom: Spacing.lg, left: Spacing.lg, right: Spacing.lg },
  heroCategory: {
    alignSelf: 'flex-start', color: '#fff', fontSize: 11, fontWeight: '700',
    backgroundColor: Colors.brand, borderRadius: Radii.pill, paddingHorizontal: 10, paddingVertical: 3,
    marginBottom: 6, overflow: 'hidden',
  },
  heroName: { ...Type.heading, fontSize: 22, color: '#fff' },
  body: { paddingHorizontal: Spacing.lg, paddingTop: Spacing.lg },
  description: { color: 'rgba(255,255,255,0.5)', fontSize: 13.5, lineHeight: 20, marginBottom: Spacing.md },
  section: { marginBottom: Spacing.lg },
  sectionHeaderRow: { flexDirection: 'row', alignItems: 'center', gap: Spacing.sm, marginBottom: Spacing.sm },
  sectionTitle: { color: '#fff', fontWeight: '700', fontSize: 14.5 },
  sectionHint: { color: 'rgba(255,255,255,0.28)', fontSize: 11.5 },
  requiredTag: {
    color: Colors.brandLight, fontSize: 10.5, fontWeight: '700',
    borderWidth: 1, borderColor: Colors.brandLight, borderRadius: Radii.pill,
    paddingHorizontal: 8, paddingVertical: 1,
  },
  toggleRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingVertical: 10, paddingHorizontal: Spacing.md, borderRadius: Radii.md,
    borderWidth: 1, borderColor: Colors.darkBorder, backgroundColor: Colors.darkElevated, marginBottom: Spacing.xs,
  },
  toggleRowRemoved: { opacity: 0.5, borderColor: '#2a1a1a' },
  toggleLabel: { color: '#fff', fontSize: 14 },
  toggleLabelRemoved: { textDecorationLine: 'line-through' },
  optionRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingVertical: 12, paddingHorizontal: Spacing.md, borderRadius: Radii.md,
    borderWidth: 1, borderColor: Colors.darkBorder, backgroundColor: Colors.darkElevated, marginBottom: Spacing.xs,
  },
  optionRowSelected: { borderColor: 'rgba(200,16,46,0.5)', backgroundColor: 'rgba(200,16,46,0.08)' },
  optionLeft: { flexDirection: 'row', alignItems: 'center', gap: Spacing.sm, flex: 1 },
  radioOuter: {
    width: 20, height: 20, borderRadius: 10, borderWidth: 2, borderColor: '#555',
    alignItems: 'center', justifyContent: 'center',
  },
  checkboxOuter: {
    width: 20, height: 20, borderRadius: 5, borderWidth: 2, borderColor: '#555',
    alignItems: 'center', justifyContent: 'center',
  },
  optionMarkSelected: { borderColor: Colors.brand },
  radioInner: { width: 10, height: 10, borderRadius: 5, backgroundColor: Colors.brand },
  checkboxInner: { width: 10, height: 10, borderRadius: 2, backgroundColor: Colors.brand },
  optionLabel: { color: '#fff', fontSize: 14, flexShrink: 1 },
  optionPrice: { color: 'rgba(255,255,255,0.5)', fontSize: 13, fontWeight: '600' },
  textarea: {
    borderWidth: 1, borderColor: Colors.darkBorder, backgroundColor: Colors.darkElevated,
    borderRadius: Radii.md, padding: Spacing.md, color: '#fff', fontSize: 14, minHeight: 64,
    textAlignVertical: 'top',
  },
  footer: {
    borderTopWidth: 1, borderTopColor: Colors.darkBorder, backgroundColor: Colors.dark,
    paddingHorizontal: Spacing.lg, paddingTop: Spacing.md,
  },
  footerTop: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: Spacing.md },
  footerPriceLabel: { color: 'rgba(255,255,255,0.3)', fontSize: 11 },
  footerPrice: { color: '#fff', fontSize: 22, fontWeight: '800' },
  qtyStepper: { flexDirection: 'row', alignItems: 'center', gap: Spacing.md },
  qtyBtnOutline: {
    width: 34, height: 34, borderRadius: 17, borderWidth: 1, borderColor: '#333',
    alignItems: 'center', justifyContent: 'center',
  },
  qtyBtnOutlineText: { color: 'rgba(255,255,255,0.6)', fontSize: 18, fontWeight: '700' },
  qtyValue: { color: '#fff', fontSize: 16, fontWeight: '700', width: 24, textAlign: 'center' },
  qtyBtnFilled: {
    width: 34, height: 34, borderRadius: 17, backgroundColor: Colors.brand,
    alignItems: 'center', justifyContent: 'center',
  },
  qtyBtnFilledText: { color: '#fff', fontSize: 18, fontWeight: '700' },
  requiredHint: { color: Colors.brandLight, fontSize: 12, textAlign: 'center', marginTop: Spacing.sm },
});
