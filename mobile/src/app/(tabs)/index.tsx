import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { Image } from 'expo-image';
import { router } from 'expo-router';
import { useEffect, useMemo, useState } from 'react';
import {
  FlatList,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Button } from '@/components/Button';
import { Colors, Radii, Shadow, Spacing, Type } from '@/constants/theme';
import { getCategories, getMenu, type MenuItem } from '@/lib/api';
import { categoryGradient, categoryIcon } from '@/lib/categoryStyle';
import { formatETB } from '@/lib/format';
import { useCartStore } from '@/store/cart';

export default function MenuScreen() {
  const [menu, setMenu] = useState<MenuItem[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [activeCategory, setActiveCategory] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const totalCount = useCartStore((s) => s.totalCount());
  const totalAmount = useCartStore((s) => s.totalAmount());
  const totalQtyForItem = useCartStore((s) => s.totalQtyForItem);

  useEffect(() => {
    (async () => {
      try {
        const [items, cats] = await Promise.all([getMenu(), getCategories()]);
        setMenu(items);
        setCategories(cats);
      } catch {
        setError('Could not load the menu. Is the server running?');
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const filtered = useMemo(
    () => (activeCategory ? menu.filter((i) => i.category === activeCategory) : menu),
    [menu, activeCategory],
  );

  return (
    <SafeAreaView style={styles.screen} edges={['top']}>
      <View style={styles.header}>
        <Text style={styles.brand}>Taste of Ethiopia</Text>
        <Text style={styles.tagline}>Order Fresh, Eat Well.</Text>
      </View>

      <FlatList
        horizontal
        showsHorizontalScrollIndicator={false}
        data={['', ...categories]}
        keyExtractor={(c) => c || 'all'}
        style={styles.categoryBarList}
        contentContainerStyle={styles.categoryBar}
        renderItem={({ item: cat }) => {
          const active = cat === activeCategory;
          return (
            <Pressable
              onPress={() => setActiveCategory(cat)}
              style={[styles.catPill, active && styles.catPillActive]}>
              <Text style={[styles.catPillText, active && styles.catPillTextActive]}>
                {cat || 'All Items'}
              </Text>
            </Pressable>
          );
        }}
      />

      {loading ? (
        <View style={styles.centered}>
          <Text style={styles.muted}>Loading menu…</Text>
        </View>
      ) : error ? (
        <View style={styles.centered}>
          <Text style={styles.errorText}>{error}</Text>
        </View>
      ) : (
        <FlatList
          data={filtered}
          keyExtractor={(i) => String(i.id)}
          numColumns={2}
          columnWrapperStyle={{ gap: Spacing.md }}
          contentContainerStyle={{ padding: Spacing.lg, paddingBottom: 120, gap: Spacing.md }}
          renderItem={({ item }) => (
            <MenuCard item={item} qtyInCart={totalQtyForItem(item.id)} />
          )}
          ListEmptyComponent={
            <View style={styles.centered}>
              <Text style={styles.muted}>No items in this category.</Text>
            </View>
          }
        />
      )}

      {totalCount > 0 && (
        <View style={styles.cartBarWrap} pointerEvents="box-none">
          <Pressable style={styles.cartBar} onPress={() => router.push('/cart')}>
            <View style={styles.cartBadge}>
              <Text style={styles.cartBadgeText}>{totalCount}</Text>
            </View>
            <Text style={styles.cartBarText}>View Cart</Text>
            <Text style={styles.cartBarTotal}>{formatETB(totalAmount)}</Text>
          </Pressable>
        </View>
      )}
    </SafeAreaView>
  );
}

function MenuCard({ item, qtyInCart }: { item: MenuItem; qtyInCart: number }) {
  const hasCustomization = item.ingredients.length > 0;
  const [from, to] = categoryGradient(item.category);

  return (
    <Pressable
      style={styles.card}
      onPress={() =>
        router.push({ pathname: '/item/[id]', params: { id: String(item.id), item: JSON.stringify(item) } })
      }>
      <View style={styles.cardImageWrap}>
        {item.image_url ? (
          <Image source={{ uri: item.image_url }} style={styles.cardImage} contentFit="cover" />
        ) : (
          <LinearGradient colors={[from, to]} style={styles.cardImage}>
            <Ionicons name={categoryIcon(item.category)} size={40} color="rgba(255,255,255,0.85)" />
          </LinearGradient>
        )}
        <View style={styles.categoryBadge}>
          <Text style={styles.categoryBadgeText}>{item.category}</Text>
        </View>
        {hasCustomization && (
          <View style={styles.customizeBadge}>
            <Text style={styles.customizeBadgeText}>Customize</Text>
          </View>
        )}
        {qtyInCart > 0 && (
          <View style={styles.qtyBadge}>
            <Text style={styles.qtyBadgeText}>{qtyInCart}</Text>
          </View>
        )}
      </View>
      <View style={styles.cardBody}>
        <Text style={styles.cardName} numberOfLines={1}>{item.name}</Text>
        <Text style={styles.cardDesc} numberOfLines={2}>{item.description}</Text>
        <View style={styles.cardFooter}>
          <Text style={styles.cardPrice}>{formatETB(item.price)}</Text>
          <Text style={styles.cardPoints}>+{Math.floor(item.price / 10)} pts</Text>
        </View>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: Colors.warm },
  header: { paddingHorizontal: Spacing.lg, paddingTop: Spacing.sm, paddingBottom: Spacing.sm },
  brand: { ...Type.title, fontSize: 24, color: Colors.ink },
  tagline: { ...Type.body, color: Colors.muted, marginTop: 2 },
  // flexGrow:0 stops the horizontal list from expanding into the leftover
  // vertical space; alignItems:'center' stops the pills being stretched to
  // that height, which made them uneven.
  categoryBarList: { flexGrow: 0 },
  categoryBar: {
    paddingHorizontal: Spacing.lg,
    gap: Spacing.sm,
    paddingBottom: Spacing.sm,
    alignItems: 'center',
  },
  catPill: {
    paddingHorizontal: Spacing.lg,
    paddingVertical: 9,
    borderRadius: Radii.pill,
    backgroundColor: Colors.surface,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  catPillActive: { backgroundColor: Colors.brand, borderColor: Colors.brand },
  catPillText: { fontSize: 13.5, fontWeight: '700', color: Colors.muted },
  catPillTextActive: { color: '#fff' },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: Spacing.xl },
  muted: { color: Colors.muted, fontSize: 14 },
  errorText: { color: Colors.danger, fontSize: 14, textAlign: 'center' },
  card: {
    flex: 1,
    backgroundColor: Colors.surface,
    borderRadius: Radii.lg,
    overflow: 'hidden',
    ...Shadow.card,
  },
  cardImageWrap: { height: 120, position: 'relative' },
  cardImage: { width: '100%', height: '100%', alignItems: 'center', justifyContent: 'center' },
  categoryBadge: {
    position: 'absolute', top: 8, left: 8,
    backgroundColor: 'rgba(0,0,0,0.45)', borderRadius: Radii.pill,
    paddingHorizontal: 8, paddingVertical: 3,
  },
  categoryBadgeText: { color: '#fff', fontSize: 10, fontWeight: '700' },
  customizeBadge: {
    position: 'absolute', top: 8, right: 8,
    backgroundColor: Colors.gold, borderRadius: Radii.pill,
    paddingHorizontal: 8, paddingVertical: 3,
  },
  customizeBadgeText: { color: Colors.ink, fontSize: 10, fontWeight: '700' },
  qtyBadge: {
    position: 'absolute', bottom: 8, right: 8,
    backgroundColor: Colors.brand, borderRadius: Radii.pill,
    width: 22, height: 22, alignItems: 'center', justifyContent: 'center',
  },
  qtyBadgeText: { color: '#fff', fontSize: 12, fontWeight: '800' },
  cardBody: { padding: Spacing.md },
  cardName: { fontSize: 14, fontWeight: '700', color: Colors.ink },
  cardDesc: { fontSize: 12, color: Colors.muted, marginTop: 2, minHeight: 30 },
  cardFooter: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', marginTop: Spacing.sm },
  cardPrice: { ...Type.price, color: Colors.brand },
  cardPoints: { fontSize: 10.5, color: Colors.muted },
  cartBarWrap: {
    position: 'absolute', left: 0, right: 0, bottom: 0,
    paddingHorizontal: Spacing.lg, paddingBottom: Spacing.md,
  },
  cartBar: {
    flexDirection: 'row', alignItems: 'center',
    backgroundColor: Colors.dark, borderRadius: Radii.xl,
    paddingVertical: 14, paddingHorizontal: Spacing.lg,
    ...Shadow.floating,
  },
  cartBadge: {
    backgroundColor: Colors.gold, borderRadius: Radii.pill,
    width: 26, height: 26, alignItems: 'center', justifyContent: 'center', marginRight: Spacing.sm,
  },
  cartBadgeText: { color: Colors.ink, fontWeight: '800', fontSize: 13 },
  cartBarText: { color: '#fff', fontWeight: '700', fontSize: 14.5, flex: 1 },
  cartBarTotal: { color: '#fff', fontWeight: '800', fontSize: 15 },
});
