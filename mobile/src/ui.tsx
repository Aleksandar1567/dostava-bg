import { ReactNode } from 'react';
import { Pressable, StyleSheet, Text, View, ViewStyle } from 'react-native';
import { Theme } from './theme';

export function Btn({
  t,
  title,
  onPress,
  primary,
  danger,
  small,
  disabled,
  style,
}: {
  t: Theme;
  title: string;
  onPress: () => void;
  primary?: boolean;
  danger?: boolean;
  small?: boolean;
  disabled?: boolean;
  style?: ViewStyle;
}) {
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      style={({ pressed }) => [
        s.btn,
        small && s.small,
        {
          backgroundColor: primary ? t.acc : t.card,
          borderColor: primary ? t.acc : t.line,
          opacity: disabled ? 0.45 : pressed ? 0.75 : 1,
        },
        style,
      ]}
    >
      <Text
        style={{
          color: primary ? t.accInk : danger ? t.bad : t.ink,
          fontWeight: '600',
          fontSize: small ? 13 : 15,
        }}
      >
        {title}
      </Text>
    </Pressable>
  );
}

export function Card({ t, children, style }: { t: Theme; children: ReactNode; style?: ViewStyle }) {
  return (
    <View style={[s.card, { backgroundColor: t.card, borderColor: t.line }, style]}>{children}</View>
  );
}

export function Tag({ t, text, color }: { t: Theme; text: string; color?: string }) {
  return (
    <View style={[s.tag, { backgroundColor: t.chip }]}>
      <Text style={{ color: color ?? t.muted, fontSize: 12, fontWeight: '600' }}>{text}</Text>
    </View>
  );
}

const s = StyleSheet.create({
  btn: {
    paddingVertical: 10,
    paddingHorizontal: 14,
    borderRadius: 10,
    borderWidth: 1,
    alignItems: 'center',
    justifyContent: 'center',
  },
  small: { paddingVertical: 6, paddingHorizontal: 10 },
  card: { borderWidth: 1, borderRadius: 14, padding: 14, marginBottom: 12 },
  tag: { borderRadius: 6, paddingHorizontal: 6, paddingVertical: 1, marginRight: 4, marginTop: 4 },
});
