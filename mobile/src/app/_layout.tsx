import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { Colors } from '@/constants/theme';

export default function RootLayout() {
  return (
    <SafeAreaProvider>
      <StatusBar style="light" />
      <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: Colors.warm } }}>
        <Stack.Screen name="(tabs)" />
        <Stack.Screen name="item/[id]" options={{ presentation: 'modal' }} />
        <Stack.Screen name="cart" options={{ presentation: 'modal' }} />
        <Stack.Screen name="checkout" options={{ presentation: 'modal' }} />
        <Stack.Screen name="pay-wait" options={{ presentation: 'modal', gestureEnabled: false }} />
        <Stack.Screen name="bank-transfer" options={{ presentation: 'modal' }} />
        <Stack.Screen name="confirmation" options={{ presentation: 'modal', gestureEnabled: false }} />
        <Stack.Screen name="track/[orderNumber]" />
      </Stack>
    </SafeAreaProvider>
  );
}
