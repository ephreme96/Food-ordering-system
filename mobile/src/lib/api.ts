// Typed client for the FastAPI backend (see backend/routes/*.py). Native fetch is
// not subject to browser CORS, so this talks to the backend directly — no proxy needed.
// Shapes below are copied field-for-field from the Pydantic response models.

const BASE_URL = process.env.EXPO_PUBLIC_API_BASE_URL ?? 'http://localhost:8000';

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  });
  const isJson = res.headers.get('content-type')?.includes('application/json');
  const body = isJson ? await res.json().catch(() => null) : null;
  if (!res.ok) {
    const detail = body?.detail;
    const message = typeof detail === 'string' ? detail : `Request failed (${res.status})`;
    throw new ApiError(res.status, message, detail);
  }
  return body as T;
}

// ─── Menu ───────────────────────────────────────────────────────────────────────

export type IngredientInputType = 'radio' | 'checkbox' | 'toggle';

export interface MenuIngredient {
  id: number;
  menu_item_id: number;
  group_name: string;
  name: string;
  price_delta: number;
  input_type: IngredientInputType;
  is_required: boolean;
  is_default: boolean;
  sort_order: number;
  is_available: boolean;
}

export interface MenuItem {
  id: number;
  name: string;
  description: string | null;
  price: number;
  category: string;
  image_url: string | null;
  is_available: boolean;
  ingredients: MenuIngredient[];
}

export const getMenu = (category?: string) =>
  request<MenuItem[]>(`/api/menu${category ? `?category=${encodeURIComponent(category)}` : ''}`);

export const getCategories = () => request<string[]>('/api/menu/categories');

// ─── Orders ─────────────────────────────────────────────────────────────────────

export type PaymentMethod = 'online' | 'cash' | 'telebirr' | 'cbebirr' | 'bank_transfer';

export interface OrderItemInput {
  menu_item_id: number;
  quantity: number;
  customizations: number[];
  removals: number[];
  special_instructions: string | null;
}

export interface CreateOrderInput {
  customer_name: string;
  customer_phone?: string | null;
  customer_email?: string | null;
  items: OrderItemInput[];
  payment_method: PaymentMethod;
  notes?: string | null;
  promo_code?: string | null;
}

export interface OrderResponse {
  id: number;
  order_number: string;
  customer_name: string;
  customer_phone: string | null;
  customer_email: string | null;
  items_snapshot: Array<Record<string, unknown>>;
  total_amount: number;
  status: string;
  payment_method: PaymentMethod;
  tx_ref: string | null;
  chapa_checkout_url: string | null;
  receipt_code: string | null;
  receipt_token: string | null;
  created_at: string;
}

// Public tracker — GET /api/orders/track/{order_number} (payment_routes.py:track_order).
// `step` is pre-computed server-side: 0 placed, 1 paid, 2 preparing, 3 ready, 4 picked
// up/completed, -1 cancelled/failed. Never exposes receipt_code/receipt_token.
export interface TrackedOrder {
  order_number: string;
  customer_name: string;
  status: string;
  step: number;
  total_amount: number;
  payment_method: PaymentMethod;
  items_count: number;
  created_at: string | null;
  picked_up_at: string | null;
}

export const createOrder = (input: CreateOrderInput) =>
  request<OrderResponse>('/api/orders', { method: 'POST', body: JSON.stringify(input) });

export const trackOrder = (orderNumber: string) =>
  request<TrackedOrder>(`/api/orders/track/${encodeURIComponent(orderNumber)}`);

// ─── Chapa (online) payment ─────────────────────────────────────────────────────

export const initChapaPayment = (orderId: number, txRef: string) =>
  request<{ checkout_url: string; tx_ref: string; order_id: number }>(
    `/api/payment/initialize/${orderId}`,
    { method: 'POST', body: JSON.stringify({ tx_ref: txRef }) },
  );

export const verifyChapaPayment = (txRef: string) =>
  request<{ status: string; order_number?: string; receipt_code?: string; receipt_token?: string }>(
    `/api/payment/verify/${encodeURIComponent(txRef)}`,
  );

// ─── Telebirr / CBE Birr (payment_routes.py) ────────────────────────────────────

export interface MobilePaymentInitResult {
  status: 'sandbox_paid' | 'initiated' | string;
  order_number: string;
  receipt_code?: string;
  checkout_url?: string;
  qr_code?: string;
  reference?: string;
  message?: string;
}

export const initiateMobilePayment = (input: {
  order_number: string;
  tx_ref: string;
  method: 'telebirr' | 'cbebirr';
  customer_phone: string;
}) => request<MobilePaymentInitResult>('/api/payments/initiate', {
  method: 'POST',
  body: JSON.stringify(input),
});

export interface PaymentStatusResult {
  order_number: string;
  status: string;
  paid: boolean;
  receipt_code: string | null;
  total_amount: number;
}

export const getPaymentStatus = (orderNumber: string, txRef: string) =>
  request<PaymentStatusResult>(
    `/api/payments/status/${encodeURIComponent(orderNumber)}?tx_ref=${encodeURIComponent(txRef)}`,
  );

// ─── Bank transfer ───────────────────────────────────────────────────────────────

export interface BankInfo {
  bank_name: string;
  account_number: string;
  account_name: string;
  instructions: string;
}

export const getBankInfo = () => request<BankInfo>('/api/payments/bank-info');

export const claimBankTransfer = (input: { order_number: string; tx_ref: string; bank_ref: string }) =>
  request<{ status: string; message: string; order_number: string }>(
    '/api/payments/bank-transfer/claim',
    { method: 'POST', body: JSON.stringify(input) },
  );

// ─── Promo codes ─────────────────────────────────────────────────────────────────

export interface PromoCheckResult {
  valid: boolean;
  code: string;
  discount_type: 'percent' | 'fixed';
  discount_value: number;
  discount_etb: number;
  new_total: number;
  description: string;
}

export const checkPromo = (code: string, orderTotal: number) =>
  request<PromoCheckResult>(
    `/api/promos/${encodeURIComponent(code)}?order_total=${orderTotal}`,
  );

// ─── Loyalty (loyalty_routes.py) ─────────────────────────────────────────────────

export interface LoyaltyBalance {
  account_id: number;
  identifier: string;
  display_name: string | null;
  points_balance: number;
  total_earned: number;
  total_redeemed: number;
  next_reward_pts: number;
}

export const loyaltyJoin = (identifier: string, displayName?: string | null) =>
  request<LoyaltyBalance>('/api/loyalty/join', {
    method: 'POST',
    body: JSON.stringify({ identifier, display_name: displayName ?? null }),
  });

export const getLoyaltyBalance = (identifier: string) =>
  request<LoyaltyBalance>(`/api/loyalty/balance?identifier=${encodeURIComponent(identifier)}`);

export interface RewardItem {
  id: number;
  name: string;
  description: string | null;
  points_required: number;
  image_url: string | null;
  quantity_limit: number | null;
  quantity_claimed: number;
  valid_until: string | null;
  can_claim: boolean;
}

export const getRewards = (identifier?: string) =>
  request<RewardItem[]>(
    `/api/loyalty/rewards${identifier ? `?identifier=${encodeURIComponent(identifier)}` : ''}`,
  );

export interface RewardClaimResult {
  claim_code: string;
  reward_name: string;
  points_spent: number;
  new_balance: number;
}

export const claimReward = (rewardId: number, identifier: string, displayName?: string | null) =>
  request<RewardClaimResult>(`/api/loyalty/rewards/${rewardId}/claim`, {
    method: 'POST',
    body: JSON.stringify({ identifier, display_name: displayName ?? null }),
  });

export interface LoyaltyHistoryEntry {
  type: 'earn' | 'redeem';
  points: number;
  description: string;
  order_number: string | null;
  created_at: string | null;
}

export const getLoyaltyHistory = (identifier: string) =>
  request<LoyaltyHistoryEntry[]>(`/api/loyalty/history?identifier=${encodeURIComponent(identifier)}`);
