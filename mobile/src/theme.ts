import { useColorScheme } from 'react-native';

const light = {
  bg: '#f4f5f7',
  card: '#ffffff',
  ink: '#15171b',
  muted: '#5d6573',
  line: '#e2e5ea',
  acc: '#0b6bcb',
  accInk: '#ffffff',
  ok: '#1a7f37',
  warn: '#b35900',
  bad: '#c62828',
  sim: '#6f42c1',
  chip: '#eef1f5',
};

const dark: typeof light = {
  bg: '#0f1114',
  card: '#1a1d22',
  ink: '#e8ebf0',
  muted: '#9aa3af',
  line: '#2b3038',
  acc: '#4ea1ff',
  accInk: '#06121f',
  ok: '#4cc26e',
  warn: '#f0a24a',
  bad: '#ff6b6b',
  sim: '#9b7be0',
  chip: '#232830',
};

export type Theme = typeof light;

export function useTheme(): Theme {
  return useColorScheme() === 'dark' ? dark : light;
}
