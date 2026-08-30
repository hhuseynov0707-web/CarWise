"use client";

/**
 * Vehicle input (spec §3 Mode B).
 *
 * Six fields are visible by default — make, model, year, mileage, price,
 * location. Everything else is optional and folded away, because the friction
 * of a twenty-field form is the difference between a user who gets an analysis
 * and one who closes the tab.
 *
 * The optional fields are not decoration: each one narrows the comparable set
 * and raises confidence, and the form says so rather than leaving the user to
 * guess why it is asking.
 */

import { useEffect, useState } from "react";
import type { ManualVehicleInput, ReferenceData } from "@/lib/types";
import { fetchReferenceData } from "@/lib/api";

const VIN_LENGTH = 17;

interface Props {
  onSubmit: (vehicle: ManualVehicleInput) => void;
  disabled: boolean;
}

const EMPTY: ManualVehicleInput = {
  make: "",
  model: "",
  model_year: null,
  mileage_km: null,
  asking_price: null,
  city: null,
};

export function VehicleForm({ onSubmit, disabled }: Props) {
  const [vehicle, setVehicle] = useState<ManualVehicleInput>(EMPTY);
  const [reference, setReference] = useState<ReferenceData | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    // A failure here is not fatal — the fields fall back to free text, and the
    // backend validates and normalizes regardless.
    fetchReferenceData()
      .then(setReference)
      .catch(() => setReference(null));
  }, []);

  const set = <K extends keyof ManualVehicleInput>(
    key: K,
    value: ManualVehicleInput[K],
  ) => setVehicle((current) => ({ ...current, [key]: value }));

  const numeric = (raw: string): number | null => {
    const cleaned = raw.replace(/[^\d.]/g, "");
    return cleaned === "" ? null : Number(cleaned);
  };

  // I, O and Q never appear in a VIN — they are too easily confused with 1 and 0.
  const setVin = (raw: string) =>
    set("vin", raw.toUpperCase().replace(/[^A-HJ-NPR-Z0-9]/g, "").slice(0, VIN_LENGTH) || null);

  const vinLength = vehicle.vin?.length ?? 0;
  const vinIncomplete = vinLength > 0 && vinLength !== VIN_LENGTH;

  // A partial VIN is rejected by the API, so it is caught here rather than
  // spent as a round trip that comes back as a validation error.
  const canSubmit =
    vehicle.make.trim() !== "" && vehicle.model.trim() !== "" && !vinIncomplete && !disabled;

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (canSubmit) onSubmit(vehicle);
      }}
      className="card"
    >
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <div>
          <label className="label" htmlFor="make">
            Make <span className="text-rating-over">*</span>
          </label>
          <input
            id="make"
            list="makes"
            required
            className="field"
            placeholder="BMW"
            value={vehicle.make}
            onChange={(event) => set("make", event.target.value)}
          />
          {reference ? (
            <datalist id="makes">
              {reference.makes.map((make) => (
                <option key={make} value={make} />
              ))}
            </datalist>
          ) : null}
        </div>

        <div>
          <label className="label" htmlFor="model">
            Model <span className="text-rating-over">*</span>
          </label>
          <input
            id="model"
            required
            className="field"
            placeholder="5 Series"
            value={vehicle.model}
            onChange={(event) => set("model", event.target.value)}
          />
        </div>

        <div>
          <label className="label" htmlFor="year">
            Model year
          </label>
          <input
            id="year"
            inputMode="numeric"
            className="field tnum"
            placeholder="2019"
            value={vehicle.model_year ?? ""}
            onChange={(event) => set("model_year", numeric(event.target.value))}
          />
        </div>

        <div>
          <label className="label" htmlFor="mileage">
            Mileage (km)
          </label>
          <input
            id="mileage"
            inputMode="numeric"
            className="field tnum"
            placeholder="120000"
            value={vehicle.mileage_km ?? ""}
            onChange={(event) => set("mileage_km", numeric(event.target.value))}
          />
          <p className="mt-1 text-xs text-ink-faint">The largest single price factor.</p>
        </div>

        <div>
          <label className="label" htmlFor="price">
            Asking price (AZN)
          </label>
          <input
            id="price"
            inputMode="numeric"
            className="field tnum"
            placeholder="42000"
            value={vehicle.asking_price ?? ""}
            onChange={(event) => set("asking_price", numeric(event.target.value))}
          />
        </div>

        <div>
          <label className="label" htmlFor="city">
            Location
          </label>
          <input
            id="city"
            list="cities"
            className="field"
            placeholder="Bakı"
            value={vehicle.city ?? ""}
            onChange={(event) => set("city", event.target.value || null)}
          />
          {reference ? (
            <datalist id="cities">
              {reference.cities.map((city) => (
                <option key={city} value={city} />
              ))}
            </datalist>
          ) : null}
        </div>
      </div>

      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        className="mt-4 text-xs font-medium text-accent hover:underline"
      >
        {expanded ? "Fewer details" : "More details — each one raises confidence"}
      </button>

      {expanded ? (
        <div className="mt-4 grid gap-4 border-t border-surface-border pt-4 sm:grid-cols-2 lg:grid-cols-3">
          <div className="sm:col-span-2 lg:col-span-3">
            <label className="label" htmlFor="vin">
              VIN
            </label>
            <input
              id="vin"
              className="field tnum"
              placeholder="WBA5A7C51FD000000"
              maxLength={VIN_LENGTH}
              autoCapitalize="characters"
              autoComplete="off"
              spellCheck={false}
              value={vehicle.vin ?? ""}
              onChange={(event) => setVin(event.target.value)}
              aria-invalid={vinIncomplete || undefined}
              aria-describedby="vin-hint"
            />
            <p id="vin-hint" className="mt-1 text-xs text-ink-faint">
              {vinIncomplete
                ? `A VIN is ${VIN_LENGTH} characters — ${vinLength} so far.`
                : "Confirms the factory specification, so the comparison is against the exact configuration rather than the stated one."}
            </p>
          </div>

          <Select
            id="fuel"
            label="Fuel"
            value={vehicle.fuel ?? ""}
            options={reference?.fuels ?? []}
            onChange={(value) => set("fuel", value)}
          />
          <Select
            id="transmission"
            label="Transmission"
            value={vehicle.transmission ?? ""}
            options={reference?.transmissions ?? []}
            onChange={(value) => set("transmission", value)}
          />
          <Select
            id="drivetrain"
            label="Drivetrain"
            value={vehicle.drivetrain ?? ""}
            options={reference?.drivetrains ?? []}
            onChange={(value) => set("drivetrain", value)}
          />
          <Select
            id="body"
            label="Body style"
            value={vehicle.body ?? ""}
            options={reference?.bodies ?? []}
            onChange={(value) => set("body", value)}
          />

          <div>
            <label className="label" htmlFor="trim">
              Trim
            </label>
            <input
              id="trim"
              className="field"
              placeholder="530i xDrive"
              value={vehicle.trim ?? ""}
              onChange={(event) => set("trim", event.target.value || null)}
            />
          </div>

          <div>
            <label className="label" htmlFor="engine">
              Engine (litres)
            </label>
            <input
              id="engine"
              inputMode="decimal"
              className="field tnum"
              placeholder="2.0"
              value={vehicle.displacement ?? ""}
              onChange={(event) => set("displacement", numeric(event.target.value))}
            />
          </div>

          <div>
            <label className="label" htmlFor="owners">
              Previous owners
            </label>
            <input
              id="owners"
              inputMode="numeric"
              className="field tnum"
              value={vehicle.owner_count ?? ""}
              onChange={(event) => set("owner_count", numeric(event.target.value))}
            />
          </div>

          <TriState
            id="damage"
            label="Accident damage"
            value={vehicle.has_damage_disclosure ?? null}
            onChange={(value) => set("has_damage_disclosure", value)}
          />
          <TriState
            id="repaint"
            label="Repainted panels"
            value={vehicle.has_repaint_disclosure ?? null}
            onChange={(value) => set("has_repaint_disclosure", value)}
          />

          <div className="sm:col-span-2 lg:col-span-3">
            <label className="label" htmlFor="description">
              Listing description
            </label>
            <textarea
              id="description"
              rows={3}
              className="field"
              placeholder="Paste the seller's description — disclosures in it are analysed."
              value={vehicle.description ?? ""}
              onChange={(event) => set("description", event.target.value || null)}
            />
          </div>

          <label className="flex items-center gap-2 text-sm text-ink-soft">
            <input
              type="checkbox"
              className="h-4 w-4 rounded border-surface-border text-accent"
              checked={vehicle.service_records_provided ?? false}
              onChange={(event) => set("service_records_provided", event.target.checked)}
            />
            Service records available
          </label>
        </div>
      ) : null}

      <div className="mt-6 flex flex-wrap items-center gap-4 border-t border-surface-border pt-4">
        <button
          type="submit"
          disabled={!canSubmit}
          className="rounded-md bg-ink px-5 py-2.5 text-sm font-medium text-white
                     transition hover:bg-ink-soft disabled:cursor-not-allowed
                     disabled:bg-ink-faint"
        >
          {disabled ? "Analysing…" : "Analyse this vehicle"}
        </button>
        <p className="text-xs text-ink-muted">
          Prices in AZN. The analysis compares against listings observed in the local
          market.
        </p>
      </div>
    </form>
  );
}

function Select({
  id,
  label,
  value,
  options,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  options: string[];
  onChange: (value: string | null) => void;
}) {
  return (
    <div>
      <label className="label" htmlFor={id}>
        {label}
      </label>
      <select
        id={id}
        className="field"
        value={value}
        onChange={(event) => onChange(event.target.value || null)}
      >
        <option value="">Not specified</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {option.charAt(0) + option.slice(1).toLowerCase().replace(/_/g, " ")}
          </option>
        ))}
      </select>
    </div>
  );
}

/**
 * Three states, not a checkbox.
 *
 * "The seller says there is no damage" and "the listing does not mention
 * damage" are different claims, and collapsing them into an unchecked box would
 * silently assert the first when only the second is true.
 */
function TriState({
  id,
  label,
  value,
  onChange,
}: {
  id: string;
  label: string;
  value: boolean | null;
  onChange: (value: boolean | null) => void;
}) {
  const options: Array<{ label: string; value: boolean | null }> = [
    { label: "Not stated", value: null },
    { label: "None", value: false },
    { label: "Disclosed", value: true },
  ];

  return (
    <div>
      <span className="label" id={`${id}-label`}>
        {label}
      </span>
      <div
        role="radiogroup"
        aria-labelledby={`${id}-label`}
        className="mt-1.5 flex rounded-md border border-surface-border"
      >
        {options.map((option, index) => (
          <button
            key={String(option.value)}
            type="button"
            role="radio"
            aria-checked={value === option.value}
            onClick={() => onChange(option.value)}
            className={`flex-1 px-2 py-2 text-xs transition ${
              index > 0 ? "border-l border-surface-border" : ""
            } ${
              value === option.value
                ? "bg-ink font-medium text-white"
                : "text-ink-soft hover:bg-surface-sunken"
            }`}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}
