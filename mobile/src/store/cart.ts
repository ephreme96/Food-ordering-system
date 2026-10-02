import { create } from 'zustand';

import type { MenuIngredient, MenuItem } from '@/lib/api';

export interface CartSelection {
  item: MenuItem;
  qty: number;
  adjustedPrice: number; // item.price + sum of selected customization price_deltas
  customizations: MenuIngredient[]; // selected radio/checkbox options
  removals: MenuIngredient[]; // toggle ingredients the customer removed
  specialInstructions: string | null;
}

export interface CartEntry extends CartSelection {
  cartKey: string;
}

// Mirrors customer.html's cart keying: same item + same selections merges into one
// line, different selections (e.g. different spice level) get their own line.
export function buildCartKey(
  itemId: number,
  customizations: MenuIngredient[],
  removals: MenuIngredient[],
  specialInstructions: string | null,
): string {
  const custIds = customizations.map((c) => c.id).sort((a, b) => a - b).join(',');
  const remIds = removals.map((r) => r.id).sort((a, b) => a - b).join(',');
  return `${itemId}|${custIds}|${remIds}|${specialInstructions ?? ''}`;
}

interface CartState {
  entries: Record<string, CartEntry>;
  addSelection: (selection: CartSelection) => void;
  changeQtyByKey: (cartKey: string, delta: number) => void;
  removeByKey: (cartKey: string) => void;
  clear: () => void;
  totalQtyForItem: (itemId: number) => number;
  totalCount: () => number;
  totalAmount: () => number;
}

export const useCartStore = create<CartState>((set, get) => ({
  entries: {},

  addSelection: (selection) => {
    const cartKey = buildCartKey(
      selection.item.id,
      selection.customizations,
      selection.removals,
      selection.specialInstructions,
    );
    set((state) => {
      const existing = state.entries[cartKey];
      const qty = (existing?.qty ?? 0) + selection.qty;
      return {
        entries: { ...state.entries, [cartKey]: { ...selection, qty, cartKey } },
      };
    });
  },

  changeQtyByKey: (cartKey, delta) => {
    set((state) => {
      const existing = state.entries[cartKey];
      if (!existing) return state;
      const qty = Math.max(0, existing.qty + delta);
      const entries = { ...state.entries };
      if (qty === 0) delete entries[cartKey];
      else entries[cartKey] = { ...existing, qty };
      return { entries };
    });
  },

  removeByKey: (cartKey) => {
    set((state) => {
      const entries = { ...state.entries };
      delete entries[cartKey];
      return { entries };
    });
  },

  clear: () => set({ entries: {} }),

  totalQtyForItem: (itemId) =>
    Object.values(get().entries)
      .filter((e) => e.item.id === itemId)
      .reduce((sum, e) => sum + e.qty, 0),

  totalCount: () => Object.values(get().entries).reduce((sum, e) => sum + e.qty, 0),

  totalAmount: () =>
    Object.values(get().entries).reduce((sum, e) => sum + e.adjustedPrice * e.qty, 0),
}));
