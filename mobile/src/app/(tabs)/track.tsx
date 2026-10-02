import { Ionicons } from '@expo/vector-icons';
import { router } from 'expo-router';
import { useState } from 'react';
import { StyleSheet, Text, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Button } from '@/components/Button';
import { Colors, Radii, Spacing, Type } from '@/constants/theme';

export default function TrackTab() {
  const [orderNumber, setOrderNumber] = useState('');

  return (
    <SafeAreaView style={styles.screen} edges={['top']}>
      <View style={styles.body}>
        <View style={styles.iconWrap}>
          <Ionicons name="receipt" size={30} color={Colors.brand} />
        </View>
        <Text style={styles.title}>Track Your Order</Text>
        <Text style={styles.subtitle}>Enter your order number to see live status updates.</Text>

        <TextInput
          style={styles.input}
          placeholder="ORD-XXXXXXXX"
          autoCapitalize="characters"
          value={orderNumber}
          onChangeText={setOrderNumber}
        />
        <Button
          label="Track Order"
          disabled={!orderNumber.trim()}
          onPress={() =>
            router.push({ pathname: '/track/[orderNumber]', params: { orderNumber: orderNumber.trim().toUpperCase() } })
          }
        />
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: Colors.warm },
  body: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: Spacing.xl, gap: Spacing.md },
  iconWrap: {
    width: 64, height: 64, borderRadius: 32, backgroundColor: '#fff5f5',
    alignItems: 'center', justifyContent: 'center', marginBottom: Spacing.sm,
  },
  title: { ...Type.heading, color: Colors.ink },
  subtitle: { color: Colors.muted, fontSize: 13, textAlign: 'center', marginBottom: Spacing.md },
  input: {
    width: '100%', borderWidth: 1, borderColor: Colors.border, backgroundColor: Colors.surface,
    borderRadius: Radii.md, paddingHorizontal: Spacing.md, paddingVertical: 14, fontSize: 15,
    fontWeight: '700', color: Colors.ink, textAlign: 'center', letterSpacing: 1,
  },
});
