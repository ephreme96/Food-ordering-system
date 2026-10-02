import type { Ionicons } from '@expo/vector-icons';

type IoniconName = keyof typeof Ionicons.glyphMap;

// Mirrors frontend/customer.html's getItemGradient()/getCategoryIcon() so the app
// keeps the same per-category visual language as the web ordering page.
// Placeholders shown when an item has no photo. These are warm mid-tones, not
// near-black: a very dark tile with a faint icon reads as a broken/empty image
// rather than a deliberate placeholder.
const GRADIENTS: Record<string, [string, string]> = {
  Chaofan: ['#8a4b25', '#c07a3e'],
  Extras: ['#a06a2c', '#d1a054'],
  'Main Dishes': ['#1a0a05', '#3d1808'],
  Vegetarian: ['#052005', '#0a3d0a'],
  Breakfast: ['#1a1005', '#3d2808'],
  Drinks: ['#050a1a', '#083d3d'],
  Sides: ['#0d0505', '#3d0a0a'],
};

const ICONS: Record<string, IoniconName> = {
  Chaofan: 'restaurant',
  // 'add-circle' rendered as a bare "+" that looked like an empty add button.
  Extras: 'bag-handle',
  'Main Dishes': 'restaurant',
  Vegetarian: 'leaf',
  Breakfast: 'sunny',
  Drinks: 'wine',
  Sides: 'fast-food',
};

export const categoryGradient = (category: string): [string, string] =>
  GRADIENTS[category] ?? ['#111111', '#333333'];

export const categoryIcon = (category: string): IoniconName => ICONS[category] ?? 'restaurant';
