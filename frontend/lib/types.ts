/**
 * Wire types, mirroring `backend/app/schemas/analysis.py`.
 *
 * Kept hand-written rather than generated so the shape stays readable at the
 * point of use. If these drift from the backend the typecheck will not catch
 * it — the contract test that does is `backend/tests/test_api.py`.
 */

export type DealRating =
  | "GREAT_VALUE"
  | "GOOD_VALUE"
  | "FAIR_VALUE"
  | "HIGH_PRICED"
  | "OVERPRICED"
  | "SUSPICIOUSLY_CHEAP"
  | "INSUFFICIENT_DATA";

export type RiskSeverity = "INFO" | "LOW" | "MODERATE" | "HIGH" | "CRITICAL";

export type Priority = "high" | "medium" | "low";

/** Whether a price is an asking price or a settled one. Never omitted. */
export type PriceBasis = "ASKING" | "TRANSACTION" | "MIXED";

export interface Money {
  amount: number;
  currency: string;
  formatted: string;
}

export interface Adjustment {
  factor: string;
  label: string;
  amount_azn: number;
  /** APPLIED, INSUFFICIENT_DATA, INPUT_UNKNOWN, INSUFFICIENT_HISTORY, … */
  status: string;
  explanation: string;
  applied: boolean;
  data_points: number;
}

export interface Valuation {
  outcome: "OK" | "INSUFFICIENT_DATA";
  price_basis: PriceBasis;
  central_estimate: Money | null;
  fair_market_low: Money | null;
  fair_market_high: Money | null;
  raw_market_median: Money | null;
  range_width_pct: number | null;
  comparable_count: number;
  effective_sample_size: number;
  dispersion: number;
  outliers_removed: number;
  adjustments: Adjustment[];
  insufficient_reason: string | null;
  notes: string[];
}

export interface GapComponent {
  factor: string;
  label: string;
  amount_azn: number;
  evidence: string;
}

export interface GapAnalysis {
  reference_median_azn: number;
  total_gap_azn: number;
  explained_azn: number;
  unexplained_azn: number;
  explained_share: number;
  components: GapComponent[];
}

export interface PricePosition {
  rating: DealRating;
  rating_label: string;
  asking_price: Money | null;
  difference_azn: number | null;
  difference_pct: number | null;
  percentile: number | null;
  within_fair_range: boolean | null;
  /** The "why" behind the badge. Never rendered without it. */
  rationale: string[];
  gap_analysis: GapAnalysis | null;
}

export interface RiskSignal {
  type: string;
  severity: RiskSeverity;
  title: string;
  evidence: string[];
  interpretation: string;
  recommended_verification: string;
  source: string;
  confidence: number;
  evidence_strength: "STRONG" | "MEDIUM" | "WEAK";
}

export interface RiskContribution {
  title: string;
  severity: RiskSeverity;
  marginal_points: number;
}

export interface PositiveSignal {
  title: string;
  evidence: string[];
  source: string;
}

export interface Risk {
  score: number;
  band: string;
  band_label: string;
  signals: RiskSignal[];
  positives: PositiveSignal[];
  contributions: RiskContribution[];
  verification_actions: string[];
}

export interface ConfidenceComponent {
  name: string;
  label: string;
  score: number;
  weight: number;
  contribution_points: number;
  explanation: string;
}

export interface Confidence {
  score_percent: number;
  band: string;
  /** False until interval coverage is validated. The UI must not imply a probability. */
  calibrated: boolean;
  components: ConfidenceComponent[];
  limiting_factors: string[];
  improvements: string[];
}

export interface Distribution {
  p10: number;
  p25: number;
  p50: number;
  p75: number;
  p90: number;
  sample_size: number;
}

export interface MarketContext {
  comparable_count: number;
  effective_sample_size: number;
  match_level: string;
  search_widened: boolean;
  mean_similarity: number;
  asking_price_distribution: Distribution | null;
  adjusted_price_distribution: Distribution | null;
}

export interface Comparable {
  listing_id: string;
  price: Money;
  mileage_km: number | null;
  model_year: number | null;
  trim: string | null;
  city: string | null;
  similarity: number;
  tier: number;
  differences: string[];
  source_url: string | null;
}

export interface LeveragePoint {
  title: string;
  evidence: string;
  strength: "strong" | "moderate" | "weak";
  monetary_basis_azn: number | null;
}

export interface Negotiation {
  available: boolean;
  unavailable_reason: string | null;
  posture: string;
  opening_offer: Money | null;
  target_low: Money | null;
  target_high: Money | null;
  walk_away_above: Money | null;
  observed_market_reduction_pct: number | null;
  reduction_sample_size: number;
  leverage: LeveragePoint[];
  rationale: string[];
}

export interface SellerQuestion {
  question: string;
  why: string;
  priority: Priority;
  triggered_by: string;
}

export interface InspectionItem {
  item: string;
  priority: Priority;
  system: string;
  reason: string;
  triggered_by: string;
}

export interface Vehicle {
  description: string;
  make: string | null;
  model: string | null;
  model_year: number | null;
  generation: string | null;
  trim: string | null;
  fuel: string;
  transmission: string;
  drivetrain: string;
  body: string;
  engine_displacement_l: number | null;
  horsepower: number | null;
  configuration_id: string;
  specificity: number;
  unknown_attributes: string[];
  mileage_km: number | null;
  city: string | null;
  region: string | null;
  vin_provided: boolean;
}

export interface NarrativeClaim {
  kind: "FACT" | "INFERENCE" | "POSSIBILITY";
  statement: string;
  basis: string;
}

export interface Narrative {
  /** "grok" or "fallback". AI-written prose is labelled as such in the UI. */
  generated_by: string;
  is_ai_generated: boolean;
  degraded_reason: string | null;
  vehicle_summary: string;
  market_context: string;
  price_explanation: string;
  final_assessment: string;
  positive_signals: NarrativeClaim[];
  risk_signals: NarrativeClaim[];
  limitations: string[];
}

export interface Analysis {
  analysis_id: string;
  generated_at: string;
  vehicle: Vehicle;
  valuation: Valuation;
  price_position: PricePosition;
  risk: Risk;
  confidence: Confidence;
  market: MarketContext;
  comparables: Comparable[];
  negotiation: Negotiation;
  seller_questions: SellerQuestion[];
  inspection_priorities: InspectionItem[];
  candidate_explanations: string[];
  limitations: string[];
  narrative: Narrative | null;
  disclaimer: string;
}

export interface ReferenceData {
  makes: string[];
  cities: string[];
  fuels: string[];
  transmissions: string[];
  drivetrains: string[];
  bodies: string[];
}

export interface ManualVehicleInput {
  make: string;
  model: string;
  model_year?: number | null;
  trim?: string | null;
  displacement?: number | null;
  fuel?: string | null;
  transmission?: string | null;
  drivetrain?: string | null;
  body?: string | null;
  mileage_km?: number | null;
  asking_price?: number | null;
  currency?: "AZN";
  city?: string | null;
  seller_type?: string | null;
  owner_count?: number | null;
  has_damage_disclosure?: boolean | null;
  has_repaint_disclosure?: boolean | null;
  service_records_provided?: boolean;
  description?: string | null;
  vin?: string | null;
}

// --- accounts --------------------------------------------------------------

export interface User {
  id: number;
  email: string;
  first_name: string | null;
  last_name: string | null;
  display_name: string | null;
  birth_year: number | null;
  locale: string;
  plan: string;
}

export interface Registration {
  email: string;
  password: string;
  first_name?: string | null;
  last_name?: string | null;
  birth_year?: number | null;
  locale?: string;
}

/** Omitted fields are left alone; this is a patch, not a replacement. */
export interface ProfileUpdate {
  first_name?: string | null;
  last_name?: string | null;
  birth_year?: number | null;
  locale?: string;
}

export interface SavedVehicle {
  id: number;
  config_id: string | null;
  analysis_id: string | null;
  label: string | null;
  target_price_azn: string | number | null;
  last_seen_price_azn: string | number | null;
  notify_on_price_drop: boolean;
  notify_on_removal: boolean;
  created_at: string;
}

export interface SaveVehiclePayload {
  config_id: string;
  analysis_id?: string | null;
  label?: string | null;
  target_price_azn?: number | null;
}

// --- today's finds ---------------------------------------------------------

export interface Find {
  listing_id: number;
  source_url: string | null;
  make: string | null;
  model: string | null;
  model_year: number | null;
  city: string | null;
  mileage_km: number | null;
  price_azn: string | number;
  median_azn: string | number;
  below_median_pct: number;
  sample_size: number;
  dispersion: number | null;
  median_mileage_km: number | null;
  mileage_vs_median_pct: number | null;
}

export interface FindsResponse {
  generated_from_snapshot: boolean;
  window_days: number;
  min_sample_size: number;
  finds: Find[];
  /** Ships with the data so no client can render the numbers without it. */
  caveat: string;
}

// --- discover --------------------------------------------------------------

export interface Budget {
  low_azn: string | number;
  high_azn: string | number;
  centre_azn: string | number;
  observations: number;
  /** "history" when inferred from behaviour, "stated" when the user said it. */
  source: "history" | "stated";
}

export interface Recommendation {
  listing_id: number;
  source_url: string | null;
  make: string | null;
  model: string | null;
  model_year: number | null;
  city: string | null;
  mileage_km: number | null;
  price_azn: string | number;
  median_azn: string | number | null;
  vs_median_pct: number | null;
}

export interface DiscoverResponse {
  budget: Budget | null;
  observations_needed: number;
  recommendations: Recommendation[];
  note: string;
}
