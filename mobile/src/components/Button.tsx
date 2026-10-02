import { memo } from 'react';
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  type StyleProp,
  type ViewStyle,
} from 'react-native';
import * as Haptics from 'expo-haptics';

import { Colors, Radii } from '@/constants/theme';

interface ButtonProps {
  label: string;
  onPress: () => void;
  variant?: 'primary' | 'secondary' | 'dark' | 'outline';
  disabled?: boolean;
  loading?: boolean;
  style?: StyleProp<ViewStyle>;
}

function ButtonBase({ label, onPress, variant = 'primary', disabled, loading, style }: ButtonProps) {
  const isDisabled = disabled || loading;
  return (
    <Pressable
      disabled={isDisabled}
      onPress={() => {
        Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
        onPress();
      }}
      style={({ pressed }) => [
        styles.base,
        variant === 'primary' && styles.primary,
        variant === 'secondary' && styles.secondary,
        variant === 'dark' && styles.dark,
        variant === 'outline' && styles.outline,
        isDisabled && styles.disabled,
        pressed && !isDisabled && styles.pressed,
        style,
      ]}>
      {loading ? (
        <ActivityIndicator color={variant === 'outline' ? Colors.brand : '#fff'} />
      ) : (
        <Text
          style={[
            styles.label,
            variant === 'outline' && { color: Colors.brand },
          ]}>
          {label}
        </Text>
      )}
    </Pressable>
  );
}

export const Button = memo(ButtonBase);

const styles = StyleSheet.create({
  base: {
    height: 52,
    borderRadius: Radii.md,
    alignItems: 'center',
    justifyContent: 'center',
    flexDirection: 'row',
  },
  primary: { backgroundColor: Colors.brand },
  secondary: { backgroundColor: Colors.gold },
  dark: { backgroundColor: Colors.dark },
  outline: { backgroundColor: 'transparent', borderWidth: 1.5, borderColor: Colors.brand },
  disabled: { opacity: 0.45 },
  pressed: { transform: [{ scale: 0.98 }] },
  label: { color: '#fff', fontSize: 15.5, fontWeight: '700' },
});
