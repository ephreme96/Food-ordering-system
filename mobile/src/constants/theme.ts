// "Taste of Ethiopia" brand palette — ported from frontend/customer.html's Tailwind
// config, refined slightly for a modern app feel. Fixed brand theme (not adaptive to
// system light/dark) to match the web app's identity.

export const Colors = {
  brand: '#C8102E',
  brandDark: '#A00D24',
  brandLight: '#EF4444',
  gold: '#D4A017',
  goldLight: '#F5D060',
  green: '#16A34A',
  ink: '#111111',
  warm: '#FFFDF8',
  surface: '#FFFFFF',
  border: '#EFEAE3',
  muted: '#8A8480',
  dark: '#0D0D0D',
  darkElevated: '#161616',
  darkBorder: '#232323',
  success: '#16A34A',
  successBg: '#F0FDF4',
  danger: '#DC2626',
  dangerBg: '#FEF2F2',
} as const;

export const Radii = {
  sm: 10,
  md: 16,
  lg: 20,
  xl: 24,
  pill: 999,
} as const;

export const Spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
} as const;

export const Type = {
  title: { fontSize: 28, fontWeight: '800' as const, letterSpacing: -0.4 },
  heading: { fontSize: 20, fontWeight: '700' as const, letterSpacing: -0.2 },
  subheading: { fontSize: 16, fontWeight: '700' as const },
  body: { fontSize: 15, fontWeight: '400' as const },
  bodyBold: { fontSize: 15, fontWeight: '700' as const },
  caption: { fontSize: 12.5, fontWeight: '500' as const },
  price: { fontSize: 17, fontWeight: '800' as const },
};

export const Shadow = {
  card: {
    shadowColor: '#000',
    shadowOpacity: 0.06,
    shadowRadius: 16,
    shadowOffset: { width: 0, height: 6 },
    elevation: 3,
  },
  floating: {
    shadowColor: '#000',
    shadowOpacity: 0.18,
    shadowRadius: 24,
    shadowOffset: { width: 0, height: 10 },
    elevation: 8,
  },
} as const;
